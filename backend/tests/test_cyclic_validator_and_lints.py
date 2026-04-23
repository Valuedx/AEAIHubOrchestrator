"""CYCLIC-01.c — validator rules + SMART-04 lints for loopback edges.

Two surfaces under test:

1. ``config_validator.validate_graph_configs`` — save-time hard
   errors. Missing/out-of-range maxIterations, duplicate loopbacks,
   target-not-ancestor, and LOOPBACK_NO_EXIT all produce error
   strings that the API layer currently surfaces as save-blocking.
2. ``app.copilot.lints.run_lints`` — post-mutation warnings the
   copilot surfaces through ``check_draft``. ``loopback_no_exit``
   (error) mirrors the validator rule; ``loopback_no_cap`` (warn)
   flags the implicit default; ``loopback_nested_deep`` (warn)
   flags graphs with ≥ 3 distinct cycles.

Zero-loopback graphs must pass both surfaces with no added
findings — the hot path for existing workflows.
"""

from __future__ import annotations

from app.copilot.lints import (
    lint_loopback_nested_deep,
    lint_loopback_no_cap,
    lint_loopback_no_exit,
    run_lints,
)
from app.engine.config_validator import validate_graph_configs


def _node(nid: str, *, label: str = "LLM Agent", category: str = "agent") -> dict:
    return {
        "id": nid,
        "type": "agenticNode",
        "data": {"label": label, "nodeCategory": category, "config": {}},
    }


def _fwd(eid: str, source: str, target: str, handle: str | None = None) -> dict:
    e = {"id": eid, "source": source, "target": target}
    if handle:
        e["sourceHandle"] = handle
    return e


def _lb(
    eid: str, source: str, target: str,
    *, handle: str | None = None, max_iter: int | None = 5,
) -> dict:
    e: dict = {"id": eid, "source": source, "target": target, "type": "loopback"}
    if handle:
        e["sourceHandle"] = handle
    if max_iter is not None:
        e["maxIterations"] = max_iter
    return e


def _linear_cycle(*, handle: str | None = "true", max_iter: int = 5) -> dict:
    """Canonical 4-node cycle: planner → tool → check → exit, with
    loopback check → planner. The forward edge check → exit is
    the required forward-exit path."""
    return {
        "nodes": [
            _node("planner"),
            _node("tool"),
            _node("check", label="Condition", category="logic"),
            _node("exit"),
        ],
        "edges": [
            _fwd("e1", "planner", "tool"),
            _fwd("e2", "tool", "check"),
            _fwd("e3", "check", "exit", handle="false"),
            _lb("lb1", "check", "planner", handle=handle, max_iter=max_iter),
        ],
    }


# ---------------------------------------------------------------------------
# Validator — hard errors
# ---------------------------------------------------------------------------


