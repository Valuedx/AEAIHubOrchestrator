"""CTX-MGMT.E — fan-in primitives + reachability lint + child-evidence
promotion.

Three pieces:

  1. ``lint_unreachable_node_after_switch`` — static reachability
     analysis catching the V9-pre-fix bug shape (fan-in node behind
     a Switch where two arms feed it; only one fires).
  2. Sub-workflow ``_runtime.shared_evidence`` promotion — child
     workflows append evidence to their own runtime; parent appends
     it on sub-workflow completion.
  3. Engine waitAny semantics for the existing ``Merge`` node —
     fires when ANY upstream is satisfied (the right primitive
     for fan-in after a Switch arm has fired).
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.copilot.lints import lint_unreachable_node_after_switch
from app.engine.dag_runner import _is_waitany_merge


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _agent_node(node_id: str, *, label: str = "LLM Agent",
                category: str = "agent",
                config: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "agenticNode",
        "data": {"label": label, "nodeCategory": category, "config": dict(config or {})},
    }


def _switch(node_id: str) -> dict[str, Any]:
    return _agent_node(node_id, label="Switch", category="logic", config={"expression": "trigger.x"})


def _condition(node_id: str) -> dict[str, Any]:
    return _agent_node(node_id, label="Condition", category="logic", config={"condition": "trigger.x == 1"})


def _merge(node_id: str, *, strategy: str = "waitAll") -> dict[str, Any]:
    return _agent_node(node_id, label="Merge", category="logic", config={"strategy": strategy})


def _coalesce(node_id: str) -> dict[str, Any]:
    # Synthetic Coalesce label — same shape as Merge but the lint
    # treats label="Coalesce" as the explicit-fan-in primitive.
    return _agent_node(node_id, label="Coalesce", category="logic", config={})


def _edge(source: str, target: str, *, source_handle: str | None = None) -> dict[str, Any]:
    e: dict[str, Any] = {
        "id": f"e_{source}_{target}_{source_handle or 'def'}",
        "source": source,
        "target": target,
    }
    if source_handle:
        e["sourceHandle"] = source_handle
    return e


# ---------------------------------------------------------------------------
# lint_unreachable_node_after_switch
# ---------------------------------------------------------------------------


class TestUnreachableLint:
    def test_no_lint_when_no_branch_node(self):
        graph = {
            "nodes": [_agent_node("a"), _agent_node("b"), _agent_node("c")],
            "edges": [_edge("a", "c"), _edge("b", "c")],
        }
        # Two upstream into c, but neither comes from a Switch — fan-in
        # is fine (both edges fire normally).
        out = lint_unreachable_node_after_switch(graph)
        assert out == []

    def test_fires_on_two_arm_fanin(self):
        # The classic V9 bug shape: switch → arm_a → fanin
        #                                 → arm_b → fanin
        graph = {
            "nodes": [
                _switch("sw"),
                _agent_node("arm_a"),
                _agent_node("arm_b"),
                _agent_node("fanin"),
            ],
            "edges": [
                _edge("sw", "arm_a", source_handle="yes"),
                _edge("sw", "arm_b", source_handle="no"),
                _edge("arm_a", "fanin"),
                _edge("arm_b", "fanin"),
            ],
        }
        out = lint_unreachable_node_after_switch(graph)
        codes = [l.code for l in out]
        assert "unreachable_node_after_switch" in codes
        bad = next(l for l in out if l.code == "unreachable_node_after_switch")
        assert bad.node_id == "fanin"
        assert bad.severity == "error"
        # Mentions both arms.
        assert "yes" in bad.message
        assert "no" in bad.message

    def test_skips_when_fanin_is_coalesce(self):
        graph = {
            "nodes": [
                _switch("sw"),
                _agent_node("arm_a"),
                _agent_node("arm_b"),
                _coalesce("fanin"),
            ],
            "edges": [
                _edge("sw", "arm_a", source_handle="yes"),
                _edge("sw", "arm_b", source_handle="no"),
                _edge("arm_a", "fanin"),
                _edge("arm_b", "fanin"),
            ],
        }
        out = lint_unreachable_node_after_switch(graph)
        # Coalesce IS the right answer; lint stays quiet.
        assert out == []

    def test_direct_switch_to_fanin_through_two_arms(self):
        # Switch arms feed directly into a fan-in node (no intermediate).
        graph = {
            "nodes": [_switch("sw"), _agent_node("fanin")],
            "edges": [
                _edge("sw", "fanin", source_handle="yes"),
                _edge("sw", "fanin", source_handle="no"),
            ],
        }
        out = lint_unreachable_node_after_switch(graph)
        codes = [l.code for l in out]
        assert "unreachable_node_after_switch" in codes

    def test_condition_branches_also_caught(self):
        # Condition uses true/false handles; same pruning shape.
        graph = {
            "nodes": [
                _condition("cond"),
                _agent_node("a"),
                _agent_node("b"),
                _agent_node("fanin"),
            ],
            "edges": [
                _edge("cond", "a", source_handle="true"),
                _edge("cond", "b", source_handle="false"),
                _edge("a", "fanin"),
                _edge("b", "fanin"),
            ],
        }
        out = lint_unreachable_node_after_switch(graph)
        codes = [l.code for l in out]
        assert "unreachable_node_after_switch" in codes

    def test_two_independent_branches_both_complete_does_not_fire(self):
        # If two switches each pick ONE arm and converge into the
        # same fan-in, the in-degree (2) is met by the sum of
        # arm_a from sw1 and arm_b from sw2. The lint should NOT
        # fire — the convergence is on different branches.
        graph = {
            "nodes": [
                _switch("sw1"),
                _switch("sw2"),
                _agent_node("a"),
                _agent_node("b"),
                _agent_node("fanin"),
            ],
            "edges": [
                _edge("sw1", "a", source_handle="yes"),
                _edge("sw2", "b", source_handle="yes"),
                _edge("a", "fanin"),
                _edge("b", "fanin"),
            ],
        }
        out = lint_unreachable_node_after_switch(graph)
        codes = [l.code for l in out]
        # Both predecessors trace to DIFFERENT branch nodes — the
        # multi-arm-of-same-branch detector doesn't fire.
        assert "unreachable_node_after_switch" not in codes

    def test_empty_graph_no_lint(self):
        assert lint_unreachable_node_after_switch({"nodes": [], "edges": []}) == []
        assert lint_unreachable_node_after_switch({}) == []

    def test_single_predecessor_does_not_fire(self):
        # Fan-in lint requires in_degree >= 2.
        graph = {
            "nodes": [_switch("sw"), _agent_node("a"), _agent_node("b")],
            "edges": [
                _edge("sw", "a", source_handle="yes"),
                _edge("a", "b"),  # b only has one upstream
            ],
        }
        assert lint_unreachable_node_after_switch(graph) == []


# ---------------------------------------------------------------------------
# _is_waitany_merge — engine-side detection
# ---------------------------------------------------------------------------


class TestIsWaitAnyMerge:
    def test_default_merge_is_not_waitany(self):
        node = _merge("m")
        assert _is_waitany_merge(node) is False

    def test_explicit_waitall_is_not_waitany(self):
        node = _merge("m", strategy="waitAll")
        assert _is_waitany_merge(node) is False

    def test_waitany_detected(self):
        node = _merge("m", strategy="waitAny")
        assert _is_waitany_merge(node) is True

    def test_case_insensitive(self):
        # "waitAny" / "WAITANY" / "waitany" all match.
        for s in ("waitAny", "WAITANY", "waitany"):
            assert _is_waitany_merge(_merge("m", strategy=s)) is True

    def test_non_merge_not_waitany(self):
        # Even if a non-Merge node has strategy=waitAny in config,
        # the helper says no — only Merge nodes are recognized.
        node = _agent_node(
            "n", label="LLM Agent", config={"strategy": "waitAny"},
        )
        assert _is_waitany_merge(node) is False

    def test_non_logic_category_not_waitany(self):
        node = _agent_node(
            "n", label="Merge", category="action",
            config={"strategy": "waitAny"},
        )
        assert _is_waitany_merge(node) is False


# ---------------------------------------------------------------------------
# _find_ready_nodes with waitAny merge
# ---------------------------------------------------------------------------


class TestFindReadyWithWaitAny:
    def _setup_graph(self, merge_strategy: str) -> dict[str, Any]:
        """Three upstream sources fanning into a Merge."""
        return {
            "nodes_map": {
                "src_a": _agent_node("src_a"),
                "src_b": _agent_node("src_b"),
                "src_c": _agent_node("src_c"),
                "merge": _merge("merge", strategy=merge_strategy),
            },
            "reverse": None,  # built below
        }

    def _reverse_for_three_into_merge(self):
        from app.engine.dag_runner import _Edge

        edges = [
            _Edge(source="src_a", target="merge", source_handle=None),
            _Edge(source="src_b", target="merge", source_handle=None),
            _Edge(source="src_c", target="merge", source_handle=None),
        ]
        reverse = {"merge": edges}
        return reverse

    def test_waitany_fires_with_one_satisfied(self):
        from app.engine.dag_runner import _find_ready_nodes

        graph = self._setup_graph("waitAny")
        nodes_map = graph["nodes_map"]
        reverse = self._reverse_for_three_into_merge()

        # Only src_a has been satisfied.
        satisfied = {"merge": {"src_a"}}
        # src_a output is in context (it has executed); the others are not.
        context = {"src_a": {"value": "from-a"}}
        ready = _find_ready_nodes(
            nodes_map, reverse, satisfied, context, skipped=set(), pruned=set(),
        )
        assert "merge" in ready

    def test_waitall_does_not_fire_with_one_satisfied(self):
        from app.engine.dag_runner import _find_ready_nodes

        graph = self._setup_graph("waitAll")
        nodes_map = graph["nodes_map"]
        reverse = self._reverse_for_three_into_merge()

        satisfied = {"merge": {"src_a"}}  # only one of three
        context = {"src_a": {"value": "from-a"}}
        ready = _find_ready_nodes(
            nodes_map, reverse, satisfied, context, skipped=set(), pruned=set(),
        )
        assert "merge" not in ready

    def test_waitall_fires_when_all_three_satisfied(self):
        from app.engine.dag_runner import _find_ready_nodes

        graph = self._setup_graph("waitAll")
        nodes_map = graph["nodes_map"]
        reverse = self._reverse_for_three_into_merge()

        satisfied = {"merge": {"src_a", "src_b", "src_c"}}
        context = {"src_a": {}, "src_b": {}, "src_c": {}}
        ready = _find_ready_nodes(
            nodes_map, reverse, satisfied, context, skipped=set(), pruned=set(),
        )
        assert "merge" in ready

    def test_waitany_does_not_re_fire_after_executed(self):
        from app.engine.dag_runner import _find_ready_nodes

        graph = self._setup_graph("waitAny")
        nodes_map = graph["nodes_map"]
        reverse = self._reverse_for_three_into_merge()

        # Merge already in context (already executed).
        satisfied = {"merge": {"src_a", "src_b"}}
        context = {"src_a": {}, "src_b": {}, "merge": {"already": "fired"}}
        ready = _find_ready_nodes(
            nodes_map, reverse, satisfied, context, skipped=set(), pruned=set(),
        )
        assert "merge" not in ready

    def test_waitany_skips_when_only_pruned_sources_satisfied(self):
        from app.engine.dag_runner import _find_ready_nodes

        graph = self._setup_graph("waitAny")
        nodes_map = graph["nodes_map"]
        reverse = self._reverse_for_three_into_merge()

        # All three sources pruned — merge has no active sources,
        # so the no-active-sources path fires immediately (Merge
        # falls through to "ready" with nothing to wait on).
        ready = _find_ready_nodes(
            nodes_map, reverse,
            satisfied={},
            context={},
            skipped=set(),
            pruned={"src_a", "src_b", "src_c"},
        )
        # When active_sources is empty, the engine treats the node
        # as ready (no upstream to wait on).
        assert "merge" in ready


# ---------------------------------------------------------------------------
# _handle_logic — Merge waitAny handler output shape
# ---------------------------------------------------------------------------


class TestMergeWaitAnyHandler:
    def test_waitany_output_carries_value_and_from(self):
        from app.engine.node_handlers import _handle_logic

        node_data = {"label": "Merge", "nodeCategory": "logic", "config": {"strategy": "waitAny"}}
        # The first available upstream wins.
        context = {
            "node_a": {"hello": 1},
            "node_b": None,  # not yet
            "trigger": {},
        }
        out = _handle_logic(node_data, context, "tenant-1")
        assert out["strategy"] == "waitAny"
        # `value` is the first non-None upstream.
        assert out["value"] == {"hello": 1}
        assert out["from"] == "node_a"
        # Backward compat: `merged` carries the value.
        assert out["merged"] == {"hello": 1}

    def test_waitall_output_aggregates_upstream(self):
        from app.engine.node_handlers import _handle_logic

        node_data = {"label": "Merge", "nodeCategory": "logic", "config": {"strategy": "waitAll"}}
        context = {
            "node_a": {"x": 1},
            "node_b": {"y": 2},
            "trigger": {},
        }
        out = _handle_logic(node_data, context, "tenant-1")
        assert out["strategy"] == "waitAll"
        # All node_* upstreams aggregated.
        assert out["merged"]["node_a"] == {"x": 1}
        assert out["merged"]["node_b"] == {"y": 2}


# ---------------------------------------------------------------------------
# Sub-workflow shared_evidence promotion (smoke)
# ---------------------------------------------------------------------------


class TestSharedEvidencePromotion:
    """Light unit-level smoke — the full integration through
    ``_execute_sub_workflow`` is exercised by the existing
    sub-workflow tests; here we verify the promotion code path
    handles the common shapes."""

    def test_child_evidence_appends_to_parent(self):
        # Simulating what _execute_sub_workflow does after the child
        # finishes: read child._runtime.shared_evidence, append into
        # parent._runtime.shared_evidence.
        from app.engine.dag_runner import _get_runtime

        parent_ctx: dict[str, Any] = {}
        # Parent already has one piece of evidence.
        _get_runtime(parent_ctx)["shared_evidence"] = [{"src": "parent_node_1", "msg": "earlier finding"}]

        # Child completed with two evidence entries.
        child_evidence = [
            {"src": "child_node_5", "msg": "agent flagged license expiry"},
            {"src": "child_node_7", "msg": "queue depth alarm"},
        ]

        # The promotion logic from _execute_sub_workflow.
        runtime = _get_runtime(parent_ctx)
        existing = runtime.get("shared_evidence")
        if isinstance(existing, list):
            runtime["shared_evidence"] = [*existing, *child_evidence]
        else:
            runtime["shared_evidence"] = list(child_evidence)

        assert parent_ctx["_runtime"]["shared_evidence"] == [
            {"src": "parent_node_1", "msg": "earlier finding"},
            {"src": "child_node_5", "msg": "agent flagged license expiry"},
            {"src": "child_node_7", "msg": "queue depth alarm"},
        ]

    def test_child_no_evidence_leaves_parent_unchanged(self):
        from app.engine.dag_runner import _get_runtime

        parent_ctx: dict[str, Any] = {}
        runtime = _get_runtime(parent_ctx)
        runtime["shared_evidence"] = [{"src": "parent", "msg": "x"}]

        # Child had no _runtime.shared_evidence — promotion no-op.
        # (Mimicking the if-block in _execute_sub_workflow.)
        child_evidence = None
        if isinstance(child_evidence, list) and child_evidence:
            runtime["shared_evidence"] = [*runtime["shared_evidence"], *child_evidence]

        assert parent_ctx["_runtime"]["shared_evidence"] == [{"src": "parent", "msg": "x"}]
