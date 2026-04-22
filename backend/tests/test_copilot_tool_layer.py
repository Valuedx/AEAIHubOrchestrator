"""Unit tests for the COPILOT-01 pure tool-layer functions.

These tests do NOT touch the DB — the tool layer is deliberately
stateless so the same functions work for both the HTTP dispatch path
and the (future) agent runner that chains calls in-memory.

The registry is loaded from ``shared/node_registry.json`` via
``_load_registry`` so changes to the registry schema will be caught
here first.
"""

from __future__ import annotations

import pytest

from app.copilot import tool_layer
from app.copilot.tool_layer import (
    EdgeNotFoundError,
    InvalidConnectionError,
    NodeNotFoundError,
    ToolLayerError,
    UnknownNodeTypeError,
)


# ---------------------------------------------------------------------------
# list_node_types / get_node_schema
# ---------------------------------------------------------------------------


def test_list_node_types_returns_categories_and_types():
    out = tool_layer.list_node_types()
    assert "categories" in out
    assert "node_types" in out
    assert len(out["node_types"]) > 0
    # No config_schema in the trimmed list — keeps agent context small.
    for node in out["node_types"]:
        assert "config_schema" not in node
        assert {"type", "category", "label", "description"}.issubset(node.keys())


def test_list_node_types_filters_by_category():
    out = tool_layer.list_node_types(category="trigger")
    assert len(out["node_types"]) > 0
    for node in out["node_types"]:
        assert node["category"] == "trigger"


def test_list_node_types_unknown_category_returns_empty():
    out = tool_layer.list_node_types(category="nonexistent")
    assert out["node_types"] == []


def test_get_node_schema_returns_full_entry():
    schema = tool_layer.get_node_schema("llm_agent")
    assert schema["type"] == "llm_agent"
    assert schema["category"] == "agent"
    assert isinstance(schema["config_schema"], dict)
    assert schema["label"]  # registry label (e.g. "LLM Agent")


def test_get_node_schema_unknown_type_raises():
    with pytest.raises(UnknownNodeTypeError):
        tool_layer.get_node_schema("totally_made_up")


# ---------------------------------------------------------------------------
# add_node
# ---------------------------------------------------------------------------


def test_add_node_creates_react_flow_shaped_node():
    graph, result = tool_layer.add_node(
        {"nodes": [], "edges": []},
        node_type="llm_agent",
        config={"model": "gemini-2.5-flash"},
        position={"x": 100, "y": 50},
    )
    assert len(graph["nodes"]) == 1
    node = graph["nodes"][0]
    assert node["id"] == "node_1"
    assert node["type"] == "agenticNode"
    assert node["position"] == {"x": 100, "y": 50}
    assert node["data"]["label"] == "LLM Agent"  # registry label, not the type
    assert node["data"]["nodeCategory"] == "agent"
    assert node["data"]["config"]["model"] == "gemini-2.5-flash"
    assert node["data"]["status"] == "idle"
    assert result["node_id"] == "node_1"


def test_add_node_sequential_ids_fill_gaps():
    graph = {
        "nodes": [{"id": "node_1", "type": "agenticNode", "data": {}},
                  {"id": "node_3", "type": "agenticNode", "data": {}}],
        "edges": [],
    }
    graph, result = tool_layer.add_node(graph, node_type="llm_agent")
    assert result["node_id"] == "node_2"


def test_add_node_unknown_type_raises():
    with pytest.raises(UnknownNodeTypeError):
        tool_layer.add_node({"nodes": [], "edges": []}, node_type="no_such_node")


def test_add_node_does_not_mutate_input():
    graph = {"nodes": [], "edges": []}
    tool_layer.add_node(graph, node_type="llm_agent")
    assert graph == {"nodes": [], "edges": []}


def test_add_node_accepts_display_name():
    graph, _ = tool_layer.add_node(
        {"nodes": [], "edges": []},
        node_type="llm_agent",
        display_name="Summarizer",
    )
    assert graph["nodes"][0]["data"]["displayName"] == "Summarizer"


# ---------------------------------------------------------------------------
# update_node_config
# ---------------------------------------------------------------------------


def test_update_node_config_merges_partial():
    graph, _ = tool_layer.add_node(
        {"nodes": [], "edges": []}, node_type="llm_agent",
        config={"model": "gemini-2.5-flash", "temperature": 0.5},
    )
    graph, _ = tool_layer.update_node_config(
        graph, node_id="node_1", partial={"temperature": 0.2, "maxTokens": 2048},
    )
    config = graph["nodes"][0]["data"]["config"]
    assert config["model"] == "gemini-2.5-flash"  # untouched
    assert config["temperature"] == 0.2           # updated
    assert config["maxTokens"] == 2048            # added


def test_update_node_config_none_value_clears_key():
    graph, _ = tool_layer.add_node(
        {"nodes": [], "edges": []}, node_type="llm_agent",
        config={"model": "gemini-2.5-flash", "temperature": 0.5},
    )
    graph, _ = tool_layer.update_node_config(
        graph, node_id="node_1", partial={"temperature": None},
    )
    assert "temperature" not in graph["nodes"][0]["data"]["config"]


def test_update_node_config_unknown_node_raises():
    with pytest.raises(NodeNotFoundError):
        tool_layer.update_node_config(
            {"nodes": [], "edges": []},
            node_id="node_missing",
            partial={"x": 1},
        )


