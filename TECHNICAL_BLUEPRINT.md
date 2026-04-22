> - **COPILOT-01b.i Agent runner + session streaming (2026-04-22)**: Second shippable slice of COPILOT-01. Ships the LLM-driven chat loop that sits on top of the 01a pure tool layer, plus the session/turn persistence and SSE streaming surface the COPILOT-02 chat pane will consume. Three new backend modules: `app/copilot/prompts.py` (system prompt enforcing the NL-first pipeline + source-of-truth rule + compact draft-snapshot context assembly), `app/copilot/tool_definitions.py` (hand-written JSON schemas for all eight tools — deliberately NOT auto-generated from the pure tool functions because the *descriptions* are prompts-in-disguise and wording here drives correct tool-use behaviour), and `app/copilot/agent.py` (the `AgentRunner` class with `send_turn` as the single entry point yielding a stream of events). The runner protocol: (1) persist user turn first so partial failure keeps history consistent; (2) for up to `MAX_TOOL_ITERATIONS=12` iterations, call Anthropic → persist assistant turn (text + raw_content blocks) → if no `tool_use` blocks, done; else dispatch each tool via `tool_layer.dispatch`, persist tool turns, build `tool_result` blocks, feed back; (3) final `done` event with the list of persisted turn ids + final text. Tool-layer errors bubble to the LLM as `is_error=True` tool_result content so the model reads the error message and self-corrects — no 500s for "LLM called add_node with a typo". Per-tenant Anthropic key via ADMIN-03's `get_anthropic_api_key(tenant_id)`. Session API at `/api/v1/copilot/sessions` with `/providers` (tool-surface introspection), CRUD on sessions (abandon preserves history), `GET .../turns` for chat-pane replay, and `POST .../turns` which returns `text/event-stream` with the agent's events as they arrive. Turns flush incrementally, then one commit when the stream closes — a mid-stream disconnect still persists partial progress. Frontend types for sessions, turns, providers, and the `CopilotAgentEvent` discriminated union. `api.sendCopilotTurn(sessionId, text, signal?)` is an async generator that uses a streaming `fetch` (EventSource can't POST a body) and parses `data: …\n\n` frames by hand, emitting a recoverable `error` event on malformed JSON rather than killing the stream. Scope boundaries: `test_node` + `execute_draft` runner-side tools (01b.ii), system-KB RAG grounding with `search_docs` + `get_node_examples` (01b.iii), OpenAI + Google providers + per-session token budget columns + enforcement middleware (01b.iv) — each a separate shippable slice. 22 new tests (`test_copilot_agent.py` × 10 with the SDK mocked + `test_copilot_sessions_api.py` × 12). Backend suite 538 passed, 21 skipped (up from 516 pre-01b.i).
>
> - **COPILOT-01a Draft-workspace foundation (2026-04-22)**: First shippable slice of the workflow authoring copilot roadmap entry (#24 → COPILOT-01/02/03). The design rests on two pillars: a **safety boundary** so the copilot never mutates `workflow_definitions` directly, and a **pure tool layer** so the HTTP dispatch path and the (future) in-process agent runner both call the same code. Schema (migration `0022`): three tenant-scoped RLS-policied tables — `workflow_drafts` (the ephemeral graph being edited; carries `version` for optimistic concurrency and `base_version_at_fork` for the promote race guard), `copilot_sessions` (one chat session per draft, holds provider/model for 01b), and `copilot_turns` (ordered conversation history, `role ∈ {user, assistant, tool}`, `content_json` role-shaped). Tool layer at `app/copilot/tool_layer.py` — eight functions (`list_node_types` / `get_node_schema` / `add_node` / `update_node_config` / `delete_node` / `connect_nodes` / `disconnect_edge` / `validate_graph`) that each take a graph dict and return a new one. Deliberately no DB access inside the tool functions — the agent runner in 01b will chain several tools per LLM function-calling turn in-memory and commit once, no per-tool savepoints. API at `/api/v1/copilot/drafts` with CRUD, a generic `POST .../tools/{tool_name}` dispatch (mutation tools persist + bump `version`; read-only tools don't write), and `/promote` which atomically either creates a net-new `WorkflowDefinition` at v1 OR snapshots the base's current graph and overwrites + bumps version (after checking `base.version == base_version_at_fork` — see §5 of `codewiki/copilot.md` for the race being guarded). NL-first turn pipeline (intent-extract → clarify → pattern-match via RAG → draft → narrate) and system-KB grounding are documented as design intent in this ticket and implemented in 01b. Frontend gets TS types + `api.*` bindings only; no UI. 47 new tests (`test_copilot_tool_layer.py` 28 unit; `test_copilot_drafts_api.py` 19 integration). Total backend suite 516 passed, 21 skipped.
>
> - **RLS-01 Systemic `get_tenant_db` cutover (2026-04-21)**: Closed a latent correctness bug across the entire tenant-scoped API surface. `backend/app/database.py` has exposed two DB-session dependencies since the very first RLS migration — `get_db` (raw session, no GUC) for cross-tenant operator surfaces, and `get_tenant_db` (session with `app.tenant_id` GUC pre-set) for per-tenant handlers. A grep at incident time showed the actual state of the codebase: **zero** tenant-scoped handlers were using `get_tenant_db`; everything used `get_db`. Only three files (`workflows.py`, `a2a.py`, `sse.py`) issued any `set_tenant_context` call at all, and only at narrowly-scoped sites (the stream polling thread, `test_node`). That disconnect produced exactly the kind of silent failure RLS is designed to prevent: the app had been running safely for months purely because the deployed DB role was a superuser, and Postgres unconditionally bypasses RLS policies for superusers. The STARTUP-01 `rls_posture` check was added in the same session specifically to nudge operators off superuser — which one did, which immediately surfaced `InsufficientPrivilege: new row violates row-level security policy for table "workflow_definitions"` on a perfectly normal `POST /api/v1/workflows`. RLS-01 is the systemic fix: (a) swap `Depends(get_db)` → `Depends(get_tenant_db)` across `workflows`, `knowledge`, `memory`, `tenant_integrations`, `tenant_mcp_servers`, `tenant_policies`, `secrets`, `conversations`, `tools`, and the four header-based A2A key/publish endpoints; (b) leave the path-based A2A surface (`agent_card`, `a2a_dispatcher`, `_get_a2a_tenant`) on raw `get_db` but add explicit `set_tenant_context(db, tenant_id)` inline since those read the tenant from the URL path, not the `X-Tenant-Id` header that `get_tenant_id` depends on; (c) preserve the existing polling-thread `set_tenant_context(poll_db, tenant_id)` in the A2A `tasks/sendSubscribe` stream (that session is a standalone `SessionLocal()`, not a FastAPI dependency); (d) add a dedicated regression test file `tests/test_rls_dependency_wired.py` that mounts each router, spies on `SessionLocal()`, and asserts the request handler issued `SELECT set_tenant_id(:tid)` with the expected tenant_id — a failure there means someone wrote `Depends(get_db)` on a tenant-scoped endpoint; (e) update eight test-fixture files to register both `get_db` and `get_tenant_db` in `app.dependency_overrides` (same fake yielding the MagicMock session) so mocked unit tests continue to work. No migration, no API surface change, no frontend change — pure dependency-wiring fix. `set_tenant_context` issues `SELECT set_tenant_id(:tid)` which is a *session-level* SET (not `SET LOCAL`), so the GUC survives `db.commit()` calls in the middle of a request — that's why `get_tenant_db` only needs to set it once per request even though handlers like `create_workflow` commit mid-flight. See `codewiki/security.md` §Database layer for the full mental model.
>
> - **ADMIN-03 Per-tenant LLM provider credentials (2026-04-21)**: Completes the per-tenant config story for LLM providers by moving `google_api_key` / `openai_api_key` / `openai_base_url` / `anthropic_api_key` from process-global env vars onto the existing Fernet-encrypted `tenant_secrets` vault under four well-known names. Design choice: reuse the vault (no new table) because (1) encryption at rest is already solved, (2) the existing `/api/v1/secrets` CRUD covers management, and (3) the `{{ env.KEY }}` templating convention in node configs can reference the same keys without extra plumbing. Discoverability (operators not having to remember conventional names) is solved by a specialised dialog on top of the vault rather than a new table schema. New `engine/llm_credentials_resolver.py` defines four `get_*_api_key(tenant_id)` functions plus `get_credentials_status(tenant_id)` for the admin UI (returns source labels only, never values). Precedence per provider: tenant vault → `settings` env default → `ValueError` with a two-path remediation message ("set the LLM_* secret OR set ORCHESTRATOR_*_API_KEY"). `get_tenant_secret` is wrapped in a broad except so vault failures (connection refused, RLS denial, Fernet rotation mid-flight) degrade to env default with a warning instead of cascading 500s — same hot-path philosophy as `tenant_policy_resolver`. Seven call sites wired: `llm_providers._google_client` (genai backend), `_call_openai`, `_call_anthropic`, `streaming_llm.stream_openai`, `stream_anthropic`, `react_loop._openai_call`, `_anthropic_call`. VERTEX-01/02 paths unchanged — Vertex uses ADC + project routing, not API keys. Status endpoint at `GET /api/v1/llm-credentials` returns `{provider: {source, secret_name}}` — an explicit test asserts the response body never contains the secret value, guarding against regression into a credential-exposure incident. Frontend `LlmCredentialsDialog` renders labelled rows (not raw key names) with password masks, show/hide toggles, per-field source badges, and a tri-action model (unchanged / set / clear) that writes through the existing `/api/v1/secrets` endpoints. Embedding paths remain on env keys — follow-up ticket if tenants need per-tenant embedding billing.
>
> - **STARTUP-01 Preflight readiness checks (2026-04-21)**: New `app/startup_checks.py` module + FastAPI `lifespan` handler + `/health/ready` endpoint. Registry of seven `CheckResult`-returning functions, each carrying a `status ∈ {pass, warn, fail}` + specific remediation string. Runs once at boot (logged at INFO/WARN/ERROR with remediation in the message so operators can fix without reading docs) and re-runs live on every `/health/ready` call (HTTP 503 on any `fail`, 200 otherwise — `warn` is not fatal for readiness since a k8s probe shouldn't cycle a pod for a non-superuser-role lint). Checks cover the four categories of pain this app had produced support tickets for: **dependency reachability** (DB, Redis, Celery worker heartbeat — this would have caught the "workflow sat queued forever" bug because `USE_CELERY=true` with no worker now warns with the exact start command), **config coherence** (auth-mode with placeholder SECRET_KEY, OIDC missing required fields, vault key blank while `tenant_secrets` has rows — fatal), **schema drift** (alembic `current` vs. `head` — warn when behind), and **security posture** (RLS silently bypassed when connected as a superuser role, per the well-buried `SETUP_GUIDE §5.2a`). Tier 2 adds `check_mcp_default_server` which TCP-connect-probes the env-fallback URL only when no tenant has a `tenant_mcp_servers` row — avoids noise for operators who've fully migrated to the MCP-02 registry. `ORCHESTRATOR_SKIP_STARTUP_CHECKS` gates the lifespan during tests; `backend/tests/conftest.py` sets it automatically so TestClient doesn't pound real dependencies. `run_all_checks()` wraps uncaught raises as synthetic `fail` results, meaning a buggy check cannot break the endpoint. Frontend `StartupHealthBanner` fetches `/health/ready` once on mount; red non-dismissible strip on `fail`, amber dismissible strip on `warn` (1-hour `localStorage` sticky so a known warn doesn't nag all day); collapsed-by-default with an expand chevron revealing per-check remediation. 26 new backend tests + the existing frontend test suite green.
>
> - **ADMIN-02 Per-tenant API rate limiting (2026-04-21)**: Turns on real per-tenant API rate limiting for the first time. Investigation during ADMIN-02 scoping revealed the existing `slowapi.Limiter` was instantiated in `rate_limiter.py` and registered on `app.state`, but no `SlowAPIMiddleware` was ever installed and no `@limiter.limit()` decorators exist on any route — the `ORCHESTRATOR_RATE_LIMIT_*` env vars had zero runtime effect pre-ADMIN-02. Rather than retrofit slowapi (which would need callable `default_limits` with a context-var to access the request, plus `SlowAPIMiddleware` installation), ADMIN-02 writes a small custom `TenantRateLimitMiddleware` (`security/tenant_rate_limit.py`) that does the same Redis INCR+EXPIRE shape as the existing `check_execution_quota`. Key is `orch:ratelimit:<tenant_key>:<floor(now/window)>`; TTL is `window_seconds + 5`. Middleware is added after CORS so OPTIONS preflight doesn't count. Fail-open on Redis errors — a broken rate-limit layer cannot 500 every endpoint. 429 responses carry `Retry-After: <seconds>`. Exempt paths: `/health`, `/docs`, `/redoc`, `/openapi.json`. Migration `0021` adds two nullable columns to `tenant_policies`: `rate_limit_requests_per_window` and `rate_limit_window_seconds`. `EffectivePolicy` dataclass grows the two fields; resolver + API + dialog updated. The old `ORCHESTRATOR_RATE_LIMIT_WINDOW: str = "1 minute"` setting is deprecated in favour of `ORCHESTRATOR_RATE_LIMIT_WINDOW_SECONDS: int = 60` which is a clean integer instead of a slowapi-format string. `slowapi` remains in `requirements.txt` and `app.state.limiter` remains registered for now — a follow-up cleanup ticket can drop both.
>
> - **ADMIN-01 Per-tenant policy overrides (2026-04-21)**: New `tenant_policies` table (migration `0020`, PK on `tenant_id` — one row per tenant) with nullable override columns for three operational knobs that were previously process-global env vars: `execution_quota_per_hour`, `max_snapshots`, `mcp_pool_size`. RLS enabled with the standard `app.tenant_id` GUC policy. New `engine/tenant_policy_resolver.get_effective_policy(tenant_id)` returns a frozen `EffectivePolicy` dataclass: each field is resolved as (override if non-null → env default if null) with a per-field `source` label ("tenant_policy" / "env_default") for UI display. Resolver wraps its DB read in a defensive try/except that logs a warning and returns env defaults on any failure — quota enforcement is a hot path and must not 500 because the `tenant_policies` table is momentarily unreachable. Three runtime call sites wired: `security/rate_limiter._check_via_redis` + `_check_via_db` (reads `execution_quota_per_hour`), `workers/scheduler.prune_old_snapshots` (reads `max_snapshots`; resolved once per tenant per run by joining `workflow_snapshots` → `workflow_definitions` and caching in a tenant→max_keep dict), `engine/mcp_client._pool_for` (reads `mcp_pool_size` at pool construction time — existing pools keep their original size until `shutdown_pool()` or process restart). API is a singleton at `/api/v1/tenant-policy` using Pydantic `model_fields_set` so PATCH can distinguish "field omitted" (keep prior override) from "explicit null" (clear override) from "integer" (set override). Frontend `TenantPolicyDialog.tsx` behind the toolbar `SlidersHorizontal` icon tracks a per-field `Pending` state (`unchanged | set | reset`) so the Save button only sends fields the user actually touched. **Deliberately out of scope** — documented in `codewiki/tenant-policies.md` §4 with a full env-var-by-env-var rationale: `RATE_LIMIT_REQUESTS/WINDOW` (tracked as **ADMIN-02**; slowapi reads its limit string at module import, dynamic per-tenant needs a route-decorator refactor), per-tenant LLM provider API keys (**ADMIN-03**; parallels VERTEX-02 shape, touches every `_call_*` and `stream_*`), and infra-bootstrap vars like `DATABASE_URL` / `SECRET_KEY` / `VAULT_KEY` / `AUTH_MODE` which are intentionally NOT moveable because they're either chicken-and-egg (DB URL) or blast-radius hazards (rotating a signing key through the UI that uses that key to authenticate).
>
> - **VERTEX-02 Per-tenant Vertex project override (2026-04-21)**: Vertex project + location move from process-global settings into per-tenant rows in the existing `tenant_integrations` table (`system='vertex'`, `config_json={project, location}`). No migration — the table was created for AutomationEdge in migration `0017` and the `system` column already takes arbitrary values. `_resolve_vertex_target(tenant_id)` in `llm_providers.py` walks the precedence chain: tenant's `is_default` row → `ORCHESTRATOR_VERTEX_PROJECT` env fallback, with partial-config fill-in (a row specifying only `location` still inherits `project` from env and vice versa). `tenant_id` is now threaded as an optional kwarg through `call_llm`, `call_llm_streaming`, and `react_loop._PROVIDERS[*]["call"]` — openai and anthropic handlers accept and discard it, Google / Vertex handlers pass it to `_google_client` → `genai.Client(vertexai=True, project, location)`. Seven call sites updated (`node_handlers._handle_agent`, `_handle_llm_router`, `intent_classifier._llm_classify`, `entity_extractor._llm_extract`, `reflection_handler._handle_reflection`, `memory_service._llm_checkpoint_summary` × 2). Frontend `VertexProjectsDialog.tsx` reuses the existing tenant-integrations CRUD — no new backend endpoint. **Scope caveat (documented in `codewiki/security.md`)**: ADC is still process-global. Workload identity and `GOOGLE_APPLICATION_CREDENTIALS` are per-process, so the orchestrator's service account needs `aiplatform.user` on every project listed in every tenant's registry. Per-tenant service-account JSON — where tenant A's traffic to Vertex authenticates as SA-A and tenant B's as SA-B — would require runtime ADC swapping + new tenant_secrets storage and is deliberately out of scope here.
>
> - **VERTEX-01 Vertex AI provider (2026-04-21)**: Every LLM-calling node (LLM Agent, ReAct Agent, LLM Router, Reflection, Intent Classifier) now accepts `provider: "vertex"` in addition to `google`, `openai`, `anthropic`. Implementation reuses the existing `google-genai` SDK — the new unified SDK handles both AI Studio (`Client(api_key=...)`) and Vertex AI (`Client(vertexai=True, project=..., location=...)`), so wire format, request shape, response parsing, and tool-calling are all shared. Three call sites refactored: `llm_providers._call_google_backend` + `streaming_llm._stream_google_backend` + `react_loop._google_call_backend` each take a `backend` flag; thin `_call_vertex` / `stream_vertex` / `_vertex_call` wrappers dispatch to them. The returned `provider` field mirrors the flag ("google" or "vertex") so Langfuse traces and execution logs distinguish the two backends. Config reuses `settings.vertex_project` + `settings.vertex_location` (previously embeddings-only); auth is Application Default Credentials via `GOOGLE_APPLICATION_CREDENTIALS` or workload identity — no API key, no vault entry. Env-var validation lives in the `_google_client` factory so a missing `ORCHESTRATOR_VERTEX_PROJECT` fails at dispatch with a specific message. Per-tenant Vertex project override (so tenant A bills to GCP project A and tenant B to project B) is deferred as VERTEX-02 and would ride on the `tenant_integrations` table with `system='vertex'`. 11 unit tests in `test_vertex_provider.py` lock the contract at each call site.
>
> - **API-18A In-app API Playground (2026-04-21)**: `frontend/src/components/toolbar/ApiPlaygroundDialog.tsx` plus two pure helpers (`lib/playgroundCurl.ts`, `lib/playgroundHistory.ts`). No new API endpoints — the dialog calls the existing `POST /api/v1/workflows/{id}/execute` through `api.executeWorkflow`, so all ExecuteRequest fields (`trigger_payload`, `sync`, `sync_timeout`, `deterministic_mode`) are surfaced in-dialog and existing tenant scoping + rate limits + JWT/dev-header auth modes all apply unchanged. Sync responses render `SyncExecuteOut.output` pretty-printed with a client-side elapsed-ms stopwatch; async responses render the `InstanceOut` summary and point the operator at the existing Execution Panel instead of duplicating its SSE UI. History lives in `localStorage` keyed by `aeai:playground:{workflow_id}:history`, capped at 10 entries with a schema-safe loader that drops malformed rows from older writes. The "Copy as curl" snippet honours `VITE_API_URL`, `VITE_TENANT_ID`, and `VITE_AUTH_MODE` (OIDC mode emits a Bearer placeholder instead of the tenant header). Roadmap item #18 is now Partial; 18B chatbot embed widget stays Planned pending a documented security design for the unauthenticated-but-scoped access model (new `workflow_embeds` table with origin allowlist + signed short-lived tokens + per-embed rate-limit overrides + strict CORS/CSP for the embed origin + a Preact widget bundle to keep parent pages off the main React 19 bundle).
>
> - **Sprint 2B MCP Maturity (2026-04-21)**: **MCP-01** — audit of the current MCP client against the 2025-06-18 spec with ranked gap list (OAuth 2.1 resource-server, elicitation, structured tool output / `outputSchema`, tool-definition drift detection, `notifications/tools/list_changed`, `HTTP DELETE` session release, protocol catch-up to `2025-11-25`). Full findings + follow-up tickets in `codewiki/mcp-audit.md`. **MCP-02** — per-tenant MCP server registry. New table `tenant_mcp_servers` (Alembic `0019`) with `auth_mode` discriminator (`none` / `static_headers` / `oauth_2_1`) enforced via CHECK constraint; `config_json` holds mode-specific payload (e.g. `{"headers": {...}}` for static headers, with `{{ env.KEY }}` placeholders resolved through the Secrets vault). Partial unique index `ux_tenant_mcp_server_default` enforces one default per tenant. Forward-declared empty `tenant_mcp_server_tool_fingerprints` side table for MCP-06 drift detection. New resolver `engine/mcp_server_resolver.py::resolve_mcp_server(tenant_id, label)` picks URL + headers (precedence: explicit label → `is_default` row → `settings.mcp_server_url` env fallback). `engine/mcp_client.py` refactored: session pool + `list_tools` cache keyed by `(tenant_id, pool_key)`; `call_tool` / `list_tools` / `get_openai_style_tool_defs` accept optional `tenant_id` + `server_label` kwargs with backward-compatible env fallback. `api/tenant_mcp_servers.py` CRUD router. Frontend `McpServersDialog.tsx` (behind the Globe toolbar icon); `mcpServerLabel` config field added to MCP Tool + ReAct Agent nodes. RLS: `ALTER TABLE tenant_mcp_servers ENABLE ROW LEVEL SECURITY` + `tenant_isolation_tenant_mcp_servers` policy on `current_setting('app.tenant_id')`.
>
> - **Sprint 2A Developer Velocity (2026-04-20)**: Seven incremental commits shortening the edit→run→inspect loop. **DV-01** `dispatch_node` short-circuits when `graph_json.nodes[*].data.pinnedOutput` is a dict — `_from_pin: True` breadcrumb flows through `output_json`, stripped from `context_json` by `_get_clean_context`. Pins live in the workflow definition (survive save / snapshot / restore / duplicate), do NOT bump version. `POST …/nodes/{id}/pin` + `DELETE …/nodes/{id}/pin`. **DV-02** `POST /api/v1/workflows/{wf}/nodes/{id}/test` — `TestNodeResponse { output, elapsed_ms, error }` — runs one handler in isolation using pinned upstream outputs as synthetic `node_X` context; handler exceptions caught and returned as `error`; no workflow_instances / execution_logs rows. One deliberate side effect documented: `NodeSuspendedAsync` still creates a real `async_jobs` row because that's the only way to verify AE connectivity. **DV-03** sticky notes — new `stickyNote` React Flow node type registered in `FlowCanvas.tsx` + `ExecutionFlowView.tsx`; `dag_runner.parse_graph` admits only `type == "agenticNode"` (legacy type-less rows default to `agenticNode`) and drops edges touching filtered nodes; `validateWorkflow`, `computeNodeStatuses`, and `PropertyInspector` all skip stickies. **DV-04** 45 new helpers in `engine/expression_helpers.py` merged into `safe_eval._WHITELISTED_FUNCTIONS` via `**EXPRESSION_HELPERS`; covers strings (18), math (7), arrays (11), objects (4), date/time (11), utility (7); size-capped (`repeat` ≤ 10k × 1MB, `parse_json` ≤ 1MB, `chunk ≥ 1`, `clamp lo ≤ hi`). `**` (`ast.Pow`) and `//` (`ast.FloorDiv`) added to `_BIN_OPS`. `ExpressionHelperError` translated to `SafeEvalError` at the call site so existing callers keep a single except branch. **DV-05** `POST …/duplicate` — deep-copies `graph_json` (including `pinnedOutput`); name uses `"<orig> (copy)"`, then `(copy 2)`, `(copy 3)` on collision; version=1; is_active=True regardless of source flag. **DV-06** `HotkeyCheatsheet.tsx` modal behind `?`; `lib/keyboardUtils.ts::isTextEditingTarget` guards single-key shortcuts so typing into inputs never triggers them; `Shift+S` (add sticky at viewport centre via CustomEvent `aeai:add-sticky`), `1` (fit view), `Tab` (toggle palette), `?` (cheatsheet). **DV-07** `workflow_definitions.is_active` (Alembic `0018`, server_default `TRUE`) — `scheduler.check_scheduled_workflows` adds `.filter(WorkflowDefinition.is_active.is_(True))`. PATCH accepts `{is_active: bool}` but does NOT bump version or snapshot. Toolbar Power/PowerOff button next to version badge. `WorkflowListDialog` renders inactive rows dimmed with a pill.
>
> - **AutomationEdge + async-external (2026-04-19)**: `automationedge_client.py` supports both `ae_session` (username + password) and `bearer` (`{prefix}_TOKEN`) auth modes via `credentialsSecretPrefix`. `async_jobs` table (Alembic `0017`) owns one row per suspended AE node with Diverted-aware timeout: `diverted_since` and `total_diverted_ms` track banked pause-the-clock time; `check_timeout` in `async_job_poller.py` ignores Diverted spans when computing the active-runtime budget. Pattern C (default) — Beat `poll_async_jobs` queries AE's `workflowinstances/{id}` on `next_poll_at <= now()`. Pattern A (opt-in) — AE workflow posts back to `POST /api/v1/async-jobs/{job_id}/complete`. Both modes call `async_job_finalizer.finalize_terminal` so the context shape downstream nodes see is identical. `workflow_instances.suspended_reason='async_external'` distinguishes from HITL (NULL). `tenant_integrations` table (`config_json` system-specific; AE uses `{baseUrl, orgCode, credentialsSecretPrefix, authMode, source, userId}`). `integration_resolver.resolve_integration_config` merges per-node config over tenant defaults by `integrationLabel`. `scheduled_triggers` (Alembic `0015`) replaces the previous 55-second wall-clock Beat dedupe with DB-enforced `UNIQUE(workflow_def_id, scheduled_for)` at minute precision. See `codewiki/automationedge.md`.
>
> - **V0.9.15 Sub-Workflows (2026-04-14)**: **Sub-Workflow** logic node (`sub_workflow` in `node_registry.json`). Executes another saved workflow as a single synchronous step within the parent DAG. Creates a linked child `WorkflowInstance` (`parent_instance_id` / `parent_node_id` columns, Alembic `0011`). Input mapping via `safe_eval` expressions builds child `trigger_payload`; output filtering by child node IDs. Version policy: `latest` (live definition) or `pinned` (snapshot). Recursion protection: `_parent_chain` tracks ancestor workflow IDs; rejects cycles and `maxDepth` violations. Cancellation cascades from parent to child instances. API: `InstanceOut` gains `parent_instance_id` / `parent_node_id`; `InstanceDetailOut` gains `children: list[ChildInstanceSummary]`. Frontend: `WorkflowSelect`, `InputMappingEditor`, `OutputNodePicker` custom widgets in `DynamicConfigForm.tsx`; `Layers` icon + version-policy badge on canvas; drill-down child instance logs in `ExecutionPanel`. Server-side and client-side validation for `workflowId`, `versionPolicy`, `pinnedVersion`, `inputMapping`, `outputNodeIds`.
>
> - **V0.9.14 NLP Nodes (2026-04-15)**: New `nlp` category with **Intent Classifier** (`intent_classifier.py`) and **Entity Extractor** (`entity_extractor.py`). Intent Classifier: hybrid scoring (lexical substring + embedding cosine + optional LLM fallback) with three configurable modes (`hybrid`, `heuristic_only`, `llm_only`); optional save-time embedding precomputation via `embedding_cache_helper.py` into new `embedding_cache` table (Alembic `0010`, pgvector VECTOR column, HNSW index, RLS). Entity Extractor: 5 rule-based types (regex, enum, number, date, free_text) with intent-entity scoping from upstream classifier and optional LLM fallback for missing required entities. Backend: `config_validator.py` validates both nodes on save; `precompute_node_embeddings()` hooked into create/update workflow endpoints. Frontend: `IntentListEditor` / `EntityListEditor` custom components in `DynamicConfigForm.tsx`; `visibleWhen` extended to support boolean values; `nlp` category (indigo) added to palette and canvas styling; validation and expression autocomplete for both nodes.
>
> - **V0.9.13 Tier 1 product UX (2026-04-10)**: **Template gallery** — bundled starter DAGs (`frontend/src/lib/templates/index.ts`), `TemplateGalleryDialog`, toolbar **Templates** button; import/export portable `{nodes, edges}` JSON via `workflowStore.importGraphJson` / `exportCurrentGraph`. **Native synchronous execute** — `POST /{workflow_id}/execute` with `sync: true` runs `execute_graph` inline in a worker thread (`run_in_threadpool` + `asyncio.wait_for`); returns **200** + `SyncExecuteOut` (`instance_id`, `status`, timestamps, `output` context with `_…` keys stripped); default remains **202** + `InstanceOut` + Celery. Request fields `sync_timeout` (5–3600 s, default 120). **Visual debug / replay** — after terminal runs (`completed`, `failed`, `cancelled`, `paused`), Execution panel **Debug** loads checkpoints, timeline scrubber (`DebugReplayBar`), context JSON viewer; canvas node status overlays + indigo ring on the active checkpoint node (`workflowStore` + `AgenticNode`). Hub UI checkbox **Sync run** for local testing. See `SETUP_GUIDE.md` §7.1.2, §4.5 / §6.10 here, `HOW_IT_WORKS.md` Step 6.
>
> - **V0.9.12 A2A Protocol (2026-04-07)**: Google A2A protocol v0.2 inbound and outbound support. **Inbound:** Per-tenant agent card (`GET /tenants/{id}/.well-known/agent.json`) lists `is_published` workflows as skills. JSON-RPC 2.0 dispatcher (`POST /tenants/{id}/a2a`) handles `tasks/send`, `tasks/get`, `tasks/cancel`, `tasks/sendSubscribe` (SSE). Inbound auth via SHA-256-hashed API keys stored in new `a2a_api_keys` table. `WorkflowInstance` status maps to A2A task states: `suspended` → `input-required` (Human Approval integration). **Outbound:** New `A2A Agent Call` action node wraps `app/engine/a2a_client.py` (`fetch_agent_card`, `send_task`, `poll_until_done`). **Key management:** `POST/GET/DELETE /api/v1/a2a/keys`. **Publish toggle:** `PATCH /api/v1/workflows/{id}/publish`. New `A2AApiKey` ORM model. `WorkflowDefinition.is_published` column (Alembic `0007_a2a_support.py`). MCP and A2A coexist — MCP is for tools, A2A is for agent delegation.
>
> - **V0.9.11 Operator execution control (2026-03-22)**: Cooperative **cancel**, **pause**, and **resume** between nodes (the current node always finishes; no mid–LLM-call interrupt). New DB columns on `workflow_instances`: `cancel_requested`, `pause_requested` (Alembic `0005_workflow_cancel_requested.py`, `0006_workflow_pause_requested.py`). `dag_runner` exposes `_finalize_cancelled`, `_finalize_paused`, and `_abort_if_cancel_or_pause` — **cancel wins** if both flags are set. Instance statuses: `cancelled` (terminal, sets `completed_at`), `paused` (operator pause, not HITL — `completed_at` stays null). **API:** `POST /{workflow_id}/instances/{instance_id}/cancel` (queued/running: sets `cancel_requested`; **paused**: immediate `cancelled`), `POST …/pause` (sets `pause_requested`), `POST …/resume-paused` (body optional `context_patch`, Celery `resume_paused_workflow_task` → `resume_paused_graph`). **SSE** (`sse.py`) ends the stream with `done` for `cancelled` and `paused` (same pattern as `suspended`). **Frontend:** `ExecutionPanel` — Pause, Resume (when `paused`), Stop (cooperative cancel while running; **discard** when paused). **`workflowStore`:** `cancelInstance`, `pauseInstance`, `resumePausedInstance`. **Example external client:** `examples/python_client.py` plus any custom bridge that polls `/context`. See §4.5, §5.2, §6.11.
>
> - **V0.9.10 Bridge reply UX + canvas display names (2026-03-22)**: **Bridge User Reply** action node (`bridge_user_reply` in `node_registry.json`) sets the final chat string for any external caller that wants a single user-facing reply: handler `_handle_bridge_user_reply` resolves `messageExpression` (safe_eval) or `responseNodeId` (same pattern as Save Conversation State); `dag_runner._promote_orchestrator_user_reply()` copies non-empty `orchestrator_user_reply` to **context root** after each completed node so `GET …/context` exposes it for polling clients. Frontend: optional `displayName` on `AgenticNodeData` + `nodeCanvasTitle()` for human-friendly canvas titles while **registry `label`** stays the engine key; PropertyInspector splits **Display name** vs **Engine type**; expression picker groups use canvas titles; `validateWorkflow` messages use canvas titles. Example workflows (`exampleOperationsRoutingWorkflow.ts`, `exampleComplexWorkflow.ts`) use `displayName` and per-branch Bridge nodes.
>
> - **Proxy Bridge (2026-03-22)**: External gateways can trigger workflows by supplying a workflow UUID plus trigger payload. **Default async** (enqueue + instance id / poll URLs); optional wait/poll behavior is implemented entirely by the caller. Section 10 documents the generic proxy pattern and `examples/python_client.py` shows a minimal implementation.
>
> - **V0.9.9 Loop Node (2026-03-22)**: New `Loop` logic node for controlled agentic cycles — repeats its downstream body nodes while a `continueExpression` evaluates to True, up to `maxIterations` times (backend hard cap: 25). Uses pre-check semantics (while-loop): condition is evaluated before each iteration; if False on the first check the body never executes. An empty expression runs unconditionally for `maxIterations` iterations. `_handle_loop` in `node_handlers.py` returns `{"continueExpression": ..., "maxIterations": ...}` — analogous to `_handle_forEach`. New `_run_loop_iterations` in `dag_runner.py` drives the iteration: clears body node context keys before each pass, sets `_loop_index` / `_loop_iteration` in context, calls `_execute_single_node` for each body node, accumulates per-node results into `{"loop_results": [...], "iterations": N}` stored back into each body node's context key after completion. Suspension and failure are handled safely: partial aggregated results are stored before returning. `_execute_ready_queue` detects `label == "Loop"` after single-node execution and routes to `_run_loop_iterations` (same pattern as ForEach). `shared/node_registry.json` — new `loop` type in `logic` category with `continueExpression` (required) and `maxIterations` (default 10) config fields. Frontend: `AgenticNode.tsx` adds `RefreshCw` lucide icon under key `"refresh-cw"`; Loop nodes display a `≤N×` badge and a `⟳ {continueExpression}` expression line. `validateWorkflow.ts` adds `"Loop": ["continueExpression"]` to `REQUIRED_FIELDS` and emits a warning if `maxIterations > 25`. No DB migration required.
>
> - **V0.9.8 Rich Token Streaming (2026-03-22)**: LLM Agent nodes now stream tokens to the browser in real time via a Redis pub/sub bridge. New `app/engine/streaming_llm.py` — `stream_google`, `stream_openai`, `stream_anthropic` each call the provider's streaming API, publish every token to `orch:stream:{instance_id}` (Redis channel), and return the same standardised result dict as the non-streaming path. `publish_token(instance_id, node_id, token)` and `publish_stream_end(instance_id, node_id)` are the publish helpers; failures are non-fatal (warning + skip). `llm_providers.py` gains `call_llm_streaming(...)` that routes to streaming variants when `instance_id` and `node_id` are non-empty; falls back to `call_llm` otherwise. `dag_runner.execute_graph` injects `_instance_id` into the shared context; `_execute_single_node` injects `_current_node_id` before each dispatch. `node_handlers._handle_agent` now calls `call_llm_streaming` (with graceful fallback). `sse.py` updated — `_subscribe_tokens` coroutine runs as a background `asyncio.Task` using `redis.asyncio`, subscribes to the instance channel, drains into an `asyncio.Queue`; the polling loop emits `event: token` SSE events from the queue before each DB poll; clean teardown of the Redis task on disconnect or done. Frontend: `api.ts` `streamInstance` gains optional `onToken` callback listening for `event: token`; `workflowStore` gains `streamingTokens: Record<string, string>` state (accumulated per node_id, cleared on execution start/done); `ExecutionPanel` passes `streamingTokens[log.node_id]` to each `LogEntry`; running nodes show a pulsing blue dot + live text preview under the expanded section. Uses `redis>=5.0.0` (already in requirements) — no new dependency. No DB migration required.
>
> - **V0.9.7 Checkpoint-aware Langfuse (2026-03-22)**: `_save_checkpoint()` now returns the checkpoint UUID string (or `None` on failure) instead of `None`. `span_node()` in `observability.py` gains an optional `checkpoint_id: str | None = None` kwarg — when provided it is written into the Langfuse span's `metadata` dict under `"checkpoint_id"`, linking the trace directly to the DB snapshot. In `_execute_single_node` (sequential path), the returned `checkpoint_id` is captured and passed to `span.update(output={..., "checkpoint_id": checkpoint_id})` while the span is still open. In `_execute_parallel._apply_result` (parallel path), the span has already exited by the time `_apply_result` runs, so the `checkpoint_id` is instead embedded in `log_entry.output_json` under the `"_checkpoint_id"` key — it remains queryable via the execution log API. This gives a complete checkpoint→trace link: sequential nodes via Langfuse metadata, parallel nodes via execution log output. No DB migration required.
>
> - **V0.9.6 Checkpointing Threads (2026-03-22)**: New `instance_checkpoints` table (Alembic migration `0004_instance_checkpoints.py`). One row is written per successfully completed node: `instance_id` (FK cascade-delete), `node_id`, `context_json` (full context with `_`-prefixed internal keys stripped), `saved_at`. `_save_checkpoint()` helper in `dag_runner.py` is called in both `_execute_single_node` (after `db.commit()`) and `_apply_result` inside `_execute_parallel` (after output is written to context). Failures in `_save_checkpoint` are non-fatal — a warning is logged and execution continues. New API endpoints: `GET /{workflow_id}/instances/{instance_id}/checkpoints` (list, `CheckpointOut` — no context payload) and `GET /{workflow_id}/instances/{instance_id}/checkpoints/{checkpoint_id}` (`CheckpointDetailOut` — includes `context_json`). `InstanceCheckpoint` SQLAlchemy model added to `workflow.py`. Schemas `CheckpointOut` / `CheckpointDetailOut` added to `schemas.py`. **Frontend (V0.9.13):** checkpoint list/detail consumed by the Hub **Debug** replay UI; still used for Langfuse linking (V0.9.7) and external tooling. Indexes: `(instance_id)` and `(instance_id, node_id)`.
>
> - **V0.9.5 Reflection Node (2026-03-22)**: New `Reflection` agent node that calls an LLM with an auto-built summary of the workflow's execution history and expects a structured JSON response. Handler in `app/engine/reflection_handler.py` — `_build_execution_summary()` collects the most recent N `node_*` keys from context (hard cap 25, configurable via `maxHistoryNodes`), truncates each to 800 chars to prevent token explosion, and injects the trigger payload. `reflectionPrompt` is a Jinja2 template with `{{ execution_summary }}` available alongside all normal context variables. `_parse_json_response()` strips markdown fences, falls back to regex `{...}` extraction, and returns `{"reflection": raw, "parse_error": True}` as a last resort. `outputKeys` warns (non-blocking) if any expected top-level keys are absent from the response. Node registered in `shared/node_registry.json` under category `agent`. Dispatch added in `node_handlers.py` via label match `"Reflection"`. Frontend: `reflectionPrompt` added to `REQUIRED_FIELDS` in `validateWorkflow.ts`; `_raw_response` added to `NODE_OUTPUT_FIELDS` in `expressionVariables.ts`. Node is intentionally read-only — it never mutates the shared context; downstream Condition nodes route on its returned JSON fields (e.g., `node_X.next_action == "escalate"`). Full Langfuse observability via `record_generation`. No DB migration required.
>
> - **V0.9.4 HITL UX (2026-03-22)**: Full Human-in-the-Loop review UI. New `GET /api/v1/workflows/{wf_id}/instances/{inst_id}/context` endpoint returns `InstanceContextOut` — the live `context_json` (internal `_`-prefixed keys stripped) plus the `approvalMessage` extracted from the suspended node's config. `CallbackRequest` gains an optional `context_patch: dict` field — a shallow-merge applied to the instance context before resuming, enabling operators to override specific node outputs without rerunning earlier nodes. `resume_graph` and `resume_workflow_task` both thread `context_patch` through. Frontend: new `HITLResumeDialog` component shows the approval message, a read-only scrollable context JSON viewer, and an editable JSON textarea for the patch; "Approve & Resume" and "Reject" buttons. `ExecutionPanel` shows a yellow "Review & Resume" button in the header when `status === "suspended"`. `workflowStore` gains `instanceContext` state plus `fetchInstanceContext` and `resumeInstance` actions. No DB migration required.
>
> - **V0.9.3 Deterministic Batch Semantics (2026-03-22)**: Added opt-in `deterministic_mode` flag to `ExecuteRequest`. When `true`, `_execute_parallel` sorts the ready-node batch by node ID before submitting to `ThreadPoolExecutor` and processes futures in submission order (instead of `as_completed`) so execution logs are written in a stable, reproducible sequence every run. The `execute_graph` and `_execute_ready_queue` signatures accept `deterministic_mode: bool = False`; `execute_workflow_task` forwards it through Celery. A `deterministic` Langfuse tag is added to the root trace when the flag is active. No DB migration required. Frontend `api.ts` `executeWorkflow` accepts an optional third `deterministicMode` parameter. Default (`false`) preserves existing as-completed throughput behaviour — no breaking changes.
>
> - **V0.9.2 UX Improvements (2026-03-21)**: Execution log UX — `JsonBlock` component adds Copy button (clipboard + 2s checkmark) and Expand button (opens `FullJsonDialog` with full scrollable JSON) to every input/output block in `ExecutionPanel`; "polling…" label corrected to "streaming…". Palette search — filter input in `NodePalette` hides non-matching categories, auto-expands matching ones, shows `n/total` count per category, clears with ✕ button. Validation highlighting on node cards — `useNodeValidation` hook runs `validateWorkflow()` reactively on every canvas change; `AgenticNode` applies red ring + `AlertCircle` icon for errors, yellow ring + `AlertTriangle` for warnings; selection ring always takes priority. MCP Tool node `toolName` field replaced with `ToolSingleSelect` — searchable list with tool title, description, safety tier badge, and clear button; live from `/api/v1/tools`. Expression variable picker (`src/lib/expressionVariables.ts`, `ExpressionInput.tsx`) — autocomplete dropdown on condition, *Expression, *NodeId, and systemPrompt fields; three modes (expression / nodeId / jinja2); cursor-aware token detection; keyboard navigation; fixed-position portal dropdown. Pre-run workflow validation (`src/lib/validateWorkflow.ts`) — checks for missing trigger, disconnected nodes, required empty fields (condition, url, toolName, arrayExpression, responseNodeId), and broken node-ID cross-references (responseNodeId, historyNodeId). `ValidationDialog` surfaces errors and warnings before execution; hard errors block run, warnings allow "Run Anyway". `Toolbar.tsx` now calls `validateWorkflow()` on every Run click. Undo/Redo — `flowStore.ts` gains `past[]`/`future[]` snapshot arrays (max 50) with `_pushHistory()` called before every destructive canvas action; `FlowCanvas.tsx` registers Ctrl+Z/Ctrl+Y/Ctrl+Shift+Z global keyboard handlers; Toolbar shows Undo/Redo buttons with disabled state when history is empty. Node ID chip — `PropertyInspector.tsx` now shows the node's machine ID (e.g., `node_3`) in a monospace chip at the top of the panel with a one-click copy button (2s checkmark confirmation), so users can easily reference nodes in expressions like `node_3.intent`. Inline field help text — every `config_schema` property in `node_registry.json` now carries a `description` string; `DynamicConfigForm.tsx` renders these as `text-[10px] text-muted-foreground` subtext below each field via a `FieldHint` helper, covering all nine renderer branches (enum, array, object, boolean, number, ToolMultiSelect, ToolSingleSelect, ExpressionInput, plain input). ForEach/Merge canvas clarity — `AgenticNode` now renders a `waitAll`/`waitAny` strategy badge for Merge nodes (same slot as the agent model badge) and a `↻ arrayExpression` monospace line below the badge row for ForEach nodes when the expression is set, so both nodes are interpretable without opening the properties panel.
>
> - **V0.9.1 Stateful DAGs (2026-03-21)**: Added robust Stateful Re-Trigger DAG Pattern. Added `ConversationSession` PostgreSQL table with Alembic migration `0003_conversation_sessions.py` + unique index `(tenant_id, session_id)`. Added REST APIs in `conversations.py` (`GET /api/v1/conversations`, `GET /{id}`, `DELETE /{id}`). Exposes 3 new conversational memory nodes in `node_registry.json`: `Load Conversation State`, `Save Conversation State`, and `LLM Router`.
> - **V0.9 Execution Enhancements (2026-03-21)**: ForEach loop node (`_handle_forEach`, `_run_forEach_iterations`) — iterates downstream subgraph per array element. Retry from failed node (`retry_graph()`, `POST /{id}/instances/{iid}/retry`). MCP connection pooling (`_MCPSessionPool`). Enhanced safe expression evaluator with whitelisted function/method calls (`len`, `lower`, `matches`, etc.). Snapshot pruning via Celery Beat (`prune_old_snapshots`, `ORCHESTRATOR_MAX_SNAPSHOTS`). Environment variable mapping (`{{ env.SECRET_NAME }}` resolved from vault). Langfuse parallel context fix — explicit trace propagation into threads. Frontend `retryInstance` action.
> - **V0.8 Enterprise Features (2026-03-20)**: Dynamic property forms generated from `shared/node_registry.json` schemas (`DynamicConfigForm.tsx`) — PropertyInspector no longer hardcoded. ReAct agent auto-discovers all MCP tools when `tools` config is empty; MCP tool cache upgraded to 5-minute TTL with `POST /api/v1/tools/invalidate-cache`. Workflow versioning: `workflow_snapshots` table + Alembic migration 0002; snapshot saved before each overwrite; `GET /{id}/versions` and `POST /{id}/rollback/{v}` endpoints; `VersionHistoryDialog` with Restore button in Toolbar. OIDC federation: Authorization Code + PKCE flow (`app/api/auth.py`), `authlib` for ID token validation, Redis PKCE state, issues internal JWT; frontend `LoginPage` + `VITE_AUTH_MODE=oidc` gate in `App.tsx`.

## AE AI Hub — Agentic Orchestrator Technical Blueprint

**Advanced Memory note:** Advanced Memory v1 adds normalized conversation storage, rolling summaries, memory profiles, semantic or episodic memory, relational entity facts, and memory inspection APIs. See `codewiki/memory-management.md`.

**Version:** 0.9.18 (Sprint 2A + 2B)
**Last updated:** 2026-04-21
**Status:** V0.9.16 Advanced Memory v1; V0.9.15 Sub-Workflows; V0.9.14 NLP Nodes (Intent Classifier + Entity Extractor); V0.9.13 Template gallery + sync execute + debug replay; V0.9.12 A2A Protocol; V0.9.11 Operator cancel/pause/resume; V0.9.10 Bridge User Reply + sync reply formatting + `displayName`; V0.9.9 Loop Node; V0.9.8 Rich Token Streaming; V0.9.7 Checkpoint-aware Langfuse; V0.9.6 Checkpointing; V0.9.5 Reflection; V0.9.4 HITL UX; V0.9.3 Deterministic batch; V0.9.2 UX; V0.9.1 Stateful DAGs; V0.9 execution; V0.8 enterprise; earlier milestones through V0.1
> - **V0.7 Observability, MCP Streaming & Tenant Tools (2026-03-20)**: Langfuse v4 integration (`app/observability.py`) — root trace per workflow execution, child spans per node, LLM generation recording with token usage, tool call spans. MCP client rewritten to use MCP Python SDK with Streamable HTTP transport (`app/engine/mcp_client.py`) — replaces raw httpx REST bridge with standard MCP protocol. Tool listing and ReAct tool definitions now fetched live from MCP server. TenantToolOverride consumed by tools endpoint to filter MCP tools per tenant.
>
> - **V0.6 Advanced Agent Capabilities (2026-03-20)**: ReAct iterative tool-calling loop (`app/engine/react_loop.py`) with multi-provider support (Google/OpenAI/Anthropic tool-calling APIs). SSE real-time execution updates (`app/api/sse.py`) replacing frontend polling. Celery Beat cron scheduler (`app/workers/scheduler.py`) for schedule triggers with croniter. Frontend palette now hydrated from `shared/node_registry.json` via `src/lib/registry.ts`. Backend config validation against registry schemas on save (`app/engine/config_validator.py`).
>
> - **V0.5 Production Hardening (2026-03-20)**: JWT-based auth with tenant claims (`app/security/jwt_auth.py`, dev-mode header fallback). Fernet-encrypted credential vault (`app/security/vault.py` + `TenantSecret` model). AST-based safe expression evaluator replaces `eval()` (`app/engine/safe_eval.py`). PostgreSQL RLS migration for tenant isolation (`alembic/versions/0001`). Per-tenant rate limiting via slowapi and execution quotas (`app/security/rate_limiter.py`). See §8 for updated security docs.
>
> - **V0.4 Branching & Parallel Execution (2026-03-20)**: Rewrote `dag_runner.py` with a ready-queue execution model. Condition nodes now prune non-matching branches (only `true` or `false` edges are followed). Independent branches execute in parallel via `ThreadPoolExecutor`. Merge nodes naturally wait for all upstream branches. Frontend edges from condition nodes show colored labels (green "Yes" / red "No") with arrow markers. See §6 for updated DAG engine docs.
>
> - **V0.3 Live LLM Integration (2026-03-20)**: Agent nodes now call real LLM providers (Google Gemini via `google-genai`, OpenAI, Anthropic). Added `app/engine/llm_providers.py` multi-provider abstraction, `app/engine/prompt_template.py` Jinja2 system-prompt templating with context variable injection (dot-accessible upstream outputs), and token usage tracking in execution logs. New config keys: `ORCHESTRATOR_GOOGLE_API_KEY`, `ORCHESTRATOR_OPENAI_API_KEY`, `ORCHESTRATOR_ANTHROPIC_API_KEY`. See `SETUP_GUIDE.md` §7 for configuration.
>
> - **V0.2 UI Wiring (2026-03-20)**: Added frontend API client + workflow toolbar (save/load/execute), a saved-workflow list dialog, and an execution log panel with polling against backend instance status. See `HOW_IT_WORKS.md` for runtime walkthrough.
> - **Initial Scaffold (2026-03-20)**: V0.1 — React Flow visual builder (frontend), FastAPI DAG execution engine (backend), Zustand state management, shadcn/ui component library, SQLAlchemy data models with multi-tenant isolation, Celery worker stubs, and MCP tool bridge. See `SETUP_GUIDE.md` for installation and `HOW_IT_WORKS.md` for runtime walkthrough.

---

### Table of Contents

1. [Purpose and Scope](#1-purpose-and-scope)
2. [Architecture Overview](#2-architecture-overview)
3. [Frontend: Visual DAG Builder](#3-frontend-visual-dag-builder)
4. [Backend: Execution Engine](#4-backend-execution-engine)
5. [Data Models](#5-data-models)
6. [DAG Execution Engine](#6-dag-execution-engine)
7. [MCP Tool Bridge (Streamable HTTP)](#7-mcp-tool-bridge-streamable-http)
8. [Multi-Tenancy and Security](#8-multi-tenancy-and-security)
9. [Observability (Langfuse)](#9-observability-langfuse)
10. [Integration with External Gateways (Proxy Pattern)](#10-integration-with-external-gateways-proxy-pattern)
11. [Shared Schemas](#11-shared-schemas)
12. [Known Limitations (V0.8)](#12-known-limitations-v08)
13. [Roadmap](#13-roadmap)

---

## 1. Purpose and Scope

The AE AI Hub is a **portable workflow orchestration service**. It provides a **no-code visual builder** for constructing agentic workflows as Directed Acyclic Graphs (DAGs), replacing the need for hardcoded Python pipelines.

It runs as an independent service pair (React frontend + FastAPI backend) and can be extracted into its own repository without code changes. It consumes any configured MCP server as a client.

**Documentation set:** `SETUP_GUIDE.md` (install and migrations), `HOW_IT_WORKS.md` (operator-facing steps), `DEVELOPER_GUIDE.md` (extending nodes, safe_eval, execution-control internals, debugging).

**Runtime boundaries:**

| Concern | Provided by orchestrator | External dependency |
|---------|--------------------------|---------------------|
| Frontend | React Flow visual canvas | Static hosting or Vite dev server |
| Backend | FastAPI + Celery execution engine | Python runtime |
| Tools | MCP client + tool palette API | Any Streamable HTTP MCP server |
| State | SQLAlchemy workflow state/log models | PostgreSQL |
| Async execution | In-process threads or Celery tasks | Optional Redis/Celery worker |
| LLM | Multi-provider agent nodes | Provider API keys |

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Browser (port 8080)                          │
│  ┌──────────────┬───────────────────────┬───────────────────────┐   │
│  │ Node Palette │     React Flow        │  Property Inspector   │   │
│  │ (drag src)   │     Canvas            │  (config panel)       │   │
│  │              │  ┌─────┐   ┌─────┐    │                       │   │
│  │  Triggers    │  │Trig │──▶│Agent│──┐ │  LLM Provider: [v]    │   │
│  │  Agents      │  └─────┘   └─────┘  │ │  Model:        [v]    │   │
│  │  Actions     │         ┌─────┐     │ │  System Prompt: [  ]  │   │
│  │  Logic       │         │Action│◀───┘ │  Temperature:   [0.7] │   │
│  │              │         └─────┘       │                       │   │
│  └──────────────┴───────────────────────┴───────────────────────┘   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ REST (JSON)
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                  FastAPI Gateway (port 8001)                         │
│                                                                      │
│  POST /api/v1/workflows          — Save graph JSON                   │
│  POST /api/v1/workflows/{id}/execute  — 202 + Celery, or 200 if sync:true │
│  POST /api/v1/workflows/{id}/instances/{iid}/callback — HITL resume  │
│  POST /api/v1/workflows/{id}/instances/{iid}/pause|resume-paused|cancel │
│  GET  /api/v1/workflows/{id}/status   — Execution logs               │
│  GET  /api/v1/tools                   — MCP palette hydration        │
└────────────────────┬─────────────────────────────────────────────────┘
                     │ Celery task
                     ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    Celery Worker                                     │
│                                                                      │
│  1. Parse graph JSON → adjacency list                                │
│  2. Topological sort (Kahn's algorithm)                              │
│  3. Execute nodes sequentially:                                      │
│     Trigger → Agent (LLM) → Action (MCP tool) → Logic (branch)      │
│  4. Human Approval? → suspend, serialize, wait for callback          │
│  5. Store per-node ExecutionLog                                      │
└────────────────────┬─────────────────────────────────────────────────┘
                     │ httpx
                     ▼
┌─────────────────────────────────┐    ┌────────────────────────────┐
│  MCP Server (port 3000)         │    │  PostgreSQL                │
│  106 tools (existing)           │    │  workflow_definitions      │
│  /call-tool                     │    │  workflow_instances        │
└─────────────────────────────────┘    │  execution_logs            │
                                       │  tenant_tool_overrides     │
                                       └────────────────────────────┘
```

---

## 3. Frontend: Visual DAG Builder

**Stack:** React 19, TypeScript, Vite 8, `@xyflow/react` 12, Zustand 5, Tailwind CSS 4, shadcn/ui.

### 3.1 Directory Layout

```
frontend/src/
├── App.tsx                         # Root layout — three-panel flex
├── main.tsx                        # Entry point — StrictMode + CSS
├── index.css                       # Tailwind + shadcn theme tokens
├── store/
│   └── flowStore.ts                # Zustand: nodes, edges, selection, CRUD
├── types/
│   └── nodes.ts                    # NodeCategory, AgenticNodeData, NODE_PALETTE
├── components/
│   ├── canvas/
│   │   └── FlowCanvas.tsx          # ReactFlow wrapper + drop handler
│   ├── nodes/
│   │   └── AgenticNode.tsx         # Polymorphic custom node
│   ├── sidebar/
│   │   ├── NodePalette.tsx         # Left: draggable node categories
│   │   └── PropertyInspector.tsx   # Right: node config forms
│   ├── toolbar/
│   │   ├── Toolbar.tsx             # Save, Run, Templates, sync-run checkbox, …
│   │   ├── ExecutionPanel.tsx      # Logs, pause/cancel, HITL, Debug replay entry
│   │   ├── TemplateGalleryDialog.tsx
│   │   └── DebugReplayBar.tsx      # Checkpoint timeline + context JSON
│   └── ui/                         # shadcn components
└── lib/
    ├── templates/index.ts          # Bundled workflow templates (marketplace)
    ├── exampleComplexWorkflow.ts   # Still imported by templates
    ├── exampleOperationsRoutingWorkflow.ts
    └── utils.ts                    # cn() utility
```

### 3.2 State Management (Zustand)

File: `store/flowStore.ts`

The single Zustand store manages all canvas state:

| State | Type | Purpose |
|-------|------|---------|
| `nodes` | `Node[]` | React Flow node objects |
| `edges` | `Edge[]` | React Flow edge connections |
| `selectedNodeId` | `string \| null` | Currently selected node for inspector |
| `past` | `Snapshot[]` | Undo history stack (max 50 entries) |
| `future` | `Snapshot[]` | Redo history stack (max 50 entries) |
| `_draggingNodeIds` | `Set<string>` | Tracks in-flight drag operations to avoid duplicate snapshots |

| Action | Signature | Description |
|--------|-----------|-------------|
| `onNodesChange` | `OnNodesChange` | React Flow node change handler; snapshots before drag-start and remove |
| `onEdgesChange` | `OnEdgesChange` | React Flow edge change handler; snapshots before remove |
| `onConnect` | `OnConnect` | New edge creation; always snapshots before connecting |
| `addNode` | `(category, label, position, config?) → void` | Create node at canvas position; snapshots before creation |
| `selectNode` | `(id \| null) → void` | Set selection for property inspector |
| `updateNodeData` | `(id, partial) → void` | Merge data updates from inspector forms |
| `deleteNode` | `(id) → void` | Remove node and connected edges; snapshots before deletion |
| `undo` | `() → void` | Restore previous canvas state from `past` stack |
| `redo` | `() → void` | Replay next state from `future` stack |
| `_pushHistory` | `() → void` | Internal: push current `{nodes, edges}` snapshot to `past`, clear `future` |

### 3.3 Node Categories and Palette

File: `types/nodes.ts`

Four categories, each with distinct visual styling:

| Category | Color | Border | Nodes |
|----------|-------|--------|-------|
| **Trigger** | Amber | `border-amber-500/60` | Webhook Trigger, Schedule Trigger |
| **Agent** | Violet | `border-violet-500/60` | LLM Agent, ReAct Agent |
| **Action** | Sky | `border-sky-500/60` | MCP Tool, HTTP Request, Human Approval, Bridge User Reply, Load/Save Conversation State |
| **Logic** | Emerald | `border-emerald-500/60` | Condition, Merge |

The `NODE_PALETTE` array defines every draggable item with its `nodeCategory`, `label`, `description`, `icon`, and `defaultConfig`. A **search input** at the top of the palette filters by label and description: non-matching categories are hidden, matching categories auto-expand, and each category header shows a `matched/total` count while a query is active.

### 3.4 Custom Node Component

File: `components/nodes/AgenticNode.tsx`

A single `memo`-ized component renders all node types polymorphically:

- **Visual:** shadcn `Card` with category-colored border, icon badge, label, category pill, model badge (agents), strategy badge (Merge), `↻ arrayExpression` line (ForEach when set), and a status/validation indicator.
- **Handles:** Target (left) on all except Triggers. Source (right) on all except Merge. Condition nodes get two source handles (`true` in green, `false` in red) at 35%/65% vertical offset.
- **Icons:** Mapped via `ICON_MAP` from Lucide icon names stored in `config.icon`.
- **Validation indicators** (design-time, via `useNodeValidation` hook):
  - `red ring + AlertCircle` — node has a hard configuration error (empty required field, broken node ID ref)
  - `yellow ring + AlertTriangle` — node is disconnected from all triggers (warning)
  - `blue ring` — node is selected (always takes priority over validation rings)
  - `status dot` — runtime execution status (shown when no validation issue)

#### Canvas display names (`displayName`, V0.9.10)

File: `types/nodes.ts` (`AgenticNodeData`, `nodeCanvasTitle()`)

| Field | Role |
|-------|------|
| `label` | **Required.** Must match a palette/registry type (e.g. `Condition`, `ReAct Agent`). Used for backend dispatch, `getConfigSchema(label)`, and execution logs. |
| `displayName` | **Optional.** Human title on the node card; defaults to `label` when unset. The card’s HTML `title` tooltip still shows `label` so operators can verify the engine type. |

**PropertyInspector** separates **Display name (canvas)** from **Engine type (registry)**. **validateWorkflow** and **expression variable picker** group labels use `nodeCanvasTitle()` (friendly title when set). Shipped examples in `src/lib/exampleOperationsRoutingWorkflow.ts` and `exampleComplexWorkflow.ts` set `displayName` per role (e.g. “Route message to specialist”, “Chat reply · diagnostics”).

### 3.5 Pre-Run Workflow Validation

Files: `src/lib/validateWorkflow.ts`, `src/components/toolbar/ValidationDialog.tsx`

Before any execution begins, `validateWorkflow(nodes, edges)` is called by the Toolbar's Run handler. It returns an array of `ValidationError` objects, each with:

| Field | Type | Description |
|-------|------|-------------|
| `nodeId` | `string` | ID of the offending node (empty for graph-level errors) |
| `nodeLabel` | `string` | Friendly name (`nodeCanvasTitle`, i.e. `displayName` or `label`) |
| `message` | `string` | Description of the problem |
| `severity` | `"error" \| "warning"` | Errors block execution; warnings allow "Run Anyway" |

**Checks performed (in order):**

1. **No trigger** — workflow must have at least one Trigger category node
2. **Reachability (BFS)** — every node must be reachable from a trigger via edges; orphaned nodes produce a warning
3. **Required fields** — per node label, specific fields must be non-empty:
   - `Condition` → `condition`
   - `HTTP Request` → `url`
   - `MCP Tool` → `toolName`
   - `ForEach` → `arrayExpression`
   - `Save Conversation State` → `responseNodeId`
   - `LLM Router` → `intents` array must have ≥ 1 entry
   - `Reflection` → `reflectionPrompt`
   - `Bridge User Reply` → at least one of `messageExpression` or `responseNodeId`
4. **Node ID cross-references** — `responseNodeId` (Save Conversation State, Bridge User Reply), `historyNodeId` (LLM Router, Intent Classifier), and `scopeFromNode` (Entity Extractor), when set, must match an existing node ID

`ValidationDialog` presents errors in red and warnings in yellow. If only warnings exist, a **Run Anyway** button is offered. Hard errors disable execution entirely until fixed.

### 3.6 Expression Variable Picker

Files: `src/lib/expressionVariables.ts`, `src/components/sidebar/ExpressionInput.tsx`

Fields that accept runtime expressions get an autocomplete dropdown instead of a plain text input. The dropdown is positioned with `position: fixed` (portal to `document.body`) so it is never clipped by the sidebar's `ScrollArea`.

**Three rendering modes** selected by `DynamicConfigForm` per field key:

| Mode | Format | Fields |
|------|--------|--------|
| `expression` | `node_2.intent` | `condition`, `arrayExpression`, `continueExpression`, `sessionIdExpression`, `userMessageExpression`, `messageExpression` |
| `nodeId` | `node_3` | `responseNodeId`, `historyNodeId` |
| `jinja2` | `{{ node_2.response }}` | `systemPrompt` |

**Known output fields per node type** (defined in `expressionVariables.ts`):

| Node | Suggested outputs |
|------|-------------------|
| Webhook Trigger | `trigger.body`, `trigger.message`, `trigger.session_id`, `trigger.headers`, `trigger.method`, `trigger.path` |
| Schedule Trigger | `trigger.scheduled_at`, `trigger.cron` |
| LLM Agent | `response`, `usage`, `provider`, `model`, `memory_debug` |
| ReAct Agent | `response`, `iterations`, `total_iterations`, `usage`, `memory_debug` |
| LLM Router | `intent`, `raw_response`, `usage`, `memory_debug` |
| Reflection | `_raw_response` (+ any user-defined `outputKeys` at runtime) |
| MCP Tool | `result` |
| HTTP Request | `status_code`, `body`, `headers` |
| Human Approval | `approved`, `approver` |
| Bridge User Reply | `orchestrator_user_reply`, `text`, `source`, `memory_debug` |
| Load Conversation State | `session_id`, `session_ref_id`, `messages`, `message_count`, `summary_text`, `summary_through_turn` |
| Save Conversation State | `saved`, `session_id`, `session_ref_id`, `message_count`, `summary_updated`, `promoted_memory_records`, `promoted_entity_facts` |

**Token detection:** `getCurrentToken()` walks backward from the cursor to the last word boundary (`space`, `(`, `=`, `!`, `<`, `>`, `,`, `"`) and uses that substring as the filter. `insertAtCursor()` replaces only the current token, preserving the rest of the expression.

**Keyboard shortcuts:** ArrowUp/Down to navigate, Enter or Tab to insert, Escape to close.

### 3.7 Property Inspector

File: `components/sidebar/PropertyInspector.tsx`

**Display name (canvas)** and **Engine type (registry)** — optional friendly title vs required registry `label` (see §3.4.1).

**Config form** — `DynamicConfigForm` renders fields from `getConfigSchema(data.label)` + `getRegistryNodeType().type` (not a fixed table per category). Typical fields include provider/model/prompts for agents, webhook path or cron for triggers, tool/URL/approval/bridge fields for actions, condition or merge/loop/forEach for logic.

All fields write back to the store via `updateNodeData`. A "Delete Node" button removes the selected node.

**Node ID chip** — at the top of the panel a `bg-muted` chip displays the node's machine ID (e.g., `node_3`) in a monospace font. A copy button (`Copy` icon → 2s `Check` icon) writes the ID to the clipboard so it can be pasted into expression fields on other nodes.

**Inline field help text** — `DynamicConfigForm` reads the optional `description` field from each `config_schema` entry and renders it as `<FieldHint>` (10px muted grey text) below the input. All renderer branches emit a hint when a description is present.

### 3.8 Drag-and-Drop Flow

1. `NodePalette` items set `onDragStart` → `dataTransfer.setData("application/reactflow", JSON.stringify({nodeCategory, label, defaultConfig}))`.
2. `FlowCanvas` handles `onDragOver` (preventDefault) and `onDrop`.
3. On drop, the canvas reads the transfer data, converts screen coordinates via `reactFlowInstance.screenToFlowPosition()`, and calls `flowStore.addNode()`.

---

## 4. Backend: Execution Engine

**Stack:** Python, FastAPI, SQLAlchemy 2, Alembic, Celery (Redis), httpx, Pydantic v2.

### 4.1 Directory Layout

```
backend/
├── main.py                         # FastAPI app, CORS, routers, health
├── alembic.ini                     # Migration config
├── requirements.txt                # Python dependencies
├── alembic/
│   ├── env.py                      # Migration environment
│   ├── script.py.mako              # Migration template
│   └── versions/                   # Migration files (empty — no DB yet)
└── app/
    ├── config.py                   # Pydantic Settings (env-driven)
    ├── database.py                 # SQLAlchemy engine, session, Base
    ├── observability.py            # Langfuse v4 traces, spans, generations
    ├── api/
    │   ├── schemas.py              # Pydantic request/response models
    │   ├── workflows.py            # CRUD + execute + callback + status
    │   ├── tools.py                # MCP tool bridge for palette
    │   └── sse.py                  # Server-Sent Events for real-time execution updates
    ├── engine/
    │   ├── dag_runner.py           # Ready-queue DAG executor with branching + parallelism
    │   ├── node_handlers.py        # Per-type dispatch (trigger/agent/action/logic)
    │   ├── llm_providers.py        # Multi-provider LLM abstraction (Google/OpenAI/Anthropic)
    │   ├── react_loop.py           # ReAct iterative tool-calling loop for agent nodes
    │   ├── mcp_client.py           # MCP SDK client (Streamable HTTP transport)
    │   ├── prompt_template.py      # Jinja2 system-prompt templating with context injection
    │   ├── safe_eval.py            # AST-based safe expression evaluator for conditions
    │   └── config_validator.py     # Validates node configs against node_registry.json
    ├── models/
    │   ├── workflow.py             # WorkflowDefinition, WorkflowInstance, ExecutionLog
    │   └── tenant.py              # TenantToolOverride
    ├── workers/
    │   ├── celery_app.py           # Celery configuration
    │   ├── tasks.py                # execute_workflow_task, resume_workflow_task
    │   └── scheduler.py            # Celery Beat cron scheduler for schedule triggers
    └── security/
        ├── tenant.py              # Re-exports get_tenant_id for backward compat
        ├── jwt_auth.py            # JWT creation + validation with tenant claim
        ├── vault.py               # Fernet-encrypted credential vault + TenantSecret model
        └── rate_limiter.py        # Per-tenant rate limiting + execution quotas
```

### 4.2 Configuration

File: `app/config.py`

| Setting | Env Variable | Default |
|---------|-------------|---------|
| `database_url` | `ORCHESTRATOR_DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/ae_orchestrator` |
| `redis_url` | `ORCHESTRATOR_REDIS_URL` | `redis://localhost:6379/0` |
| `mcp_server_url` | `ORCHESTRATOR_MCP_SERVER_URL` | `http://localhost:8000/mcp` |
| `secret_key` | `ORCHESTRATOR_SECRET_KEY` | `change-me-in-production` |
| `cors_origins` | `ORCHESTRATOR_CORS_ORIGINS` | `["http://localhost:8080"]` |
| `google_api_key` | `ORCHESTRATOR_GOOGLE_API_KEY` | `""` |
| `google_project` | `ORCHESTRATOR_GOOGLE_PROJECT` | `""` |
| `google_location` | `ORCHESTRATOR_GOOGLE_LOCATION` | `us-central1` |
| `openai_api_key` | `ORCHESTRATOR_OPENAI_API_KEY` | `""` |
| `openai_base_url` | `ORCHESTRATOR_OPENAI_BASE_URL` | `https://api.openai.com/v1` |
| `anthropic_api_key` | `ORCHESTRATOR_ANTHROPIC_API_KEY` | `""` |
| `auth_mode` | `ORCHESTRATOR_AUTH_MODE` | `dev` |
| `vault_key` | `ORCHESTRATOR_VAULT_KEY` | `""` |
| `rate_limit_requests` | `ORCHESTRATOR_RATE_LIMIT_REQUESTS` | `100` |
| `rate_limit_window` | `ORCHESTRATOR_RATE_LIMIT_WINDOW` | `1 minute` |
| `execution_quota_per_hour` | `ORCHESTRATOR_EXECUTION_QUOTA_PER_HOUR` | `50` |

### 4.3 LLM Provider Abstraction

File: `app/engine/llm_providers.py`

The `call_llm()` function routes to one of three provider backends based on the
node's `config.provider` value:

| Provider | SDK | Default Model | Config Key |
|----------|-----|---------------|------------|
| `google` | `google-genai` | `gemini-2.5-flash` | `ORCHESTRATOR_GOOGLE_API_KEY` |
| `openai` | `openai` | `gpt-4o` | `ORCHESTRATOR_OPENAI_API_KEY` |
| `anthropic` | `anthropic` | `claude-sonnet-4-20250514` | `ORCHESTRATOR_ANTHROPIC_API_KEY` |

Each provider returns a standardized response:

```python
{
    "response": str,          # LLM text output
    "usage": {
        "input_tokens": int,  # tracked per node in ExecutionLog
        "output_tokens": int,
    },
    "model": str,
    "provider": str,
}
```

### 4.4 Jinja2 Prompt Templating

File: `app/engine/prompt_template.py`

System prompts support Jinja2 template syntax with upstream context injection.
All execution context keys are available as top-level template variables with
dot-access to nested fields:

```jinja2
You are an IT support assistant for {{ trigger.customer_name }}.
The user reported: {{ trigger.user_query }}
Ticket status from ServiceNow: {{ node_1.output.status }}
Recent logs: {{ node_2.body | truncate(500) }}
```

Missing variables resolve to empty strings instead of raising errors, allowing
prompts to be reusable across different workflow topologies.

### 4.5 API Endpoints

**Workflow CRUD** (prefix: `/api/v1/workflows`)

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| `POST` | `/` | 201 | Create a workflow definition |
| `GET` | `/` | 200 | List workflows for tenant |
| `GET` | `/{workflow_id}` | 200 | Get single workflow |
| `PATCH` | `/{workflow_id}` | 200 | Update name/description/graph (bumps version) |
| `DELETE` | `/{workflow_id}` | 204 | Delete workflow and cascade instances |
| `POST` | `/{workflow_id}/execute` | 202 / 200 | **202:** create instance, enqueue Celery (`InstanceOut`). **200:** if body has `sync: true` — run `execute_graph` inline (thread pool + timeout), return `SyncExecuteOut` with final `output` (see `ExecuteRequest` in `schemas.py`) |
| `POST` | `/{workflow_id}/instances/{instance_id}/callback` | 200 | Resume **suspended** (HITL) instance; optional `context_patch` |
| `POST` | `/{workflow_id}/instances/{instance_id}/retry` | 200 | Retry **failed** instance (`RetryRequest`) |
| `POST` | `/{workflow_id}/instances/{instance_id}/pause` | 200 | Request cooperative **pause** after current node (`pause_requested`) |
| `POST` | `/{workflow_id}/instances/{instance_id}/resume-paused` | 200 | Resume **paused** run (`ResumePausedRequest`, optional `context_patch`) |
| `POST` | `/{workflow_id}/instances/{instance_id}/cancel` | 200 | Request **cancel** after current node, or abandon **paused** run |
| `GET` | `/{workflow_id}/status` | 200 | List execution instances (limit 50) |
| `GET` | `/{workflow_id}/instances/{instance_id}` | 200 | Instance detail with execution logs |
| `GET` | `/{workflow_id}/instances/{instance_id}/checkpoints` | 200 | List per-node checkpoints (no context payload) |
| `GET` | `/{workflow_id}/instances/{instance_id}/checkpoints/{checkpoint_id}` | 200 | Checkpoint detail with full context snapshot |

**Tools** (prefix: `/api/v1/tools`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List all MCP tools from parent `tool_specs.py` |

**Conversations** (prefix: `/api/v1/conversations`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | List all conversation sessions for tenant (summaries) |
| `GET` | `/{session_id}` | Full message history for a session |
| `DELETE` | `/{session_id}` | Delete session (next DAG run auto-recreates) |

These endpoints still return transcript-style payloads, but the backing storage is now normalized `conversation_messages` plus `conversation_sessions` metadata.

**Advanced Memory APIs**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/memory-profiles` | List tenant and workflow memory profiles |
| `POST` | `/api/v1/memory-profiles` | Create a memory profile |
| `GET` | `/api/v1/memory-profiles/{profile_id}` | Fetch a memory profile |
| `PUT` | `/api/v1/memory-profiles/{profile_id}` | Update a memory profile |
| `DELETE` | `/api/v1/memory-profiles/{profile_id}` | Delete a memory profile |
| `GET` | `/api/v1/memory/records` | List semantic and episodic memory rows |
| `GET` | `/api/v1/memory/entity-facts` | List entity facts |
| `GET` | `/api/v1/memory/instances/{instance_id}/resolved` | Resolve the exact memory rows used by logged agent, router, or classifier runs |

**A2A Inbound** (per-tenant, auth: Bearer A2A key)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/tenants/{tenant_id}/.well-known/agent.json` | Agent card — lists published workflows as skills (no auth) |
| `POST` | `/tenants/{tenant_id}/a2a` | JSON-RPC 2.0 dispatcher: `tasks/send`, `tasks/get`, `tasks/cancel`, `tasks/sendSubscribe` |

**A2A Key Management** (prefix: `/api/v1/a2a/keys`, auth: standard tenant credentials)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/` | Generate inbound A2A key — raw key shown once |
| `GET` | `/` | List keys (no key material) |
| `DELETE` | `/{key_id}` | Revoke key immediately |

**A2A Publish Toggle** (extends `/api/v1/workflows`)

| Method | Path | Description |
|--------|------|-------------|
| `PATCH` | `/{workflow_id}/publish` | Set `is_published` true/false |

**Health**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Returns `{"status": "ok", "service": "ae-ai-hub-orchestrator"}` |

All workflow/tool endpoints require the `X-Tenant-Id` request header for tenant isolation.

**Execute request body** (`ExecuteRequest`): `trigger_payload`, `deterministic_mode`, **`sync`** (default `false`), **`sync_timeout`** (seconds, 5–3600, default 120). Synchronous runs bypass Celery for that HTTP request even when `ORCHESTRATOR_USE_CELERY=true`; use only for short, API-first callers. Timeout → **504**.

---

## 5. Data Models

File: `app/models/workflow.py`, `app/models/tenant.py`

### 5.1 WorkflowDefinition

Stores the visual graph designed in the React Flow canvas.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` (PK) | Auto-generated |
| `tenant_id` | `VARCHAR(64)` | Indexed. RLS discriminator |
| `name` | `VARCHAR(256)` | Workflow display name |
| `description` | `TEXT` | Optional |
| `graph_json` | `JSONB` | Full React Flow export `{nodes: [], edges: []}` |
| `version` | `INTEGER` | Bumped on each graph update |
| `is_published` | `BOOLEAN` | When `True`, listed in the A2A agent card as a skill |
| `created_at` | `TIMESTAMPTZ` | Auto |
| `updated_at` | `TIMESTAMPTZ` | Auto on update |

Index: `(tenant_id, name)`. Migration `0007_a2a_support.py` adds `is_published`.

### 5.2 WorkflowInstance

One row per execution run of a workflow definition.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` (PK) | Auto-generated |
| `tenant_id` | `VARCHAR(64)` | Indexed |
| `workflow_def_id` | `UUID` (FK) | References `workflow_definitions.id` |
| `status` | `VARCHAR(32)` | `queued` → `running` → `completed` / `failed` / `suspended` / `paused` / `cancelled` |
| `trigger_payload` | `JSONB` | Input data from webhook/schedule |
| `context_json` | `JSONB` | Accumulated node outputs during execution |
| `current_node_id` | `VARCHAR(128)` | Last node executed (for resume) |
| `started_at` | `TIMESTAMPTZ` | Set when worker picks up |
| `completed_at` | `TIMESTAMPTZ` | Set on completion, failure, or **cancelled** (not on `paused` / `suspended`) |
| `created_at` | `TIMESTAMPTZ` | Auto |
| `cancel_requested` | `BOOLEAN` | Set by `POST …/cancel` (worker clears when finalizing `cancelled`) — migration `0005` |
| `pause_requested` | `BOOLEAN` | Set by `POST …/pause` (worker clears when finalizing `paused`) — migration `0006` |
| `parent_instance_id` | `UUID` (FK, nullable) | Parent instance for sub-workflow children — cascade delete — migration `0011` |
| `parent_node_id` | `VARCHAR(128)` (nullable) | Node ID in parent workflow that spawned this child — migration `0011` |

Indexes: `(tenant_id, status)`, `(parent_instance_id)`.

### 5.3 ExecutionLog

Per-node execution trace within a workflow instance.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` (PK) | Auto-generated |
| `instance_id` | `UUID` (FK) | References `workflow_instances.id` |
| `node_id` | `VARCHAR(128)` | React Flow node ID (e.g. `node_3`) |
| `node_type` | `VARCHAR(64)` | Format: `category:label` (e.g. `agent:LLM Agent`) |
| `status` | `VARCHAR(32)` | `pending` → `running` → `completed` / `failed` / `suspended` |
| `input_json` | `JSONB` | Node input (config + upstream outputs) |
| `output_json` | `JSONB` | Node return value |
| `error` | `TEXT` | Error message on failure |
| `started_at` | `TIMESTAMPTZ` | |
| `completed_at` | `TIMESTAMPTZ` | |

### 5.4 ConversationSession

Persistent session metadata for the Stateful Re-Trigger Pattern.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` (PK) | Auto-generated |
| `session_id` | `VARCHAR(256)` | Unique conversational thread ID |
| `tenant_id` | `VARCHAR(64)` | Indexed |
| `message_count` | `INTEGER` | Total normalized turns |
| `last_message_at` | `TIMESTAMPTZ` | Latest turn timestamp |
| `summary_text` | `TEXT` | Rolling summary of older turns |
| `summary_updated_at` | `TIMESTAMPTZ` | Last summary refresh |
| `summary_through_turn` | `INTEGER` | Highest turn index already summarized |
| `created_at` | `TIMESTAMPTZ` | Auto |
| `updated_at` | `TIMESTAMPTZ` | Auto on update |

Index: `(tenant_id, session_id)` (Unique).

### 5.5 ConversationMessage

Normalized append-only conversation turns.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` (PK) | Auto-generated |
| `session_ref_id` | `UUID` (FK) | References `conversation_sessions.id` |
| `tenant_id` | `VARCHAR(64)` | Indexed |
| `session_id` | `VARCHAR(256)` | External session ID copied for lookup/debug |
| `turn_index` | `INTEGER` | Stable ordering within the session |
| `role` | `VARCHAR(32)` | Usually `user` or `assistant` |
| `content` | `TEXT` | Message text |
| `message_at` | `TIMESTAMPTZ` | Turn timestamp |
| `workflow_def_id` | `UUID` | Nullable provenance |
| `instance_id` | `UUID` | Nullable provenance |
| `node_id` | `VARCHAR(128)` | Nullable provenance |
| `idempotency_key` | `VARCHAR(128)` | Retry dedupe key |
| `created_at` | `TIMESTAMPTZ` | Auto |

Indexes: `(session_ref_id, turn_index)` unique, `(session_ref_id, idempotency_key, role)` unique.

### 5.6 MemoryProfile

Tenant- or workflow-scoped advanced-memory policy.

Key fields: `workflow_def_id`, `is_default`, `instructions_text`, `enabled_scopes`, `max_recent_tokens`, `max_semantic_hits`, `include_entity_memory`, `summary_*`, `history_order`, `semantic_score_threshold`, embedding config, and `entity_mappings_json`.

### 5.7 MemoryRecord

Semantic and episodic memory rows with inline pgvector embeddings.

Key fields: `scope`, `scope_key`, `kind`, `content`, `metadata_json`, provenance fields, `dedupe_key`, `embedding_provider`, `embedding_model`, `vector_store`, and `embedding`.

### 5.8 EntityFact

Relational entity memory with last-write-wins semantics.

Key fields: `entity_type`, `entity_key`, `fact_name`, `fact_value`, `confidence`, `valid_from`, `valid_to`, `superseded_by`, and provenance fields.

The partial unique index on active facts guarantees one active fact per `(tenant_id, entity_type, entity_key, fact_name)` where `valid_to IS NULL`.

### 5.9 InstanceCheckpoint

Point-in-time snapshot of the execution context after each successful node completion. Used for post-mortem debugging and as the foundation for checkpoint-aware Langfuse tracing (V0.9.7).

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` (PK) | Auto-generated |
| `instance_id` | `UUID` (FK) | References `workflow_instances.id` — cascade delete |
| `node_id` | `VARCHAR(128)` | The node that just completed |
| `context_json` | `JSONB` | Full execution context at that moment (internal `_`-prefixed keys stripped) |
| `saved_at` | `TIMESTAMPTZ` | Auto |

Indexes: `(instance_id)`, `(instance_id, node_id)`.

Migration: `alembic/versions/0004_instance_checkpoints.py`

### 5.10 TenantToolOverride

Per-tenant MCP tool visibility and configuration overrides.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` (PK) | Auto-generated |
| `tenant_id` | `VARCHAR(64)` | |
| `tool_name` | `VARCHAR(256)` | MCP tool name |
| `enabled` | `BOOLEAN` | Show/hide tool in palette for this tenant |
| `config_json` | `JSONB` | Tenant-specific parameter defaults |

Unique index: `(tenant_id, tool_name)`.

### 5.7 A2AApiKey

Hashed inbound API keys issued to external A2A agents per tenant. Only the SHA-256 digest is stored — a DB breach cannot expose working credentials.

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` (PK) | Auto-generated |
| `tenant_id` | `VARCHAR(64)` | Indexed |
| `label` | `VARCHAR(128)` | Human-readable name, e.g. `teams-bot`. Unique per tenant. |
| `key_hash` | `VARCHAR(64)` | SHA-256 hex of the raw 32-byte key |
| `created_at` | `TIMESTAMPTZ` | Auto |

Indexes: `(tenant_id)`, `(key_hash)` unique.  
Constraints: `UNIQUE (tenant_id, label)`.  
Migration: `alembic/versions/0007_a2a_support.py`

---

## 6. DAG Execution Engine

File: `app/engine/dag_runner.py`

### 6.1 Graph Parsing (Handle-Aware)

`parse_graph(graph_json)` converts React Flow JSON into two structures:

- `nodes_map`: `{node_id: node_dict}` — the full node object including `data`.
- `edges`: list of `_Edge(source, target, source_handle)` — preserves `sourceHandle` from React Flow edges (e.g. `"true"` / `"false"` for condition outputs).

`_build_graph_structures()` derives forward adjacency, reverse adjacency, and in-degree maps from the parsed edges.

### 6.2 Cycle Detection

`_detect_cycles()` runs Kahn's algorithm over the graph to verify it is a valid DAG before execution begins.

### 6.3 Ready-Queue Execution Model

Instead of a simple linear topological order, the engine uses a **ready-queue** model that naturally supports branching and parallelism:

```
┌───────────────────────────────────────────────────────────────┐
│  1. Find all nodes with in_degree == 0 → initial ready set   │
│  2. While ready set is non-empty:                             │
│     a. If 1 ready node  → execute sequentially                │
│     b. If N ready nodes → execute in parallel (ThreadPool)    │
│     c. After each node completes:                             │
│        - If CONDITION node → propagate only matching branch   │
│          edges; prune the non-matching subtree                │
│        - Otherwise → propagate all outgoing edges             │
│     d. Recompute ready set (nodes with all incoming edges     │
│        satisfied, not pruned, not yet executed)                │
│  3. Mark instance completed/failed                            │
└───────────────────────────────────────────────────────────────┘
```

### 6.4 Branch Pruning

When a Condition node evaluates to `{"branch": "true"}`:

1. Edges with `sourceHandle == "true"` → satisfied (downstream nodes may become ready).
2. Edges with `sourceHandle == "false"` → target and its entire subtree are **pruned** (never executed).
3. Pruned nodes are excluded from the ready-set and the completion check.

This ensures only the chosen branch executes, matching the visual flow on the canvas.

### 6.5 Parallel Execution

When multiple nodes are ready simultaneously (e.g. two branches after a fan-out):

- A `ThreadPoolExecutor` (max 8 workers) runs their handlers concurrently.
- Each node writes to a unique key in the shared context dict (`context[node_id]`), so no locking is needed.
- `ExecutionLog` entries are created before dispatch and updated after all futures complete.
- If any parallel node fails or suspends, the engine stops after the current batch.

#### Deterministic Mode (V0.9.3)

By default, `as_completed` is used so results are processed as threads finish — maximising throughput. Set `deterministic_mode: true` in the execute request to enable stable ordering:

| Aspect | Default (`false`) | Deterministic (`true`) |
|--------|-------------------|------------------------|
| Node submission order | Arbitrary list order | Sorted by node ID |
| Result processing order | Completion order (`as_completed`) | Submission order (`.result()` in order) |
| Log write order | Non-deterministic across runs | Stable across runs |
| Throughput | Maximum | Slightly reduced for large batches |
| Use case | Production | Debugging, replay, test assertions |

A `deterministic` tag is added to the Langfuse root trace when the flag is active.

### 6.6 Merge / Wait-All

Merge nodes have multiple incoming edges. Under the ready-queue model, a merge node only becomes ready when **all** non-pruned upstream edges are satisfied — implementing wait-all semantics naturally without special-case code.

### 6.7 Resume Flow

`resume_graph(db, instance_id, approval_payload, context_patch=None)`:

1. Load suspended instance, inject `approval_payload` into context under key `"approval"`.
2. If `context_patch` is provided, apply it with `context.update(context_patch)` — shallow-merge overrides specific context keys before re-entering the ready queue.
3. Re-parse the graph and mark already-executed nodes (from context keys) as skipped.
4. Re-run `_execute_ready_queue()`, which finds the next ready nodes and continues.

#### HITL Context Inspection (V0.9.4)

`GET /api/v1/workflows/{wf_id}/instances/{inst_id}/context` returns `InstanceContextOut`:

| Field | Description |
|-------|-------------|
| `instance_id` | UUID of the instance |
| `status` | Current instance status (always `suspended` when useful) |
| `current_node_id` | ID of the node at which execution is paused |
| `approval_message` | `approvalMessage` from that node's config, if set |
| `context_json` | Full execution context with `_`-prefixed internal keys stripped |

The `CallbackRequest` body sent to `POST /{wf_id}/callback` accepts an optional `context_patch` field alongside `approval_payload`. Any keys in `context_patch` are shallow-merged into the context before the workflow resumes.

### 6.8 Node Handlers

File: `app/engine/node_handlers.py`

`dispatch_node()` routes by `nodeCategory`:

| Category | Handler | Behavior |
|----------|---------|----------|
| `trigger` | `_handle_trigger` | Pass through `context["trigger"]` |
| `agent` | `_handle_agent` | Render Jinja2 prompt, call LLM provider (Google/OpenAI/Anthropic), return response + token usage |
| `action` | `_handle_action` | Routes to MCP tool call, HTTP request, or no-op based on config keys |
| `logic` | `_handle_logic` | Evaluates condition expressions (returns `{branch: "true"|"false"}`) or merges upstream outputs |

**Special-label dispatches** override category routing for nodes identified by their `label` string:

| Label | Handler | Notes |
|-------|---------|-------|
| `ForEach` | `_handle_forEach` | Returns `{items, itemVariable}`; DAG runner drives iteration |
| `Load Conversation State` | `_handle_load_conversation_state` | Fetches or creates a session and returns messages plus summary metadata |
| `Save Conversation State` | `_handle_save_conversation_state` | Appends normalized turns, refreshes summary, promotes entity facts, and promotes episodic memory for successful outputs |
| `LLM Router` | `_handle_llm_router` | Classification call, returns `{intent}` |
| `Reflection` | `_handle_reflection` (in `reflection_handler.py`) | Builds execution summary, calls LLM, parses JSON — read-only |
| `Bridge User Reply` | `_handle_bridge_user_reply` | Resolves `messageExpression` (safe_eval) or `responseNodeId` → `{orchestrator_user_reply, text, source, memory_debug}` |
| `Sub-Workflow` | `_handle_sub_workflow` → `_execute_sub_workflow` | Loads child definition, recursion check, builds input mapping, creates child `WorkflowInstance`, runs `execute_graph` inline, returns child outputs |

**`orchestrator_user_reply` promotion (V0.9.10):** After any node completes successfully, `dag_runner._promote_orchestrator_user_reply(context, output)` copies a non-empty string `output["orchestrator_user_reply"]` onto **context root** (`context["orchestrator_user_reply"]`). The API strips only `_*` keys, so this field is visible in `GET …/instances/{id}/context` and is the first source a polling client should use when it wants a single user-facing reply. Place one Bridge node per terminal branch when multiple LLM paths exist so the chat text is explicit (avoids longest-response heuristics picking the wrong node). Example workflows demonstrate this pattern.

**MCP tool invocation:** `app/engine/mcp_client.py` uses the MCP Python SDK over Streamable HTTP and calls `session.call_tool(tool_name, arguments=...)`.

**HTTP request:** `_call_http()` makes arbitrary HTTP requests via httpx with a 30s timeout.

### 6.9 Reflection Node

File: `app/engine/reflection_handler.py`

The Reflection node lets the workflow reason about its own execution so far and return a decision or assessment that downstream Condition nodes can route on.

**Execution summary builder (`_build_execution_summary`):**
- Collects all `node_*` keys from context in insertion (execution) order
- Takes the last `min(maxHistoryNodes, 25)` entries — the hard cap prevents token explosion regardless of user config
- Serializes each value with `json.dumps(indent=2, default=str)` and truncates to 800 chars
- Prepends the `trigger` payload if present

**Prompt rendering:**
- `reflectionPrompt` is a Jinja2 template; `{{ execution_summary }}` injects the history block; all other context variables are available too
- If the rendered prompt is empty, a safe default is substituted rather than calling the LLM blind
- The user message always includes a JSON-only instruction plus the summary (and lists `outputKeys` if configured)

**JSON parsing (`_parse_json_response`):**
1. Strip markdown code fences (` ```json ... ``` `)
2. `json.loads()` — if dict, return as-is; if non-object, wrap as `{"reflection": value}`
3. Regex `{...}` extraction fallback
4. Last resort: `{"reflection": raw, "parse_error": True}`

**Return value:** `{**parsed, "_usage": usage, "_raw_response": raw_response}`

The handler is strictly read-only — it never mutates the shared `context` dict. The dag_runner stores the returned dict under the node's own key (e.g., `context["node_5"]`), from which downstream nodes read `node_5.next_action`, `node_5.confidence`, etc.

### 6.10 Checkpointing

File: `app/engine/dag_runner.py` (`_save_checkpoint`), `app/models/workflow.py` (`InstanceCheckpoint`)

After every successful node completion the engine calls `_save_checkpoint(db, instance_id, node_id, context)`, which:
1. Strips all keys whose names begin with `_` (internal runtime keys such as `_trace`, `_loop_item`)
2. Creates an `InstanceCheckpoint` row with the cleaned context snapshot
3. Calls `db.commit()` — the checkpoint is immediately durable
4. If the write fails (e.g., DB connectivity blip), logs a warning and calls `db.rollback()` — the checkpoint failure never propagates back to the execution path

**Where it is called:**
- `_execute_single_node` — after `log_entry.completed_at` is written and the first `db.commit()` succeeds
- `_apply_result` (inside `_execute_parallel`) — after `context[node_id] = output` in the `"completed"` branch

**Why not in ForEach iterations?** ForEach re-executes downstream nodes once per item; `_execute_single_node` is reused for each iteration, so checkpoints are naturally saved per iteration at the same call site.

**API surface:**
- `GET /instances/{id}/checkpoints` → `list[CheckpointOut]` — id, instance_id, node_id, saved_at (no context payload for brevity)
- `GET /instances/{id}/checkpoints/{checkpoint_id}` → `CheckpointDetailOut` — adds `context_json`

**Hub UI (V0.9.13):** After a terminal run, **Debug** in `ExecutionPanel` fetches the checkpoint list and steps through snapshots; `flowStore.updateNodeData` sets per-node `status` for completed vs current checkpoint; `AgenticNode` shows an indigo ring on the checkpoint-under-inspection node.

**Langfuse linking (V0.9.7):** `_save_checkpoint` returns the checkpoint UUID. For sequential nodes (`_execute_single_node`), the id is passed to `span.update(output={..., "checkpoint_id": ...})` while the Langfuse span is still open — the span metadata in the Langfuse UI directly references the DB row. For parallel nodes (`_apply_result`), the Langfuse span has already closed; the checkpoint_id is instead embedded in `log_entry.output_json["_checkpoint_id"]`, remaining queryable via the execution log API. `span_node()` accepts an optional `checkpoint_id` kwarg for callers that can supply it at span creation time.

### 6.11 Operator execution control (V0.9.11)

File: `app/engine/dag_runner.py`, `app/workers/tasks.py`, `app/api/workflows.py`, `app/api/sse.py`

Operators can **pause** a run (resume later), **cancel** cooperatively (stop after the current node), or **discard** a **paused** run. This is distinct from **HITL suspension** (`suspended` + `POST …/callback`): pause uses `paused` + `POST …/resume-paused`.

| Mechanism | API | Worker | Terminal status |
|-----------|-----|--------|-------------------|
| Pause (between nodes) | `POST …/pause` → `pause_requested` | `_abort_if_cancel_or_pause` → `_finalize_paused` | `paused` |
| Resume from pause | `POST …/resume-paused` (`ResumePausedRequest`) | `resume_paused_workflow_task` → `resume_paused_graph` | `running` → … |
| Cancel (between nodes) | `POST …/cancel` → `cancel_requested` | `_finalize_cancelled` | `cancelled` |
| Abandon paused run | `POST …/cancel` when status is `paused` | synchronous in API | `cancelled` |

**Semantics:** Checks run at the same points as cooperative cancel (top of ready-queue iterations, after single-node and parallel batches, inside ForEach/Loop inner loops). **Cancel is evaluated before pause** on each check. A parallel batch cannot be interrupted mid-batch; the next check runs before the following batch.

**Celery tasks:** `execute_workflow_task`, `resume_workflow_task` (HITL), `retry_workflow_task`, `resume_paused_workflow_task` (operator pause).

**Example external client:** `examples/python_client.py` — minimal execute + poll flow. Any client can call the same REST endpoints directly.

---

## 7. MCP Tool Bridge (Streamable HTTP)

Files: `app/engine/mcp_client.py`, `app/api/tools.py`

The orchestrator connects to a configured MCP server using the **MCP Python SDK** over **Streamable HTTP** transport — the standard MCP protocol, not a custom REST bridge.

### 7.1 Architecture

```
Orchestrator (FastAPI / Celery)          MCP Server (FastMCP)
┌───────────────────────────┐           ┌──────────────────────┐
│ mcp_client.call_tool()    │  HTTP     │ --transport           │
│ mcp_client.list_tools()   │ ───────▶ │   streamable-http     │
│                           │  /mcp     │   --port 8000         │
│ Uses:                     │           │                       │
│  streamablehttp_client()  │ ◀─────── │ SSE response stream   │
│  ClientSession            │           │                       │
└───────────────────────────┘           └──────────────────────┘
```

### 7.2 MCP Client Module

`app/engine/mcp_client.py` provides:

| Function | Purpose |
|----------|---------|
| `call_tool(name, args)` | Invoke an MCP tool; returns parsed JSON result |
| `list_tools()` | List all available tools with schemas (cached) |
| `get_openai_style_tool_defs(names)` | Convert MCP tool schemas to OpenAI function-calling format for LLM providers |

All functions are synchronous wrappers around the async MCP SDK client, safe to call from Celery workers and FastAPI sync endpoints.

### 7.3 Tool Listing API

The `/api/v1/tools` endpoint uses `mcp_client.list_tools()` to fetch tools directly from the running MCP server:

```json
{
  "name": "ae.request.get_status",
  "title": "Get Request Status",
  "description": "Retrieve the current status of an AE request",
  "category": "status",
  "safety_tier": "safe_read",
  "tags": ["request", "status"]
}
```

Tools are filtered per-tenant using `TenantToolOverride` records (V0.7). The frontend uses this endpoint to hydrate the Action node palette with real MCP tools.

### 7.4 Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `ORCHESTRATOR_MCP_SERVER_URL` | `http://localhost:8000/mcp` | MCP server Streamable HTTP endpoint |

The MCP server must be running with `--transport streamable-http`.

---

## 8. Multi-Tenancy and Security

### 8.1 Authentication

File: `app/security/jwt_auth.py`

The `get_tenant_id()` dependency supports two modes controlled by `ORCHESTRATOR_AUTH_MODE`:

| Mode | Header | Behavior |
|------|--------|----------|
| `dev` (default) | `X-Tenant-Id` | Extracts tenant from plain header — for local development only |
| `jwt` | `Authorization: Bearer <token>` | Validates HS256-signed JWT, extracts `tenant_id` claim |

A development-only `/auth/token?tenant_id=xxx` endpoint generates test JWTs.
In production, tokens are issued by the organization's identity provider.

### 8.2 Database-Level Tenant Isolation (RLS)

Migration: `alembic/versions/0001_enable_rls_policies.py`

PostgreSQL Row-Level Security policies enforce that every query only sees rows
belonging to the current tenant. The application sets `app.tenant_id` via
`SET LOCAL` at the start of each database session.

Tables with RLS: `workflow_definitions`, `workflow_instances`, `tenant_tool_overrides`, `tenant_secrets`.

This provides defense-in-depth on top of application-level `WHERE tenant_id = ...` filtering.

### 8.3 Encrypted Credential Vault

File: `app/security/vault.py`

Per-tenant secrets (LLM API keys, SaaS credentials) are stored in the
`tenant_secrets` table encrypted at rest using Fernet symmetric encryption.
The vault key is set via `ORCHESTRATOR_VAULT_KEY`.

```python
from app.security.vault import encrypt_secret, decrypt_secret

ciphertext = encrypt_secret("sk-my-openai-key")
plaintext  = decrypt_secret(ciphertext)
```

### 8.4 Safe Expression Evaluator

File: `app/engine/safe_eval.py`

Condition node expressions are evaluated by an AST-walking evaluator that
**only** allows: comparisons, boolean ops, arithmetic, variable lookups,
attribute/subscript access on dict values, and literals. Function calls,
imports, `exec`, `eval`, and all other code execution are rejected.

### 8.5 Rate Limiting and Execution Quotas

File: `app/security/rate_limiter.py`

Two levels of protection:

| Level | Mechanism | Config |
|-------|-----------|--------|
| API request rate | slowapi (backed by Redis) | `ORCHESTRATOR_RATE_LIMIT_REQUESTS` / `ORCHESTRATOR_RATE_LIMIT_WINDOW` |
| Execution quota | DB count of recent instances | `ORCHESTRATOR_EXECUTION_QUOTA_PER_HOUR` |

The execute endpoint checks the hourly quota before creating a new instance,
returning HTTP 429 if the tenant has exceeded their limit.

---

## 9. Observability (Langfuse)

File: `app/observability.py`

The orchestrator integrates with Langfuse v4 (OpenTelemetry-based) for full execution tracing.
It uses the standard `LANGFUSE_*` environment variables.

### 9.1 Trace Hierarchy

```
workflow:My Workflow            ← root trace (trace_workflow)
├── node:Webhook Trigger        ← child span (span_node)
├── node:LLM Agent              ← child span
│   └── llm:google/gemini-2.5   ← generation (record_generation)
├── node:Condition              ← child span
├── node:MCP Tool               ← child span
│   └── tool:get_request_status  ← tool span (span_tool)
└── node:HTTP Request           ← child span
```

### 9.2 What Gets Recorded

| Observation | Type | Data |
|-------------|------|------|
| Workflow execution | Root trace | workflow_id, instance_id, tenant_id, trigger payload, final status |
| Node execution | Span | node_id, node_type, input config, output/error |
| LLM call | Generation | provider, model, system prompt, user message, response, token usage |
| Tool call | Tool span | tool_name, arguments, result |
| ReAct iteration | Nested generations + tool spans | Per-iteration tool calls and LLM responses |

### 9.3 Compatibility

The module follows a few simple observability patterns:
- Lazy singleton initialization via `get_langfuse()`
- `_NoOpSpan` stub when Langfuse is disabled — callers never need null checks
- All operations wrapped in try/except — Langfuse failures never break execution
- `atexit` shutdown hook registered in `main.py`
- `flush()` called after each workflow completes

---

## 10. Integration with External Gateways (Proxy Pattern)

Any external caller can act as a **dumb proxy** to the orchestrator. The caller receives or constructs a workflow UUID and trigger payload, calls the orchestrator directly over HTTP, and optionally polls for completion without involving another LLM hop.

### Architecture

```
External caller                 Orchestrator
┌─────────────────────┐         ┌────────────────────────────┐
│ Scheduler / webhook │ POST    │ FastAPI backend            │
│ chat gateway /      │ execute │                            │
│ another workflow    │ ───────▶│ queues or runs workflow    │
│                     │         │                            │
│ poll /context       │◀───────▶│ returns status + context   │
│ or callback API     │         │                            │
└─────────────────────┘         └────────────────────────────┘
```

### Invocation contract

The caller needs only a workflow UUID plus trigger payload:

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `workflow_id` | `str` (UUID) | Yes | UUID of the saved workflow to execute |
| `trigger_payload` | `dict` | No | Input passed to the DAG as `trigger_payload` |
| `sync` | `bool` | No | If true, execute inline and return terminal status in one HTTP response |
| `sync_timeout` | `int` | No | Max seconds to wait for a sync run (default: 120) |

**Default behavior:** **Async** — `POST /execute` returns an instance id immediately and the caller decides whether to poll `/context`, subscribe to SSE, or resume later through the callback API.

### Sync completion text (chat callers)

When a caller wants a single end-user reply, it should prefer `context_json.orchestrator_user_reply` and fall back to node outputs:

1. **`completed`** — If `context_json.orchestrator_user_reply` is set (usually via **Bridge User Reply** in the DAG), return that string. Otherwise fall back to the most relevant LLM/ReAct `response` or a truncated `context_json` dump.
2. **`suspended`** — Return a short **Human approval required** lead-in, instance id, node id, resume instructions, then truncated context JSON.

**DAG recommendation:** Add **Bridge User Reply** immediately before **Save Conversation State** on each customer-facing branch so sync callers receive the intended answer in one reply. Async mode still returns only queue metadata unless a separate poller posts the result back to the channel.

### Key files

| File | Role |
|------|------|
| `app/api/workflows.py` | Execute, callback, pause/resume/cancel, and context endpoints |
| `examples/python_client.py` | Minimal execute + poll client for external callers |
| `backend/.env.example` | Backend runtime settings for auth, MCP, DB, Redis, and providers |

### What the bridge does NOT do

- **No extra LLM hop.** The caller is only routing HTTP requests.
- **No auto-resume for HITL.** The caller must still POST `/callback` or use the Hub UI.
- **No free chat formatting.** If the caller wants a user-facing string, it must choose how to interpret the returned context.

### When to use this pattern

Use the proxy pattern when the **workflow UUID and payload are already known** at call time — for example from a cron scheduler, inbound webhook, or another workflow.

---

## 11. A2A Protocol (Agent-to-Agent)

File: `app/api/a2a.py`, `app/engine/a2a_client.py`

### 11.1 Architecture

```
External Agent                     This Orchestrator (tenant: acme)
──────────────                     ────────────────────────────────

GET  /tenants/acme/.well-known/agent.json          (no auth)
  ← agent card listing published workflows as skills

POST /tenants/acme/a2a   Bearer <a2a-key>          (A2A auth)
  body: {"jsonrpc":"2.0","method":"tasks/send","params":{...}}
  → creates WorkflowInstance, enqueues Celery task
  ← Task {id, status:{state:"submitted"}}

POST /tenants/acme/a2a   {"method":"tasks/get","params":{"id":"..."}}
  ← Task {status:{state:"working"|"completed"|"input-required"}}

POST /tenants/acme/a2a   {"method":"tasks/sendSubscribe",...}
  ← SSE: event:task (status updates) + event:artifact (final output)
```

### 11.2 Status Mapping

| WorkflowInstance.status | A2A state | Notes |
|---|---|---|
| `queued` | `submitted` | |
| `running` | `working` | |
| `completed` | `completed` | Final LLM response in `artifacts[0]` |
| `suspended` | `input-required` | Human Approval waiting — message in `status.message` |
| `failed` | `failed` | |
| `cancelled` | `canceled` | |

### 11.3 Inbound Authentication

`POST /tenants/{tenant_id}/a2a` requires `Authorization: Bearer <raw_key>`. The server hashes the key with SHA-256 and looks it up in `a2a_api_keys` filtered by `tenant_id`. Tenant isolation is enforced at the URL path level — a key for tenant A cannot reach tenant B's surface.

Key lifecycle:
1. Tenant admin calls `POST /api/v1/a2a/keys` with normal credentials → raw key returned once.
2. External agent stores key in its vault, sends it as Bearer on every A2A request.
3. Admin calls `DELETE /api/v1/a2a/keys/{id}` to revoke — takes effect immediately.

### 11.4 Outbound — A2A Agent Call Node

Handler: `_handle_a2a_call` in `node_handlers.py`. Uses `app/engine/a2a_client.py`:

1. `fetch_agent_card(agent_card_url)` — GET the remote discovery document.
2. `send_task(agent_url, skill_id, message, api_key)` — POST `tasks/send`, return initial Task.
3. `poll_until_done(agent_url, task_id, api_key, timeout)` — poll `tasks/get` every 3s until terminal.
4. `extract_response_text(task)` — pull text from `artifacts` or `status.message`.

The `apiKeySecret` config field uses the vault reference pattern (`{{ env.REMOTE_AGENT_KEY }}`), resolved by `resolve_config_env_vars` before the handler runs.

### 11.5 Design Constraints

- **MCP and A2A coexist** — MCP is for structured tool calls within a DAG; A2A is for delegating entire tasks to external agents. Do not replace one with the other.
- **A2A does not replace webhooks** — the existing `POST /execute` webhook trigger still works. A2A is an additive surface.
- **`tasks/sendSubscribe` uses DB polling** — the SSE stream polls the `workflow_instances` table every 1s. For very high-frequency tenants, add Redis pub/sub (same pattern as token streaming in `sse.py`).

---

## 12. Shared Schemas

File: `shared/node_registry.json`

A version-controlled JSON file defining all node types with their `config_schema`. This serves as the canonical schema that both frontend and backend can reference:

- 6 categories: `trigger`, `agent`, `action`, `logic`, `knowledge`, `nlp`.
- **17 node types** (triggers, agents including Router/ReAct/Reflection, actions including MCP/HTTP/Human Approval/Bridge User Reply/conversation memory/**A2A Agent Call**/Code, logic including Condition/Merge/ForEach/Loop/**Sub-Workflow**, knowledge including Knowledge Retrieval, NLP including Intent Classifier/Entity Extractor) with typed `config_schema` objects (type, default, enum, min/max).
- Drives `DynamicConfigForm` in the UI and server-side config validation on save.

---

## 13. Known Limitations (V0.8)

| Area | Limitation | Planned Resolution |
|------|------------|-------------------|
| **MCP sessions** | New session per call; no connection pooling | Add session pool for high-throughput deployments |
| **SAML federation** | OIDC implemented; SAML requires XML parsing + SP metadata | Add SAML 2.0 SP via python3-saml in V0.9 |
| **Condition expressions** | Safe evaluator supports basic ops; no custom functions or regex | Add pluggable expression functions |
| **Snapshot pruning** | Snapshots accumulate indefinitely; no max-per-workflow limit | Add background Celery task to prune oldest beyond N snapshots |
| **Langfuse in threads** | Parallel node execution may not propagate OTel context to worker threads | Use explicit span passing for parallel branches |
| **OIDC frontend callback** | Token must be stored via a thin redirect page after OIDC callback | Add `/auth/oidc/callback` frontend route that stores token and redirects |

---

## 14. Roadmap

**V0.2 — Wire Frontend to Backend (Implemented)**
- Frontend API client for save/load/execute workflows.
- Workflow list dialog and execution status/log viewer with polling.
- Next: real-time updates via WebSocket or SSE.

**V0.3 — Live LLM Integration (Implemented)**
- Multi-provider LLM abstraction (`app/engine/llm_providers.py`): Google Gemini, OpenAI, Anthropic.
- Jinja2 system prompt templating with dot-accessible context variables (`app/engine/prompt_template.py`).
- Token usage tracking (input/output tokens) returned in execution logs.

**V0.4 — Branching and Parallel Execution (Implemented)**
- Ready-queue execution model replaces linear topological traversal.
- Condition nodes prune non-matching branch subtrees based on `sourceHandle`.
- Independent nodes execute in parallel via `ThreadPoolExecutor` (max 8 workers).
- Merge nodes wait for all upstream branches naturally via the ready-queue model.
- Frontend edges from condition nodes show colored "Yes"/"No" labels with arrow markers.

**V0.5 — Production Hardening (Implemented)**
- JWT-based authentication with tenant claims (`jwt_auth.py`) + dev-mode fallback.
- Fernet-encrypted credential vault per tenant (`vault.py` + `TenantSecret` model).
- PostgreSQL RLS policies via Alembic migration (`0001_enable_rls_policies.py`).
- AST-based safe expression evaluator replacing `eval()` (`safe_eval.py`).
- Per-tenant rate limiting (slowapi + Redis) and hourly execution quotas.

**V0.6 — Advanced Agent Capabilities (Implemented)**
- ReAct iterative tool-calling loop (`react_loop.py`) with Google/OpenAI/Anthropic tool-calling APIs.
- SSE real-time execution updates (`sse.py`) replacing frontend polling.
- Celery Beat cron scheduler (`scheduler.py`) with croniter for schedule triggers.
- Frontend palette hydrated from `shared/node_registry.json`; backend validates configs on save.

**V0.7 — Observability, MCP Streaming & Tenant Tools (Implemented)**
- Langfuse v4 integration (`app/observability.py`): root traces per workflow, child spans per node, LLM generation recording with token usage, tool call spans.
- MCP client rewritten to use MCP Python SDK with Streamable HTTP transport (`app/engine/mcp_client.py`).
- Tool listing, tool execution, and ReAct tool definitions all fetched live from MCP server via standard protocol.
- TenantToolOverride consumed by tools endpoint to filter MCP tools per tenant.

**V0.8 — Enterprise Features (Implemented)**
- Dynamic property forms generated from `shared/node_registry.json` schemas (`DynamicConfigForm.tsx`): Select for enum fields, Textarea for prompts, number inputs with min/max, JSON textarea for objects/arrays, ToolMultiSelect for the ReAct tools field. `PropertyInspector.tsx` refactored to delegate entirely to the dynamic form.
- ReAct auto-discovery: when `tools` config is empty, `react_loop.py` calls `list_tools()` and passes all MCP tools. Tool cache upgraded to 5-minute TTL; `POST /api/v1/tools/invalidate-cache` for manual refresh.
- Workflow version history: `workflow_snapshots` table (Alembic 0002); snapshot inserted before each graph overwrite; `GET /{id}/versions`, `POST /{id}/rollback/{v}` endpoints; `VersionHistoryDialog` in Toolbar with Restore button.
- OIDC federation: Authorization Code + PKCE flow (`app/api/auth.py`), `authlib` for ID token validation, Redis PKCE state (5-min TTL); issues internal JWT; frontend `LoginPage` + `VITE_AUTH_MODE=oidc` gate.

**V0.9 — Planned**
- SAML 2.0 federation (python3-saml).
- Snapshot pruning (max N snapshots per workflow via Celery task).
- OIDC frontend callback route that auto-stores token.
- Pluggable condition expression functions.

---

This blueprint is the single technical reference for the orchestrator module. Setup instructions are in `SETUP_GUIDE.md`, and a step-by-step runtime walkthrough is in `HOW_IT_WORKS.md`.
