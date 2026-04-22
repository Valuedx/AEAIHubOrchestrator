"""JSON-schema definitions for the copilot tool surface.

Deliberately hand-written (not auto-generated from the pure tool-layer
function signatures) because:

1. The agent-facing description is a *prompt* — it matters more what
   shape it nudges the LLM toward than what the Python signature says.
   Good descriptions here drive the "pattern match before synthesis"
   behaviour the system prompt asks for.
2. Auto-generating from types would miss the semantic hints ("use
   this when..." / "do NOT use this to..."). A stub in the tool
   layer and the rich description here is the separation we want.

Shape matches Anthropic's ``{name, description, input_schema}`` form.
The OpenAI ``{type: "function", function: {...}}`` and Google
``Tool(function_declarations=[...])`` shapes are derived from this
same structure — see ``agent.py::_to_anthropic_tools`` etc. when
those providers land.
"""

from __future__ import annotations

from typing import Any


COPILOT_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "list_node_types",
        "description": (
            "Browse the catalogue of node types available on the canvas. "
            "Returns a compact list without full config schemas — call "
            "get_node_schema for the one you pick. Use category to filter "
            "(trigger, agent, action, logic, knowledge, notification, nlp). "
            "Call this BEFORE add_node when you're unsure which node "
            "matches the user's intent."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Optional category filter.",
                },
            },
        },
    },
    {
        "name": "get_node_schema",
        "description": (
            "Full config schema for one node type. Use this to learn what "
            "fields a node accepts BEFORE calling add_node with a config. "
            "Returns {type, category, label, description, icon, "
            "config_schema} where config_schema is the JSON schema of "
            "valid config fields."
        ),
        "input_schema": {
            "type": "object",
            "required": ["node_type"],
            "properties": {
                "node_type": {
                    "type": "string",
                    "description": "The node type id, e.g. 'llm_agent'.",
                },
            },
        },
    },
    {
        "name": "add_node",
        "description": (
            "Append a new node to the draft graph. The resulting node "
            "gets its canonical label from the registry; you supply the "
            "node_type id (not the label). Always look up the schema "
            "via get_node_schema first so you know which config fields "
            "are valid. Returns {node_id, node}."
        ),
        "input_schema": {
            "type": "object",
            "required": ["node_type"],
            "properties": {
                "node_type": {
                    "type": "string",
                    "description": "Registry type id, e.g. 'llm_agent'.",
                },
                "config": {
                    "type": "object",
                    "description": "Node config — keys per the schema.",
                },
                "position": {
                    "type": "object",
                    "description": "Canvas position {x, y} in pixels.",
                    "properties": {
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                    },
                },
                "display_name": {
                    "type": "string",
                    "description": (
                        "Optional human-readable label shown on the "
                        "canvas. Useful when you have several LLM Agent "
                        "nodes and want the user to tell them apart."
                    ),
                },
            },
        },
    },
    {
        "name": "update_node_config",
        "description": (
            "Merge a partial config into an existing node. Keys not in "
            "'partial' are left alone; pass null to clear a key. Use "
            "this to iterate on a node's config without deleting and "
            "re-adding. Also accepts display_name (empty string clears)."
        ),
        "input_schema": {
            "type": "object",
            "required": ["node_id", "partial"],
            "properties": {
                "node_id": {"type": "string"},
                "partial": {
                    "type": "object",
                    "description": "Config keys to merge.",
                },
                "display_name": {"type": "string"},
            },
        },
    },
    {
        "name": "delete_node",
        "description": (
            "Remove a node. Any edges touching it are also removed — "
            "the result payload lists which edge ids were cascaded so "
            "you can reconnect if needed."
        ),
        "input_schema": {
            "type": "object",
            "required": ["node_id"],
            "properties": {
                "node_id": {"type": "string"},
            },
        },
    },
    {
        "name": "connect_nodes",
        "description": (
            "Add a directed edge source → target. Refuses self-loops "
            "(workflows are DAGs) and refuses duplicate edges between "
            "the same pair of handles. source_handle / target_handle "
            "are only needed for nodes that expose multiple handles "
            "(e.g. Condition node's 'true' / 'false' outputs); omit "
            "them for single-handle nodes."
        ),
        "input_schema": {
            "type": "object",
            "required": ["source", "target"],
            "properties": {
                "source": {"type": "string"},
                "target": {"type": "string"},
                "source_handle": {"type": "string"},
                "target_handle": {"type": "string"},
            },
        },
    },
    {
        "name": "disconnect_edge",
        "description": "Remove one edge by id.",
        "input_schema": {
            "type": "object",
            "required": ["edge_id"],
            "properties": {
                "edge_id": {"type": "string"},
            },
        },
    },
    {
        "name": "validate_graph",
        "description": (
            "Run the same validator the live workflow-save path uses. "
            "Returns {errors, warnings}. Call this after a run of "
            "mutations before you narrate the draft to the user — a "
            "validation failure is something the user needs to know."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]


def to_anthropic_tools() -> list[dict[str, Any]]:
    """Anthropic uses the definitions as-is."""
    return [dict(t) for t in COPILOT_TOOL_DEFINITIONS]


def to_google_tools() -> list[Any]:
    """Convert definitions into Google's unified ``google-genai`` tool
    shape: one ``types.Tool`` wrapping a list of
    ``types.FunctionDeclaration(name, description, parameters)``. The
    ``parameters`` field is the same JSON Schema we hand Anthropic —
    Google's SDK accepts it verbatim.

    Lazy-imports ``google.genai.types`` so unit tests that don't use
    Google/Vertex don't pay the import cost (and don't fail when the
    SDK is absent in a minimal environment).
    """
    from google.genai import types  # noqa: F401 — lazy

    func_decls = [
        types.FunctionDeclaration(
            name=t["name"],
            description=t["description"],
            parameters=t["input_schema"],
        )
        for t in COPILOT_TOOL_DEFINITIONS
    ]
    return [types.Tool(function_declarations=func_decls)]