def test_update_node_config_blank_display_name_clears():
    graph, _ = tool_layer.add_node(
        {"nodes": [], "edges": []}, node_type="llm_agent", display_name="X",
    )
    graph, _ = tool_layer.update_node_config(
        graph, node_id="node_1", partial={}, display_name="",
    )
    assert "displayName" not in graph["nodes"][0]["data"]


# ---------------------------------------------------------------------------
# delete_node
# ---------------------------------------------------------------------------


def test_delete_node_removes_node_and_cascading_edges():
    graph, _ = tool_layer.add_node({"nodes": [], "edges": []}, node_type="llm_agent")
    graph, _ = tool_layer.add_node(graph, node_type="llm_agent")
    graph, _ = tool_layer.connect_nodes(graph, source="node_1", target="node_2")
    graph, result = tool_layer.delete_node(graph, node_id="node_1")
    assert len(graph["nodes"]) == 1
    assert graph["nodes"][0]["id"] == "node_2"
    assert graph["edges"] == []
    assert len(result["removed_edges"]) == 1


def test_delete_node_missing_raises():
    with pytest.raises(NodeNotFoundError):
        tool_layer.delete_node({"nodes": [], "edges": []}, node_id="node_x")


# ---------------------------------------------------------------------------
# connect_nodes / disconnect_edge
# ---------------------------------------------------------------------------


def test_connect_nodes_happy_path():
    graph, _ = tool_layer.add_node({"nodes": [], "edges": []}, node_type="llm_agent")
    graph, _ = tool_layer.add_node(graph, node_type="llm_agent")
    graph, result = tool_layer.connect_nodes(graph, source="node_1", target="node_2")
    assert len(graph["edges"]) == 1
    edge = graph["edges"][0]
    assert edge["source"] == "node_1"
    assert edge["target"] == "node_2"
    assert edge["sourceHandle"] is None
    assert edge["targetHandle"] is None
    assert result["edge_id"] == edge["id"]


def test_connect_nodes_refuses_self_loop():
    graph, _ = tool_layer.add_node({"nodes": [], "edges": []}, node_type="llm_agent")
    with pytest.raises(InvalidConnectionError):
        tool_layer.connect_nodes(graph, source="node_1", target="node_1")


def test_connect_nodes_refuses_duplicate_edge():
    graph, _ = tool_layer.add_node({"nodes": [], "edges": []}, node_type="llm_agent")
    graph, _ = tool_layer.add_node(graph, node_type="llm_agent")
    graph, _ = tool_layer.connect_nodes(graph, source="node_1", target="node_2")
    with pytest.raises(InvalidConnectionError):
        tool_layer.connect_nodes(graph, source="node_1", target="node_2")


def test_connect_nodes_missing_endpoint_raises():
    graph, _ = tool_layer.add_node({"nodes": [], "edges": []}, node_type="llm_agent")
    with pytest.raises(NodeNotFoundError):
        tool_layer.connect_nodes(graph, source="node_1", target="node_missing")


def test_disconnect_edge_removes_by_id():
    graph, _ = tool_layer.add_node({"nodes": [], "edges": []}, node_type="llm_agent")
    graph, _ = tool_layer.add_node(graph, node_type="llm_agent")
    graph, conn = tool_layer.connect_nodes(graph, source="node_1", target="node_2")
    graph, _ = tool_layer.disconnect_edge(graph, edge_id=conn["edge_id"])
    assert graph["edges"] == []


def test_disconnect_edge_missing_raises():
    with pytest.raises(EdgeNotFoundError):
        tool_layer.disconnect_edge({"nodes": [], "edges": []}, edge_id="edge_missing")


# ---------------------------------------------------------------------------
# validate_graph
# ---------------------------------------------------------------------------


def test_validate_graph_empty_has_no_warnings():
    out = tool_layer.validate_graph({"nodes": [], "edges": []})
    assert out == {"errors": [], "warnings": []}


def test_validate_graph_catches_enum_violation():
    graph, _ = tool_layer.add_node(
        {"nodes": [], "edges": []},
        node_type="llm_agent",
        config={"provider": "notarealprovider"},
    )
    out = tool_layer.validate_graph(graph)
    # Config validator flags unknown enum values as warnings.
    assert any("not in allowed values" in w for w in out["warnings"])


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


def test_dispatch_unknown_tool_raises():
    with pytest.raises(ToolLayerError):
        tool_layer.dispatch("no_such_tool", {}, {})


def test_dispatch_readonly_tool_returns_none_graph():
    new_graph, result = tool_layer.dispatch(
        "list_node_types", {"nodes": [], "edges": []}, {},
    )
    assert new_graph is None
    assert "node_types" in result


def test_dispatch_mutation_tool_returns_graph():
    new_graph, result = tool_layer.dispatch(
        "add_node",
        {"nodes": [], "edges": []},
        {"node_type": "llm_agent"},
    )
    assert new_graph is not None
    assert len(new_graph["nodes"]) == 1
    assert result["node_id"] == "node_1"


def test_dispatch_missing_required_arg_raises():
    with pytest.raises(ToolLayerError):
        tool_layer.dispatch("add_node", {"nodes": [], "edges": []}, {})
