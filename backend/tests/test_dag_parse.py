"""Unit tests for pure graph-parsing helpers in dag_runner (no DB / no LLM)."""

import pytest

from app.engine.dag_runner import (
    _build_graph_structures,
    _detect_cycles,
    parse_graph,
)


def _graph(nodes: list[str], edges: list[tuple[str, str]]) -> dict:
    return {
        "nodes": [{"id": n, "type": "agent", "data": {"label": "LLM Agent"}} for n in nodes],
        "edges": [{"source": s, "target": t} for s, t in edges],
    }


class TestDetectCycles:
    def test_linear_graph_ok(self):
        nodes_map, edges = parse_graph(_graph(["a", "b", "c"], [("a", "b"), ("b", "c")]))
        forward, _reverse, in_degree = _build_graph_structures(nodes_map, edges)
        _detect_cycles(nodes_map, forward, in_degree)  # no raise

    def test_diamond_graph_ok(self):
        nodes_map, edges = parse_graph(
            _graph(["a", "b", "c", "d"], [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")])
        )
        forward, _reverse, in_degree = _build_graph_structures(nodes_map, edges)
        _detect_cycles(nodes_map, forward, in_degree)  # no raise

    def test_three_node_cycle_raises_and_names_nodes(self):
        nodes_map, edges = parse_graph(
            _graph(["a", "b", "c"], [("a", "b"), ("b", "c"), ("c", "a")])
        )
        forward, _reverse, in_degree = _build_graph_structures(nodes_map, edges)
        with pytest.raises(ValueError) as exc:
            _detect_cycles(nodes_map, forward, in_degree)
        msg = str(exc.value)
        assert "cycle" in msg.lower()
        for node_id in ("a", "b", "c"):
            assert node_id in msg, f"expected cycle node {node_id!r} in error: {msg}"

    def test_cycle_with_clean_prefix_names_only_cycle_nodes(self):
        # a -> b -> c -> d -> b  (a is acyclic prefix; cycle is b,c,d)
        nodes_map, edges = parse_graph(
            _graph(
                ["a", "b", "c", "d"],
                [("a", "b"), ("b", "c"), ("c", "d"), ("d", "b")],
            )
        )
        forward, _reverse, in_degree = _build_graph_structures(nodes_map, edges)
        with pytest.raises(ValueError) as exc:
            _detect_cycles(nodes_map, forward, in_degree)
        msg = str(exc.value)
        assert "'a'" not in msg, "acyclic prefix node should not be reported"
        for node_id in ("b", "c", "d"):
            assert node_id in msg

    def test_self_loop_raises(self):
        nodes_map, edges = parse_graph(_graph(["a"], [("a", "a")]))
        forward, _reverse, in_degree = _build_graph_structures(nodes_map, edges)
        with pytest.raises(ValueError):
            _detect_cycles(nodes_map, forward, in_degree)
