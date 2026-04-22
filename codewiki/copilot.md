# Workflow Authoring Copilot

A conversational assistant that drafts, modifies, validates, and (eventually) debugs workflows through tool-calling — Claude-Code-shape applied to this orchestrator's DAG builder. The end-user describes intent in natural language; the copilot asks for what it needs, drafts a graph, runs validation, narrates what it built, and lets the human accept or reject before anything lands in the live workflow catalogue.

Scoped across three tickets: [COPILOT-01](feature-roadmap.md#copilot-01--draft-workspace-model--agent-tool-surface) (backend foundation), [COPILOT-02](feature-roadmap.md#copilot-02--chat-pane--diff-apply-ui) (chat UI + diff apply), [COPILOT-03](feature-roadmap.md#copilot-03--debug--test-scenario--auto-heal-loop) (debug / test-scenario / auto-heal). This page documents what's shipped today — **COPILOT-01a: the draft-workspace safety boundary + pure tool layer**. The agent runner (the thing that actually drives an LLM through the tool surface) and the system-KB RAG ingestion land in COPILOT-01b.

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

Deferred to COPILOT-01b: `test_node`, `execute_draft`, `get_execution_logs`, `search_docs`, `get_node_examples`. The endpoint surface for these is not yet exposed — adding them there means no additional frontend churn for this slice.

---

## §4. HTTP surface

Router prefix `/api/v1/copilot/drafts`, registered in `backend/main.py`. All endpoints use `Depends(get_tenant_db)` per RLS-01 — the tenant GUC is set before the first query.

### CRUD

```
POST   /api/v1/copilot/drafts                         create (optionally from base_workflow_id)
GET    /api/v1/copilot/drafts                         list drafts for tenant
GET    /api/v1/copilot/drafts/{id}                    read draft + live validation
PATCH  /api/v1/copilot/drafts/{id}                    manual graph / title update
DELETE /api/v1/copilot/drafts/{id}                    abandon
```

### Tool dispatch

```
POST   /api/v1/copilot/drafts/{id}/tools/{tool_name}
Body: {"args": {...}, "expected_version": <int|null>}
```

Dispatches one pure tool-layer call against the draft. Mutation tools persist the new graph and bump `version` in the same transaction; read-only tools never write. Unknown tool name → 400. Stale `expected_version` → 409.

### Promote

```
POST   /api/v1/copilot/drafts/{id}/promote
Body: {"name": <str|null>, "description": <str|null>, "expected_version": <int|null>}
```

Two paths:

- **Net-new** (`base_workflow_id is null`) — require `name`; check for tenant-scoped name collision (409); insert a fresh `WorkflowDefinition` at version 1.
- **New version of existing** (`base_workflow_id` set) — verify the base still exists (404 if deleted); verify `base.version == draft.base_version_at_fork` (409 if diverged — see §5); append a `WorkflowSnapshot` of the current graph; overwrite + bump version.

Draft is deleted in the same transaction in both paths.

---

## §5. Races we defend against

**Race A: two concurrent tool calls on the same draft.** Common once the agent runner lands (LLM function-calling fires several tools per turn, often concurrently). Guard: every mutation sends `expected_version`; stale writes return 409 with the current version in the detail so the caller refetches. Versions bump monotonically.

**Race B: colleague edits the base while a draft is open.** The failure mode this guards against is: user opens a draft against base v5, colleague saves the base → v6 in another tab, user hits Promote — without the guard we'd silently overwrite v6. Guard: `base_version_at_fork` column, checked on `/promote`, 409 on mismatch with a "base advanced from v5 to v7" message.

Neither path attempts a three-way merge. That's a COPILOT-03+ concern — for now the resolution is to discard the draft (or re-fork against the new base) and redo.

---

## §6. Frontend surface (types only in 01a)

`frontend/src/lib/api.ts` exports the typed bindings:

- `CopilotDraftOut`, `CopilotDraftValidation`
- `CopilotToolName` (a union of the eight tool names)
- `CopilotToolCallOut`, `CopilotPromoteOut`
- `api.listDrafts`, `api.createDraft`, `api.getDraft`, `api.updateDraft`, `api.deleteDraft`
- `api.callCopilotTool(draftId, toolName, args, expectedVersion?)`
- `api.promoteDraft(draftId, body)`

No UI yet — the chat pane, diff overlay, and `PromoteDialog` land in COPILOT-02. The typed bindings are here so 02 is a pure frontend change against a stable contract.

---

## §7. What COPILOT-01b adds

- The agent runner (`app/copilot/agent.py`) — holds the chat loop, exposes the tool layer to Anthropic/OpenAI/Google function-calling shapes, persists turns into `copilot_turns`, streams tool calls back over SSE.
- `test_node` and `execute_draft` tools — reuse DV-02 single-node test + materialise a throwaway `WorkflowDefinition` to run the engine against the draft.
- System KB ingestion — codewiki + flattened `node_registry.json` + template descriptions ingested into a dedicated `kb_documents` row with a reserved tenant-id sentinel. Exposes `search_docs` and `get_node_examples` tools. Source-of-truth rule: schema-shaped questions MUST prefer the live API over RAG chunks.
- `CopilotSession` / `CopilotTurn` write path (in 01a the tables exist but are not yet populated).

COPILOT-02 is the chat pane, diff overlay, and `PromoteDialog`. COPILOT-03 is the debug / test-scenario / auto-heal loop. See [feature-roadmap.md](feature-roadmap.md#workflow-authoring-copilot--copilot-01--copilot-03) for the ordered breakdown.

---

## §8. Testing

- **`backend/tests/test_copilot_tool_layer.py`** — 28 unit tests on the pure tool functions. Every tool gets a happy path + at least one error path. Exercises the real `node_registry.json` so registry shape changes surface here.
- **`backend/tests/test_copilot_drafts_api.py`** — 19 integration tests against a mocked session: CRUD, version conflicts, tool dispatch (read-only vs. mutation), promote-new, promote-new-version, race guard (409 on base diverged), 404 on base deleted, 400 on validation failure, 409 on name collision.
- Full suite: **516 passed, 21 skipped** on this branch (up from 469 pre-COPILOT-01).