class TestValidator:
    def test_happy_path_graph_has_no_loopback_errors(self):
        warnings = validate_graph_configs(_linear_cycle())
        loopback_msgs = [w for w in warnings if "Loopback edge" in w]
        assert loopback_msgs == []

    def test_zero_loopback_graph_untouched(self):
        """The entire existing test surface must still pass —
        loopback-less graphs produce zero loopback-related
        warnings."""
        graph = {
            "nodes": [_node("a"), _node("b")],
            "edges": [_fwd("e1", "a", "b")],
        }
        warnings = validate_graph_configs(graph)
        assert all("Loopback edge" not in w for w in warnings)

    def test_duplicate_loopbacks_from_same_source_rejected(self):
        graph = _linear_cycle()
        # Add a second loopback from `check` → `tool`.
        graph["edges"].append(_lb("lb2", "check", "tool", max_iter=3))
        warnings = validate_graph_configs(graph)
        duplicates = [w for w in warnings if "already has another loopback" in w]
        assert len(duplicates) == 1
        assert "lb2" in duplicates[0]

    def test_non_ancestor_target_rejected(self):
        """Loopback ``a→c`` on a linear chain ``a→b→c`` is a
        forward skip masquerading as a cycle: target ``c`` is
        downstream of source ``a``, not upstream. Re-enqueueing
        ``c`` re-executes ``c`` only (nothing to loop back
        through) — not a cycle."""
        graph = {
            "nodes": [_node("a"), _node("b"), _node("c")],
            "edges": [
                _fwd("e1", "a", "b"),
                _fwd("e2", "b", "c"),
                _lb("lb-bad", "a", "c"),  # source=a, target=c (downstream)
            ],
        }
        warnings = validate_graph_configs(graph)
        ancestor_msgs = [
            w for w in warnings
            if "lb-bad" in w and "not reachable" in w
        ]
        assert len(ancestor_msgs) == 1

    def test_no_forward_exit_rejected(self):
        """Linear cycle with NO forward exit — the loopback is the
        only way out, so termination is entirely cap-driven."""
        graph = {
            "nodes": [_node("a"), _node("b"), _node("c")],
            "edges": [
                _fwd("e1", "a", "b"),
                _fwd("e2", "b", "c"),
                _lb("lb1", "c", "a"),
            ],
        }
        warnings = validate_graph_configs(graph)
        no_exit = [w for w in warnings if "no forward exit path" in w]
        assert len(no_exit) == 1

    def test_max_iterations_below_minimum_rejected(self):
        graph = _linear_cycle(max_iter=0)
        warnings = validate_graph_configs(graph)
        assert any("'maxIterations' must be ≥ 1" in w for w in warnings)

    def test_max_iterations_above_hard_cap_rejected(self):
        graph = _linear_cycle(max_iter=9999)
        warnings = validate_graph_configs(graph)
        assert any("'maxIterations' must be ≤ 100" in w for w in warnings)

    def test_max_iterations_non_integer_rejected(self):
        graph = _linear_cycle()
        graph["edges"][-1]["maxIterations"] = "forever"
        warnings = validate_graph_configs(graph)
        assert any(
            "'maxIterations' must be an integer" in w
            for w in warnings
        )

    def test_missing_max_iterations_is_not_an_error(self):
        """Validator doesn't error on missing maxIterations — the
        runtime fills in the default. SMART-04's
        ``loopback_no_cap`` lint warns about it separately."""
        graph = _linear_cycle()
        del graph["edges"][-1]["maxIterations"]
        warnings = validate_graph_configs(graph)
        assert not any("'maxIterations'" in w for w in warnings)


# ---------------------------------------------------------------------------
# SMART-04 lints
# ---------------------------------------------------------------------------


class TestLoopbackNoExitLint:
    def test_cycle_without_exit_flags_error_lint(self):
        graph = {
            "nodes": [_node("a"), _node("b"), _node("c")],
            "edges": [
                _fwd("e1", "a", "b"),
                _fwd("e2", "b", "c"),
                _lb("lb1", "c", "a"),
            ],
        }
        lints = lint_loopback_no_exit(graph)
        assert len(lints) == 1
        assert lints[0].code == "loopback_no_exit"
        assert lints[0].severity == "error"
        assert lints[0].node_id == "a"  # loopback target

    def test_cycle_with_forward_exit_clean(self):
        assert lint_loopback_no_exit(_linear_cycle()) == []

    def test_zero_loopback_graph_clean(self):
        graph = {
            "nodes": [_node("a"), _node("b")],
            "edges": [_fwd("e1", "a", "b")],
        }
        assert lint_loopback_no_exit(graph) == []

    def test_malformed_cycle_skipped_to_avoid_double_error(self):
        """When target is NOT a forward ancestor of source, the
        validator already flags it — the lint should stay silent
        to avoid stacking two errors on the same edge."""
        graph = {
            "nodes": [_node("a"), _node("b")],
            "edges": [
                _fwd("e1", "a", "b"),
                _lb("lb-bad", "a", "b"),  # forward skip, not cycle
            ],
        }
        assert lint_loopback_no_exit(graph) == []


