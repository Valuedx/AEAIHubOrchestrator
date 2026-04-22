"""COPILOT-01 tool layer — pure graph-mutation helpers.

Every function here takes a ``graph_json`` dict and returns a new one
(plus a small result payload). No DB access, no tenant_id, no
side-effects on the filesystem. This discipline buys two things:

1. The HTTP layer (``app/api/copilot_drafts.py``) can dispatch a tool
   call, apply the returned graph inside a DB transaction with the
   optimistic-concurrency version bump, and commit atomically.
2. The agent runner in COPILOT-01b can chain many tool calls in-memory
   (LLM function-calling often fires several per turn) and commit once
   at the end — no per-tool savepoints, no partial-write rollback mess.

Node shape matches what the React Flow canvas and the existing
workflow engine already produce (see ``frontend/src/lib/templates``
for live examples)::

    {
      "id": "node_7",
      "type": "agenticNode",
      "position": {"x": 240, "y": 200},
      "data": {
        "label": "LLM Agent",           # registry label, source-of-truth identifier
        "displayName": "...optional...",
        "nodeCategory": "agent",
        "config": {...per-schema...},
        "status": "idle"
      }
    }

Edges::

    {
      "id": "edge_X",
      "source": "node_7",
      "target": "node_8",
      "sourceHandle": null,
      "targetHandle": null
    }
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

from app.engine.config_validator import _load_registry, validate_graph_configs


# ---------------------------------------------------------------------------
# Errors surfaced by the tool layer. The API layer translates these to 4xx.
# ---------------------------------------------------------------------------


class ToolLayerError(ValueError):
    """Base class. Always raises with a message the copilot can read back."""


class UnknownNodeTypeError(ToolLayerError):
    pass


class NodeNotFoundError(ToolLayerError):
    pass


class EdgeNotFoundError(ToolLayerError):
    pass


class InvalidConnectionError(ToolLayerError):
    pass


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------


def _registry_node_types() -> list[dict[str, Any]]:
    return list(_load_registry().get("node_types", []))


def _registry_categories() -> list[dict[str, Any]]:
    return list(_load_registry().get("categories", []))


def _registry_entry_for(node_type: str) -> dict[str, Any]:
    """Look up a registry entry by its ``type`` id (e.g. ``llm_agent``)."""
    for entry in _registry_node_types():
        if entry.get("type") == node_type:
            return entry
    raise UnknownNodeTypeError(
        f"Unknown node_type '{node_type}'. Call list_node_types() for the allowed set."
    )


def list_node_types(category: str | None = None) -> dict[str, Any]:
    """Return the trimmed-for-context catalogue of node types.

    Intentionally does NOT return ``config_schema`` — a copilot agent
    that needs the schema for a specific node should follow up with
    ``get_node_schema(type)``. This two-step flow keeps the registry
    payload small enough to fit in an LLM prompt even for ~50 node
    types.

    Shape::

        {
          "categories": [{"id": "trigger", "label": "Triggers", ...}, ...],
          "node_types": [
            {"type": "llm_agent", "category": "agent", "label": "LLM Agent",
             "description": "..."},
            ...
          ]
        }
    """
    types = _registry_node_types()
    if category is not None:
        types = [t for t in types if t.get("category") == category]

    trimmed = [
        {
            "type": t.get("type"),
            "category": t.get("category"),
            "label": t.get("label"),
            "description": t.get("description", ""),
        }
        for t in types
    ]
    return {"categories": _registry_categories(), "node_types": trimmed}


def get_node_schema(node_type: str) -> dict[str, Any]:
    """Full registry entry for one node type — schema + description +
    category + canonical ``label`` used in ``graph.nodes[*].data.label``.

    Raises :class:`UnknownNodeTypeError` if the type doesn't exist.
    """
    entry = _registry_entry_for(node_type)
    return {
        "type": entry.get("type"),
        "category": entry.get("category"),
        "label": entry.get("label"),
        "description": entry.get("description", ""),
        "icon": entry.get("icon"),
        "config_schema": entry.get("config_schema", {}),
    }


# ---------------------------------------------------------------------------
# Graph shape helpers
# ---------------------------------------------------------------------------


def _empty_graph() -> dict[str, Any]:
    return {"nodes": [], "edges": []}


def _clone(graph: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy so callers never mutate the input. The graph is small
    enough that deep-copy cost is negligible relative to JSON
    serialisation on the API boundary."""
    return copy.deepcopy(graph) if graph else _empty_graph()


