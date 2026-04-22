# Workflow Authoring Copilot

A conversational assistant that drafts, modifies, validates, and (eventually) debugs workflows through tool-calling — Claude-Code-shape applied to this orchestrator's DAG builder. The end-user describes intent in natural language; the copilot asks for what it needs, drafts a graph, runs validation, narrates what it built, and lets the human accept or reject before anything lands in the live workflow catalogue.

Scoped across three tickets: [COPILOT-01](feature-roadmap.md#copilot-01--draft-workspace-model--agent-tool-surface) (backend foundation), [COPILOT-02](feature-roadmap.md#copilot-02--chat-pane--diff-apply-ui) (chat UI + diff apply), [COPILOT-03](feature-roadmap.md#copilot-03--debug--test-scenario--auto-heal-loop) (debug / test-scenario / auto-heal). This page documents what's shipped today — **COPILOT-01a + COPILOT-01b.i + Google/Vertex slice of COPILOT-01b.iv**: draft-workspace safety boundary, pure tool layer, agent runner driving **Anthropic + Google AI Studio + Google Vertex AI** through a single adapter-dispatched loop, session/turn SSE streaming. Still outstanding: `test_node` / `execute_draft` runner tools (01b.ii), system-KB RAG ingestion with `search_docs` / `get_node_examples` (01b.iii), OpenAI provider + per-session token budget (remainder of 01b.iv).

---

## §1. Architecture

```
┌─────────────────┐  NL intent   ┌─────────────────┐  function calls  ┌──────────────┐
│ User (chat pane │─────────────▶│  Agent runner   │─────────────────▶│  Tool layer  │
│ COPILOT-02)     │              │  (COPILOT-01b)  │                  │  (pure fns)  │
└─────────────────┘              └─────────────────┘                  └──────┬───────┘
        ▲                                ▲                                   │ graph in
        │ diff + promote                 │ SSE stream                        │ graph out
        │                                │                                   ▼
┌─────────────────┐                      │                           ┌──────────────┐
│ Canvas (draft   │                      └───────────────────────────│ workflow_    │
│ overlay)        │                                                  │ drafts table │
└─────────────────┘                                                  └──────┬───────┘
                                                                            │  /promote
                                                                            ▼
                                                              ┌─────────────────────────┐
                                                              │ workflow_definitions    │
                                                              │ (+ workflow_snapshots)  │
                                                              └─────────────────────────┘
```

**Safety boundary.** Every mutation lands in a `workflow_drafts` row. Nothing reaches `workflow_definitions` until the human calls `/promote`. This is the single most important design call — a copilot that edit-and-executes a *published* workflow is too high blast radius, and the draft layer makes every downstream surface (chat UI, auto-heal, test scenarios) safe to build.

**Agent runner (01b.i + Google/Vertex).** `app/copilot/agent.py` holds a provider-agnostic per-turn loop: load prior history from `copilot_turns` → build provider-specific state via the adapter → call LLM → for each tool call, dispatch through `tool_layer.dispatch` → append results to state → loop until the assistant produces text with no more tool calls. Capped at `MAX_TOOL_ITERATIONS = 12` so a pathological "flap" can't burn unbounded cost. Every turn (user + assistant + tool) is persisted to `copilot_turns` as it happens; a disconnected client never loses progress.

**Provider adapters.** Three providers ship today, all behind the same `_PROVIDER_ADAPTERS` dispatch: **Anthropic** (`claude-sonnet-4-6`), **Google AI Studio** (`gemini-3.1-pro-preview-customtools`), **Vertex AI** (same Gemini 3.x model, but through the unified `google-genai` SDK with `vertexai=True` + per-tenant `project`/`location` resolved by VERTEX-02's `_resolve_vertex_target`). Each adapter bundles three callables — `build_state`, `call`, `append_tool_round` — that encapsulate the provider's message-history shape. The runner's loop body is provider-agnostic; only the state object differs. Adding OpenAI (and any other function-calling provider) is a third entry in `_PROVIDER_ADAPTERS` plus the three adapter functions — no change to `send_turn`. The `gemini-3.1-pro-preview-customtools` endpoint is specifically optimised for agentic tool-calling workloads and is the default for both `google` and `vertex` providers.

**Pure tool layer.** Every mutation function in `app/copilot/tool_layer.py` takes a `graph_json` dict and returns a new one. No DB access inside the tool functions. The HTTP dispatch path persists atomically; the (future) agent runner chains many calls in-memory and commits once per turn. Same code drives both — no fork.

**NL-first turn pipeline.** The agent runner (01b) enforces a four-phase turn shape via its system prompt: **intent-extract** → **clarify** (can't advance while ambiguities remain — same shape as MCP-07 elicitation) → **pattern-match** (RAG-retrieve 2-3 nearest templates from the system KB) → **draft** → **narrate**. Pattern match before drafting keeps hallucination low: "adapt this template" is a much smaller search space than "synthesise from nothing."

---

## §2. Schema (migration `0022`)

Three tenant-scoped tables. All three carry denormalised `tenant_id` so the RLS policy from migration `0022` is a simple equality check with no joins — same pattern RLS-01 landed across every tenant-scoped endpoint.

### `workflow_drafts`

| Column | Notes |
|---|---|
| `id` | UUID PK |
| `tenant_id` | Indexed; RLS policy keys on this |
| `base_workflow_id` | Nullable FK to `workflow_definitions`. Null = net-new draft |
| `base_version_at_fork` | `WorkflowDefinition.version` at draft-creation time. Promote refuses to land if base has moved on since (race guard — see §4) |
| `title` | Human-readable label; seeded from first NL intent in 01b |
| `graph_json` | JSONB — same shape as `workflow_definitions.graph_json` |
| `version` | Optimistic-concurrency token. Every successful mutation bumps it; stale write → 409 |
| `created_by` | Reserved for user-attribution when we add per-user draft ACLs |
| `created_at` / `updated_at` | Standard timestamps |

### `copilot_sessions`

One chat session per draft (possibly many sequential — a user may abandon a session and reopen the draft later with a different provider). Holds `provider` (google/openai/anthropic) + `model` so the FE doesn't have to remember which model drafted which part.

### `copilot_turns`

Ordered conversation history. `role ∈ {user, assistant, tool}`. `content_json` is role-shaped — text for user/assistant, `{name, args, result}` for tool turns. `tool_calls_json` is populated on assistant turns that emit function-calling requests.

Deliberately NOT in the schema:

- `last_copilot_session_id` back-pointer on drafts — would create a FK cycle with `copilot_sessions.draft_id`. Recover the latest session with `ORDER BY created_at DESC LIMIT 1`.
- `token_budget` / `token_used` columns — deferred to COPILOT-01b where the agent runner actually exists to enforce.

---

## §3. Tool surface

Lives in `backend/app/copilot/tool_layer.py`. Every function is pure: takes a graph dict, returns a new one plus a small result payload. The HTTP dispatch path (§4) is the only stateful caller today; the agent runner lands in 01b.

Twenty-three tools ship today — eight pure tools (01a) + fifteen runner tools (01b.ii.a + AE-handoff + 01b.ii.b + 01b.iii + SMART-04 + SMART-06 + SMART-02 + COPILOT-03.a + COPILOT-03.b + COPILOT-03.c):

| Tool | Kind | Family | Notes |
|---|---|---|---|
| `list_node_types(category?)` | Read | Pure | Trimmed registry — no `config_schema`. Keeps agent context small |
| `get_node_schema(type)` | Read | Pure | Full registry entry for one node type |
| `add_node(node_type, config?, position?, display_name?)` | Mutation | Pure | Returns the new `node_id`. Writes `data.label` from registry (validator uses label, not type) |
| `update_node_config(node_id, partial, display_name?)` | Mutation | Pure | Merge semantics; `null` value clears a key |
| `delete_node(node_id)` | Mutation | Pure | Cascades edges that touch the deleted node |
| `connect_nodes(source, target, source_handle?, target_handle?)` | Mutation | Pure | Refuses self-loops and duplicate edges |
| `disconnect_edge(edge_id)` | Mutation | Pure | — |
| `validate_graph()` | Read | Pure | Wraps the existing `config_validator`; returns `{errors, warnings}` |
| `test_node(node_id, trigger_payload?, pins?)` | Stateful | Runner | Runs ONE handler in isolation using pinned upstream data. No instance / log rows written. Handler exceptions return as `{error, elapsed_ms}` so the LLM can self-correct. `pins` override any graph-stored `pinnedOutput` for that probe. |
| `get_automationedge_handoff_info()` | Read | Runner | For deterministic-automation tasks (SAP postings, form fills, file transfers, etc.). Returns the tenant's registered `automationedge` connections + the AE Copilot deep-link URL so the agent can propose two paths to the user — **inline** (add an `automationedge` node here pointing at an existing AE workflow) vs. **handoff** (open AE Copilot — a separate product — to design the RPA steps first). System prompt enforces the fork; the agent does NOT try to design the inner RPA itself. Per-tenant `copilotUrl` lives on `tenant_integrations(system='automationedge').config_json.copilotUrl`; `ORCHESTRATOR_AE_COPILOT_URL` env is the fallback. |
| `execute_draft(payload?, deterministic_mode?, timeout_seconds?)` | Stateful | Runner | Trial-run the WHOLE draft end-to-end through the real engine. Materialises an ephemeral `workflow_definitions` row (`is_ephemeral=True`, excluded from `list_workflows` / scheduler / A2A agent card) + a real `WorkflowInstance`, then runs `execute_graph` in a background thread with the caller's timeout (default 30s, capped at 300s). Returns `{instance_id, status, elapsed_ms, output, started_at, completed_at}` on completion; `{instance_id, status: "timeout", hint}` if the run exceeded the timeout (still running in background — poll via `get_execution_logs`). Validation errors short-circuit the call. Agent should narrate the draft and get user consent before calling — trial runs make real LLM / MCP / external-API calls. |
| `get_execution_logs(instance_id, node_id?)` | Read | Runner | Per-node logs for a prior `execute_draft` run. Returns `{instance_id, status, log_count, logs: [{node_id, node_type, status, output_json, error, started_at, completed_at}]}`. **Safety:** only ephemeral instances are accessible — arbitrary production `instance_id` values are refused so the LLM can't be used to leak execution history from other workflows. |
| `search_docs(query, top_k?)` | Read | Runner | **Non-vector** word-overlap search over `codewiki/*.md` + a flattened view of `shared/node_registry.json`. Returns `{query, match_count, results: [{source_path, title, anchor, score, excerpt}]}`, capped at `top_k` (default 5, max 20). Index loads from disk on first call and caches in-process; `docs_index.reset_cache()` forces reload. Deliberately **not** hooked into the vector RAG pipeline — the docs are small, change on git commits, and a simple file-backed index avoids ingestion infrastructure. Vector-backed follow-up keeps the same tool surface. |
| `get_node_examples(node_type)` | Read | Runner | Targeted lookup for one registry `type` id. Returns `{node_type, registry_entry, related_sections}` — `registry_entry` is the registry's own chunk (config schema + defaults + enums); `related_sections` is the top 3 codewiki sections most relevant to this node. `registry_entry` is `null` when the type isn't in the registry, signalling the agent to call `list_node_types` and pick a real one. |
| `check_draft()` | Read | Runner | **SMART-04** — supersedes `validate_graph` for agent use. Returns `{errors, warnings, lints, lints_enabled}` where each lint has `{code, severity, message, fix_hint, node_id}`. Four rules today: `no_trigger` (error), `disconnected_node` (warn), `orphan_edge` (error), `missing_credential` (error — checks the LLM-family node's provider against the ADMIN-03 credential resolver). Zero LLM calls. Per-tenant opt-out via `tenant_policies.smart_04_lints_enabled`; when off, the lint step skips and `lints` is `[]` (schema validation still runs). |
| `discover_mcp_tools(server_label?)` | Read | Runner | **SMART-06** — lists the tenant's connected MCP server tools so the agent can propose relevant ones during drafting. Returns `{discovery_enabled, server_label, tools: [{name, title, description, category, safety_tier, tags}]}`. Wraps `engine.mcp_client.list_tools` which already TTL-caches per `(tenant_id, server)` for 5 min — calling more than once a turn is cheap. Per-tenant opt-out via `tenant_policies.smart_06_mcp_discovery_enabled`. Graceful-degrade: if the MCP server is unreachable, returns `tools=[]` + an `error` string so the agent can narrate the failure without crashing the turn. |
| `recall_patterns(query, top_k?)` | Read | Runner | **SMART-02** — accepted-patterns library. Every `/promote` saves the accepted graph + NL intent to `copilot_accepted_patterns`; `recall_patterns` returns the top-k most relevant prior patterns for this tenant scored by word-overlap against `query` (user's intent). Result: `{enabled, query, match_count, patterns: [{id, title, score, nl_intent, tags, node_types, node_count, edge_count, created_at, graph_json}]}`. Agent adapts the closest high-score pattern as few-shot instead of synthesising from scratch — this is how the copilot learns the tenant's conventions. Default `top_k=3`, capped at 10. Opt-out via `tenant_policies.smart_02_pattern_library_enabled` — when off, both the save (at promote time) and the recall skip. |
| `save_test_scenario(name, payload, expected_output_contains?)` | Mutation-ish | Runner | **COPILOT-03.a** — persist a named regression scenario against the current draft. Cap: 50 scenarios per draft, unique name per draft. Returns `{scenario_id, name, created_at}` on success or `{error}` on validation failure / duplicate / cap hit. Scenarios live in `copilot_test_scenarios` (migration 0027), RLS tenant-scoped, and are CASCADE-deleted if the draft is deleted. On promote (03.e) the binding is rewritten to `workflow_id` so scenarios survive the draft lifecycle. |
| `run_scenario(scenario_id)` | Read | Runner | **COPILOT-03.a** — re-runs the saved scenario via `execute_draft_sync` under the hood, diffs the output against `expected_output_contains` (recursive partial match: every key/value in expected must appear in actual; lists match positionally, actual can be longer). Returns `{scenario_id, name, status, mismatches, actual_output, execution}` where `status` is `pass` / `fail` / `stale` / `error`. Mismatches are `[{path, expected, actual, reason?}]`; path uses JSONPath-ish `$.user.id` / `$[0]` form. When no `expected_output_contains` was saved, always returns `pass` with `actual_output` so the agent has something to narrate. |
| `list_scenarios()` | Read | Runner | **COPILOT-03.a** — enumerate saved scenarios on the current draft so the agent can pick one to run without guessing an id. Returns `{count, scenarios: [{scenario_id, name, payload, has_expected, created_at}]}` ordered by creation. |
| `run_debug_scenario(payload?, pins?, node_overrides?, deterministic_mode?, timeout_seconds?)` | Stateful | Runner | **COPILOT-03.b** — ad-hoc trial run with optional overrides that do NOT touch the saved draft. `pins` maps `node_id → synthetic output` (written to that node's `data.pinnedOutput` on a local deep copy); `node_overrides` maps `node_id → partial config dict` (merged into `data.config`). Unknown node ids short-circuit with an error. Delegates to `execute_draft_sync` on a shim draft; returns the same shape plus `{overrides_applied: {pins: [...], node_overrides: [...]}}` so the agent can narrate exactly what was in force. Good for "what if node_3 returned X" or "what if retries were 5 on the email node" probes without persisting a scenario. |
| `get_node_error(instance_id, node_id)` | Read | Runner | **COPILOT-03.b** — narrow on one node in a prior `execute_draft` / `run_debug_scenario` run. Returns `{instance_id, node_id, node_type, status, error, resolved_config, output_json, started_at, completed_at}` — `resolved_config` is `input_json` on the `ExecutionLog` row (post-expression resolution, what the handler actually saw, which is almost always what you want for a config-fix suggestion). If the node succeeded, the response carries a `note` pointing the agent at downstream nodes. Same ephemeral-only safety gate as `get_execution_logs` — arbitrary production instance_ids are refused. |
| `suggest_fix(node_id, error)` | Read-ish | Runner | **COPILOT-03.c** — node-scoped LLM subcall proposing a minimal config patch for a failing node. Hard-rule **never auto-applies** — result has `applied: false` and the agent must surface `{proposed_patch, rationale, confidence}` to the user, wait for approval, then call `update_node_config` with whatever the user agreed to. Patch is filtered against `config_schema.properties` — anything outside that set is dropped with a `dropped_keys` list so the agent can narrate "the model also suggested X but that's not a valid field so I didn't include it". **Two caps (COPILOT-03.d)**: (a) per-draft `MAX_SUGGEST_FIX_PER_DRAFT = 5` enforced inside `suggest_fix` by counting prior `copilot_turns` via the JSONB filter `content_json->>'name' = 'suggest_fix'`; (b) per-turn `MAX_SUGGEST_FIX_PER_TURN = 3` enforced by `AgentRunner._dispatch_runner_tool` via a counter reset at the top of each `send_turn`. When either cap hits, the tool result carries `is_error=True` so the prompt's "hand off to the user" path fires. Uses the tenant's Anthropic credential via `get_anthropic_api_key` (ADMIN-03). |

**Pure vs. runner tool families.** Pure tools live in `app/copilot/tool_layer.py` — graph dict in, graph dict out, no DB access. Runner tools live in `app/copilot/runner_tools.py` — they need a DB session and tenant scope because they call node handlers (which touch credentials, MCP, LLM providers). The agent's `_dispatch_tool` routes to the pure dispatch by default and falls through to `runner_tools.dispatch` when the name is in `RUNNER_TOOL_NAMES`. Runner tools don't mutate the draft graph, so `validation` is always `null` and `draft_version` is unchanged in their `tool_result` events.

Deferred to later COPILOT-01b sub-slices: `execute_draft` + `get_execution_logs` (01b.ii.b — needs an `is_ephemeral` flag on `workflow_definitions` so engine-materialised temp rows don't pollute the UI), `search_docs` + `get_node_examples` RAG grounding (01b.iii), OpenAI provider + per-session token budget (01b.iv remainder).

---

## §4. HTTP surface

Two routers, both use `Depends(get_tenant_db)` per RLS-01 — the tenant GUC is set before the first query.

### `/api/v1/copilot/drafts` — the draft safety boundary

```
POST   /api/v1/copilot/drafts                         create (optionally from base_workflow_id)
GET    /api/v1/copilot/drafts                         list drafts for tenant
GET    /api/v1/copilot/drafts/{id}                    read draft + live validation
PATCH  /api/v1/copilot/drafts/{id}                    manual graph / title update
DELETE /api/v1/copilot/drafts/{id}                    abandon
POST   /api/v1/copilot/drafts/{id}/tools/{tool_name}  dispatch one pure tool (body: {args, expected_version?})
POST   /api/v1/copilot/drafts/{id}/promote            atomically merge into workflow_definitions
```

Tool dispatch: mutation tools persist the new graph and bump `version` in the same transaction; read-only tools never write. Unknown tool name → 400. Stale `expected_version` → 409.

Promote: **net-new** (no base) creates a fresh `WorkflowDefinition` at v1; **new version of existing** (base set) verifies the base still exists (404) and hasn't advanced past `base_version_at_fork` (409), appends a `WorkflowSnapshot` of the current graph, overwrites + bumps version. Draft is deleted on success in both paths. **COPILOT-03.e** — saved test scenarios migrate from `draft_id` → `workflow_id` via a bulk `UPDATE` inside the promote transaction, so regression cases survive the draft's deletion.

**Scenario endpoints (COPILOT-03.e):**
- `GET /api/v1/copilot/drafts/{id}/scenarios` — lightweight list for the PromoteDialog: `[{scenario_id, name, payload, has_expected, created_at}]`.
- `POST /api/v1/copilot/drafts/{id}/scenarios/run_all` — runs every scenario sequentially via the same `runner_tools.run_scenario` code path the agent uses, returns `{count, pass_count, fail_count, stale_count, error_count, results: [{scenario_id, name, status, mismatches, actual_output, message}]}`. Sequential (not parallel) so ephemeral-workflow rows don't step on each other and cost stays predictable.

### `/api/v1/copilot/sessions` — chat sessions + streaming turns

```
GET    /api/v1/copilot/sessions/providers             providers + default model + tool surface
POST   /api/v1/copilot/sessions                       create session bound to a draft
GET    /api/v1/copilot/sessions                       list sessions (optional ?draft_id=)
GET    /api/v1/copilot/sessions/{id}                  read session meta
DELETE /api/v1/copilot/sessions/{id}                  mark session abandoned (preserves turns)
GET    /api/v1/copilot/sessions/{id}/turns            list turns chronologically
POST   /api/v1/copilot/sessions/{id}/turns            send user message; stream agent response (SSE)
```

`POST …/turns` returns `text/event-stream`. One `data: {json}\n\n` frame per agent event. Event types: `assistant_text`, `tool_call`, `tool_result`, `error`, `done`. Turns are flushed as they're produced, then committed once the stream finishes (or the client disconnects — partial progress is preserved).

### Event shapes

| `type` | Fields | When |
|---|---|---|
| `assistant_text` | `text` | After each LLM round-trip that produced prose |
| `tool_call` | `id`, `name`, `args` | Right before dispatch — lets the UI render an in-progress pill |
| `tool_result` | `id`, `name`, `result`, `validation`, `draft_version`, `error` | After dispatch. `validation` is `null` for read-only tools; non-null for mutations. `error` is `null` on success, a string on failure (the LLM sees the error and self-corrects) |
| `error` | `message`, `recoverable` | Catastrophic or iteration-cap failure. `recoverable=true` means the user can retry their turn |
| `done` | `turns_added`, `final_text` | Always the last event. `turns_added` lists the persisted turn ids |

---

## §5. Races we defend against

**Race A: two concurrent tool calls on the same draft.** Common once the agent runner lands (LLM function-calling fires several tools per turn, often concurrently). Guard: every mutation sends `expected_version`; stale writes return 409 with the current version in the detail so the caller refetches. Versions bump monotonically.

**Race B: colleague edits the base while a draft is open.** The failure mode this guards against is: user opens a draft against base v5, colleague saves the base → v6 in another tab, user hits Promote — without the guard we'd silently overwrite v6. Guard: `base_version_at_fork` column, checked on `/promote`, 409 on mismatch with a "base advanced from v5 to v7" message.

Neither path attempts a three-way merge. That's a COPILOT-03+ concern — for now the resolution is to discard the draft (or re-fork against the new base) and redo.

---

## §6. Frontend surface (types + API client; UI lands in COPILOT-02)

`frontend/src/lib/api.ts` exports the typed bindings:

**Drafts (01a):**
- `CopilotDraftOut`, `CopilotDraftValidation`, `CopilotToolName`, `CopilotToolCallOut`, `CopilotPromoteOut`
- `api.listDrafts`, `createDraft`, `getDraft`, `updateDraft`, `deleteDraft`, `callCopilotTool`, `promoteDraft`

**Sessions + turn streaming (01b.i):**
- `CopilotSessionOut`, `CopilotTurnOut`, `CopilotProvidersOut`
- `CopilotAgentEvent` — discriminated union matching the SSE event shapes in §4
- `api.getCopilotProviders`, `listCopilotSessions`, `createCopilotSession`, `getCopilotSession`, `abandonCopilotSession`, `listCopilotTurns`
- `api.sendCopilotTurn(sessionId, text, signal?)` — async generator yielding `CopilotAgentEvent` items. Uses a streaming `fetch` (EventSource can't POST a body), parses `data: ...\n\n` frames by hand, emits a recoverable `error` event on malformed JSON rather than killing the stream.

**Chat pane (COPILOT-02.i + 02.ii):**
- `components/copilot/CopilotPanel.tsx` — right-side drawer toggled from the toolbar Sparkles icon. **Mutually exclusive with PropertyInspector** (they share the right column; a chat pane squeezed next to a 288-px inspector would leave no room for the canvas) — opening the copilot hides the inspector and vice versa. Default width 460 px so prose bubbles + tool-result cards stay readable; this is explicit user feedback ("panels should be large enough and visible") rather than an arbitrary number.
- `components/copilot/CopilotMessageList.tsx` — scrollable chat with auto-stick-to-bottom (disabled when the user scrolls up; a "Jump to latest" pill reappears then) and a dotted thinking indicator while streaming.
- `components/copilot/CopilotComposer.tsx` — auto-growing textarea (1–12 rows), Cmd/Ctrl+Enter to send, disabled while a turn streams. The **Stop** button in the panel header aborts the in-flight turn (02.ii).
- `components/copilot/CopilotToolResultCard.tsx` — discriminated dispatch over `CopilotAgentEvent`. Assistant text → prose bubble; `tool_call` → compact "🔧 name — summary" pill that expands to JSON; `tool_result` → success / error card with per-tool summary strings (`add_node` → "added node_N", `validate_graph` → "N errors · M warnings", `execute_draft` → "completed (412 ms)" or "timeout", etc.) + a collapsible detail drawer showing full result JSON, validation list, and `draft vN`.
- `components/copilot/PromoteDialog.tsx` (02.ii + 03.e) — confirmation modal for Apply. Shows node/edge counts with +/- deltas against the base workflow, surfaces the draft's validation (errors + lints block; warnings + lint-warnings pass through), collects an optional description (plus a required name for net-new drafts), and wires the Apply button to `api.promoteDraft` with `expected_version` for the optimistic-concurrency race guard. **COPILOT-03.e** — on open we fetch `listDraftScenarios`; the dialog renders saved scenarios inline with a "Run all" button that calls `runAllDraftScenarios` and paints per-scenario `pass` / `fail` / `stale` / `error` badges. Failing scenarios gate Apply behind a "Promote anyway — I've reviewed the N failing scenario(s) and still want to promote" checkbox. Lists without results stay informational — we show the names + "not run" badges without auto-running, because each scenario ticks the engine. `max-w-2xl max-h-[85vh] flex flex-col` so the diff summary + name + validation list + scenarios section all fit a 1366×768 viewport without cramping.

**Session lifecycle + resume in the panel (02.ii).** Each open of the panel **resumes** a draft + session when one exists, so closing and reopening the panel preserves the conversation:
1. `api.listDrafts()` → pick the most-recent draft whose `base_workflow_id` matches the current workflow (or the most-recent net-new draft if the canvas is empty). Fall back to `createDraft`.
2. `api.listCopilotSessions(draft.id)` → reuse the first `status === "active"` session; otherwise `createCopilotSession`.
3. On a reused session, `api.listCopilotTurns(session.id)` returns all prior turns, which `turnsToChatItems` fans out into the same `ChatItem` shape the live stream produces (user text → user bubble, assistant turn → `assistant_text` event + one `tool_call` event per entry in `tool_calls_json`, tool turn → `tool_result` event with `validation: null` / `draft_version: 0` sentinels because those aren't persisted per-turn).
4. Closing the panel aborts any in-flight `sendCopilotTurn` via `AbortController` and resets local state (the draft + session rows stay on the backend for the next open).
5. **Stop** button in the header becomes visible while a turn streams and calls the same `AbortController.abort()` path without closing the panel.
6. **Apply** button in the header becomes enabled once `draft.version > 0` (at least one mutation since fork) and opens `PromoteDialog`. On successful promote we `fetchWorkflows()`, `loadWorkflow(result.workflow_id)`, then close the panel — the user lands on their freshly-promoted workflow.

---

## §7. System prompt + NL-first turn pipeline

`app/copilot/prompts.py` holds the system prompt and the context-assembly helper. The prompt enforces a four-phase turn shape that the agent runner delegates to:

1. **Intent extract.** Read the user's message without drafting. What trigger? Primary operation? Downstream effects? What's *not* specified?
2. **Clarification loop.** Ask ONE question at a time while anything required for drafting is ambiguous. Don't draft against guesses.
3. **Pattern match, then draft.** Prefer adapting a known pattern (classifier + router, RAG-over-KB, ReAct-with-MCP) to synthesis from scratch. Call tools to build (add_node → connect_nodes → update_node_config).
4. **Narrate.** After mutations, call `validate_graph`, then tell the user what was built in plain language.

The prompt also enforces a **source-of-truth rule**: for schema-shaped questions (node types, config fields) the agent MUST prefer the live `list_node_types` / `get_node_schema` tools over its training-data recall — training data is stale; the registry is source-of-truth by construction.

**Auto-heal loop (COPILOT-03.d).** The prompt has a dedicated section telling the agent how to recover from a failed `execute_draft`: call `get_execution_logs` to find the failing node, then `get_node_error` → `suggest_fix`, surface the proposal to the user, wait for approval, call `update_node_config` with the approved subset, and re-run. The runner enforces two ceilings on `suggest_fix` so a flapping loop can't burn the budget — a per-turn cap of 3 (reset each `send_turn`) and a per-draft cap of 5 (counted across sessions). When either hits, the tool result comes back with `is_error=True` + a message telling the agent to hand off to the user, and the agent's prompt has the corresponding "I tried X, Y, Z — can you take it from here?" fallback shape.

`build_system_prompt(draft_snapshot=…)` appends a compact snapshot of the current graph (node ids + labels + config keys, edge src/tgt) so the agent can see what's on the canvas without paying for a `get_draft` tool call every turn. The snapshot is intentionally compact — full config is available via a tool call when needed.

---

## §8. What the remaining COPILOT-01b slices add

- **01b.ii.a — `test_node` runner tool (shipped).** See §3 table. Reuses `dispatch_node` against the draft's graph directly — no ephemeral `WorkflowDefinition` required because it's single-node.
- **01b.ii.b — `execute_draft` + `get_execution_logs` (shipped).** See §3 table. Adds migration `0023` (`is_ephemeral BOOLEAN NOT NULL DEFAULT FALSE` on `workflow_definitions`), filter sweep across `list_workflows` / `scheduler.check_scheduled_workflows` / `a2a.agent_card`, and the `cleanup_ephemeral_workflows(db, older_than_seconds=7*86400)` operator utility. Ephemeral rows stay around after a run so `get_execution_logs` works; a Beat wire-up for automated cleanup is a follow-up.
- **01b.iii — Docs grounding (shipped).** See §3 for `search_docs` and `get_node_examples`. Shipped as a *file-backed* word-overlap search rather than a vector RAG pipeline because the docs are small, change on git commits, and a simple in-process index avoids ingestion infrastructure (no migration, no embedding provider config, no cross-tenant RLS carveout). Vector-backed retrieval is a clean follow-up — the tool surface is identical, only `app/copilot/docs_index.py`'s internals change.
- **01b.iv remainder — OpenAI + token budget.** OpenAI's function-calling shape behind the same adapter-dispatched `AgentRunner` interface — a third entry in `_PROVIDER_ADAPTERS` plus three adapter functions, no change to `send_turn`. Per-session `token_used` + `token_budget` columns with a middleware that suspends the session and prompts the user when the budget is hit. (Google AI Studio + Vertex AI landed early — see §3.)

COPILOT-02 is the chat pane, diff overlay, and `PromoteDialog`. **Status:** 02.i (chat pane + streaming) and 02.ii (stop button + history replay + `PromoteDialog`) have shipped; 02.ii.b (canvas `DraftDiffOverlay` — ghost nodes + edges overlaid on the React-Flow graph) is deferred because it needs the canvas to accept a preview-graph override, which is a bigger refactor than the other 02.ii bits. **COPILOT-03 status:** 03.a (scenario storage) + 03.b (run_debug_scenario / get_node_error) + 03.c (suggest_fix) + 03.d (auto-heal prompt + per-turn cap) + 03.e (PromoteDialog scenario badges + run_all endpoint + scenario migration on promote) have all shipped. The auto-heal loop + regression-gate plumbing is end-to-end usable; SMART-01 (Scenario memory + strict promote-gate mode) can now build on top without new primitive tools. See [feature-roadmap.md](feature-roadmap.md#workflow-authoring-copilot--copilot-01--copilot-03) for the ordered breakdown.

---

## §8. Testing

- **`backend/tests/test_copilot_tool_layer.py`** — 28 unit tests on the pure tool functions. Every tool gets a happy path + at least one error path. Exercises the real `node_registry.json` so registry shape changes surface here.
- **`backend/tests/test_copilot_drafts_api.py`** — 24 integration tests against a mocked session: CRUD, version conflicts, tool dispatch (read-only vs. mutation), promote-new, promote-new-version, race guard (409 on base diverged), 404 on base deleted, 400 on validation failure, 409 on name collision. **COPILOT-03.e**: list + run_all scenario endpoints (ordered list with `has_expected`, empty-draft happy path, run_all aggregates pass/fail/stale/error with per-scenario mismatches preserved, 404 on missing draft).
- **`backend/tests/test_copilot_agent.py`** — 15 agent-runner tests with the Anthropic SDK mocked out. Covers text-only turns, mutation-tool dispatch + version bump, read-only tool dispatch (no version bump), bad-args surface to LLM (no 500), unknown tool name, iteration cap (`MAX_TOOL_ITERATIONS`), unsupported provider, runner-tool dispatch + runner-tool error surface (01b.ii.a), and three COPILOT-03.d tests: the per-turn `suggest_fix` cap short-circuits the 4th call without hitting `runner_tools.dispatch`, the counter resets on the next `send_turn`, and a constant-pin test to stop the cap being silently bumped.
- **`backend/tests/test_copilot_agent_google.py`** — 9 Google/Vertex tests with `_call_google` mocked. Pins the `gemini-3.x-*-customtools` default, asserts the Vertex provider routes through the same adapter (backend=`vertex`), verifies the Google state builder reconstructs `types.Content(role, parts)` history from persisted turns, and checks the iteration cap under Google's response shape (no per-call tool_use ids — the runner synthesises them from function name).
- **`backend/tests/test_copilot_sessions_api.py`** — 12 integration tests for the session API: providers endpoint, session CRUD, turn streaming SSE shape, abandoned-session 409, missing-draft 404, empty-text 422.
- **`backend/tests/test_copilot_runner_tools.py`** — 12 runner-tool tests with `dispatch_node` mocked: happy-path return shape, graph-pin fallback, caller-pin override precedence, trigger-payload pass-through, missing/unknown node error surface, non-dict `pins` validation, handler exceptions returned as `{error}` not raised, `NodeSuspendedAsync` surfaced with a human-readable explanation, dispatch routing, KeyError on unknown runner-tool name. Plus 2 new integration tests in `test_copilot_agent.py` that exercise the end-to-end runner-tool dispatch path through the agent loop.
- **`backend/tests/test_copilot_test_scenarios.py`** (03.a) — 24 tests for the scenario CRUD + replay tools: the `_diff_contains` helper (empty-when-subsumes, missing-key path, scalar mismatch, list-shorter, list-longer-actual-ok, type mismatch), `save_test_scenario` (happy path, empty-name / non-dict payload / non-dict expected rejects, duplicate-name guard, 50-scenario cap), `run_scenario` (missing/invalid/not-found id, pass when expected matches, fail with mismatches, engine-failure status mismatch, no-expected → pass with actual, pre-run validation error surfaced as status=error), `list_scenarios` happy path, and three dispatch-routing tests confirming the new tools reach their handlers.
- **`backend/tests/test_copilot_debug_tools.py`** (03.b) — 16 tests for the debug + node-error tools: `run_debug_scenario` payload forwarding, pins merged without mutating the original draft (deep-copy isolation asserted), `node_overrides` merged into `data.config` while preserving unrelated keys, unknown-node-id short-circuit on both override maps, non-dict `pins` / `node_overrides` / override-value rejects, `deterministic_mode` + `timeout_seconds` passthrough; `get_node_error` input validation (missing / invalid / not-found / non-ephemeral / log-not-found), failure-details happy path with `resolved_config` surface, and the successful-node `note` hint. Plus two dispatch-routing tests.
- **`backend/tests/test_copilot_suggest_fix.py`** (03.c) — 14 tests covering the LLM subcall pipeline: `_parse_suggest_fix_response` (fenced JSON stripped, plain JSON accepted, garbage / empty / non-dict returns None); input validation (missing `node_id` / `error` / non-string error, node absent from graph, unknown `node_type` surfaces cleanly); happy-path proposal (valid patch returned, `applied: false` always); patch filtering (keys outside `config_schema.properties` moved to `dropped_keys`); unknown `confidence` values coerced to `"medium"`; empty patch allowed when the LLM honestly has no idea; cap enforcement (LLM never called when `prior_calls >= MAX_SUGGEST_FIX_PER_DRAFT`); LLM exception → clean error surface; JSON parse failure → clean error surface. Plus a dispatch-routing test confirming `suggest_fix` reaches the handler.
- **`frontend/src/components/copilot/CopilotToolResultCard.test.tsx`** — 15 renderer unit tests for `CopilotEventCard`: every `tool_result` name gets its own summary-string assertion plus an expand-toggle check; plus `assistant_text`, `done` (null DOM), recoverable `error` (amber), fatal `error` (red).
- **`frontend/src/components/copilot/PromoteDialog.test.tsx`** (02.ii + 03.e) — 9 dialog unit tests: validation errors block promote, warnings pass through, net-new drafts require a non-empty name, +/- deltas render against base workflow counts, confirm calls `api.promoteDraft` with the `expected_version` body shape and forwards the result. **COPILOT-03.e**: scenarios section hidden when empty, list renders saved scenarios + Run all button, failing-scenario gate blocks Apply until "promote anyway" is checked, all-pass results leave Apply enabled and hide the confirm checkbox.
- **`frontend/src/components/copilot/CopilotPanel.history.test.tsx`** (02.ii) — 6 replay-helper tests for `turnsToChatItems`: user turn → bubble, assistant text turn → `assistant_text` event, assistant + tool_calls → text event + one `tool_call` event per call, tool turn → `tool_result` with `validation: null` + `draft_version: 0` sentinels, tool errors carried through, chronological order across mixed turns.
- Full suite: **710 passed, 21 skipped** backend + **138 passed** frontend on this branch (up from 469 + 128 pre-COPILOT).