class TestLoopbackNoCapLint:
    def test_missing_max_iterations_flags_warn(self):
        graph = _linear_cycle()
        del graph["edges"][-1]["maxIterations"]
        lints = lint_loopback_no_cap(graph)
        assert len(lints) == 1
        assert lints[0].code == "loopback_no_cap"
        assert lints[0].severity == "warn"

    def test_explicit_max_iterations_does_not_warn(self):
        """An explicit cap — even if it equals the default 10 —
        means the author was intentional."""
        graph = _linear_cycle(max_iter=10)
        assert lint_loopback_no_cap(graph) == []

    def test_zero_loopback_graph_clean(self):
        graph = {"nodes": [_node("a")], "edges": []}
        assert lint_loopback_no_cap(graph) == []


class TestLoopbackNestedDeepLint:
    def _many_cycles(self, n: int) -> dict:
        """n independent 3-node cycles, each with its own forward
        exit. Nothing overlaps so each counts as a distinct cycle."""
        nodes: list[dict] = []
        edges: list[dict] = []
        for i in range(n):
            a, b, c, x = f"a{i}", f"b{i}", f"c{i}", f"x{i}"
            nodes.extend([_node(a), _node(b),
                          _node(c, label="Condition", category="logic"),
                          _node(x)])
            edges.extend([
                _fwd(f"e{i}1", a, b),
                _fwd(f"e{i}2", b, c),
                _fwd(f"e{i}3", c, x, handle="false"),
                _lb(f"lb{i}", c, a, handle="true", max_iter=5),
            ])
        return {"nodes": nodes, "edges": edges}

    def test_two_cycles_does_not_warn(self):
        assert lint_loopback_nested_deep(self._many_cycles(2)) == []

    def test_three_cycles_warns(self):
        lints = lint_loopback_nested_deep(self._many_cycles(3))
        assert len(lints) == 1
        assert lints[0].code == "loopback_nested_deep"
        assert lints[0].severity == "warn"
        assert "3 distinct cycles" in lints[0].message

    def test_overlapping_cycles_counted_as_one(self):
        """Two loopback edges whose bodies share a node are the
        same logical cycle from the author's perspective —
        shouldn't inflate the nested count."""
        # Diamond with two loopbacks both landing on A.
        graph = {
            "nodes": [_node("a"), _node("b"), _node("c"), _node("d"), _node("exit")],
            "edges": [
                _fwd("e1", "a", "b"),
                _fwd("e2", "a", "c"),
                _fwd("e3", "b", "d"),
                _fwd("e4", "c", "d"),
                _fwd("e5", "d", "exit"),
                _lb("lb1", "b", "a"),   # body = {a, b}
                _lb("lb2", "c", "a"),   # body = {a, c} — shares {a}
            ],
        }
        # Both cycles share 'a' so dedup merges them → count=1.
        assert lint_loopback_nested_deep(graph) == []


# ---------------------------------------------------------------------------
# Composer — run_lints runs all rules
# ---------------------------------------------------------------------------


class TestRunLintsIntegration:
    def test_runs_all_three_loopback_lints(self):
        """A graph that triggers no_exit (no forward exit) + no_cap
        (missing maxIterations) + the existing no_trigger (no
        trigger node) should surface all three in one call."""
        graph = {
            "nodes": [_node("a"), _node("b")],
            "edges": [
                _fwd("e1", "a", "b"),
                _lb("lb1", "b", "a", max_iter=None),  # omit maxIter
            ],
        }
        out = run_lints(graph, tenant_id="t1", db=None)
        codes = {l.code for l in out}
        assert "loopback_no_exit" in codes
        assert "loopback_no_cap" in codes

    def test_zero_loopback_graph_runs_no_cycle_lints(self):
        """Regression guard — existing graphs must not pick up new
        lint findings from CYCLIC-01.c."""
        graph = {
            "nodes": [
                _node("trig", label="Webhook Trigger", category="trigger"),
                _node("agent"),
            ],
            "edges": [_fwd("e1", "trig", "agent")],
        }
        out = run_lints(graph, tenant_id="t1", db=None)
        loopback_codes = {
            l.code for l in out
            if l.code.startswith("loopback_")
        }
        assert loopback_codes == set()
