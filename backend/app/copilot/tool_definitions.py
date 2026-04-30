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

COPILOT-V2 ``side_effects`` field
---------------------------------

Each tool definition carries an extra ``side_effects`` list (added in
COPILOT-V2). Vocabulary:

  * ``read_only`` — no state change, no LLM call, no external API
  * ``mutates_draft`` — modifies the WorkflowDraft graph
  * ``writes_db`` — persists rows to non-draft tables (e.g. scenarios)
  * ``consumes_tokens`` — burns LLM tokens via a provider call
  * ``spawns_run`` — creates a WorkflowInstance; can have downstream
    side-effects (sends Slack messages, hits APIs, writes files)
  * ``external_call`` — hits an external system (MCP server, LLM
    provider, AutomationEdge, third-party API)

The frontend uses these for warning UI; the agent's system prompt
reads them to decide when to confirm with the user before calling.
SDK converters (Anthropic / Google) STRIP this field — the model
never sees it in its tool schema.
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
        "side_effects": ["read_only"],
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
        "side_effects": ["read_only"],
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
        "side_effects": ["mutates_draft"],
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
        "side_effects": ["mutates_draft"],
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
        "side_effects": ["mutates_draft"],
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
        "side_effects": ["mutates_draft"],
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
        "side_effects": ["mutates_draft"],
        "input_schema": {
            "type": "object",
            "required": ["edge_id"],
            "properties": {
                "edge_id": {"type": "string"},
            },
        },
    },
    {
        "name": "check_draft",
        "description": (
            "Preferred post-mutation check. Returns {errors, "
            "warnings, lints, lints_enabled} — the schema-validator's "
            "output PLUS the SMART-04 proactive structure lints "
            "(no_trigger, disconnected_node, orphan_edge, "
            "missing_credential, prompt_cache_breakage [V2], "
            "react_role_no_category_restriction [V2], "
            "react_worker_iterations_too_low [V2], loopback_*). "
            "Call this after every run of mutations and before "
            "narrating the draft to the user so the narration can "
            "call out fix-before-promote issues. Each lint has "
            "{code, severity, message, fix_hint, node_id}. If "
            "`lints_enabled` is false the tenant has opted out "
            "(cost-conscious config); schema validation still runs."
        ),
        "side_effects": ["read_only"],
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "validate_graph",
        "description": (
            "Schema-only validator (no SMART-04 lints). Prefer "
            "`check_draft` instead — it returns schema validation "
            "AND structure lints in one call. Kept for back-compat."
        ),
        "side_effects": ["read_only"],
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
        "name": "recall_patterns",
        "side_effects": ["read_only"],
        "description": (
            "Retrieve the nearest accepted workflow patterns this "
            "tenant has promoted in the past so you can adapt them "
            "as few-shot instead of synthesising from scratch. The "
            "tenant's own conventions — naming, preferred MCP "
            "servers, memory profile choices — live in these "
            "patterns. Returns {enabled, query, match_count, "
            "patterns: [{id, title, score, nl_intent, tags, "
            "node_types, node_count, edge_count, created_at, "
            "graph_json}]}. Call this AFTER intent-extract but "
            "BEFORE add_node — if a strong match exists, adapt "
            "that graph rather than synthesising a fresh one. "
            "`enabled: false` means the tenant has opted out; "
            "fall back to synthesising with no patterns to show "
            "and don't offer to retrieve."
        ),
        "input_schema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Free-form intent query — typically the "
                        "user's most recent message. Tokens are "
                        "scored against each candidate pattern's "
                        "stored NL intent + tags + node types + "
                        "title (2× title boost)."
                    ),
                },
                "top_k": {
                    "type": "integer",
                    "description": (
                        "How many patterns to return. Default 3; "
                        "capped at 10 server-side."
                    ),
                },
            },
        },
    },
    {
        "name": "discover_mcp_tools",
        "side_effects": ["external_call"],
        "description": (
            "List the tools available on the tenant's connected MCP "
            "servers so you can surface relevant ones to the user "
            "during drafting (e.g. 'this tenant has threat_intel."
            "enrich_ip on their SOC MCP — consider adding an MCP "
            "Tool node for it'). Returns {discovery_enabled, "
            "server_label, tools: [{name, title, description, "
            "category, safety_tier, tags}]}. When "
            "`discovery_enabled` is false the tenant opted out — "
            "don't mention MCP tools in the narration. List is "
            "cached server-side for 5 minutes per (tenant, "
            "server) — calling more than once a turn is cheap."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "server_label": {
                    "type": "string",
                    "description": (
                        "Optional — which tenant_mcp_servers label "
                        "to query. Omit for the tenant's default "
                        "server (or the ORCHESTRATOR_MCP_SERVER_URL "
                        "env fallback when no per-tenant row exists)."
                    ),
                },
            },
        },
    },
    {
        "name": "get_automationedge_handoff_info",
        "side_effects": ["read_only"],
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
        "side_effects": ["read_only"],
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
        "side_effects": ["read_only"],
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
        "side_effects": ["spawns_run", "consumes_tokens", "external_call", "writes_db"],
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
        "side_effects": ["read_only"],
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
        "side_effects": ["consumes_tokens", "external_call"],
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
    # ----------------------------------------------------------------------
    # Test scenarios (COPILOT-03.a). Saved regression cases: given this
    # trigger payload, the draft should produce output containing X.
    # ----------------------------------------------------------------------
    {
        "name": "save_test_scenario",
        "side_effects": ["writes_db"],
        "description": (
            "Save a named regression scenario the user cares about — "
            "a trigger payload plus an optional assertion about the "
            "output. Call this when the user says 'remember that I "
            "want this to work' or similar. The scenario can be "
            "re-run at any time via run_scenario, and Promote (once "
            "03.e lands) will re-run all scenarios before letting "
            "the draft leave the draft workspace. Names must be "
            "unique per draft; cap is 50 scenarios per draft. "
            "Returns {scenario_id, name, created_at} on success or "
            "{error} on validation failure / duplicate / cap hit."
        ),
        "input_schema": {
            "type": "object",
            "required": ["name", "payload"],
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Short human-readable name (e.g. 'empty slack "
                        "message', 'oversized attachment'). Unique "
                        "per draft. Max 128 chars."
                    ),
                },
                "payload": {
                    "type": "object",
                    "description": (
                        "Trigger payload the run will receive "
                        "(becomes context['trigger'])."
                    ),
                },
                "expected_output_contains": {
                    "type": "object",
                    "description": (
                        "Optional partial-match assertion on the "
                        "output. Every key/value must appear in the "
                        "actual output (recursive dict match; list "
                        "positional match). Omit to record the "
                        "scenario without an assertion — run_scenario "
                        "then just returns the actual output."
                    ),
                },
                "expected_output_predicates": {
                    "type": "array",
                    "description": (
                        "Optional list of behavior-quality assertions "
                        "(COPILOT-V2). Each entry is "
                        "`{type, args}`. Predicate types: "
                        "ends_with_question, contains_any, "
                        "contains_all, lacks_terms, intent_in, "
                        "regex_match, no_max_iterations_marker, "
                        "tool_called, no_tool_called, tool_call_count. "
                        "Use this for rubric-style assertions the "
                        "dict-match `expected_output_contains` "
                        "can't express cleanly (e.g. 'reply ends with "
                        "a question', 'reply does NOT contain "
                        "system prompt')."
                    ),
                    "items": {
                        "type": "object",
                        "required": ["type"],
                        "properties": {
                            "type": {"type": "string"},
                            "args": {"type": "object"},
                        },
                    },
                },
            },
        },
    },
    {
        "name": "run_scenario",
        "side_effects": ["spawns_run", "consumes_tokens", "external_call", "writes_db"],
        "description": (
            "Re-run a saved test scenario against the current draft "
            "and diff the output against the scenario's "
            "expected_output_contains. Returns {scenario_id, name, "
            "status, mismatches, actual_output, execution}. Status "
            "is one of 'pass' / 'fail' / 'stale' / 'error'. "
            "'mismatches' is a list of {path, expected, actual} for "
            "fail cases. 'execution' carries the underlying "
            "execute_draft_sync result so you can surface logs via "
            "get_execution_logs without another call. Call this "
            "after edits the user cares about to confirm a regression "
            "didn't sneak in."
        ),
        "input_schema": {
            "type": "object",
            "required": ["scenario_id"],
            "properties": {
                "scenario_id": {
                    "type": "string",
                    "description": (
                        "UUID returned by save_test_scenario or "
                        "list_scenarios."
                    ),
                },
            },
        },
    },
    {
        "name": "list_scenarios",
        "side_effects": ["read_only"],
        "description": (
            "List all test scenarios saved on this draft. Returns "
            "{count, scenarios: [{scenario_id, name, payload, "
            "has_expected, created_at}]}. Use this when the user "
            "asks 'what scenarios do we have' or when you need to "
            "pick a scenario to run without guessing an id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    # ----------------------------------------------------------------------
    # Debug + error inspection (COPILOT-03.b). Ad-hoc runs with pins /
    # config overrides (nothing persisted), and a per-node error probe.
    # ----------------------------------------------------------------------
    {
        "name": "run_debug_scenario",
        "side_effects": ["spawns_run", "consumes_tokens", "external_call", "writes_db"],
        "description": (
            "Ad-hoc trial run with optional overrides that do NOT "
            "touch the saved draft. Use when the user wants to try "
            "'what if node_3 returned X' or 'what if retries were "
            "5 on the email node' without persisting the change. "
            "'pins' maps node_id -> synthetic upstream output "
            "(written to that node's data.pinnedOutput on a local "
            "copy); 'node_overrides' maps node_id -> partial "
            "config dict merged into that node's data.config. Any "
            "unknown node_id short-circuits with an error — run "
            "check_draft if you're unsure what's on the graph. "
            "Returns the same shape as execute_draft plus "
            "{overrides_applied: {pins: [...], node_overrides: [...]}} "
            "so you can narrate exactly what was in force."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "payload": {
                    "type": "object",
                    "description": "Trigger payload. Default {}.",
                },
                "pins": {
                    "type": "object",
                    "description": (
                        "Map of node_id -> synthetic output dict. "
                        "Takes precedence over any pinnedOutput "
                        "already on the draft for that node."
                    ),
                },
                "node_overrides": {
                    "type": "object",
                    "description": (
                        "Map of node_id -> partial config dict "
                        "(merged into data.config). Use to probe "
                        "config alternatives without saving."
                    ),
                },
                "deterministic_mode": {
                    "type": "boolean",
                    "description": (
                        "Passthrough to execute_draft — LLM "
                        "temperature 0. Default false."
                    ),
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": (
                        "Passthrough to execute_draft. Default 30, "
                        "max 300."
                    ),
                },
            },
        },
    },
    {
        "name": "get_node_error",
        "side_effects": ["read_only"],
        "description": (
            "Narrow on one failed node from a prior execute_draft "
            "or run_debug_scenario run. Returns {instance_id, "
            "node_id, node_type, status, error, resolved_config, "
            "output_json, started_at, completed_at}. "
            "'resolved_config' is the config the handler actually "
            "saw AFTER expression resolution — the post-resolution "
            "view is almost always what you want for a config-fix "
            "suggestion (pre-resolution config is visible on the "
            "draft graph). If the node succeeded, the response "
            "includes a 'note' telling you the failure is likely "
            "downstream. Safety: only copilot-initiated "
            "(is_ephemeral) instances are readable."
        ),
        "input_schema": {
            "type": "object",
            "required": ["instance_id", "node_id"],
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": "Instance id from execute_draft.",
                },
                "node_id": {
                    "type": "string",
                    "description": "Node id that you want to inspect.",
                },
            },
        },
    },
    {
        "name": "suggest_fix",
        "side_effects": ["consumes_tokens"],
        "description": (
            "Propose a minimal config patch for ONE failing node. "
            "Makes a constrained LLM subcall scoped to the node's "
            "config schema; the result's 'proposed_patch' only "
            "contains keys that appear in the schema (anything "
            "else the model suggested is listed in 'dropped_keys' "
            "for narration). "
            "NEVER AUTO-APPLIES. Always round-trip the proposal "
            "through the user: show the patch + rationale + "
            "confidence, wait for approval, then call "
            "update_node_config with the fields the user agreed "
            "to. "
            "Per-draft cap of 5 suggest_fix calls to prevent a "
            "runaway auto-heal loop; beyond the cap the tool "
            "returns an error telling you to hand off to the user "
            "(structural problem, not a config fix). "
            "Typical use: after get_node_error shows a failure, "
            "call suggest_fix to get a proposal you can surface to "
            "the user."
        ),
        "input_schema": {
            "type": "object",
            "required": ["node_id", "error"],
            "properties": {
                "node_id": {
                    "type": "string",
                    "description": (
                        "The failing node's id (must exist in the "
                        "current draft graph)."
                    ),
                },
                "error": {
                    "type": "string",
                    "description": (
                        "The error message the node raised — "
                        "typically get_node_error's 'error' field."
                    ),
                },
            },
        },
    },
    # ----------------------------------------------------------------------
    # COPILOT-V2 — debugging power tools.
    # ----------------------------------------------------------------------
    {
        "name": "diff_drafts",
        "side_effects": ["read_only"],
        "description": (
            "Show what changed between the current draft and either "
            "(a) the published workflow it forked from "
            "(against='base_workflow', the default) or (b) another "
            "draft owned by the same tenant (against='draft' + "
            "other_draft_id). Returns a structured diff: "
            "{node_changes: {added, removed, modified}, edge_changes: "
            "{added, removed}, summary}. Use this when the user asks "
            "'what did I just change?' or before promote so you can "
            "narrate the diff. Long string values (prompts, etc.) are "
            "auto-truncated; positions are NOT diffed (canvas moves "
            "aren't semantic changes)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "against": {
                    "type": "string",
                    "enum": ["base_workflow", "draft"],
                    "description": (
                        "What to diff against. Default 'base_workflow' "
                        "(the published workflow the draft forked "
                        "from). Use 'draft' + other_draft_id to compare "
                        "two drafts."
                    ),
                },
                "other_draft_id": {
                    "type": "string",
                    "description": (
                        "Required when against='draft'. The other "
                        "draft's UUID."
                    ),
                },
            },
        },
    },
    {
        "name": "replay_node_with_overrides",
        "side_effects": ["consumes_tokens", "external_call"],
        "description": (
            "Re-run ONE node from a prior copilot-initiated run, "
            "with optional config overrides applied just for this "
            "replay. The captured upstream context is reused, so this "
            "is the FAST iteration loop for prompt edits: edit a "
            "Worker's systemPrompt → replay the Worker on last "
            "execute_draft's instance_id → see the new output in "
            "seconds without re-running the whole graph. "
            "DO NOT use this on production instance ids — only "
            "is_ephemeral (copilot-initiated) instances are readable. "
            "Returns {instance_id, node_id, node_type, output, "
            "elapsed_ms, overrides_applied} on success or {error} "
            "on handler failure."
        ),
        "input_schema": {
            "type": "object",
            "required": ["instance_id", "node_id"],
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": (
                        "Instance id from a prior execute_draft / "
                        "run_scenario / run_debug_scenario."
                    ),
                },
                "node_id": {
                    "type": "string",
                    "description": "Node id to replay.",
                },
                "config_overrides": {
                    "type": "object",
                    "description": (
                        "Optional partial config dict merged into "
                        "the node's data.config for this replay only. "
                        "The draft is NOT modified. Use to A/B prompt "
                        "edits, model swaps, maxIterations, etc."
                    ),
                },
                "deterministic_mode": {
                    "type": "boolean",
                    "description": (
                        "Reserved for handler hint. Default false."
                    ),
                },
            },
        },
    },
    {
        "name": "evaluate_run",
        "side_effects": ["consumes_tokens"],
        "description": (
            "LLM-as-judge over a prior run's user-facing reply, "
            "given a free-form NL rubric. Use this for "
            "behavior-quality checks the dict-match / predicate "
            "matchers can't express cleanly: 'leads with the answer "
            "not filler', 'tone is appropriate for a business user', "
            "'reply correctly handled a corrected identifier'. "
            "Returns {verdicts: [{criterion, status: pass|fail|partial, "
            "why}], overall, summary, model_used, usage}. "
            "ONLY copilot-initiated (is_ephemeral) instances are "
            "judgeable. Costs tokens via the same provider as your "
            "session."
        ),
        "input_schema": {
            "type": "object",
            "required": ["instance_id", "rubric"],
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": (
                        "Instance id from a prior execute_draft etc."
                    ),
                },
                "rubric": {
                    "type": "string",
                    "description": (
                        "Free-form NL rubric — one or more criteria. "
                        "Numbered list / bullet list / prose all "
                        "fine. Be specific: 'reply leads with the "
                        "answer not a filler greeting' beats "
                        "'good UX'."
                    ),
                },
            },
        },
    },
    {
        "name": "inspect_node_artifact",
        "side_effects": ["read_only"],
        "description": (
            "Fetch the full output payload for a node whose in-context "
            "value is an overflow stub (CTX-MGMT.A). When a node's "
            "output exceeded its `contextOutputBudget` (default 64 kB), "
            "the engine replaced `context[node_id]` with a small stub "
            "`{_overflow: True, _artifact_id: <uuid>, summary, "
            "preview, ...}` and persisted the full payload to "
            "`node_output_artifacts`. This tool reads that table when "
            "the user asks 'show me what node X actually produced'. "
            "Same ephemeral-only safety as get_execution_logs — "
            "production instances are NOT readable. If the node "
            "didn't overflow (output fits inline), this returns an "
            "error pointing at get_execution_logs instead."
        ),
        "input_schema": {
            "type": "object",
            "required": ["instance_id", "node_id"],
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": (
                        "Instance id from a prior execute_draft / "
                        "run_scenario / run_debug_scenario."
                    ),
                },
                "node_id": {
                    "type": "string",
                    "description": "Node id whose overflow artifact to fetch.",
                },
            },
        },
    },
    {
        "name": "inspect_context_flow",
        "side_effects": ["read_only"],
        "description": (
            "Read the per-instance context-write trace (CTX-MGMT.H). "
            "When tracing is on for an instance (always on for "
            "copilot-ephemeral runs; opt-in via "
            "`tenant_policies.context_trace_enabled` for production), "
            "the engine writes one row to `instance_context_trace` "
            "per `context[node_id] = output` write. Use this to "
            "answer 'where did node_X come from?' or 'which writes "
            "touched the case slot?' without scraping "
            "`instance.context_json` by hand. Returns "
            "`{instance_id, key_filter, event_count, events: "
            "[{id, node_id, op, key, size_bytes, reducer, "
            "overflowed, ts}, ...]}` ordered ts ASC, capped at 200. "
            "Each event records the reducer that was applied "
            "(`overwrite` / `append` / `merge` / etc. — CTX-MGMT.L) "
            "and whether the write overflowed (CTX-MGMT.A). Same "
            "ephemeral-only safety as get_execution_logs — "
            "production instances are NOT readable."
        ),
        "input_schema": {
            "type": "object",
            "required": ["instance_id"],
            "properties": {
                "instance_id": {
                    "type": "string",
                    "description": (
                        "Instance id from a prior execute_draft / "
                        "run_scenario / run_debug_scenario."
                    ),
                },
                "key": {
                    "type": "string",
                    "description": (
                        "Optional. Filter to events where the slot "
                        "key matches. Use a trailing `*` for prefix "
                        "match (e.g. `node_*` returns every node "
                        "write); omit for all events on the instance."
                    ),
                },
            },
        },
    },
    {
        "name": "suggest_issue_filing",
        "side_effects": ["read_only"],
        "description": (
            "Build a tenant-gated GitHub issue deep-link with the "
            "draft snapshot (shape only — configs are NOT included), "
            "recent tool-call trace (names + arg KEYS only), and "
            "user-supplied summary pre-filled in the body. Use this "
            "ONLY when (a) the user explicitly asks to file a bug / "
            "feature, OR (b) you've hit an engine error / documented "
            "PENDING capability and the user might want to surface "
            "it to the product team. The link is a SUGGESTION — "
            "surface it to the user; never claim you opened the "
            "issue. The body is auto-redacted for high-confidence "
            "secret patterns (API keys, JWTs, bearer tokens, "
            "emails) but the user MUST review before submitting. "
            "Returns {enabled, link, body_preview, "
            "redactions_applied, repo, labels}. When enabled=false "
            "the deployment hasn't configured a target repo — "
            "don't surface a link in that case."
        ),
        "input_schema": {
            "type": "object",
            "required": ["category", "summary"],
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["bug", "feature"],
                    "description": (
                        "'bug' for unexpected engine errors or "
                        "broken behavior; 'feature' for capability "
                        "gaps and PENDING items."
                    ),
                },
                "summary": {
                    "type": "string",
                    "description": (
                        "One-line user-visible summary that will "
                        "become the issue title."
                    ),
                },
                "error_context": {
                    "type": "string",
                    "description": (
                        "Optional engine error text or traceback "
                        "snippet. Will be redacted before inclusion."
                    ),
                },
            },
        },
    },
]


def _strip_internal_metadata(t: dict[str, Any]) -> dict[str, Any]:
    """Strip COPILOT-internal fields (``side_effects`` and any future
    ones) before handing the tool definition to an LLM SDK. The model
    never sees these — they're for the frontend / agent dispatcher."""
    return {k: v for k, v in t.items() if k != "side_effects"}


def to_anthropic_tools() -> list[dict[str, Any]]:
    """Anthropic uses the {name, description, input_schema} shape —
    strip the ``side_effects`` metadata before sending."""
    return [_strip_internal_metadata(t) for t in COPILOT_TOOL_DEFINITIONS]


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
