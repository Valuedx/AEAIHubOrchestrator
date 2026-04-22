> - **COPILOT-01b.iii — docs grounding (2026-04-22)**: Agent can now read the orchestrator's own documentation — `codewiki/*.md` + a flattened view of the node registry — via two new runner tools `search_docs(query, top_k?)` and `get_node_examples(node_type)`. Implementation is a file-backed word-overlap index (`app/copilot/docs_index.py`) rather than a full RAG pipeline — the docs are small, change via git commits, and the simpler path avoids per-tenant ingestion / embedding config / RLS carveouts. Vector-backed retrieval is a clean follow-up with the same tool surface. System prompt guides the agent to consult docs for concept questions before drafting.
>
> - **COPILOT-01b.ii.b — `execute_draft` + `get_execution_logs` (2026-04-22)**: Closes the copilot's construct → test → debug → fix loop. Two new runner tools: `execute_draft` materialises an ephemeral `WorkflowDefinition` (migration `0023` adds the `is_ephemeral` flag) + a real `WorkflowInstance` for the draft and runs it through the existing engine with a threadpool + timeout (default 30s, capped at 300s); `get_execution_logs` reads per-node logs back, scoped to copilot-initiated runs so the LLM can't read production execution history. Ephemeral rows are filtered out of `list_workflows`, the scheduler, and the A2A agent card. A `cleanup_ephemeral_workflows` utility reaps old rows (operator-scheduled for now; Beat wire-up is a follow-up).
>
> - **COPILOT — AutomationEdge handoff fork (2026-04-22)**: The workflow authoring copilot now recognises deterministic RPA intent (SAP/ERP posting, form fill, file transfer, data entry) and offers the user a fork rather than trying to chain LLM nodes for it. Two paths: **inline** (add an `automationedge` node pointing at an existing AE workflow) or **handoff** (open the AutomationEdge Copilot — separate product — to design the RPA first, then come back). New `get_automationedge_handoff_info` runner tool surfaces the tenant's AE connections + deep-link URL. System prompt enforces the fork so the agent never tries to synthesise RPA steps inside this orchestrator.
>
> - **COPILOT-01b.ii.a — `test_node` runner tool (2026-04-22)**: Adds the copilot's first stateful tool — "run one node in isolation with pinned upstream data". Separate module `app/copilot/runner_tools.py` from the pure `tool_layer.py` because runner tools need DB + tenant scope (they call real node handlers). The agent's `_dispatch_tool` tries pure first, then runner. Tool description nudges the agent to use `test_node` for debug-a-node-config turns; it does NOT mutate the draft graph, so `validation` stays `null` in the tool_result event. Deferred: `execute_draft` + `get_execution_logs` to 01b.ii.b (needs an `is_ephemeral` flag migration first).
>
> - **COPILOT-01b.iv — Google AI Studio + Vertex AI providers (2026-04-22)**: The copilot agent supports three providers today, all dispatched through the same `send_turn` loop via a `_PROVIDER_ADAPTERS` registry — Anthropic (`claude-sonnet-4-6`), Google AI Studio (`gemini-3.1-pro-preview-customtools`), Vertex AI (same Gemini 3.x model, `genai.Client(vertexai=True, project, location)` with per-tenant project from VERTEX-02). Each adapter bundles `build_state` / `call` / `append_tool_round` callables that encapsulate the provider's message-history shape; the runner itself stays provider-agnostic. Google doesn't emit per-call tool_use ids — the runner synthesises stable `gfn_<name>_<idx>` ids so tool-turn rows have a UI-renderable link. Adding OpenAI is a third registry entry plus three adapter functions — no change to `send_turn`.
>
> - **COPILOT-01b.i Agent runner + session streaming (2026-04-22)**: The LLM-driven half of the copilot. `app/copilot/agent.py` drives Anthropic tool-calling through the pure tool layer from 01a — load prior turns from `copilot_turns`, build messages with a system prompt that enforces the NL-first turn pipeline (intent-extract → clarify → pattern-match → draft → narrate), call `client.messages.create`, dispatch each `tool_use` block, feed results back, loop until the assistant produces final text with no more tool calls. Capped at 12 iterations per turn. Per-tenant API key via ADMIN-03. New endpoint `/api/v1/copilot/sessions/{id}/turns` returns `text/event-stream` with `assistant_text` / `tool_call` / `tool_result` / `error` / `done` events. Still deferred: `test_node` / `execute_draft` (01b.ii), RAG docs grounding (01b.iii), multi-provider + token budget (01b.iv), UI (COPILOT-02).
>
> - **COPILOT-01a Draft-workspace foundation (2026-04-22)**: Backend-only foundation for the natural-language workflow authoring copilot ([codewiki/copilot.md](codewiki/copilot.md)). Ships a draft-workspace safety boundary — every copilot mutation lands in `workflow_drafts` and nothing reaches `workflow_definitions` until the human hits `/promote`. Pure tool layer (`list_node_types`, `get_node_schema`, `add_node`, `update_node_config`, `delete_node`, `connect_nodes`, `disconnect_edge`, `validate_graph`) takes a graph dict and returns a new one; persistence and version-bump happen in the HTTP dispatch path. Two race guards: optimistic-concurrency `version` token (stale tool call → 409), and `base_version_at_fork` (promote refuses if the base workflow advanced since the draft was forked). Agent runner, `test_node`, `execute_draft`, RAG grounding are all COPILOT-01b; chat UI is COPILOT-02.
>
> - **RLS-01 Systemic `get_tenant_db` cutover (2026-04-21)**: Every tenant-scoped API handler now uses `Depends(get_tenant_db)` so the `app.tenant_id` Postgres GUC is set on the session before any query. Previously, most handlers used `get_db` (which doesn't set the GUC) — this was silently fine under a superuser role (superusers bypass RLS) but broke immediately when a tenant followed STARTUP-01's `rls_posture` warn to switch to a non-superuser role. Path-based A2A endpoints (`/tenants/{tenant_id}/...`) keep using `get_db` because `get_tenant_db` reads the `X-Tenant-Id` header, not the URL path — they now call `set_tenant_context(db, tenant_id)` inline instead. Regression test `tests/test_rls_dependency_wired.py` spies on each tenant-scoped endpoint and asserts the GUC call happened.
>
> - **ADMIN-03 Per-tenant LLM provider credentials (2026-04-21)**: Tenants now carry their own Google AI Studio / OpenAI / Anthropic API keys (and OpenAI base URL) instead of sharing the orchestrator's process-global env keys. Storage reuses the existing Fernet-encrypted `tenant_secrets` vault under four well-known names. New resolver in `engine/llm_credentials_resolver.py`; seven chat/stream/ReAct call sites wired. Dialog behind the toolbar Key icon with password-masked inputs and per-field source badges. Read-only `/api/v1/llm-credentials` endpoint surfaces status (never values) for the UI.
>
> - **STARTUP-01 Preflight readiness checks (2026-04-21)**: Seven boot-time checks (DB, Redis, Celery workers, RLS posture, auth-mode, vault key, MCP default probe) log pass/warn/fail with per-check remediation. New `/health/ready` endpoint runs them live — **503** on fail for k8s readiness cycling; **200** otherwise. UI banner surfaces problems inline. `/health` stays a plain liveness probe. See `codewiki/startup-checks.md`.
>
> - **ADMIN-02 Per-tenant API rate limiting (2026-04-21)**: First real enforcement of API rate limits — the previous `slowapi.Limiter` was never wired into a middleware, so the env vars were inert. New `TenantRateLimitMiddleware` does Redis INCR+EXPIRE per `(tenant, time-bucket)`, reading the limit from `tenant_policies.rate_limit_requests_per_window` + `rate_limit_window_seconds` (migration `0021`). Fails open on Redis errors. The old `RATE_LIMIT_WINDOW` string is deprecated in favour of `RATE_LIMIT_WINDOW_SECONDS: int`. Admin dialog now has five fields.
>
> - **ADMIN-01 Per-tenant policy overrides (2026-04-21)**: Three operational knobs (`execution_quota_per_hour`, `max_snapshots`, `mcp_pool_size`) are now per-tenant via a new `tenant_policies` table (migration `0020`). Env vars become fallbacks. Toolbar **SlidersHorizontal** icon opens the per-tenant dialog. Rate-limit / rate-window and LLM provider keys are deliberately carved out as **ADMIN-02** / **ADMIN-03**; a full env-var-by-env-var rationale for what moved and what didn't is in `codewiki/tenant-policies.md` §4.
>
> - **VERTEX-02 Per-tenant Vertex projects (2026-04-21)**: Operators can now register per-tenant GCP projects for Vertex AI via the toolbar **Cloud** icon. Rides on the existing `tenant_integrations` table (`system='vertex'`) — no migration. Resolver precedence: tenant's `is_default` row → `ORCHESTRATOR_VERTEX_PROJECT` env fallback. Each tenant can bill to their own GCP project. ADC (service-account identity) stays process-global; the orchestrator's service account needs `aiplatform.user` on every target project. Per-tenant service-account JSON is explicitly out of scope. See `codewiki/node-types.md` provider-selection table and `codewiki/security.md` Vertex section.
>
> - **VERTEX-01 Vertex AI support (2026-04-21)**: Gemini chat / ReAct / streaming nodes can now run through Vertex AI in addition to AI Studio. Adds `vertex` to the `provider` enum on LLM Agent, ReAct Agent, LLM Router, Reflection, and Intent Classifier. Reuses the unified `google-genai` SDK via `Client(vertexai=True, project, location)` — no new Python dependency, identical wire format as the AI Studio path, only the Client constructor differs. Auth is ADC (`GOOGLE_APPLICATION_CREDENTIALS` or workload identity). Reuses the existing `ORCHESTRATOR_VERTEX_PROJECT` / `ORCHESTRATOR_VERTEX_LOCATION` settings previously used only for embeddings. Per-tenant project override tracked as VERTEX-02. See `codewiki/node-types.md` for the provider-selection table and `codewiki/security.md` for the ADC setup.
>
> - **API-18A In-app API Playground (2026-04-21)**: Toolbar FlaskConical icon → `ApiPlaygroundDialog` — test the currently-loaded workflow against an arbitrary JSON trigger payload without leaving the app. Sync shows the `SyncExecuteOut.output` context pretty-printed; async shows the returned `InstanceOut` and points at the Execution Panel for streaming logs. Live "Copy as curl" snippet, last-10-runs history per workflow (localStorage). Uses the existing `POST /execute` endpoint so existing auth, tenant scoping, and rate limits apply. Roadmap item #18 bumped to Partial; 18B chatbot embed widget is deferred pending a security design for the unauth-but-scoped access model. See `codewiki/feature-roadmap.md` §18.
>
> - **Sprint 2B MCP Maturity (2026-04-21)**: **MCP-01** — full audit of our MCP client against the 2025-06-18 spec. Ranked gap list (OAuth 2.1, elicitation, structured output, drift detection, protocol-version catch-up) lives in `codewiki/mcp-audit.md`. **MCP-02** — per-tenant MCP server registry. New `tenant_mcp_servers` (Alembic `0019`) with `auth_mode` discriminator (`none` / `static_headers` / `oauth_2_1` — the last is registry-accepted but runtime-deferred to MCP-03). `mcp_server_resolver.py` picks by explicit label → `is_default` row → legacy `ORCHESTRATOR_MCP_SERVER_URL` env fallback. `{{ env.KEY }}` header placeholders resolve through the Secrets vault. Session pool + `list_tools` cache are re-keyed by `(tenant_id, server)` so tenants never share warm connections. `tenant_mcp_server_tool_fingerprints` side table forward-declared empty for MCP-06 drift detection. Toolbar **Globe** icon → `McpServersDialog`. MCP Tool + ReAct Agent nodes accept an optional `mcpServerLabel` config field. See `codewiki/mcp-audit.md`.
>
> - **Sprint 2A Developer Velocity (2026-04-20)**: Seven incremental commits shipping edit→run→inspect loop improvements. **DV-01** data pinning — `dispatch_node` short-circuits on `pinnedOutput`. **DV-02** test single node — `POST …/nodes/{id}/test` probes one handler with upstream pins as synthetic context. **DV-03** sticky notes — non-executable canvas annotations filtered by `parse_graph`, `validateWorkflow`, and `computeNodeStatuses`. **DV-04** 45 new `safe_eval` helpers (string / math / array / object / date / utility) in `expression_helpers.py`; `**` and `//` also added. **DV-05** duplicate workflow — deep-copies graph incl. pins with `(copy)`, `(copy 2)`, `(copy 3)`... naming. **DV-06** hotkey cheatsheet — `?` opens modal; `Shift+S` / `1` / `Tab` registered with shared input-focus guard. **DV-07** active/inactive toggle — `workflow_definitions.is_active` (Alembic `0018`) filters Beat schedule triggers; manual Run / PATCH / duplicate remain available on inactive workflows. See `codewiki/dev-workflow.md`.
>
> - **AutomationEdge + async-external (2026-04-19)**: First-class node for AE-style async-external systems. Pattern C (Beat poll, default) and Pattern A (webhook callback) both resume through the shared `finalize_terminal` path. New tables `async_jobs`, `tenant_integrations`, `scheduled_triggers` (Alembic `0015`, `0017`). `workflow_instances.suspended_reason` distinguishes HITL suspend (NULL) from async-external (`'async_external'`). Diverted pause-the-clock timeout model banks elapsed Diverted time into `total_diverted_ms` so the active-runtime budget ignores it. See `codewiki/automationedge.md`.
>
> - **V0.9.15 Sub-Workflows (2026-04-14)**: **Sub-Workflow** logic node — execute another saved workflow as a single step. Child workflow runs synchronously inline as a linked `WorkflowInstance` (`parent_instance_id` / `parent_node_id`). Input mapping via `safe_eval` expressions, output filtering by child node IDs. Version policy: `latest` (live definition) or `pinned` (specific snapshot). Recursion protection via `_parent_chain` with configurable `maxDepth` (default 10, max 20). Cancellation cascades from parent to child instances. Frontend: `WorkflowSelect` (searchable, excludes current workflow), `InputMappingEditor`, `OutputNodePicker` custom widgets in `DynamicConfigForm.tsx`; `Layers` icon and version-policy badge on canvas node; drill-down child instance logs in `ExecutionPanel`. New Alembic migration `0011`. See `codewiki/node-types.md` for full config reference.
>
> - **V0.9.14 NLP Nodes (2026-04-15)**: **Intent Classifier** and **Entity Extractor** nodes under a new `nlp` category. Intent Classifier supports three modes: `heuristic_only` (lexical + embedding, zero LLM cost), `hybrid` (heuristic with LLM fallback below confidence threshold), and `llm_only`. Optional save-time embedding precomputation (`cacheEmbeddings`) for high-throughput workflows. Entity Extractor supports 5 rule-based extraction types (regex, enum, number, date, free_text) with optional LLM fallback for missing required entities. Intent-entity scoping restricts extraction based on upstream classification results. New `embedding_cache` table (Alembic `0010`). Custom UI editors `IntentListEditor` and `EntityListEditor` in `DynamicConfigForm.tsx`. See `codewiki/node-types.md` for full config reference.
>
> - **V0.9.13 Tier 1 UX (2026-04-10)**: **Template gallery** (toolbar **Templates**) — starter workflows, category filters, search, **Import JSON** / **Export current** (`{nodes, edges}`). **Sync run** — toolbar checkbox; `POST /execute` with `sync: true` holds the HTTP connection until a terminal status and returns final context (**200** + `SyncExecuteOut`); default async **202** unchanged. **Debug replay** — after **completed** / **failed** / **cancelled** / **paused**, **Debug** loads checkpoints, timeline scrubber, full `context_json` per step; canvas highlights the checkpoint node. See `SETUP_GUIDE.md` §7.1.2, `TECHNICAL_BLUEPRINT.md` V0.9.13 / §4.5 / §6.10.
>
> - **V0.9.11 Operator pause / cancel / resume (2026-03-22)**: While a run is **queued** or **running**, the execution panel offers **Pause** (cooperative pause after the current node), **Stop** (cooperative **cancel**), and (same timing) the SSE stream ends when the instance reaches **`paused`** or **`cancelled`**. **Resume** continues a **`paused`** run via `POST …/resume-paused` (optional `context_patch`). From **`paused`**, **Stop** abandons the run (immediate **`cancelled`**). Distinct from HITL **`suspended`** + **Review & Resume** (`POST …/callback`). DB: `cancel_requested`, `pause_requested` (migrations `0005`, `0006`). See `TECHNICAL_BLUEPRINT.md` §4.5, §5.2, §6.11.
>
> - **V0.9.10 Bridge reply + display names (2026-03-22)**: **Bridge User Reply** node promotes `orchestrator_user_reply` to context root for sync callers that want a single user-facing string. Optional **`displayName`** on canvas nodes (registry **`label`** unchanged) — see Step 4 / Step 17 and `TECHNICAL_BLUEPRINT.md` §3.4.1, §6.8, §10.
>
> - **Proxy Bridge (2026-03-22)**: Step 17 — any external gateway can call `POST /execute`, then poll `/context` or use SSE/callback APIs. Default pattern is async enqueue + poll. See Step 17 and `TECHNICAL_BLUEPRINT.md` §10.
>
> - **V0.9.9 Loop Node (2026-03-22)**: New `Loop` logic node for controlled agentic cycles. Drop it between any two nodes; its directly-connected downstream nodes form the loop "body". Configure `continueExpression` (a `safe_eval` expression evaluated before each iteration — loop runs while True) and `maxIterations` (default 10, backend cap 25). An empty `continueExpression` runs the body unconditionally for `maxIterations`. At each iteration `_loop_index` (0-based) and `_loop_iteration` (1-based) are injected into context and accessible from body nodes' prompts/expressions. After the loop, each body node's context key is replaced with `{"loop_results": [...per-iteration outputs...], "iterations": N}` — downstream nodes can reference individual iteration results via expressions. `validateWorkflow` blocks missing `continueExpression` (error) and warns if `maxIterations > 25`. Canvas shows `≤N×` badge and `⟳ expr` preview. New Step 16 added below.
>
> - **V0.9.8 Rich Token Streaming (2026-03-22)**: LLM Agent nodes now stream tokens to the browser as they are generated. The Celery worker publishes each token to a Redis pub/sub channel (`orch:stream:{instance_id}`). The FastAPI SSE endpoint subscribes to this channel in a background asyncio task and forwards tokens as `event: token` SSE events. The frontend accumulates tokens per node_id in `streamingTokens` state; the ExecutionPanel shows a live preview under any running node's expanded log entry. Falls back silently to non-streaming if Redis is unavailable. No DB migration required.
>
> - **V0.9.7 Checkpoint-aware Langfuse (2026-03-22)**: Checkpoints now link to Langfuse traces. For sequential nodes, after the checkpoint is saved its UUID is included in the node's Langfuse span output (`checkpoint_id` field). For parallel nodes (where the Langfuse span has already exited), the checkpoint UUID is embedded in the execution log's `output_json` under `_checkpoint_id`. This means every completed node in Langfuse now carries a direct reference to its DB context snapshot. No DB migration required.
>
> - **V0.9.6 Checkpointing (2026-03-22)**: Every time a node completes successfully the engine now saves a checkpoint — a full snapshot of the execution context at that moment. Checkpoints are stored in the new `instance_checkpoints` table (Alembic `0004`). Two new read-only API endpoints: `GET /instances/{id}/checkpoints` lists all checkpoints (node_id + timestamp, no payload), and `GET /instances/{id}/checkpoints/{checkpoint_id}` returns the full context snapshot. The `_save_checkpoint()` helper strips internal `_`-prefixed keys before writing, so only user-visible data is stored. Checkpoint failures are non-fatal — execution is never blocked by a checkpoint write error.
>
> - **V0.9.5 Reflection Node (2026-03-22)**: New `Reflection` agent node. Drag it onto the canvas, write a `reflectionPrompt` Jinja2 template (use `{{ execution_summary }}` to get an auto-built summary of all node outputs so far), and configure expected `outputKeys`. When the workflow reaches this node, it calls the LLM, parses the JSON response, and stores it like any other node output — e.g., `node_5.next_action`. A downstream Condition node can then branch on `node_5.next_action == "escalate"`. The node is read-only: it never edits earlier node outputs. No DB migration required.
>
> - **V0.9.4 HITL UX (2026-03-22)**: Step 9 updated — new `GET /instances/{id}/context` endpoint returns live `context_json` + `approvalMessage` from the suspended node. `POST /callback` now accepts optional `context_patch` for operator-supplied overrides merged before resume. Frontend: `ExecutionPanel` shows a "Review & Resume" button when suspended; `HITLResumeDialog` provides approval-message display, read-only context viewer, patch editor JSON textarea, and Approve / Reject buttons.
>
> - **V0.9.3 Deterministic Batch Semantics (2026-03-22)**: Step 6 updated — `POST /{id}/execute` now accepts an optional `deterministic_mode: true` flag. When set, the execution engine sorts every parallel ready-batch by node ID and processes futures in submission order instead of completion order, giving identical log sequences on every run. Default behaviour (non-deterministic, maximum throughput) is unchanged.
>
> - **V0.9.2 UX Improvements (2026-03-21)**: New Step 5b (pre-run validation) — `validateWorkflow()` runs client-side before every execution. Checks: trigger presence, node reachability (BFS from triggers), required empty fields, broken node-ID cross-references. `ValidationDialog` blocks hard errors and allows "Run Anyway" for warnings only. Undo/Redo — `flowStore.past[]`/`future[]` history stacks (max 50); `_pushHistory()` called before add/delete/connect/drag-start/edge-delete; Ctrl+Z/Ctrl+Y keyboard shortcuts in `FlowCanvas.tsx`; toolbar Undo/Redo buttons with disabled state. Node ID chip — `PropertyInspector` now shows the node's machine ID (e.g., `node_3`) in a monospace chip at the top of the panel with a one-click copy button so users can easily reference it in expression fields on other nodes. Inline field help text — every field in `DynamicConfigForm` now renders a `FieldHint` (10px muted subtext) when the field's `config_schema` entry carries a `description`; all node types in `node_registry.json` have been populated with descriptions. ForEach/Merge canvas clarity — `AgenticNode` renders a `waitAll`/`waitAny` strategy badge for Merge nodes and a `↻ arrayExpression` monospace hint for ForEach nodes, making both nodes interpretable at a glance without opening the properties panel.
>
> - **V0.9 Execution Enhancements (2026-03-21)**: New Step 14 — ForEach Loop iteration with downstream node re-execution per array element. New Step 15 — Retry from Failed Node (API + engine). Step 11 MCP section updated — connection pooling with configurable pool size. Step 8 updated — enhanced safe expression evaluator supports whitelisted functions (`len`, `lower`, `matches` etc.) and method calls. New config: `ORCHESTRATOR_MAX_SNAPSHOTS` (snapshot pruning) and `ORCHESTRATOR_MCP_POOL_SIZE`. Environment variable mapping via `{{ env.SECRET_NAME }}` for node config values. Langfuse context fix for parallel execution.
> - **V0.8 Enterprise Features (2026-03-20)**: Step 4 updated — property forms now generated from registry schemas via DynamicConfigForm; no more hardcoded panels. Step 5 updated — each graph save creates a snapshot in workflow_snapshots. New Step 12 — Version History & Rollback. Step 13 MCP section updated — 5-minute TTL cache + invalidate-cache endpoint. New Step 14 — OIDC Authentication. ReAct section updated for auto-discovery.
>
> - **V0.7 Observability, MCP Streaming & Tenant Tools (2026-03-20)**: Langfuse v4 integration — root trace per workflow, child spans per node, LLM generation recording with token usage, tool call spans. MCP client rewritten to use MCP Python SDK with Streamable HTTP transport — replaces raw REST bridge with standard MCP protocol. Tool listing and ReAct tool definitions fetched live from MCP server. TenantToolOverride consumed by tools endpoint.
>
> - **V0.6 Advanced Agent Capabilities (2026-03-20)**: ReAct iterative tool-calling loop for agent nodes (Google/OpenAI/Anthropic tool-calling APIs). SSE real-time execution updates replacing frontend polling. Celery Beat cron scheduler for schedule triggers. Frontend palette hydrated from `node_registry.json`; backend validates configs on save.
>
> - **V0.5 Production Hardening (2026-03-20)**: JWT-based auth with tenant claims (dev-mode header fallback). Fernet-encrypted credential vault per tenant. PostgreSQL RLS policies for DB-level tenant isolation. AST-based safe expression evaluator replaces `eval()`. Per-tenant rate limiting (slowapi) and hourly execution quotas.
>
> - **V0.4 Branching & Parallel Execution (2026-03-20)**: DAG engine rewritten with ready-queue model. Condition nodes now prune non-matching branches; independent nodes execute in parallel. Merge nodes wait for all upstream branches. Condition edges show colored "Yes"/"No" labels on the canvas.
>
> - **V0.3 Live LLM Integration (2026-03-20)**: Agent nodes now call real LLM providers (Google Gemini, OpenAI, Anthropic). System prompts support Jinja2 templating with upstream context injection (`{{ trigger.user_query }}`, `{{ node_1.response }}`). Token usage tracked in execution logs.
>
> - **V0.2 UI Wiring (2026-03-20)**: Frontend now saves/loads/executes workflows via the FastAPI backend and shows execution logs using a polling execution panel. See `TECHNICAL_BLUEPRINT.md` for architecture and `SETUP_GUIDE.md` for setup.
> - **Initial Walkthrough (2026-03-20)**: V0.1 — covers visual builder interaction, drag-and-drop, graph persistence, DAG execution, human-in-the-loop suspension, and MCP tool integration. See `TECHNICAL_BLUEPRINT.md` for architecture and `SETUP_GUIDE.md` for installation.

## AE AI Hub — How It Works (Step-by-Step)

**Purpose:** This document explains how the orchestrator works end-to-end, from building a visual workflow to executing it asynchronously. Each step includes pointers to the relevant **code files** so you can trace behavior or extend it. For contributor-focused topics (custom nodes, `safe_eval`, pause/cancel internals), see `DEVELOPER_GUIDE.md`.

**Advanced Memory note:** Advanced Memory v1 adds normalized conversation storage, rolling summaries, memory profiles, semantic or episodic memory, relational entity facts, and memory inspection APIs. See `codewiki/memory-management.md`.

**Version:** 0.9.18 (Sprint 2A + 2B)
**Last updated:** 2026-04-21

---

### Table of Contents

1. [Overview](#1-overview)
2. [Step 1 — User Builds a Workflow on the Canvas](#2-step-1--user-builds-a-workflow-on-the-canvas)
3. [Step 2 — Drag-and-Drop: Palette to Canvas](#3-step-2--drag-and-drop-palette-to-canvas)
4. [Step 3 — Connecting Nodes with Edges](#4-step-3--connecting-nodes-with-edges)
5. [Step 4 — Configuring Node Properties (Dynamic Forms)](#5-step-4--configuring-node-properties-dynamic-forms)
6. [Step 5 — Saving the Workflow (with Snapshots)](#6-step-5--saving-the-workflow-with-snapshots)
7. [Step 6 — Executing the Workflow](#7-step-6--executing-the-workflow)
8. [Step 7 — DAG Parsing and Topological Sort](#8-step-7--dag-parsing-and-topological-sort)
9. [Step 8 — Node-by-Node Execution](#9-step-8--node-by-node-execution)
10. [Step 9 — Human-in-the-Loop Suspension](#10-step-9--human-in-the-loop-suspension)
11. [Step 10 — Completion and Callback](#11-step-10--completion-and-callback)
12. [Step 11 — MCP Tool Bridge (Streamable HTTP)](#12-step-11--mcp-tool-bridge-streamable-http)
13. [Step 12 — Version History and Rollback](#13-step-12--version-history-and-rollback)
14. [Step 13 — OIDC Authentication Flow](#14-step-13--oidc-authentication-flow)
15. [Step 14 — ForEach Loop Iteration](#15-step-14--foreach-loop-iteration)
16. [Step 15 — Retry from Failed Node](#16-step-15--retry-from-failed-node)
17. [Step 17 — External Gateway Bridge](#18-step-17--external-gateway-bridge)
18. [Step 18 — NLP Nodes](#step-18--nlp-nodes-intent-classifier-and-entity-extractor)
19. [Step 19 — Sub-Workflow Execution](#step-19--sub-workflow-execution)
20. [End-to-End Example](#19-end-to-end-example)

---

## 1. Overview

The orchestrator has two independent layers:

```
 ┌────────────────────────────────────────┐
 │           DESIGN TIME                  │
 │  (Browser — React Flow visual canvas)  │
 │                                        │
 │  User drags nodes, connects edges,     │
 │  configures LLM prompts and tools.     │
 │  State lives in Zustand store.         │
 └───────────────┬────────────────────────┘
                 │  POST /api/v1/workflows
                 │  (graph_json: {nodes, edges})
                 ▼
 ┌────────────────────────────────────────┐
 │           RUN TIME                     │
 │  (Server — FastAPI + Celery worker)    │
 │                                        │
 │  Graph JSON is parsed into a DAG,      │
 │  topologically sorted, and executed    │
 │  node-by-node. Each node's output      │
 │  feeds into the next node's input.     │
 └────────────────────────────────────────┘
```

---

## 2. Step 1 — User Builds a Workflow on the Canvas

**Code:** `frontend/src/App.tsx`

When the user opens the orchestrator at `http://localhost:8080`, they see a three-panel layout:

| Panel | Component | File | Purpose |
|-------|-----------|------|---------|
| Left | `NodePalette` | `components/sidebar/NodePalette.tsx` | Draggable node list, grouped by category |
| Center | `FlowCanvas` | `components/canvas/FlowCanvas.tsx` | React Flow canvas with background grid, minimap, controls |
| Right | `PropertyInspector` | `components/sidebar/PropertyInspector.tsx` | Schema-driven config form for the selected node |

The entire app is wrapped in `ReactFlowProvider` (required by React Flow for coordinate transforms) and `TooltipProvider` (required by shadcn tooltips).

If `VITE_AUTH_MODE=oidc` and no token is stored in `localStorage`, the OIDC `LoginPage` is shown instead.

### Node card visual indicators

Each node card shows one of four visual states at a glance, evaluated in priority order:

| Priority | Visual | Meaning |
|----------|--------|---------|
| 1 (highest) | Blue ring | Node is currently selected |
| 2 | Red ring + `AlertCircle` icon | Configuration error (empty required field, broken node-ID reference) |
| 3 | Yellow ring + `AlertTriangle` icon | Warning — node is not reachable from any trigger |
| 4 (default) | Coloured dot | Runtime status: grey=idle, blue=running, green=completed, red=failed, yellow=suspended |

The `useNodeValidation` hook (`src/lib/useNodeValidation.ts`) runs `validateWorkflow()` inside a `useMemo` whenever `nodes` or `edges` change, returning `errorIds` and `warningIds` sets. Each `AgenticNode` reads its own `id` from NodeProps and checks membership in these sets.

In addition to the validation ring, node cards show type-specific summary info in the badge row:

| Node type | Extra info shown on card |
|-----------|--------------------------|
| Agent / ReAct Agent / LLM Router / Reflection | Model badge (e.g. `gemini-2.5-flash`) |
| Merge | Strategy badge (`waitAll` or `waitAny`) |
| ForEach | `↻ arrayExpression` monospace line (when set) |

---

## 3. Step 2 — Finding and Dragging Nodes: Palette to Canvas

**Code:** `components/sidebar/NodePalette.tsx` → `components/canvas/FlowCanvas.tsx` → `store/flowStore.ts`

The palette has a **search input** at the top. Typing filters nodes by label and description:
- Categories with zero matches are hidden entirely
- Categories with matches auto-expand (collapsing is disabled during a search)
- Category headers show `matched/total` count while a query is active
- A ✕ button clears the search

The data flow for a drag-and-drop operation:

```
NodePalette                    FlowCanvas                    flowStore
──────────                    ──────────                    ─────────
onDragStart                        │                             │
  │ setData("application/          │                             │
  │   reactflow", JSON)            │                             │
  └──────────────────────▶    onDragOver                         │
                              │ preventDefault()                 │
                              │                                  │
                              onDrop                             │
                              │ getData(...)                     │
                              │ screenToFlowPosition(x,y)       │
                              │                                  │
                              └──────────────────────────▶  addNode(category,
                                                              label, position,
                                                              defaultConfig)
                                                            │
                                                            ▼
                                                         nodes: [..., newNode]
                                                         selectedNodeId: newNode.id
```

The JSON payload in `dataTransfer` carries:
- `nodeCategory`: `"trigger"`, `"agent"`, `"action"`, or `"logic"`
- `label`: Display name (e.g. "LLM Agent")
- `defaultConfig`: Derived from the registry schema defaults

The Zustand store generates a sequential ID (`node_1`, `node_2`, ...) and creates a React Flow node with type `"agenticNode"`.

---

## 4. Step 3 — Connecting Nodes with Edges

**Code:** `store/flowStore.ts` → `onConnect`

When the user drags from one node's output handle to another node's input handle, React Flow fires the `onConnect` callback. The store calls `addEdge(connection, edges)` to create a new edge.

Each edge records:
- `source`: The upstream node ID
- `target`: The downstream node ID
- `sourceHandle`: Handle ID (relevant for Condition nodes: `"true"` or `"false"`)

---

## 5. Step 4 — Configuring Node Properties (Dynamic Forms + Expression Picker)

**Code:** `components/sidebar/PropertyInspector.tsx` → `components/sidebar/DynamicConfigForm.tsx` → `lib/registry.ts`

When the user clicks a node on the canvas:

1. `FlowCanvas.onNodeClick` calls `flowStore.selectNode(node.id)`.
2. `PropertyInspector` reads `selectedNodeId` from the store and finds the matching node.
3. A **Node ID chip** at the top of the panel shows the node's machine ID (e.g., `node_3`) in a monospace badge with a one-click copy button. This is the value to use in expressions on other nodes (e.g., `node_3.intent`).
4. **Display name (canvas)** — optional friendly title stored in `data.displayName`. **Engine type (registry)** is `data.label` and must stay aligned with `node_registry.json` so schemas and execution dispatch work. The canvas card shows `nodeCanvasTitle()` (`displayName` if set, else `label`); hover the card to see the engine type in the tooltip.
5. It calls `getRegistryNodeType(data.label)` and `getConfigSchema(data.label)` from `lib/registry.ts` to load the node's schema from `shared/node_registry.json`.
6. It renders `<DynamicConfigForm>` with the schema, current config, and an `onUpdate` callback.

`DynamicConfigForm` renders one field per schema entry:

| Schema field type | Rendered as | Notes |
|-------------------|-------------|-------|
| `string` + `enum` | `<Select>` dropdown | Options from enum array |
| `string` key in `EXPRESSION_KEYS` | `<ExpressionInput>` (expression mode) | Autocomplete: `node_2.intent`, `trigger.body`, `messageExpression` (Bridge User Reply), `continueExpression` (Loop) |
| `string` key in `NODE_ID_KEYS` | `<ExpressionInput>` (nodeId mode) | Autocomplete: `node_3`, `node_5` |
| `systemPrompt` | `<ExpressionInput>` (jinja2 mode) | Autocomplete: `{{ node_2.response }}` |
| `string` (`approvalMessage`, `body`) | `<Textarea>` | Multi-line plain text |
| `string` (other) | `<Input type="text">` | |
| `number` / `integer` | `<Input type="number">` | `min`/`max`/`step` from schema |
| `boolean` | `<input type="checkbox">` | |
| `object` | `<Textarea>` (JSON) | Validated on blur; red border on invalid JSON |
| `string` key `toolName` on `mcp_tool` | `ToolSingleSelect` | Searchable single-select from live tool list; shows title, description, safety tier |
| `array` + key is `tools` on `react_agent` | `ToolMultiSelect` | Fetches live tool list from `/api/v1/tools` |
| `array` (other) | `<Textarea>` (JSON array) | |

Every field change calls `flowStore.updateNodeData(id, { config: { ...updated } })`, merging the update immutably.

Each field also renders a `FieldHint` — a 10px muted grey line of subtext below the input — when the `config_schema` entry for that field has a `description` property. This gives users in-context guidance without leaving the panel (e.g., *"Cron expression in UTC (e.g. '0 * * * *' = every hour)"* or *"waitAll: hold until every incoming branch completes; waitAny: continue as soon as any one branch completes"*).

### ExpressionInput autocomplete

`ExpressionInput` wraps an `<input>` or `<textarea>` and shows a **fixed-position dropdown** (rendered via `createPortal` to `document.body` — not clipped by sidebar `overflow: hidden`). On every keystroke, `getCurrentToken()` detects the word under the cursor and filters suggestions. Pressing **Enter** or **Tab** calls `insertAtCursor()` to splice the selected variable in-place, leaving the rest of the expression intact.

### ToolMultiSelect (ReAct Agent)

When configuring a **ReAct Agent** node's `tools` field, a special `ToolMultiSelect` sub-component:

1. On mount, calls `api.listTools()` → `GET /api/v1/tools`.
2. Renders tools grouped by category, each with a checkbox and safety tier badge.
3. Selected tool names are stored in `config.tools` as a string array.
4. If no tools are selected (empty array), the backend will **auto-discover** all available MCP tools at runtime.

---

## 6. Step 5 — Saving the Workflow (with Snapshots)

**Code:** `backend/app/api/workflows.py` → `PATCH /{workflow_id}`

To persist a workflow, the frontend serializes the Zustand store's `nodes` and `edges` into a JSON object:

```http
POST /api/v1/workflows
X-Tenant-Id: acme-corp
Content-Type: application/json

{
  "name": "IT RCA Pipeline",
  "description": "Root cause analysis for failed AE requests",
  "graph_json": {
    "nodes": [ ... React Flow node objects ... ],
    "edges": [ ... React Flow edge objects ... ]
  }
}
```

The backend creates a `WorkflowDefinition` row with `version: 1`.

**On subsequent saves** (PATCH), before overwriting `graph_json`, the backend:

1. Validates node configs against `node_registry.json` schemas.
2. Precomputes embeddings for any Intent Classifier nodes with `cacheEmbeddings=true` (stores vectors in `embedding_cache`; no-op if no such nodes exist).
3. Creates a `WorkflowSnapshot` row with the **current** `graph_json` and `version`.
4. Replaces `graph_json` with the new content.
5. Increments `version`.

This gives every save an immutable point-in-time backup. The version badge in the Toolbar (`v{n}`) reflects the current version number.

---

## 7. Step 5a — Undo / Redo (Client-Side Canvas History)

**Code:** `frontend/src/store/flowStore.ts`, `frontend/src/components/canvas/FlowCanvas.tsx`

Every destructive canvas action is tracked in two in-memory stacks inside the Zustand store so users can freely experiment and reverse mistakes without saving.

```
User action (add / delete / connect / drag)
      │
      ▼
_pushHistory()
  → push {nodes, edges} snapshot to past[]
  → clear future[]
      │
      ▼
Apply the change to canvas

Ctrl+Z (undo)                      Ctrl+Y / Ctrl+Shift+Z (redo)
      │                                       │
      ▼                                       ▼
pop past[last]               pop future[0]
push current to future[]     push current to past[]
set nodes/edges = snapshot   set nodes/edges = snapshot
```

Key rules:
- **Max 50 snapshots** in each direction — older history is evicted.
- **Drag captures once per gesture** via `_draggingNodeIds`: the first `dragging: true` event pushes a snapshot; subsequent pixel-by-pixel events do not.
- **Config edits are not snapshotted** — property panel keystrokes fire too frequently; users edit the field back instead.
- **`replaceGraph()` resets both stacks** — loading a different workflow always starts with a clean history.

---

## Step 5b — Pre-Run Validation (Client-Side)

**Code:** `frontend/src/lib/validateWorkflow.ts`, `frontend/src/components/toolbar/ValidationDialog.tsx`

Before calling the execute API, the **Run** button runs `validateWorkflow(nodes, edges)` entirely in the browser. This gives instant feedback without making a network request.

```
User clicks Run
      │
      ▼
validateWorkflow(nodes, edges)
      │
      ├── Check 1: At least one Trigger node exists
      │
      ├── Check 2: BFS reachability from all triggers
      │              → orphaned nodes → WARNING
      │
      ├── Check 3: Required fields per node type
      │              condition, url, toolName,
      │              arrayExpression, responseNodeId,
      │              intents (≥1), entities (≥1) → ERROR
      │
      └── Check 4: Node ID cross-references
                     responseNodeId (Save/Bridge),
                     historyNodeId (Router/Intent Classifier),
                     scopeFromNode (Entity Extractor)
                     must point to existing node IDs → ERROR

      │
      ├── errors.length === 0 → proceed to API
      │
      ├── only warnings → show ValidationDialog with "Run Anyway"
      │
      └── any hard errors → show ValidationDialog, block execution
```

`ValidationError` shape:
```ts
interface ValidationError {
  nodeId: string;       // e.g. "node_3" (empty for graph-level errors)
  nodeLabel: string;    // e.g. "Save Conversation State"
  message: string;      // human-readable description
  severity: "error" | "warning";
}
```

---

## Step 6 — Executing the Workflow

**Code:** `backend/app/api/workflows.py` → `POST /api/v1/workflows/{id}/execute`

The frontend `Run` button calls this endpoint (only after validation passes). Real-time status arrives via SSE stream (`/instances/{instanceId}/stream`).

```
Client                          API Gateway                     Celery Worker
──────                          ───────────                     ─────────────
POST /execute                        │                               │
  {trigger_payload: {...},           │                               │
   deterministic_mode: false}        │                               │
                                     │                               │
                              Create WorkflowInstance                │
                              status = "queued"                      │
                              ─────────────────────▶           │
                              202 Accepted                     execute_workflow_task
                              {id: <instance_id>}              (instance_id, deterministic_mode)
                                                               │
                                                               │ execute_graph(db, instance_id,
                                                               │   deterministic_mode)
                                                               ▼
                                                         (DAG execution begins)
```

The API returns **`202 Accepted`** with the new instance ID when execution is **asynchronous** (default). The worker runs the DAG (Celery when enabled, or the in-process worker when `ORCHESTRATOR_USE_CELERY=false`).

### Synchronous mode (V0.9.13)

For API clients that cannot poll or open an SSE stream, send:

```json
{
  "trigger_payload": { "message": "hello" },
  "sync": true,
  "sync_timeout": 120
}
```

The API waits (up to `sync_timeout` seconds) and responds with **`200 OK`** and a **`SyncExecuteOut`** body: `instance_id`, `status`, `started_at`, `completed_at`, and `output` (the instance `context_json` with internal `_…` keys removed). On timeout the server returns **504**. This path calls `execute_graph` directly in a background thread and does **not** enqueue Celery for that request.

In the Hub UI, enable **Sync run** next to **Run** to use the same contract from the browser (the client then loads full instance detail including logs).

### Template gallery (V0.9.13)

Click the **Templates** (layout) icon in the toolbar to open the gallery: bundled example DAGs (helpdesk, onboarding, research, etc.), category tabs, search, **Use template** (replaces the canvas after confirm), **Import JSON**, and **Export current**. Template graphs live in `frontend/src/lib/templates/index.ts` and reuse the same `graph_json` shape as the save API.

### Debug replay (V0.9.13)

When a run ends in **`completed`**, **`failed`**, **`cancelled`**, or **`paused`**, **Debug** appears in the execution panel. It loads `GET …/checkpoints`, lets you step forward/back or click timeline dots, shows the **`context_json`** for each checkpoint, and updates node status colors on the canvas (indigo ring = checkpoint under inspection). Exit with **X** on the replay bar or by closing execution.

### Deterministic Mode (V0.9.3)

Pass `"deterministic_mode": true` in the request body to enable reproducible log ordering:

```json
POST /api/v1/workflows/{id}/execute
{
  "trigger_payload": { "input": "hello" },
  "deterministic_mode": true
}
```

When enabled, every parallel ready-batch is sorted by node ID before submission, and the engine waits for each future in that fixed order rather than using `as_completed`. Execution logs will appear in the same node sequence on every run, which makes debugging and test assertions against log order reliable. Default is `false` (maximum throughput).

### Pause, Stop, and Resume (V0.9.11)

While the instance is **`queued`** or **`running`**, the **Execution** panel shows:

| Control | Effect |
|---------|--------|
| **Pause** | Sets `pause_requested`. After the **current node** finishes, status becomes **`paused`** and context is saved. Does **not** set `completed_at`. |
| **Stop** | Sets `cancel_requested`. After the current node finishes, status becomes **`cancelled`** and `completed_at` is set. |

When status is **`paused`**:

| Control | Effect |
|---------|--------|
| **Resume** | `POST /api/v1/workflows/{workflow_id}/instances/{instance_id}/resume-paused` (optional JSON `{"context_patch": {...}}`). Celery runs `resume_paused_graph` — same ready-queue continuation as HITL resume, but without injecting `approval`. Reconnects the SSE stream from the client. |
| **Stop** | Abandons the run: **`cancelled`** immediately (no worker round-trip). |

**HITL** (**`suspended`**) is unchanged: use **Review & Resume** and `POST …/callback` with `approval_payload` / `context_patch`. Pause/resume APIs do not apply to `suspended` instances.

---

## 8. Step 7 — DAG Parsing and Topological Sort

**Code:** `backend/app/engine/dag_runner.py` → `parse_graph()`, `_detect_cycles()`

The worker loads the `WorkflowDefinition.graph_json` and processes it:

**Step 7a — Parse (Handle-Aware):** Build data structures from the React Flow JSON, preserving `sourceHandle` info for condition branches:

```python
nodes_map = {"node_1": {...}, "node_2": {...}, "node_3": {...}, "node_4": {...}}

edges = [
    Edge(source="node_1", target="node_2", source_handle=None),      # Trigger → Condition
    Edge(source="node_2", target="node_3", source_handle="true"),    # Condition → Agent (Yes)
    Edge(source="node_2", target="node_4", source_handle="false"),   # Condition → Action (No)
]
```

**Step 7b — Cycle Detection:**

Kahn's algorithm validates the graph is a valid DAG before execution begins.

**Step 7c — Ready-Queue Initialization:**

Nodes with `in_degree == 0` (no incoming edges, typically Trigger nodes) form the initial ready set.

---

## 9. Step 8 — Ready-Queue Execution

**Code:** `backend/app/engine/dag_runner.py` → `_execute_ready_queue()`, `backend/app/engine/node_handlers.py` → `dispatch_node()`

The engine uses a ready-queue model instead of a linear topological order:

```
┌──────────────────────────────────────────────────────────────┐
│  While ready_nodes is non-empty:                             │
│                                                              │
│  1. If 1 ready node → execute sequentially                   │
│     If N ready nodes → execute in parallel (ThreadPool)      │
│                                                              │
│  2. For each executed node:                                  │
│     a. Create ExecutionLog (status: "running")               │
│     b. dispatch_node(node_data, context, tenant_id)          │
│        ├── trigger  → pass through trigger_payload           │
│        ├── agent    → render prompt + call LLM provider      │
│        │              (or run ReAct loop for ReAct Agent)    │
│        ├── action   → call MCP tool / HTTP request           │
│        └── logic    → evaluate condition / merge branches    │
│     c. Store output in context[node_id]                      │
│                                                              │
│  3. Propagate edges:                                         │
│     - CONDITION node → only matching branch edges satisfied  │
│       Non-matching subtree is PRUNED (never executed)        │
│     - Other nodes → all outgoing edges satisfied             │
│                                                              │
│  4. Recompute ready set (all incoming edges satisfied,       │
│     not pruned, not yet executed)                            │
│                                                              │
│  On error: mark log and instance as "failed", stop.          │
└──────────────────────────────────────────────────────────────┘
```

The **context** dictionary accumulates outputs:

```python
context = {
    "trigger": {"user_query": "Why did request 12345 fail?"},
    "node_1": {"output": {"user_query": "Why did request 12345 fail?"}},
    "node_2": {"provider": "google", "model": "gemini-2.5-flash", "response": "..."},
    "node_3": {"status_code": 200, "body": "..."},
}
```

### LLM Agent Node

When `dispatch_node()` routes to an **LLM Agent** node:

```
1. Read config: provider, model, systemPrompt, temperature, maxTokens
2. Render system prompt via Jinja2:
   "Analyze {{ trigger.user_query }}"  →  "Analyze Why did request 12345 fail?"
3. Resolve advanced memory policy:
   - explicit `memoryProfileId` if set
   - else workflow default profile
   - else tenant default profile
   - else built-in defaults
4. If advanced memory is enabled:
   - load rolling summary + recent raw turns from the resolved session
   - retrieve active entity facts from profile mappings
   - retrieve semantic or episodic memories from enabled scopes
   - assemble prompt from instructions, history, entity memory, semantic hits,
     latest user message, and non-memory workflow context
5. If advanced memory is disabled:
   - fall back to one structured user block built from upstream context
6. Route to provider SDK (Google / OpenAI / Anthropic)
7. Return: { "response": "...", "usage": {...}, "model": "...", "provider": "...", "memory_debug": {...} }
8. Token counts and memory metadata are persisted in `ExecutionLog.output_json`
```

### ReAct Agent Node

When `dispatch_node()` routes to a **ReAct Agent** node (detected by `label == "ReAct Agent"`):

```
react_loop.py
─────────────
1. config.tools = ["ae.request.get_status"]  (explicit list)
   OR
   config.tools = []   →  auto-discover ALL tools from MCP via list_tools()

2. Load tool definitions in OpenAI function-calling format

3. Iterative loop (max maxIterations, hard cap 25):
   a. Build initial messages with the same advanced-memory packer used by LLM Agent
   b. Call LLM with system prompt + conversation history + tool schemas
   c. If LLM returns tool_calls:
      - Execute each tool via call_tool()
      - Append tool results to conversation
      - Continue loop
   d. If LLM returns text (no tool calls):
      - Return final response + token usage + iteration log + `memory_debug`

4. If max iterations reached: return "Maximum iterations reached"
```

Auto-discovery means ReAct agents configured with an empty `tools` list will use every tool the MCP server exposes. Use the explicit list to restrict a ReAct agent to a safe subset.

---

## 10. Step 9 — Human-in-the-Loop Suspension

**Code:** `backend/app/engine/dag_runner.py` (suspension), `backend/app/api/workflows.py` → `POST /callback` (resume), `frontend/src/components/toolbar/HITLResumeDialog.tsx` (UI)

When the DAG runner encounters an Action node with `approvalMessage` in its config:

```
DAG Runner                              Database                     External System
──────────                              ────────                     ───────────────
Reaches "Human Approval" node               │                             │
                                            │                             │
Check: is "approval" key in context?        │                             │
  NO → Suspend                              │                             │
    │                                       │                             │
    ├─ instance.status = "suspended"        │                             │
    ├─ instance.context_json = context  ───▶│ (serialized)                │
    ├─ instance.current_node_id = node_id   │                             │
    └─ log.status = "suspended"             │                             │
                                            │                             │
    (Worker thread released)                │                             │
                                            │                             │
             ... time passes ...            │                             │
                                            │                             │
POST /callback                              │                       Human approves
  {approval_payload: {"approved": true},    │                             │
   context_patch: {"node_3": {...}}}    ───▶│                             │
                                            │                             │
resume_workflow_task.delay(...)             │                             │
  │                                         │                             │
  ├─ Load context from DB                   │                             │
  ├─ Inject approval_payload                │                             │
  ├─ Apply context_patch (shallow merge)    │                             │
  ├─ Re-parse graph, skip executed nodes    │                             │
  └─ Continue _execute_ready_queue()        │                             │
```

This pattern allows the workflow to sleep indefinitely without holding a worker thread. The approval can come from any channel — WhatsApp, Teams, a web UI, or a direct API call.

### HITL Review UI (V0.9.4)

When an execution is suspended, the **Execution Panel** shows a yellow **Review & Resume** button. Clicking it:

1. Calls `GET /instances/{id}/context` → loads the live execution context and the suspended node's `approvalMessage`.
2. Opens the **`HITLResumeDialog`**, which shows:
   - The `approvalMessage` in a yellow alert banner.
   - The full `context_json` in a read-only scrollable viewer.
   - A JSON textarea pre-filled with `{}` for the operator to enter an optional context patch.
3. **Approve & Resume** — parses the patch, calls `POST /callback` with both `approval_payload: {approved: true}` and the patch, then streams the resumed execution.
4. **Reject** — calls `POST /callback` with `approval_payload: {rejected: true}` and no patch, allowing downstream Condition nodes to route the rejection branch.

---

## 11. Step 10 — Completion and Callback

**Code:** `backend/app/engine/dag_runner.py` → end of `_execute_ready_queue()`

After the last node completes:

1. `instance.status` is set to `"completed"`.
2. `instance.context_json` contains the full execution context (all node outputs).
3. `instance.completed_at` is set.
4. The `ExecutionLog` for each node records its individual input, output, timing, and status.

To retrieve results:

```http
GET /api/v1/workflows/{workflow_id}/instances/{instance_id}
X-Tenant-Id: acme-corp
```

This returns the full instance with all execution logs, ordered by start time.

In a proxy pattern, the final Action node in the graph can be an HTTP Request node that POSTs the result back to an external delivery endpoint, which then formats it for chat, email, or downstream automation.

### Viewing results in the Execution Panel

The `ExecutionPanel` (`components/toolbar/ExecutionPanel.tsx`) displays each node's log as a collapsible row. Each JSON block (Input and Output) has two action buttons:

| Button | Action |
|--------|--------|
| `Copy` (clipboard icon) | Copies full JSON to clipboard; icon becomes a green ✓ for 2 seconds |
| `Expand` (maximize icon) | Opens `FullJsonDialog` — scrollable full-size view of the JSON, also with a copy button |

The panel header shows "streaming…" while the SSE connection is active (not "polling…" — the frontend uses Server-Sent Events, not HTTP polling).

---

## 12. Step 11 — MCP Tool Bridge (Streamable HTTP)

**Code:** `backend/app/engine/mcp_client.py`, `backend/app/api/tools.py`, `backend/app/engine/node_handlers.py` → `_call_mcp_tool()`

The orchestrator connects to a configured MCP server using the **MCP Python SDK** over **Streamable HTTP** transport.

### Design Time — Palette Hydration

```
GET /api/v1/tools                    mcp_client.py             MCP Server (:8000)
X-Tenant-Id: acme-corp               │                         │
                                      ├─ list_tools()          │
                                      │  ├─ Check TTL cache    │
                                      │  │   (5 min)           │
                                      │  ├─ If stale:          │
                                      │  │   streamablehttp    │
                                      │  │   _client(/mcp) ──▶ │ tools/list
                                      │  └─ Cache result       │
                                      ├─ Filter by tenant      │
                                      └─ Return JSON list      │
```

Tools are cached for 5 minutes. To force an immediate refresh (e.g. after deploying new MCP tools):

```bash
curl -X POST http://localhost:8001/api/v1/tools/invalidate-cache \
  -H "X-Tenant-Id: acme-corp"
```

### Run Time — Tool Execution

```
DAG Runner                            mcp_client.py          MCP Server (:8000)
──────────                            ────────────           ──────────────────
dispatch_node("action", ...)
  │
  ├─ config.toolName = "ae.request.get_status"
  │
  └─ _call_mcp_tool()
       │
       call_tool("ae.request.get_status",
                 {"request_id": "REQ-12345"})
         │
         streamablehttp_client(/mcp) ──▶ tools/call
         │                                │
         ◀── SSE response stream ────────┘
         │
         └─ Returns parsed JSON result
```

---

## 13. Step 12 — Version History and Rollback

**Code:** `backend/app/api/workflows.py` → `GET /{id}/versions`, `POST /{id}/rollback/{v}`, `frontend/src/components/toolbar/VersionHistoryDialog.tsx`

Every time a workflow's graph is saved (PATCH with `graph_json`), the backend creates an immutable snapshot **before** overwriting:

```
PATCH /api/v1/workflows/{id}
  body: { graph_json: <new graph> }

Backend:
  1. Create WorkflowSnapshot(version=wf.version, graph_json=wf.graph_json)
  2. wf.graph_json = new graph
  3. wf.version += 1
  4. Commit
```

### Listing Snapshots

```http
GET /api/v1/workflows/{id}/versions
X-Tenant-Id: acme-corp

Response: [
  {"id": "...", "workflow_def_id": "...", "version": 2, "saved_at": "2026-03-20T10:00:00Z"},
  {"id": "...", "workflow_def_id": "...", "version": 1, "saved_at": "2026-03-20T09:50:00Z"}
]
```

### Rolling Back

```
POST /api/v1/workflows/{id}/rollback/{version}

Backend:
  1. Snapshot current state (version N) → creates snapshot
  2. wf.graph_json = snapshot[version].graph_json
  3. wf.version = N + 1  (rollback is a forward operation, not a rewind)
  4. Commit + refresh
  5. Return updated WorkflowOut
```

Rollback **always increments** the version counter — version history is an append-only ledger. This prevents accidentally losing the current state when restoring an old snapshot.

### Frontend

The **History** (clock) button in the Toolbar opens `VersionHistoryDialog`. It shows:
- The current live version (highlighted)
- All snapshots with timestamps and version numbers
- A **Restore** button per snapshot that calls rollback and reloads the canvas

---

## 14. Step 13 — OIDC Authentication Flow

**Code:** `backend/app/api/auth.py`, `frontend/src/components/auth/LoginPage.tsx`, `frontend/src/lib/api.ts`

The OIDC flow is opt-in. It activates when `ORCHESTRATOR_OIDC_ENABLED=true` (backend) and `VITE_AUTH_MODE=oidc` (frontend).

```
Browser                        FastAPI (/auth/oidc)        Identity Provider
───────                        ───────────────────        ─────────────────
App.tsx: no token in                  │                          │
  localStorage → show LoginPage       │                          │
                                      │                          │
"Sign in with SSO" clicked            │                          │
  └── GET /auth/oidc/login            │                          │
                                      │                          │
                              Generate state + nonce + PKCE      │
                              Store in Redis (5-min TTL)          │
                              Redirect to authorization_endpoint  │
                              ─────────────────────────────────▶ │
                                                                  │
                                                         User authenticates
                                                         Redirect to callback
                              ◀───────────────────────────────── │
                              GET /auth/oidc/callback             │
                                ?code=...&state=...               │
                                                                  │
                              Validate state from Redis           │
                              Exchange code for tokens            │
                              ─────────────────────────────────▶ │
                              ◀─ id_token ──────────────────────  │
                                                                  │
                              Validate ID token (authlib + JWKS)  │
                              Extract tenant_id from claim         │
                              Issue internal JWT                   │
                              Return { access_token, tenant_id }  │
                                                                  │
  Frontend stores token in           │                            │
    localStorage ("ae_access_token") │                            │
  App renders normally               │                            │
```

Once stored, all API calls use `Authorization: Bearer <token>` instead of `X-Tenant-Id`. The backend validates the JWT via `app/security/jwt_auth.py`.

---

## 15. Step 14 — ForEach Loop Iteration

**Code:** `backend/app/engine/dag_runner.py` → `_run_forEach_iterations()`, `backend/app/engine/node_handlers.py` → `_handle_forEach()`

The **ForEach** node (category: `logic`) enables iterating over an array, executing all immediately-downstream nodes once per element.

### Configuration

| Field | Type | Description |
|-------|------|-------------|
| `arrayExpression` | string | Expression that resolves to an iterable from the context (e.g. `trigger.items`) |
| `itemVariable` | string | Variable name injected into context per iteration (default: `item`) |

### Execution Flow

```
_execute_ready_queue
  │
  ├─ Detects ForEach node completed
  │
  └─ _run_forEach_iterations()
       │
       for each element in items:
       │  ├─ context["_loop_item"] = element
       │  ├─ context["_loop_index"] = idx
       │  ├─ context[itemVariable] = element
       │  │
       │  └─ for each downstream node:
       │       └─ _execute_single_node(...)
       │           → output collected into iteration_results
       │
       └─ context[downstream_id] = {
            "forEach_results": [...all_outputs...],
            "iterations": N
          }
```

Downstream nodes receive `loop_item`, `loop_index`, and `loop_variable` in their input payload, allowing prompts and expressions to reference the current iteration item.

---

## 16. Step 15 — Retry from Failed Node

**Code:** `backend/app/engine/dag_runner.py` → `retry_graph()`, `backend/app/api/workflows.py` → `POST /{id}/instances/{iid}/retry`, `backend/app/workers/tasks.py` → `retry_workflow_task`

When a workflow instance fails, users can retry execution from the point of failure instead of re-running the entire workflow.

### API

```http
POST /api/v1/workflows/{workflow_id}/instances/{instance_id}/retry
X-Tenant-Id: acme-corp
Content-Type: application/json

{
  "from_node_id": "node_5"  // optional — defaults to current_node_id
}
```

### Engine Behavior

```
retry_graph(db, instance_id, from_node_id)
  │
  ├─ Validate instance.status == "failed"
  ├─ Determine retry node (from_node_id or instance.current_node_id)
  ├─ Remove failed node output from context
  ├─ Delete failed ExecutionLog entry
  ├─ Set instance.status = "running"
  │
  ├─ Re-parse graph, build forward/reverse/in_degree
  ├─ All nodes with context entries = "already_executed" (skipped)
  │
  └─ _execute_ready_queue(... skipped=already_executed)
       → Only the failed node and its downstream successors re-execute
```

### Frontend

The `workflowStore.retryInstance(workflowId, instanceId, fromNodeId?)` action calls the retry API and re-streams the instance logs via SSE.

---

## 17. Step 16 — Loop Node (Controlled Cycles)

**Code:** `backend/app/engine/dag_runner.py` → `_run_loop_iterations()`, `backend/app/engine/node_handlers.py` → `_handle_loop()`

The **Loop** node (category: `logic`) repeats its directly-connected downstream body nodes while a condition holds, up to a configurable maximum.

### Configuration

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `continueExpression` | string | — | `safe_eval` expression evaluated before each iteration. Loop continues while True. Leave empty to run unconditionally. |
| `maxIterations` | integer | 10 | Maximum iterations (backend hard cap: 25). |

### Execution Flow

```
_execute_ready_queue
  │
  ├─ Detects Loop node completed
  │
  └─ _run_loop_iterations()
       │
       for idx in range(maxIterations):
       │  ├─ _eval_condition(idx)  ← pre-check (while-loop semantics)
       │  │    if False → break
       │  │
       │  ├─ context["_loop_index"] = idx
       │  ├─ context["_loop_iteration"] = idx + 1
       │  ├─ clear body node outputs from context
       │  │
       │  └─ for each body node:
       │       └─ _execute_single_node(...)
       │           → output appended to all_iteration_results[nid]
       │
       └─ for each body node:
            context[nid] = {
              "loop_results": [<iter-0>, <iter-1>, ...],
              "iterations": N
            }
```

### Accessing Loop Results Downstream

After the Loop, a body node `node_3` holds:

```json
{
  "loop_results": [
    { "response": "attempt 1 output" },
    { "response": "attempt 2 output" }
  ],
  "iterations": 2
}
```

A downstream node can reference `node_3.loop_results[-1].response` (last iteration) via a Jinja2 expression, or use a Condition to branch on `node_3.iterations < 3`.

### Suspension and Failure

- **Failure in body**: partial `loop_results` up to the failed iteration are stored; instance moves to `failed`.
- **Suspension in body**: partial results stored; instance moves to `suspended`; HITL Resume continues from the suspended node.

### Visual Indicators

On the canvas the Loop node shows:
- `≤N×` badge (maxIterations)
- `⟳ {continueExpression}` monospace line (when expression is set)

---

## 18. Step 17 — External Gateway Bridge

**Code:** `app/api/workflows.py`, `examples/python_client.py`

Any external caller can act as a dumb proxy to the orchestrator. When a scheduler, webhook, or controlling workflow already knows the workflow UUID and trigger payload, it can call the orchestrator directly without adding another LLM hop.

### Trigger

The bridge pattern starts with a direct execute call:

```http
POST /api/v1/workflows/<workflow_id>/execute
Content-Type: application/json
X-Tenant-Id: default

{
  "trigger_payload": {
    "message": "Run incident response workflow",
    "session_id": "sys-scheduler-001",
    "incident_id": "INC-456"
  }
}
```

**Async vs sync (default: async):** by default the caller posts `/execute`, gets an instance id back, and decides whether to poll `GET /context`, subscribe to SSE, or return immediately. If a blocking flow is needed, the caller can use `sync: true` on execute or poll `/context` until terminal.

**Sync reply text:** when the run finishes with `completed`, the caller should first prefer `context_json.orchestrator_user_reply` (set by **Bridge User Reply** nodes), else choose the most relevant LLM/ReAct `response`, else fall back to a JSON dump. Suspended workflows should surface approval instructions plus truncated context.

### Code path

| Step | Location | What happens |
|------|----------|-------------|
| 1 | External caller | Builds `workflow_id` + `trigger_payload` |
| 2 | `app/api/workflows.py` `execute_workflow()` | Validates the request, creates a workflow instance, and dispatches execution |
| 3 | `workers/tasks.py` or sync branch | Runs the workflow async or inline depending on request settings |
| 4 | External caller | Polls `GET /context`, subscribes to SSE, or resumes later via callback |

### Properties

| Property | Value |
|----------|-------|
| LLM tokens spent by the bridge | 0 |
| Tool calls made by the bridge | 0 |
| Default mode | Async enqueue; caller decides whether to poll |
| Poll interval | Caller-defined |
| Default timeout | Caller-defined, or `sync_timeout` for inline sync execute |

### Constraints

- HITL still requires `POST /callback` or the Hub UI.
- JWT mode requires a Bearer token; dev mode accepts `X-Tenant-Id`.
- There is no built-in chat formatting layer beyond what the caller derives from the returned context.

### Config

Set these in `backend/.env`:

```env
ORCHESTRATOR_AUTH_MODE=dev
ORCHESTRATOR_SECRET_KEY=change-me-in-production
```

### When to use this pattern

Use the proxy pattern when the workflow UUID and payload are already known at call time, for example from a cron scheduler, inbound webhook, or another workflow.

## Step 18 — NLP Nodes: Intent Classifier and Entity Extractor

**Code:** `backend/app/engine/intent_classifier.py`, `backend/app/engine/entity_extractor.py`, `backend/app/engine/embedding_cache_helper.py`

Two dedicated NLP nodes provide structured text understanding without requiring full LLM calls for every request.

### Intent Classifier

Classifies user intent using a hybrid scoring algorithm ported from IntentEdge:

```
User utterance
      │
      ├─ mode == llm_only ──────────────────▶ LLM classify (skip embeddings)
      │
      ├─ Lexical scoring (intent name/example substring matching)
      │
      ├─ Embedding scoring (cosine similarity of utterance vs intent vectors)
      │     ├─ cacheEmbeddings=true  → read from DB embedding_cache
      │     └─ cacheEmbeddings=false → compute on-the-fly
      │
      ├─ Combined score = lexical + (embed_score × 4.0)
      │
      ├─ confidence ≥ threshold → return heuristic result
      │
      └─ confidence < threshold (hybrid mode) → LLM fallback classify
```

**Save-time embedding precomputation:** When `cacheEmbeddings=true`, saving the workflow triggers `precompute_node_embeddings()` which embeds all configured intents and stores the vectors in the `embedding_cache` table. At runtime, these are read from the DB instead of recomputed. This is optional — for simple intent lists, on-the-fly embedding works fine.

### Entity Extractor

Extracts structured data from text using configurable rule-based patterns:

```
Source text (from expression)
      │
      ├─ Scope entities by upstream intent (if scopeFromNode configured)
      │
      ├─ Rule-based extraction (regex, enum, number, date, free_text)
      │
      ├─ Check for missing required entities
      │
      └─ llmFallback=true and missing required → LLM extract remaining
```

### Typical NLP workflow pattern

```
[Webhook Trigger] → [Intent Classifier] → [Entity Extractor] → [Condition]
                                                                   ↙      ↘
                                                           [Branch A]  [Branch B]
```

The Entity Extractor's `scopeFromNode` points at the Intent Classifier. The `intentEntityMapping` restricts which entities are relevant per intent. Downstream Condition nodes branch on `node_X.intents[0]` or `node_Y.missing_required`.

---

## Step 19 — Sub-Workflow Execution

**Code:** `backend/app/engine/node_handlers.py` → `_handle_sub_workflow`, `_execute_sub_workflow`; `backend/app/engine/dag_runner.py` (child instance creation, cancellation cascade)

The **Sub-Workflow** node (category: `logic`) lets you execute another saved workflow as a single step within the current workflow. This enables modularity — break complex pipelines into reusable building blocks.

### Configuration

| Field | Type | Description |
|-------|------|-------------|
| `workflowId` | string | ID of the child workflow definition |
| `versionPolicy` | enum | `latest` (live definition) or `pinned` (specific snapshot) |
| `pinnedVersion` | integer | Version number when using `pinned` policy |
| `inputMapping` | object | Map of child trigger keys → parent context expressions |
| `outputNodeIds` | string[] | Return only these child node outputs (empty = all) |
| `maxDepth` | integer | Maximum nesting depth (1–20, default 10) |

### Execution Flow

```
Parent DAG runner reaches Sub-Workflow node
      │
      ├─ Load child WorkflowDefinition (by workflowId)
      │    ├─ versionPolicy == "latest" → use live graph_json
      │    └─ versionPolicy == "pinned" → load from workflow_snapshots
      │
      ├─ Recursion check
      │    ├─ Is workflowId already in _parent_chain? → FAIL (cycle)
      │    └─ len(_parent_chain) >= maxDepth? → FAIL (depth exceeded)
      │
      ├─ Build child trigger_payload
      │    └─ For each key in inputMapping: safe_eval(expression, parent_context)
      │
      ├─ Create child WorkflowInstance
      │    ├─ parent_instance_id = parent.id
      │    ├─ parent_node_id = current node ID
      │    └─ context_json._parent_chain = [ancestor IDs...]
      │
      ├─ execute_graph(child_instance) — synchronous, inline
      │
      ├─ Check child status
      │    ├─ completed → extract and return outputs
      │    ├─ suspended → FAIL (HITL in child not supported v1)
      │    └─ failed/cancelled → FAIL with child error details
      │
      └─ Return { child_instance_id, child_workflow_name,
                  child_status, outputs: {...} }
```

### Parent-Child Instance Linking

Each child execution creates a separate `WorkflowInstance` row linked via:
- `parent_instance_id` — FK to the parent instance
- `parent_node_id` — the Sub-Workflow node's ID in the parent graph

This enables:
- **Independent logs:** Each child has its own `execution_logs` entries
- **Drill-down debugging:** The Execution Panel shows a collapsible child log section under Sub-Workflow nodes
- **Cancellation cascade:** When a parent is cancelled, all running/queued children are also cancelled

### Frontend

- **WorkflowSelect** — searchable dropdown that lists all workflows (excluding the current one to prevent self-reference)
- **InputMappingEditor** — key-value editor for mapping child trigger keys to parent context expressions
- **OutputNodePicker** — fetches child workflow nodes and lets you select which outputs to return
- **Canvas badge** — shows `latest` or `v{N}` version policy on the node card
- **Execution Panel** — Sub-Workflow log entries with `child_instance_id` in the output render an expandable child instance section with its own logs

---

## 19. End-to-End Example

**Scenario:** An IT support agent that diagnoses a failed AE request using a ReAct loop with auto-discovered tools.

### Graph Design

```
[Webhook Trigger] ──▶ [ReAct Agent] ──▶ [Human Approval] ──▶ [MCP: restart_request]
```

The ReAct Agent has `tools: []` (empty) — it will auto-discover all MCP tools at runtime.

### Execution Trace

| Step | Node | Type | Key Behavior |
|------|------|------|-------------|
| 1 | Webhook Trigger | trigger | Pass through `{request_id: "REQ-12345"}` |
| 2 | ReAct Agent | agent | Auto-discovers 106 MCP tools. Calls `get_request_status(REQ-12345)`, then `get_execution_logs(REQ-12345)`, then reasons over the results and produces a diagnosis. |
| 3 | Human Approval | action | Workflow suspended. Approval request sent with diagnosis. |
| — | *(Human approves)* | — | `POST /callback {approved: true, action: "restart"}` |
| 4 | restart_request | action | `call_tool("ae.request.restart", {request_id: "REQ-12345"})` |

### Version History

After editing this workflow (e.g. changing the ReAct system prompt), the user saves again. The previous version is automatically snapshotted. If the new prompt breaks the agent's behavior, they open the History dialog and click **Restore** on the previous version.

### Timing

- Step 1-2: ~15 seconds (ReAct loop with 2-3 tool calls).
- Step 3: Suspended for 2 hours (human reviewing).
- Step 4: ~3 seconds (after resume).
- Total worker thread held: ~18 seconds across all steps.

---

For architecture details, see `TECHNICAL_BLUEPRINT.md`.
For installation and setup, see `SETUP_GUIDE.md`.

