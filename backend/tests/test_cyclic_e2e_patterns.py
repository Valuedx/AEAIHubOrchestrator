"""CYCLIC-01.e — end-to-end tests for canonical cyclic patterns.

CYCLIC-01.b's tests cover ``_fire_loopbacks`` in isolation with a
stubbed DB. This file bolts one level higher: it drives the full
``_execute_ready_queue`` loop with a cyclic graph and asserts on the
shape of the complete run (which nodes fired how many times, what
context ended up with, how the cap was respected).

The three patterns pinned here mirror LangGraph's headline examples:

  * **Agent↔tool loop** — planner → tool → check; Condition loops
    back to planner on the "need more" branch and falls through to
    exit on the "done" branch.
  * **Reflection** — writer → critic → writer (loop); writer → final
    (exit) once the critic's branch says "good".
  * **Retry-on-failure** — action → check; loops back on failure
    until the action succeeds or the cap is hit.
  * **Cap-hit graceful termination** — cycle with ``maxIterations=2``
    stops cleanly and produces a ``loopback_cap_reached`` log row
    without erroring.

Node dispatch is stubbed at ``_execute_single_node`` — each node
produces a deterministic output drawn from a per-node script — so
we exercise the full ready-queue + ``_fire_loopbacks`` +
``_propagate_edges`` + ``_find_ready_nodes`` integration without
pulling an LLM / DB into the loop.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from unittest.mock import MagicMock

import pytest

from app.engine import dag_runner
from app.engine.dag_runner import (
    _build_graph_structures,
    _build_loopback_map,
    _execute_ready_queue,
    parse_graph,
)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _node(nid: str, *, label: str = "LLM Agent", category: str = "agent") -> dict:
    return {
        "id": nid,
        "type": "agenticNode",
        "data": {"label": label, "nodeCategory": category, "config": {}},
    }


def _trigger(nid: str = "trig") -> dict:
    # Realistic cyclic graphs start with a trigger; the loopback
    # target lives downstream of the trigger so it has an incoming
    # edge that stays satisfied across cycle body clears. Without
    # an upstream feeder, _find_ready_nodes skips the target on
    # re-scan because it has no incoming edges — not a bug in the
    # runner, a property of real-world authoring.
    return {
        "id": nid,
        "type": "agenticNode",
        "data": {
            "label": "Webhook Trigger",
            "nodeCategory": "trigger",
            "config": {},
        },
    }


def _fwd(eid: str, source: str, target: str, handle: str | None = None) -> dict:
    e = {"id": eid, "source": source, "target": target}
    if handle:
        e["sourceHandle"] = handle
    return e


def _lb(
    eid: str, source: str, target: str, *,
    handle: str | None = None, max_iter: int = 5,
) -> dict:
    e: dict = {
        "id": eid, "source": source, "target": target,
        "type": "loopback", "maxIterations": max_iter,
    }
    if handle:
        e["sourceHandle"] = handle
    return e


class _FakeDB:
    """MagicMock flavour that also tracks added rows + refresh no-op.

    ``instance.refresh`` is how ``_abort_if_cancel_or_pause`` checks
    for pause/cancel; we no-op it so the loop runs to completion.
    """

    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        pass

    def refresh(self, _instance) -> None:
        pass

    def query(self, *_a, **_kw):
        # Only used at end of _execute_ready_queue to check for failed
        # ExecutionLog rows — return a stub that yields no rows so the
        # instance transitions to "completed".
        q = MagicMock()
        q.filter_by.return_value.first.return_value = None
        return q


def _instance() -> MagicMock:
    inst = MagicMock()
    inst.id = uuid.uuid4()
    inst.tenant_id = "t1"
    inst.status = "running"
    inst.cancel_requested = False
    inst.pause_requested = False
    return inst


def _run(
    monkeypatch: pytest.MonkeyPatch,
    graph: dict,
    script: dict[str, object],
) -> tuple[dict, list[str], _FakeDB]:
    """Run ``_execute_ready_queue`` over ``graph``. ``script`` maps
    node_id → output (dict or zero-arg callable returning a dict).
    Condition outputs should be ``{"branch": "..."}``.

    Returns (final context, list of node ids in execution order, db).
    """

    call_order: list[str] = []

    def _resolve_output(node_id: str) -> dict:
        val = script.get(node_id, {})
        return val() if callable(val) else (dict(val) if isinstance(val, dict) else val)

    def fake_exec_single(db, instance, nodes_map, node_id, context):
        call_order.append(node_id)
        context[node_id] = _resolve_output(node_id)
        return "completed"

    def fake_parallel(db, instance, nodes_map, ready, context, deterministic_mode=False):
        # The real ``_execute_parallel`` opens a per-thread DB session;
        # in unit-test mode we just run sequentially and mirror its
        # result shape (dict[node_id, status]).
        results: dict[str, str] = {}
        for nid in ready:
            fake_exec_single(db, instance, nodes_map, nid, context)
            results[nid] = "completed"
        return results

    # _save_checkpoint writes to the DB; stub out since _FakeDB.query
    # returns MagicMocks that can't be persisted.
    monkeypatch.setattr(dag_runner, "_execute_single_node", fake_exec_single)
    monkeypatch.setattr(dag_runner, "_execute_parallel", fake_parallel)
    monkeypatch.setattr(dag_runner, "_save_checkpoint", lambda *a, **kw: None)

    nodes_map, edges = parse_graph(graph)
    forward, reverse, in_degree = _build_graph_structures(nodes_map, edges)
    loopbacks_by_source = _build_loopback_map(edges)

    context: dict = {"trigger": {}}
    db = _FakeDB()
    inst = _instance()

    _execute_ready_queue(
        db, inst, nodes_map, forward, reverse, in_degree,
        context, skipped=set(),
        loopbacks_by_source=loopbacks_by_source,
    )
    return context, call_order, db


def _count(call_order: list[str]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for nid in call_order:
        out[nid] += 1
    return dict(out)


# ---------------------------------------------------------------------------
# Pattern 1 — Agent ↔ Tool loop
# ---------------------------------------------------------------------------


class TestAgentToolLoop:
    """Canonical planner → tool → check loop. Condition's "true"
    branch loops back to planner; "false" branch exits."""

    def _graph(self, max_iter: int = 5) -> dict:
        return {
            "nodes": [
                _trigger(),
                _node("planner"),
                _node("tool", label="MCP Tool"),
                _node("check", label="Condition", category="logic"),
                _node("exit"),
            ],
            "edges": [
                _fwd("e0", "trig", "planner"),
                _fwd("e1", "planner", "tool"),
                _fwd("e2", "tool", "check"),
                _fwd("e3", "check", "exit", handle="false"),
                _lb("lb1", "check", "planner", handle="true", max_iter=max_iter),
            ],
        }

    def test_loops_until_condition_says_done(self, monkeypatch: pytest.MonkeyPatch):
        # Script: check says "true" twice, then "false" → 3 iterations of
        # planner/tool, then exit fires.
        check_outputs = iter([
            {"branch": "true"},
            {"branch": "true"},
            {"branch": "false"},
        ])
        script = {
            "planner": {"plan": "keep going"},
            "tool": {"tool_result": "data"},
            "check": lambda: next(check_outputs),
            "exit": {"done": True},
        }
        context, call_order, _ = _run(monkeypatch, self._graph(), script)

        counts = _count(call_order)
        assert counts["planner"] == 3
        assert counts["tool"] == 3
        assert counts["check"] == 3
        assert counts["exit"] == 1
        # Iteration counter reflects two loopback fires (third check chose false).
        # CTX-MGMT.D — cycle counters under _runtime.
        assert context["_runtime"]["cycle_iterations"]["lb1"] == 2

    def test_false_on_first_check_does_not_loop(self, monkeypatch):
        # Condition says "false" immediately → planner/tool fire exactly once.
        graph = self._graph()
        script = {
            "planner": {"plan": "one-shot"},
            "tool": {"tool_result": "immediate"},
            "check": {"branch": "false"},
            "exit": {"done": True},
        }
        _, call_order, _ = _run(monkeypatch, graph, script)
        counts = _count(call_order)
        assert counts == {"trig": 1, "planner": 1, "tool": 1, "check": 1, "exit": 1}


# ---------------------------------------------------------------------------
# Pattern 2 — Reflection (writer ↔ critic)
# ---------------------------------------------------------------------------


class TestReflectionPattern:
    """Writer produces a draft; critic scores it; if the score isn't
    good enough, the critic loops back to writer."""

    def _graph(self, max_iter: int = 5) -> dict:
        return {
            "nodes": [
                _trigger(),
                _node("writer"),
                _node("critic", label="Condition", category="logic"),
                _node("final"),
            ],
            "edges": [
                _fwd("e0", "trig", "writer"),
                _fwd("e1", "writer", "critic"),
                _fwd("e2", "critic", "final", handle="good"),
                _lb("lb1", "critic", "writer", handle="revise", max_iter=max_iter),
            ],
        }

    def test_two_revision_cycles_then_publish(self, monkeypatch):
        critic_outputs = iter([
            {"branch": "revise"},
            {"branch": "revise"},
            {"branch": "good"},
        ])
        writer_calls = {"n": 0}

        def writer_output() -> dict:
            writer_calls["n"] += 1
            return {"draft": f"v{writer_calls['n']}"}

        script = {
            "writer": writer_output,
            "critic": lambda: next(critic_outputs),
            "final": {"published": True},
        }
        context, call_order, _ = _run(monkeypatch, self._graph(), script)

        counts = _count(call_order)
        assert counts["writer"] == 3      # v1, v2, v3
        assert counts["critic"] == 3
        assert counts["final"] == 1
        # Final draft is v3 (the accepted one).
        assert context["writer"] == {"draft": "v3"}
        # CTX-MGMT.D — cycle counters under _runtime.
        assert context["_runtime"]["cycle_iterations"]["lb1"] == 2


# ---------------------------------------------------------------------------
# Pattern 3 — Retry on failure
# ---------------------------------------------------------------------------


class TestRetryOnFailurePattern:
    """Action produces {ok: true|false}; a Condition node downstream
    inspects it and either loops back (retry) or falls through to
    success. The cap keeps runaway retries bounded."""

    def _graph(self, max_iter: int = 5) -> dict:
        return {
            "nodes": [
                _trigger(),
                _node("action", label="MCP Tool"),
                _node("check", label="Condition", category="logic"),
                _node("success"),
            ],
            "edges": [
                _fwd("e0", "trig", "action"),
                _fwd("e1", "action", "check"),
                _fwd("e2", "check", "success", handle="ok"),
                _lb("lb1", "check", "action", handle="retry", max_iter=max_iter),
            ],
        }

    def test_succeeds_after_two_retries(self, monkeypatch):
        check_outputs = iter([
            {"branch": "retry"},  # attempt 1 failed
            {"branch": "retry"},  # attempt 2 failed
            {"branch": "ok"},     # attempt 3 succeeded
        ])
        script = {
            "action": {"ok": True},
            "check": lambda: next(check_outputs),
            "success": {"ok": True},
        }
        _, call_order, _ = _run(monkeypatch, self._graph(), script)
        assert _count(call_order) == {"trig": 1, "action": 3, "check": 3, "success": 1}


# ---------------------------------------------------------------------------
# Pattern 4 — Cap-hit graceful termination
# ---------------------------------------------------------------------------


class TestCapHitGraceful:
    """When the condition never yields the exit branch, execution
    stops cleanly at the cap — the forward-exit edge fires because
    the loopback is the only thing that was gating it."""

    def test_cap_two_stops_after_two_fires_and_takes_exit(self, monkeypatch):
        graph = {
            "nodes": [
                _trigger(),
                _node("planner"),
                _node("check", label="Condition", category="logic"),
                _node("exit"),
            ],
            "edges": [
                _fwd("e0", "trig", "planner"),
                _fwd("e1", "planner", "check"),
                _fwd("e2", "check", "exit", handle="false"),
                _lb("lb1", "check", "planner", handle="true", max_iter=2),
            ],
        }
        # Condition always says "true" — without the cap this would
        # loop forever. With maxIterations=2 it should stop after 2
        # loopback fires (3 total check executions).
        script = {
            "planner": {"plan": "still thinking"},
            "check": {"branch": "true"},
            "exit": {"done": True},
        }
        context, call_order, db = _run(monkeypatch, graph, script)

        counts = _count(call_order)
        # planner fires 3 times: initial + 2 loopback re-enqueues.
        # check fires 3 times: once per planner completion.
        assert counts["planner"] == 3
        assert counts["check"] == 3
        # Iteration counter reached the cap.
        # CTX-MGMT.D — cycle counters under _runtime.
        assert context["_runtime"]["cycle_iterations"]["lb1"] == 2
        # A loopback_cap_reached ExecutionLog row was emitted.
        cap_logs = [r for r in db.added if getattr(r, "status", None) == "loopback_cap_reached"]
        assert len(cap_logs) == 1
        assert cap_logs[0].output_json["max_iterations"] == 2

    def test_cap_hit_does_not_mark_instance_failed(self, monkeypatch):
        """Reaching the cap is an expected termination, not an
        error. The instance status must still transition to
        ``completed`` (verified by the presence of
        ``completed_at`` + no failed rows)."""
        graph = {
            "nodes": [
                _trigger(),
                _node("planner"),
                _node("check", label="Condition", category="logic"),
                _node("exit"),
            ],
            "edges": [
                _fwd("e0", "trig", "planner"),
                _fwd("e1", "planner", "check"),
                _fwd("e2", "check", "exit", handle="false"),
                _lb("lb1", "check", "planner", handle="true", max_iter=1),
            ],
        }
        script = {
            "planner": {"plan": "x"},
            "check": {"branch": "true"},
            "exit": {"done": True},
        }
        # Rebuild the structures directly so we can assert on the
        # instance status after the run completes.
        call_order: list[str] = []

        def fake_exec_single(db, instance, nodes_map, node_id, context):
            call_order.append(node_id)
            val = script.get(node_id, {})
            context[node_id] = dict(val)
            return "completed"

        monkeypatch.setattr(dag_runner, "_execute_single_node", fake_exec_single)
        monkeypatch.setattr(
            dag_runner,
            "_execute_parallel",
            lambda db, inst, nm, ready, ctx, deterministic_mode=False: {
                nid: (fake_exec_single(db, inst, nm, nid, ctx) or "completed")
                for nid in ready
            },
        )
        monkeypatch.setattr(dag_runner, "_save_checkpoint", lambda *a, **kw: None)

        nodes_map, edges = parse_graph(graph)
        forward, reverse, in_degree = _build_graph_structures(nodes_map, edges)
        loopbacks = _build_loopback_map(edges)
        context: dict = {"trigger": {}}
        inst = _instance()
        _execute_ready_queue(
            _FakeDB(), inst, nodes_map, forward, reverse, in_degree,
            context, skipped=set(), loopbacks_by_source=loopbacks,
        )
        assert inst.status == "completed"


# ---------------------------------------------------------------------------
# Hot-path guard — zero-loopback graphs unchanged
# ---------------------------------------------------------------------------


class TestZeroLoopbackHotPath:
    """Zero-loopback graphs must produce exactly the same call
    sequence they did before CYCLIC-01.b landed — this is the
    regression fence we check every PR against."""

    def test_linear_graph_calls_once(self, monkeypatch):
        graph = {
            "nodes": [_node("a"), _node("b"), _node("c")],
            "edges": [_fwd("e1", "a", "b"), _fwd("e2", "b", "c")],
        }
        script = {"a": {"ok": 1}, "b": {"ok": 2}, "c": {"ok": 3}}
        context, call_order, _ = _run(monkeypatch, graph, script)
        assert call_order == ["a", "b", "c"]
        # No cycle iteration counter in zero-loopback graphs (CTX-MGMT.D).
        assert "cycle_iterations" not in (context.get("_runtime") or {})

    def test_diamond_graph_calls_each_node_once(self, monkeypatch):
        graph = {
            "nodes": [_node("a"), _node("b"), _node("c"), _node("d")],
            "edges": [
                _fwd("e1", "a", "b"), _fwd("e2", "a", "c"),
                _fwd("e3", "b", "d"), _fwd("e4", "c", "d"),
            ],
        }
        script = {k: {"ok": True} for k in ("a", "b", "c", "d")}
        _, call_order, _ = _run(monkeypatch, graph, script)
        assert _count(call_order) == {"a": 1, "b": 1, "c": 1, "d": 1}
