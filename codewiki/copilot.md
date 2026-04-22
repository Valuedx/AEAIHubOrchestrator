# Workflow Authoring Copilot

A conversational assistant that drafts, modifies, validates, and (eventually) debugs workflows through tool-calling — Claude-Code-shape applied to this orchestrator's DAG builder. The end-user describes intent in natural language; the copilot asks for what it needs, drafts a graph, runs validation, narrates what it built, and lets the human accept or reject before anything lands in the live workflow catalogue.

Scoped across three tickets: [COPILOT-01](feature-roadmap.md#copilot-01--draft-workspace-model--agent-tool-surface) (backend foundation), [COPILOT-02](feature-roadmap.md#copilot-02--chat-pane--diff-apply-ui) (chat UI + diff apply), [COPILOT-03](feature-roadmap.md#copilot-03--debug--test-scenario--auto-heal-loop) (debug / test-scenario / auto-heal). This page documents what's shipped today — **COPILOT-01a + COPILOT-01b.i**: the draft-workspace safety boundary, pure tool layer, **agent runner with Anthropic tool-calling**, and session/turn SSE streaming. Still outstanding (01b.ii + 01b.iii + 01b.iv): `test_node` / `execute_draft` runner tools, system-KB RAG ingestion with `search_docs` / `get_node_examples`, and multi-provider (OpenAI / Google) support.

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

**Agent runner (01b.i).** `app/copilot/agent.py` holds the per-turn loop: load prior history from `copilot_turns` → build Anthropic messages with the system prompt + tool definitions → call `client.messages.create` → for each `tool_use` block, dispatch through `tool_layer.dispatch` → append results → loop until the assistant produces text with no more tool calls. Capped at `MAX_TOOL_ITERATIONS = 12` so a pathological "flap" can't burn unbounded cost. Every turn (user + assistant + tool) is persisted to `copilot_turns` as it happens; a disconnected client never loses progress.

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

Eight tools:

| Tool | Kind | Notes |
|---|---|---|
| `list_node_types(category?)` | Read | Trimmed registry — no `config_schema`. Keeps agent context small |
| `get_node_schema(type)` | Read | Full registry entry for one node type |
| `add_node(node_type, config?, position?, display_name?)` | Mutation | Returns the new `node_id`. Writes `data.label` from registry (validator uses label, not type) |
| `update_node_config(node_id, partial, display_name?)` | Mutation | Merge semantics; `null` value clears a key |
| `delete_node(node_id)` | Mutation | Cascades edges that touch the deleted node |
| `connect_nodes(source, target, source_handle?, target_handle?)` | Mutation | Refuses self-loops and duplicate edges |
| `disconnect_edge(edge_id)` | Mutation | — |
| `validate_graph()` | Read | Wraps the existing `config_validator`; returns `{errors, warnings}` |

Deferred to later COPILOT-01b sub-slices: `test_node` + `execute_draft` + `get_execution_logs` (01b.ii), `search_docs` + `get_node_examples` RAG grounding (01b.iii), OpenAI + Google providers + per-session token budget (01b.iv). The agent runner surface is stable — adding these tools means new entries in `tool_definitions.py` + new handlers in `agent._dispatch_tool` (or a split `runner_tools.py` module for the stateful ones), no frontend churn.

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

Promote: **net-new** (no base) creates a fresh `WorkflowDefinition` at v1; **new version of existing** (base set) verifies the base still exists (404) and hasn't advanced past `base_version_at_fork` (409), appends a `WorkflowSnapshot` of the current graph, overwrites + bumps version. Draft is deleted on success in both paths.

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

No UI yet — the chat pane, diff overlay, and `PromoteDialog` land in COPILOT-02. The typed bindings + async generator are here so 02 is a pure frontend change against a stable contract.

---

## §7. System prompt + NL-first turn pipeline

`app/copilot/prompts.py` holds the system prompt and the context-assembly helper. The prompt enforces a four-phase turn shape that the agent runner delegates to:

1. **Intent extract.** Read the user's message without drafting. What trigger? Primary operation? Downstream effects? What's *not* specified?
2. **Clarification loop.** Ask ONE question at a time while anything required for drafting is ambiguous. Don't draft against guesses.
3. **Pattern match, then draft.** Prefer adapting a known pattern (classifier + router, RAG-over-KB, ReAct-with-MCP) to synthesis from scratch. Call tools to build (add_node → connect_nodes → update_node_config).
4. **Narrate.** After mutations, call `validate_graph`, then tell the user what was built in plain language.

The prompt also enforces a **source-of-truth rule**: for schema-shaped questions (node types, config fields) the agent MUST prefer the live `list_node_types` / `get_node_schema` tools over its training-data recall — training data is stale; the registry is source-of-truth by construction.

`build_system_prompt(draft_snapshot=…)` appends a compact snapshot of the current graph (node ids + labels + config keys, edge src/tgt) so the agent can see what's on the canvas without paying for a `get_draft` tool call every turn. The snapshot is intentionally compact — full config is available via a tool call when needed.

---

## §8. What the remaining COPILOT-01b slices add

- **01b.ii — runner tools.** `test_node` (reuse DV-02 single-node test against the draft graph) and `execute_draft` (materialise a throwaway `WorkflowDefinition`, run the engine sync, return `SyncExecuteOut`). These need DB + tenant scope so they live in `app/copilot/runner_tools.py`, separate from the pure `tool_layer.py`. Also `get_execution_logs` for post-run debugging by the agent itself.
- **01b.iii — RAG grounding.** Ingest `codewiki/*.md`, the flattened `node_registry.json`, and template descriptions into a dedicated `kb_documents` row under a reserved tenant-id sentinel (cross-tenant-readable, admin-write-only). Expose `search_docs(query, top_k=5)` and `get_node_examples(node_type)` tools. Re-index as a CLI command that runs at deploy time.
- **01b.iv — multi-provider + token budget.** OpenAI function-calling shape + Google `Tool(function_declarations=…)` shape behind the same `AgentRunner` interface — nothing else changes. Per-session `token_used` + `token_budget` columns with a middleware that suspends the session and prompts the user when the budget is hit.

COPILOT-02 is the chat pane, diff overlay, and `PromoteDialog`. COPILOT-03 is the debug / test-scenario / auto-heal loop. See [feature-roadmap.md](feature-roadmap.md#workflow-authoring-copilot--copilot-01--copilot-03) for the ordered breakdown.

---

## §8. Testing

- **`backend/tests/test_copilot_tool_layer.py`** — 28 unit tests on the pure tool functions. Every tool gets a happy path + at least one error path. Exercises the real `node_registry.json` so registry shape changes surface here.
- **`backend/tests/test_copilot_drafts_api.py`** — 19 integration tests against a mocked session: CRUD, version conflicts, tool dispatch (read-only vs. mutation), promote-new, promote-new-version, race guard (409 on base diverged), 404 on base deleted, 400 on validation failure, 409 on name collision.
- **`backend/tests/test_copilot_agent.py`** — 10 agent-runner tests with the Anthropic SDK mocked out. Covers text-only turns, mutation-tool dispatch + version bump, read-only tool dispatch (no version bump), bad-args surface to LLM (no 500), unknown tool name, iteration cap (`MAX_TOOL_ITERATIONS`), unsupported provider.
- **`backend/tests/test_copilot_sessions_api.py`** — 12 integration tests for the session API: providers endpoint, session CRUD, turn streaming SSE shape, abandoned-session 409, missing-draft 404, empty-text 422.
- Full suite: **538 passed, 21 skipped** on this branch (up from 469 pre-COPILOT-01).
