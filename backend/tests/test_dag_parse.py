"""Unit tests for pure graph-parsing helpers in dag_runner (no DB / no LLM)."""

import pytest

from app.engine.dag_runner import (
    _build_graph_structures,
    _detect_cycles,
    parse_graph,
)


def _graph(nodes: list[str], edges: list[tuple[str, str]]) -> dict:
    # Use the real runtime type so parse_graph's type-filter (added in
    # DV-03 for sticky-note support) doesn't drop our test nodes.
    return {
        "nodes": [
            {"id": n, "type": "agenticNode",
             "data": {"label": "LLM Agent"}}
            for n in nodes
        ],
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


class TestParseGraphStickyNoteFiltering:
    """DV-03 — sticky notes are canvas artifacts, never executable."""

    def test_sticky_nodes_are_dropped_from_nodes_map(self):
        graph = {
            "nodes": [
                {"id": "a", "type": "agenticNode", "data": {"label": "Trigger"}},
                {"id": "note_1", "type": "stickyNote",
                 "data": {"text": "Remember to handle timeouts", "color": "yellow"}},
                {"id": "b", "type": "agenticNode", "data": {"label": "Agent"}},
            ],
            "edges": [{"source": "a", "target": "b"}],
        }
        nodes_map, edges = parse_graph(graph)
        assert set(nodes_map.keys()) == {"a", "b"}
        assert len(edges) == 1
        assert edges[0].source == "a"
        assert edges[0].target == "b"

    def test_edges_touching_sticky_nodes_are_dropped(self):
        """If an operator somehow connects an edge to a sticky (React
        Flow normally blocks this), the edge must be filtered too so
        in_degree / cycle detection stays correct."""
        graph = {
            "nodes": [
                {"id": "a", "type": "agenticNode", "data": {"label": "A"}},
                {"id": "note", "type": "stickyNote",
                 "data": {"text": "orphan", "color": "blue"}},
                {"id": "b", "type": "agenticNode", "data": {"label": "B"}},
            ],
            "edges": [
                {"source": "a", "target": "note"},     # → filtered
                {"source": "note", "target": "b"},     # → filtered
                {"source": "a", "target": "b"},        # → kept
            ],
        }
        nodes_map, edges = parse_graph(graph)
        assert set(nodes_map.keys()) == {"a", "b"}
        assert [(e.source, e.target) for e in edges] == [("a", "b")]

    def test_legacy_nodes_without_type_default_to_agenticNode(self):
        """Workflows persisted before DV-03 may omit the ``type`` field.
        Treat missing type as agenticNode so old graphs still execute."""
        graph = {
            "nodes": [
                {"id": "a", "data": {"label": "LegacyAgent"}},
                {"id": "b", "data": {"label": "AlsoLegacy"}},
            ],
            "edges": [{"source": "a", "target": "b"}],
        }
        nodes_map, edges = parse_graph(graph)
        assert set(nodes_map.keys()) == {"a", "b"}
        assert len(edges) == 1

    def test_pure_sticky_graph_yields_empty_executable_map(self):
        graph = {
            "nodes": [
                {"id": "note_1", "type": "stickyNote",
                 "data": {"text": "todo", "color": "grey"}},
                {"id": "note_2", "type": "stickyNote",
                 "data": {"text": "also todo", "color": "green"}},
            ],
            "edges": [],
        }
        nodes_map, edges = parse_graph(graph)
        assert nodes_map == {}
        assert edges == []