def _next_node_id(graph: dict[str, Any]) -> str:
    existing = {n.get("id", "") for n in graph.get("nodes", [])}
    i = 1
    while f"node_{i}" in existing:
        i += 1
    return f"node_{i}"


def _next_edge_id(graph: dict[str, Any]) -> str:
    existing = {e.get("id", "") for e in graph.get("edges", [])}
    # Use a uuid hex suffix so regenerating an identical graph through
    # a different code path doesn't collide on e.g. ``edge_1``.
    return f"edge_{uuid.uuid4().hex[:8]}" if existing else "edge_1"


def _find_node(graph: dict[str, Any], node_id: str) -> dict[str, Any]:
    for node in graph.get("nodes", []):
        if node.get("id") == node_id:
            return node
    raise NodeNotFoundError(f"Node '{node_id}' not found in draft graph.")


def _find_edge_index(graph: dict[str, Any], edge_id: str) -> int:
    for i, edge in enumerate(graph.get("edges", [])):
        if edge.get("id") == edge_id:
            return i
    raise EdgeNotFoundError(f"Edge '{edge_id}' not found in draft graph.")


# ---------------------------------------------------------------------------
# Mutations — each returns (new_graph, result_payload)
# ---------------------------------------------------------------------------


def add_node(
    graph: dict[str, Any],
    *,
    node_type: str,
    config: dict[str, Any] | None = None,
    position: dict[str, float] | None = None,
    display_name: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Append a node to the graph. ``node_type`` is the registry ``type``
    field (e.g. ``"llm_agent"``); the resulting node's ``data.label``
    is filled from the registry's canonical label so the existing
    validator (which looks up schemas by label, not by type) keeps
    working unchanged."""
    entry = _registry_entry_for(node_type)

    new_graph = _clone(graph)
    new_graph.setdefault("nodes", [])
    new_graph.setdefault("edges", [])

    node_id = _next_node_id(new_graph)
    node_data: dict[str, Any] = {
        "label": entry.get("label"),
        "nodeCategory": entry.get("category"),
        "config": dict(config or {}),
        "status": "idle",
    }
    if entry.get("icon") and "icon" not in node_data["config"]:
        node_data["config"]["icon"] = entry["icon"]
    if display_name:
        node_data["displayName"] = display_name

    node = {
        "id": node_id,
        "type": "agenticNode",
        "position": dict(position) if position else {"x": 0, "y": 0},
        "data": node_data,
    }
    new_graph["nodes"].append(node)
    return new_graph, {"node_id": node_id, "node": node}


def update_node_config(
    graph: dict[str, Any],
    *,
    node_id: str,
    partial: dict[str, Any],
    display_name: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge ``partial`` into the node's ``data.config``. Pass a value
    of ``None`` for a key to clear it."""
    new_graph = _clone(graph)
    node = _find_node(new_graph, node_id)
    data = node.setdefault("data", {})
    config = data.setdefault("config", {})

    for key, value in partial.items():
        if value is None:
            config.pop(key, None)
        else:
            config[key] = value

    if display_name is not None:
        if display_name == "":
            data.pop("displayName", None)
        else:
            data["displayName"] = display_name

    return new_graph, {"node_id": node_id, "node": node}


def delete_node(
    graph: dict[str, Any],
    *,
    node_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Remove a node and any edges that touch it."""
    new_graph = _clone(graph)
    _find_node(new_graph, node_id)  # raises if missing

    removed_edges: list[str] = []
    new_graph["nodes"] = [n for n in new_graph.get("nodes", []) if n.get("id") != node_id]
    kept_edges: list[dict[str, Any]] = []
    for edge in new_graph.get("edges", []):
        if edge.get("source") == node_id or edge.get("target") == node_id:
            removed_edges.append(edge.get("id", ""))
        else:
            kept_edges.append(edge)
    new_graph["edges"] = kept_edges
    return new_graph, {"node_id": node_id, "removed_edges": removed_edges}


def connect_nodes(
    graph: dict[str, Any],
    *,
    source: str,
    target: str,
    source_handle: str | None = None,
    target_handle: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Add a directed edge source → target. Refuses self-loops and
    duplicate (source, target, handles) pairs."""
    if source == target:
        raise InvalidConnectionError(
            f"Cannot connect node '{source}' to itself — workflows are DAGs."
        )

    new_graph = _clone(graph)
    _find_node(new_graph, source)
    _find_node(new_graph, target)

    for existing in new_graph.get("edges", []):
        if (
            existing.get("source") == source
            and existing.get("target") == target
            and existing.get("sourceHandle") == source_handle
            and existing.get("targetHandle") == target_handle
        ):
            raise InvalidConnectionError(
                f"Edge {source} → {target} already exists."
            )

    edge_id = _next_edge_id(new_graph)
    edge = {
        "id": edge_id,
        "source": source,
        "target": target,
        "sourceHandle": source_handle,
        "targetHandle": target_handle,
    }
    new_graph.setdefault("edges", []).append(edge)
    return new_graph, {"edge_id": edge_id, "edge": edge}


def disconnect_edge(
    graph: dict[str, Any],
    *,
    edge_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Remove an edge by id."""
    new_graph = _clone(graph)
    idx = _find_edge_index(new_graph, edge_id)
    removed = new_graph["edges"].pop(idx)
    return new_graph, {"edge_id": edge_id, "removed_edge": removed}


# ---------------------------------------------------------------------------
# Validation — wraps the existing validator so drafts use the same shape
# as published workflows.
# ---------------------------------------------------------------------------


def validate_graph(graph: dict[str, Any]) -> dict[str, Any]:
    """Return ``{errors, warnings}``. Today the config-validator returns
    a single flat list of warning strings; surface it under the
    ``warnings`` key so the shape is forward-compatible with a future
    hard-error distinction (e.g. from the DAG runner's cycle check)."""
    warnings = validate_graph_configs(graph)
    return {"errors": [], "warnings": list(warnings)}


# ---------------------------------------------------------------------------
# Dispatch — maps a tool name to its handler. The HTTP layer is the only
# caller; validation and argument coercion live there so the tool
# functions stay typed-Python-clean.
# ---------------------------------------------------------------------------


TOOL_NAMES = {
    "list_node_types",
    "get_node_schema",
    "add_node",
    "update_node_config",
    "delete_node",
    "connect_nodes",
    "disconnect_edge",
    "validate_graph",
}


# Mutation tools produce a new graph; read-only tools do not.
MUTATION_TOOLS = {
    "add_node",
    "update_node_config",
    "delete_node",
    "connect_nodes",
    "disconnect_edge",
}


def dispatch(
    tool_name: str,
    graph: dict[str, Any],
    args: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Dispatch a tool call by name. Returns ``(new_graph_or_None, result)``.

    ``new_graph`` is ``None`` for read-only tools (``list_node_types``,
    ``get_node_schema``, ``validate_graph``) — the caller must not
    persist anything in that case.
    """
    if tool_name not in TOOL_NAMES:
        raise ToolLayerError(
            f"Unknown tool '{tool_name}'. Known tools: {sorted(TOOL_NAMES)}."
        )

    if tool_name == "list_node_types":
        return None, list_node_types(category=args.get("category"))

    if tool_name == "get_node_schema":
        node_type = args.get("node_type") or args.get("type")
        if not node_type:
            raise ToolLayerError("get_node_schema requires 'node_type'.")
        return None, get_node_schema(node_type)

    if tool_name == "validate_graph":
        return None, validate_graph(graph)

    if tool_name == "add_node":
        node_type = args.get("node_type") or args.get("type")
        if not node_type:
            raise ToolLayerError("add_node requires 'node_type'.")
        return add_node(
            graph,
            node_type=node_type,
            config=args.get("config"),
            position=args.get("position"),
            display_name=args.get("display_name"),
        )

    if tool_name == "update_node_config":
        node_id = args.get("node_id")
        partial = args.get("partial")
        if not node_id:
            raise ToolLayerError("update_node_config requires 'node_id'.")
        if partial is None:
            raise ToolLayerError("update_node_config requires 'partial'.")
        return update_node_config(
            graph,
            node_id=node_id,
            partial=partial,
            display_name=args.get("display_name"),
        )

    if tool_name == "delete_node":
        node_id = args.get("node_id")
        if not node_id:
            raise ToolLayerError("delete_node requires 'node_id'.")
        return delete_node(graph, node_id=node_id)

    if tool_name == "connect_nodes":
        source = args.get("source")
        target = args.get("target")
        if not source or not target:
            raise ToolLayerError("connect_nodes requires 'source' and 'target'.")
        return connect_nodes(
            graph,
            source=source,
            target=target,
            source_handle=args.get("source_handle"),
            target_handle=args.get("target_handle"),
        )

    if tool_name == "disconnect_edge":
        edge_id = args.get("edge_id")
        if not edge_id:
            raise ToolLayerError("disconnect_edge requires 'edge_id'.")
        return disconnect_edge(graph, edge_id=edge_id)

    # Unreachable — TOOL_NAMES set is exhaustive above.
    raise ToolLayerError(f"Tool '{tool_name}' recognised but not dispatched.")
