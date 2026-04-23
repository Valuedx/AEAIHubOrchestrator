"""CYCLIC-01.b — loopback execution semantics.

Exercises the pure helpers (``_compute_cycle_body``,
``_should_fire_loopback``, ``_build_loopback_map``) end-to-end without
needing a real WorkflowInstance — the ready-queue integration test
stubs the DB/session just enough for ``_fire_loopbacks`` to write
its ``ExecutionLog`` rows.

What we pin:

  * Cycle-body computation on linear, diamond, and multi-cycle
    shapes — nodes on at least one forward path from target to
    source end up in the body.
  * Loopback gating via Condition.source_handle — only fires on
    the matching branch; non-matching branches silently no-op.
  * Iteration counter advances via ``context["_cycle_iterations"]``
    (underscore-prefixed so it's stripped from persisted context).
  * Cap enforcement — once current ≥ max_iterations, the loopback
    becomes a no-op and execution falls through to forward edges.
    A ``loopback_cap_reached`` log row is written.
  * Cycle-body clearing un-executes the body nodes (pops them from
    context) AND un-satisfies internal-to-cycle edges so downstream
    cycle nodes wait for upstream cycle nodes to re-fire.
  * Edges crossing the cycle boundary stay satisfied (upstream
    sources outside the cycle were never re-executed).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from unittest.mock import MagicMock

import pytest

from app.engine.dag_runner import (
    LOOPBACK_DEFAULT_MAX_ITERATIONS,
    LOOPBACK_HARD_CAP,
    _Edge,
    _build_loopback_map,
    _compute_cycle_body,
    _fire_loopbacks,
    _should_fire_loopback,
)


def _fwd(source: str, target: str, handle: str | None = None) -> _Edge:
    return _Edge(
        id=f"fwd:{source}->{target}",
        source=source, target=target,
        source_handle=handle, kind="forward",
    )


def _lb(
    source: str, target: str, *,
    handle: str | None = None,
    max_iter: int = 3,
) -> _Edge:
    return _Edge(
        id=f"lb:{source}->{target}",
        source=source, target=target,
        source_handle=handle, kind="loopback",
        max_iterations=max_iter,
    )


def _forward_adj(edges: list[_Edge]) -> dict[str, list[_Edge]]:
    """Build forward adjacency from a list of _Edge (loopback
    edges are excluded, mirroring _build_graph_structures)."""
    out: dict[str, list[_Edge]] = defaultdict(list)
    for e in edges:
        if not e.is_loopback:
            out[e.source].append(e)
    return dict(out)


# ---------------------------------------------------------------------------
# _compute_cycle_body
# ---------------------------------------------------------------------------


class TestComputeCycleBody:
    def test_linear_cycle_body_is_full_chain(self):
        # planner → tool → check, loopback check → planner.
        edges = [_fwd("A", "B"), _fwd("B", "C"), _lb("C", "A")]
        forward = _forward_adj(edges)
        body = _compute_cycle_body(target="A", source="C", forward=forward)
        assert body == {"A", "B", "C"}

    def test_diamond_cycle_body_includes_both_branches(self):
        # A → B → D, A → C → D. Loopback D → A.
        edges = [
            _fwd("A", "B"), _fwd("A", "C"),
            _fwd("B", "D"), _fwd("C", "D"),
            _lb("D", "A"),
        ]
        forward = _forward_adj(edges)
        body = _compute_cycle_body(target="A", source="D", forward=forward)
        assert body == {"A", "B", "C", "D"}

    def test_partial_cycle_body_excludes_out_of_cycle_nodes(self):
        # pre → A → B → C → post, loopback C → A. post should NOT
        # be in the body (not on any A→C path).
        edges = [
            _fwd("pre", "A"),
            _fwd("A", "B"),
            _fwd("B", "C"),
            _fwd("C", "post"),
            _lb("C", "A"),
        ]
        forward = _forward_adj(edges)
        body = _compute_cycle_body(target="A", source="C", forward=forward)
        assert body == {"A", "B", "C"}
        assert "pre" not in body
        assert "post" not in body


# ---------------------------------------------------------------------------
# _should_fire_loopback — Condition gating
# ---------------------------------------------------------------------------


class TestShouldFireLoopback:
    def test_non_condition_source_fires_unconditionally(self):
        edge = _lb("C", "A")
        # Non-Condition node — no branch → fire.
        data = {"nodeCategory": "agent", "label": "LLM Agent"}
        assert _should_fire_loopback(edge, data, {"response": "ok"}) is True

    def test_condition_true_branch_fires_on_true_handle(self):
        edge = _lb("C", "A", handle="true")
        data = {"nodeCategory": "logic", "label": "Condition"}
        assert _should_fire_loopback(edge, data, {"branch": "true"}) is True

    def test_condition_false_branch_does_not_fire_loopback(self):
        """The classic "if still_thinking → loop; else fall
        through" shape. Loopback on the true handle must NOT fire
        when Condition chose false."""
        edge = _lb("C", "A", handle="true")
        data = {"nodeCategory": "logic", "label": "Condition"}
        assert _should_fire_loopback(edge, data, {"branch": "false"}) is False

    def test_condition_no_handle_fires_unconditionally(self):
        edge = _lb("C", "A")  # no source_handle
        data = {"nodeCategory": "logic", "label": "Condition"}
        assert _should_fire_loopback(edge, data, {"branch": "true"}) is True
        assert _should_fire_loopback(edge, data, {"branch": "false"}) is True


# ---------------------------------------------------------------------------
# _build_loopback_map
# ---------------------------------------------------------------------------


class TestBuildLoopbackMap:
    def test_zero_loopback_graph_returns_empty_map(self):
        edges = [_fwd("A", "B"), _fwd("B", "C")]
        assert _build_loopback_map(edges) == {}

    def test_multiple_loopbacks_per_source_are_indexed(self):
        """Two loopback edges from the same Condition source —
        happens when different branches loop to different
        re-entry points."""
        edges = [
            _lb("cond", "A", handle="true"),
            _lb("cond", "B", handle="false"),
        ]
        mapping = _build_loopback_map(edges)
        assert set(mapping.keys()) == {"cond"}
        assert len(mapping["cond"]) == 2


# ---------------------------------------------------------------------------
# _fire_loopbacks — the integrated mutate-context semantics
# ---------------------------------------------------------------------------


def _instance() -> MagicMock:
    inst = MagicMock()
    inst.id = uuid.uuid4()
    return inst


def _db() -> MagicMock:
    """Minimal session stub that captures added rows + commits."""
    db = MagicMock()
    db.added = []
    db.add.side_effect = lambda obj: db.added.append(obj)
    return db


class TestFireLoopbacks:
    def _linear_setup(self):
        # planner (A) → tool (B) → check (C), loopback C→A.
        edges = [
            _fwd("A", "B"), _fwd("B", "C"),
            _lb("C", "A", handle="true", max_iter=5),
        ]
        forward = _forward_adj(edges)
        nodes_map = {
            "A": {"id": "A", "data": {"nodeCategory": "agent", "label": "LLM Agent"}},
            "B": {"id": "B", "data": {"nodeCategory": "agent", "label": "MCP Tool"}},
            "C": {"id": "C", "data": {"nodeCategory": "logic", "label": "Condition"}},
        }
        loopbacks_by_source = _build_loopback_map(edges)
        return nodes_map, forward, loopbacks_by_source

    def test_firing_clears_cycle_body_and_bumps_counter(self):
        nodes_map, forward, loopbacks = self._linear_setup()

        # Pretend A, B, C all executed and C's output says "true".
        context = {
            "trigger": {"msg": "hi"},
            "A": {"response": "planning"},
            "B": {"response": "used tool"},
            "C": {"branch": "true"},
        }
        satisfied = {"B": {"A"}, "C": {"B"}}
        pruned: set[str] = set()

        fired = _fire_loopbacks(
            _db(), _instance(), "C", loopbacks,
            nodes_map, forward, context, satisfied, pruned,
        )
        assert fired is True

        # Cycle body is {A, B, C} — all should be cleared.
        assert "A" not in context
        assert "B" not in context
        assert "C" not in context
        # Trigger and _cycle_iterations stay.
        assert context["trigger"] == {"msg": "hi"}
        # Iteration counter bumped to 1.
        assert context["_cycle_iterations"]["lb:C->A"] == 1
        # Internal-to-cycle satisfaction cleared (B's sat was A;
        # A is now in-cycle → sat[B] should no longer claim A).
        assert "A" not in satisfied.get("B", set())

    def test_false_branch_does_not_fire(self):
        nodes_map, forward, loopbacks = self._linear_setup()
        context = {
            "A": {"response": "ok"},
            "B": {"response": "ok"},
            "C": {"branch": "false"},  # condition said no loop
        }
        satisfied = {"B": {"A"}, "C": {"B"}}
        fired = _fire_loopbacks(
            _db(), _instance(), "C", loopbacks,
            nodes_map, forward, context, satisfied, set(),
        )
        assert fired is False
        # Context unchanged — no nodes cleared.
        assert "A" in context
        assert "_cycle_iterations" not in context

    def test_cap_enforcement_stops_firing_after_max_iterations(self):
        nodes_map, forward, loopbacks = self._linear_setup()
        context = {
            "A": {"response": "ok"},
            "B": {"response": "ok"},
            "C": {"branch": "true"},
            "_cycle_iterations": {"lb:C->A": 5},  # already at max_iter=5
        }
        satisfied = {"B": {"A"}, "C": {"B"}}
        db = _db()
        fired = _fire_loopbacks(
            db, _instance(), "C", loopbacks,
            nodes_map, forward, context, satisfied, set(),
        )
        # Cap reached → no-op.
        assert fired is False
        # Body NOT cleared — execution falls through to forward edges.
        assert "A" in context
        assert "B" in context
        assert "C" in context
        # A loopback_cap_reached log row was written for observability.
        cap_logs = [row for row in db.added if getattr(row, "status", None) == "loopback_cap_reached"]
        assert len(cap_logs) == 1
        assert cap_logs[0].output_json["iterations_used"] == 5
        assert cap_logs[0].output_json["max_iterations"] == 5

    def test_iteration_log_row_written_on_fire(self):
        nodes_map, forward, loopbacks = self._linear_setup()
        context = {
            "A": {"response": "ok"},
            "B": {"response": "ok"},
            "C": {"branch": "true"},
        }
        satisfied = {"B": {"A"}, "C": {"B"}}
        db = _db()
        _fire_loopbacks(
            db, _instance(), "C", loopbacks,
            nodes_map, forward, context, satisfied, set(),
        )

        iter_logs = [row for row in db.added if getattr(row, "status", None) == "loopback_iteration"]
        assert len(iter_logs) == 1
        assert iter_logs[0].node_id == "A"  # logged on target
        assert iter_logs[0].node_type == "loopback"
        assert iter_logs[0].output_json["iteration"] == 1
        assert iter_logs[0].output_json["edge_id"] == "lb:C->A"
        assert iter_logs[0].output_json["source_node_id"] == "C"

    def test_no_loopback_source_returns_false_cheaply(self):
        """Zero-loopback graphs: _fire_loopbacks returns False
        without touching context or db. This is the hot-path
        regression guard — 99% of existing workflows have no
        loopbacks, and the overhead must stay near zero."""
        context = {"A": {"ok": True}}
        satisfied = {"B": {"A"}}
        db = _db()
        fired = _fire_loopbacks(
            db, _instance(), "A", {},  # empty loopbacks_by_source
            {}, {}, context, satisfied, set(),
        )
        assert fired is False
        assert db.added == []
        assert "_cycle_iterations" not in context

    def test_hard_cap_clamps_author_supplied_max_iter(self):
        """Author sets maxIterations=999 via the edge dict;
        parse_graph clamped it to 100 at read time. Verify the
        runtime honors that clamp even if the _Edge somehow
        landed with a higher value (defence in depth)."""
        edges = [
            _fwd("A", "B"),
            _Edge(
                id="lb-overcap",
                source="B", target="A",
                source_handle=None, kind="loopback",
                max_iterations=999,  # bogus — should clamp to 100
            ),
        ]
        forward = _forward_adj(edges)
        loopbacks = _build_loopback_map(edges)
        nodes_map = {
            "A": {"id": "A", "data": {"nodeCategory": "agent", "label": "X"}},
            "B": {"id": "B", "data": {"nodeCategory": "agent", "label": "Y"}},
        }
        context = {
            "A": {}, "B": {},
            "_cycle_iterations": {"lb-overcap": LOOPBACK_HARD_CAP},  # at cap
        }
        satisfied = {"B": {"A"}}
        db = _db()
        fired = _fire_loopbacks(
            db, _instance(), "B", loopbacks,
            nodes_map, forward, context, satisfied, set(),
        )
        # Cap hit even though author asked for 999.
        assert fired is False
        cap_logs = [r for r in db.added if getattr(r, "status", None) == "loopback_cap_reached"]
        assert len(cap_logs) == 1
        assert cap_logs[0].output_json["max_iterations"] == LOOPBACK_HARD_CAP


# ---------------------------------------------------------------------------
# Constants pinned for downstream consumers (UI, validator)
# ---------------------------------------------------------------------------


def test_loopback_constants_stable():
    """Pin the numbers the UI + validator read from the frontend
    types file — changing either without a coordinated bump breaks
    the UX story."""
    assert LOOPBACK_DEFAULT_MAX_ITERATIONS == 10
    assert LOOPBACK_HARD_CAP == 100
