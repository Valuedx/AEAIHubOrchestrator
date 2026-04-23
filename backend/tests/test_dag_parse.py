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


# ---------------------------------------------------------------------------
# CYCLIC-01.a — loopback edge schema + cycle-detection exclusion
# ---------------------------------------------------------------------------


class TestLoopbackEdges:
    """A loopback edge exists in the parsed graph (for downstream
    tooling + CYCLIC-01.b's runtime) but is EXCLUDED from the
    forward-adjacency used for execution + cycle detection. The
    forward subgraph therefore stays strictly acyclic even when
    loopback edges are present.
    """

    def _loopback_graph(self) -> dict:
        """3-node classic: planner → tool → check, with a
        ``check → planner`` loopback. Forward subgraph is the
        linear chain; loopback edge is the cycle-closing link."""
        return {
            "nodes": [
                {"id": "planner", "type": "agenticNode", "data": {"label": "LLM Agent"}},
                {"id": "tool",    "type": "agenticNode", "data": {"label": "MCP Tool"}},
                {"id": "check",   "type": "agenticNode", "data": {"label": "Condition"}},
            ],
            "edges": [
                {"id": "e1", "source": "planner", "target": "tool"},
                {"id": "e2", "source": "tool",    "target": "check"},
                {
                    "id": "e3",
                    "source": "check",
                    "target": "planner",
                    "type": "loopback",
                    "maxIterations": 5,
                    "sourceHandle": "true",
                },
            ],
        }

    def test_loopback_edge_is_parsed_with_kind_and_max_iterations(self):
        nodes_map, edges = parse_graph(self._loopback_graph())
        assert set(nodes_map) == {"planner", "tool", "check"}
        assert len(edges) == 3
        loopbacks = [e for e in edges if e.is_loopback]
        assert len(loopbacks) == 1
        lb = loopbacks[0]
        assert lb.source == "check"
        assert lb.target == "planner"
        assert lb.source_handle == "true"
        assert lb.max_iterations == 5

    def test_loopback_edge_excluded_from_forward_adjacency(self):
        """The forward subgraph must be exactly the non-loopback
        edges — anything else makes Kahn's check wrong."""
        nodes_map, edges = parse_graph(self._loopback_graph())
        forward, reverse, in_degree = _build_graph_structures(nodes_map, edges)

        # Forward: planner → tool → check; NO check → planner.
        assert [e.target for e in forward.get("planner", [])] == ["tool"]
        assert [e.target for e in forward.get("tool", [])] == ["check"]
        assert forward.get("check", []) == []
        # Reverse mirrors the same exclusion.
        assert reverse.get("planner", []) == []
        # In-degree reflects only forward edges.
        assert in_degree == {"planner": 0, "tool": 1, "check": 1}

    def test_cycle_detection_passes_with_loopback_edges(self):
        """Same graph that WOULD be a cycle if loopback were a
        forward edge — must pass Kahn's check because loopbacks
        are excluded from forward adjacency."""
        nodes_map, edges = parse_graph(self._loopback_graph())
        forward, _reverse, in_degree = _build_graph_structures(nodes_map, edges)
        _detect_cycles(nodes_map, forward, in_degree)  # no raise

    def test_missing_max_iterations_falls_back_to_default(self):
        from app.engine.dag_runner import LOOPBACK_DEFAULT_MAX_ITERATIONS

        graph = {
            "nodes": [
                {"id": "a", "type": "agenticNode", "data": {}},
                {"id": "b", "type": "agenticNode", "data": {}},
            ],
            "edges": [
                {"id": "e1", "source": "a", "target": "b"},
                {"id": "e2", "source": "b", "target": "a", "type": "loopback"},
            ],
        }
        _nodes, edges = parse_graph(graph)
        lb = next(e for e in edges if e.is_loopback)
        assert lb.max_iterations == LOOPBACK_DEFAULT_MAX_ITERATIONS

    def test_oversized_max_iterations_clamped_to_hard_cap(self):
        from app.engine.dag_runner import LOOPBACK_HARD_CAP

        graph = {
            "nodes": [
                {"id": "a", "type": "agenticNode", "data": {}},
                {"id": "b", "type": "agenticNode", "data": {}},
            ],
            "edges": [
                {"id": "e1", "source": "a", "target": "b"},
                {"id": "e2", "source": "b", "target": "a",
                 "type": "loopback", "maxIterations": 999_999},
            ],
        }
        _nodes, edges = parse_graph(graph)
        lb = next(e for e in edges if e.is_loopback)
        assert lb.max_iterations == LOOPBACK_HARD_CAP

    def test_non_integer_max_iterations_falls_back_to_default(self):
        from app.engine.dag_runner import LOOPBACK_DEFAULT_MAX_ITERATIONS

        graph = {
            "nodes": [
                {"id": "a", "type": "agenticNode", "data": {}},
                {"id": "b", "type": "agenticNode", "data": {}},
            ],
            "edges": [
                {"id": "e2", "source": "b", "target": "a",
                 "type": "loopback", "maxIterations": "bogus"},
            ],
        }
        _nodes, edges = parse_graph(graph)
        lb = next(e for e in edges if e.is_loopback)
        assert lb.max_iterations == LOOPBACK_DEFAULT_MAX_ITERATIONS

    def test_loopback_edges_helper_filters_correctly(self):
        from app.engine.dag_runner import loopback_edges

        _nodes, edges = parse_graph(self._loopback_graph())
        loops = loopback_edges(edges)
        assert len(loops) == 1
        assert loops[0].kind == "loopback"

    def test_zero_loopback_graphs_are_bit_identical(self):
        """Regression guard: a graph with ZERO loopback edges must
        parse into the same structures as before the feature
        landed. Anything else risks silent breakage across the
        existing 700+ tests."""
        graph = _graph(
            ["a", "b", "c", "d"],
            [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")],
        )
        nodes_map, edges = parse_graph(graph)
        forward, reverse, in_degree = _build_graph_structures(nodes_map, edges)

        # Every edge is forward; the helper returns nothing.
        from app.engine.dag_runner import loopback_edges
        assert loopback_edges(edges) == []
        # Edge count + shape unchanged — diamond has 4 edges.
        assert len(edges) == 4
        assert all(e.kind == "forward" for e in edges)
        # In-degree is the classic diamond distribution.
        assert in_degree == {"a": 0, "b": 1, "c": 1, "d": 2}
