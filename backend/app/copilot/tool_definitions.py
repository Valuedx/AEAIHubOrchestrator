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
    # ----------------------------------------------------------------------
    # Runner tools (COPILOT-01b.ii — stateful, touch the engine).
    # These are handled by app/copilot/runner_tools.py rather than the
    # pure tool_layer.py; the agent's dispatcher routes by tool name.
    # ----------------------------------------------------------------------
    {
        "name": "get_automationedge_handoff_info",
        "description": (
            "Use when the user's request (or a sub-workflow within it) "
            "is a DETERMINISTIC RPA task — form submission, SAP / ERP "
            "posting, file transfer, data entry, anything rule-based "
            "that belongs in an RPA platform rather than an AI agent. "
            "Returns the tenant's registered AutomationEdge connections "
            "and the AE Copilot deep-link URL (AE Copilot is a separate "
            "product for designing the RPA steps themselves — NOT built "
            "by this orchestrator). Call this BEFORE adding an "
            "automationedge node so you can (a) propose two paths to "
            "the user — inline vs. hand-off to AE Copilot — and (b) "
            "know which AE connection label to use. Read-only; doesn't "
            "mutate the draft."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "search_docs",
        "description": (
            "Search the orchestrator's own documentation + node "
            "registry for relevant context. Use this when the user's "
            "question is about HOW something works ('how does the "
            "Intent Classifier scope entities?', 'what does this "
            "error mean?', 'how do I set up AutomationEdge?') — "
            "docs are source-of-truth for concepts and patterns, "
            "whereas list_node_types / get_node_schema are source-"
            "of-truth for live node structure. Prefer the live API "
            "for schema-shaped questions. Returns {query, match_count, "
            "results: [{source_path, title, anchor, score, excerpt}]}. "
            "If match_count is 0 or results look off-topic, try "
            "rephrasing with different keywords — the search is "
            "word-overlap-based (no semantic matching)."
        ),
        "input_schema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Free-form search query. Case-insensitive; "
                        "stopwords are stripped automatically."
                    ),
                },
                "top_k": {
                    "type": "integer",
                    "description": (
                        "How many results to return. Default 5; "
                        "capped at 20 server-side."
                    ),
                },
            },
        },
    },
    {
        "name": "get_node_examples",
        "description": (
            "Look up one node type's full registry entry plus related "
            "codewiki sections. Use this BEFORE proposing a complex "
            "config to the user — you'll see the canonical defaults, "
            "enum values, and linked sections that explain how the "
            "node behaves at runtime. Returns {node_type, "
            "registry_entry, related_sections}. registry_entry is "
            "null when the type isn't in the registry — call "
            "list_node_types to pick a real one."
        ),
        "input_schema": {
            "type": "object",
            "required": ["node_type"],
            "properties": {
                "node_type": {
                    "type": "string",
                    "description": "Registry type id, e.g. 'llm_agent'.",
                },
            },
        },
    },
    {
        "name": "execute_draft",
        "description": (
            "Trial-run the WHOLE draft graph end-to-end through the "
            "real engine. Use this to prove the draft works before "
            "asking the user to promote it. Blocks up to "
            "timeout_seconds (default 30, max 300) before returning. "
            "Returns {instance_id, status, elapsed_ms, output, "
            "started_at, completed_at} on completion; status='timeout' "
            "with a hint if the run is still going (call "
            "get_execution_logs to check progress). Validation errors "
            "block the run — fix them via update_node_config first. "
            "DO NOT call this before narrating the draft to the user "
            "— trial runs consume real tokens / external API calls / "
            "side effects, so the user should OK the draft first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "payload": {
                    "type": "object",
                    "description": (
                        "Trigger payload for the run (becomes "
                        "context['trigger']). Default {}."
                    ),
                },
                "deterministic_mode": {
                    "type": "boolean",
                    "description": (
                        "Set LLM temperature to 0 for reproducible "
                        "runs. Default false."
                    ),
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": (
                        "How long to block before returning a "
                        "timeout result. Default 30; capped at 300 "
                        "server-side."
                    ),
                },
            },
        },
    },
    {
        "name": "get_execution_logs",
        "description": (
            "Fetch per-node execution logs for a prior execute_draft "
            "run. Use to debug failures — the result includes each "
            "node's output_json and error so you can localise where "
            "the run went wrong and propose a config fix. Only "
            "instance_ids returned by execute_draft are readable; "
            "arbitrary production instance_ids are rejected. Pass "
            "node_id to narrow to one node."
        ),
        "input_schema": {
            "type": "object",
            "required": ["instance_id"],
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": (
                        "The instance_id returned by execute_draft."
                    ),
                },
                "node_id": {
                    "type": "string",
                    "description": "Optional. Filter to one node.",
                },
            },
        },
    },
    {
        "name": "test_node",
        "description": (
            "Run ONE node handler in isolation against pinned upstream "
            "data — does not run the rest of the workflow. Use this to "
            "debug a specific node's config without paying for a full "
            "end-to-end run. The node's handler is dispatched exactly "
            "as it would be at runtime, so LLM / MCP / credential "
            "lookups all resolve the same way. 'pins' is a dict keyed "
            "by upstream node_id with the synthetic output each "
            "upstream should return for this probe — takes precedence "
            "over any pinnedOutput already on the draft. "
            "'trigger_payload' seeds the synthetic context's 'trigger' "
            "key (use this to simulate the webhook / scheduled payload "
            "the node would receive at runtime). Exceptions from the "
            "handler are caught and returned as {node_id, error, "
            "elapsed_ms} so you can read the failure message and "
            "suggest a config fix."
        ),
        "input_schema": {
            "type": "object",
            "required": ["node_id"],
            "properties": {
                "node_id": {"type": "string"},
                "trigger_payload": {
                    "type": "object",
                    "description": (
                        "Optional. Seeds context['trigger']. Defaults "
                        "to {} when absent."
                    ),
                },
                "pins": {
                    "type": "object",
                    "description": (
                        "Optional. Map of upstream node_id → synthetic "
                        "output the probe should see for that node. "
                        "Values can be objects (merged into context[node_id]) "
                        "or any JSON scalar (placed verbatim). "
                        "Overrides any pinnedOutput already on the draft."
                    ),
                },
            },
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
