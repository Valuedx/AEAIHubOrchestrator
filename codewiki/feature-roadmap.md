# Feature Roadmap

Gap analysis comparing AEAIHubOrchestrator against top DAG workflow builders for agentic AI: **LangGraph**, **Dify**, **n8n**, **Flowise**, **CrewAI**, and **Rivet**.

> **Skip to:** [Recent releases](#recent-releases) · [Priority matrix](#priority-matrix) · [Pending backlog](#pending-backlog)

---

## Recent releases

### Sprint 2A — Developer Velocity (DV-01 … DV-07) — done

| # | Feature | Commit |
|---|---------|--------|
| DV-01 | Data pinning — short-circuit dispatch on a pinned node output | `1e7994b` |
| DV-02 | Test a single node in isolation (uses DV-01 pins for upstream context) | `47ce5f8` |
| DV-03 | Sticky notes on the canvas (non-executable annotations) | `dd0b510` |
| DV-04 | Expression helpers library — 45 new `safe_eval` functions | `8899574` |
| DV-05 | Duplicate workflow with copy-suffix collision handling | `625adbc` |
| DV-06 | Hotkey cheatsheet (`?`) + Shift+S / `1` / Tab shortcuts | `dd0b510` |
| DV-07 | Active / Inactive toggle — Schedule Triggers stop firing on inactive workflows | `625adbc` |

See [Developer Workflow](dev-workflow.md) for each feature's UI flow, storage semantics, and test coverage.

### Sprint 2B — MCP Maturity (MCP-01 … MCP-02) — done

| # | Feature | Commit |
|---|---------|--------|
| MCP-01 | Audit current client vs. 2025-06-18 spec + ranked gap list | `091403c` |
| MCP-02 | Per-tenant MCP server registry (`tenant_mcp_servers`) + resolver + dialog | `f2327e6` |

See [MCP Audit](mcp-audit.md) for findings and the per-tenant registry design.

### Sprint 2C in flight — Delivery + GCP parity

| # | Feature | Status |
|---|---------|--------|
| API-18A | In-app API Playground (JSON payload editor + sync/async + Copy-as-curl + last-10-runs) | **Done** — `f2103c4` |
| VERTEX-01 | First-class Vertex AI support for Gemini chat/ReAct/streaming nodes | **Done** — `c663450` |
| VERTEX-02 | Per-tenant Vertex project override via `tenant_integrations(system='vertex')` | **Done** — see below |

VERTEX-01 adds `vertex` to every LLM node's `provider` enum, reusing the unified `google-genai` SDK via `Client(vertexai=True, project, location)`. Zero new dependencies. ADC auth (`GOOGLE_APPLICATION_CREDENTIALS` or workload identity). Previously Vertex was embeddings-only; the gap felt awkward for any tenant already on GCP.

VERTEX-02 moves the Vertex project + location off the process-global env vars onto per-tenant rows in `tenant_integrations` (`system='vertex'`). Each tenant can bill to their own GCP project. No migration — rides the existing table. New `VertexProjectsDialog` behind the toolbar Cloud icon. ADC stays process-global (service-account identity can't be tenant-scoped without runtime ADC swapping + tenant_secrets storage — a separate, larger feature). **Full scope caveat + future-work sketch** lives in [vertex.md §5](vertex.md).

### Sprint 2E in flight — Workflow Authoring Copilot

| # | Feature | Status |
|---|---------|--------|
| COPILOT-01a | Draft-workspace model + pure tool layer + API + types | **Done** — see [copilot.md](copilot.md) |
| COPILOT-01b.i | Agent runner (Anthropic tool-calling loop) + session/turn API + SSE streaming | **Done** — see [copilot.md §3/§4](copilot.md) |
| COPILOT-01b.ii.a | `test_node` runner tool — one node in isolation using pinned upstream data | **Done** — see [copilot.md §3](copilot.md) |
| COPILOT-01b.ii.a+ | `get_automationedge_handoff_info` runner tool — deterministic-automation fork (inline `automationedge` node vs. handoff to AE Copilot) | **Done** — see [copilot.md §3](copilot.md) + [automationedge.md §2c](automationedge.md#2c-copilot-handoff-optional) |
| COPILOT-01b.ii.b | `execute_draft` + `get_execution_logs` — full-graph trial runs with threadpool+timeout, agent-scoped log read, `is_ephemeral` column (migration `0023`) + filter sweep + operator cleanup utility | **Done** — see [copilot.md §3](copilot.md) |
| COPILOT-01b.iii | Docs grounding — `search_docs` + `get_node_examples` over codewiki + flattened node_registry. File-backed word-overlap search in `app/copilot/docs_index.py`; no migration, vector RAG is a follow-up. | **Done** — see [copilot.md §3](copilot.md) |
| COPILOT-01b.iv (Google/Vertex) | Google AI Studio + Vertex AI providers in the agent runner (shared adapter via unified `google-genai` SDK; Vertex per-tenant project via VERTEX-02). Default model `gemini-3.1-pro-preview-customtools`. | **Done** — see [copilot.md §3](copilot.md) |
| COPILOT-01b.iv (OpenAI + budget) | OpenAI provider adapter + per-session token budget enforcement | Planned |
| COPILOT-02.i | Chat pane + streaming message list + composer + toolbar Sparkles toggle + mutually-exclusive-with-PropertyInspector layout | **Done** — see [copilot.md §6](copilot.md) |
| COPILOT-02.ii | `PromoteDialog` (diff summary + validation surface + name/description) + stop-generating button + session history replay | **Partially done** — `PromoteDialog`, stop button, history replay shipped; canvas `DraftDiffOverlay` (ghost nodes + sparkle badges) deferred to 02.ii.b because it needs `FlowCanvas` to accept a preview-graph override. See [copilot.md §6](copilot.md) |
| COPILOT-02.ii.b | Draft diff preview on canvas — `flowStore.copilotPreview` slot + `FlowCanvas` switches to read-only preview mode with a sparkle banner + `AgenticNode` paints `added` (dashed amber ring + `new` badge) vs. `modified` (solid amber ring + `edit` badge) based on a `draft ↔ base` diff. Per-node accept-per-node affordance deferred to a later slice — v1 accepts or rejects the whole draft via the existing Apply / Close path. | **Done** — see [copilot.md §6](copilot.md) |
| COPILOT-03.a | Scenario storage (migration 0027) + `save_test_scenario` / `run_scenario` / `list_scenarios` runner tools — persisted regression scenarios the agent can save and replay via `execute_draft` with a recursive `expected_output_contains` partial-match diff | **Done** — see [copilot.md §3](copilot.md) |
| COPILOT-03.b | Debug / log inspection tools — `run_debug_scenario` (ad-hoc graph override run with pins + node_overrides, nothing persisted) + `get_node_error` (narrow one failing node with resolved_config for fix suggestions). `get_instance_logs` already shipped as `get_execution_logs` (01b.ii.b). | **Done** — see [copilot.md §3](copilot.md) |
| COPILOT-03.c | `suggest_fix` node-scoped LLM subcall — propose-never-auto-apply, patch filtered to schema keys, per-draft cap of 5 calls | **Done** — see [copilot.md §3](copilot.md) |
| COPILOT-03.d | Auto-heal loop prompt pattern + per-turn `suggest_fix` cap (3) enforced by `AgentRunner._dispatch_runner_tool` via a counter reset at the top of `send_turn`; per-draft cap of 5 (03.c) bounds lifetime usage | **Done** — see [copilot.md §3 + §7](copilot.md) |
| COPILOT-03.e | `PromoteDialog` scenario pass/fail badges + `run_all` endpoint + scenario migration from draft_id → workflow_id inside the promote transaction. Failing scenarios gate Apply behind a "promote anyway" confirm checkbox. | **Done** — see [copilot.md §6](copilot.md) |

### Sprint 2D in flight — Multi-tenant admin knobs

| # | Feature | Status |
|---|---------|--------|
| ADMIN-01 | Per-tenant overrides for `execution_quota_per_hour`, `max_snapshots`, `mcp_pool_size` | **Done** |
| ADMIN-02 | Per-tenant API rate limits (real enforcement via `TenantRateLimitMiddleware`) | **Done** — see below |
| ADMIN-03 | Per-tenant LLM provider API keys (Google AI Studio / OpenAI / Anthropic — Fernet-encrypted vault + labelled dialog) | **Done** |
| STARTUP-01 | Preflight readiness checks (DB, Redis, Celery, RLS, auth, vault) + `/health/ready` + UI banner | **Done** |

ADMIN-01 adds the `tenant_policies` table (migration `0020`) + the resolver + API + dialog (toolbar Sliders icon).

ADMIN-02 extends `tenant_policies` with two rate-limit columns (migration `0021`) and adds a `TenantRateLimitMiddleware`. **Notably, this is the first real API rate-limit enforcement in the orchestrator** — the previous `slowapi.Limiter` was instantiated but never wired into a middleware, so the pre-ADMIN-02 `RATE_LIMIT_*` env vars were inert. The new middleware does Redis INCR+EXPIRE per `(tenant, time-bucket)` with graceful fail-open on Redis errors. See [tenant-policies.md §4](tenant-policies.md) for the full env-var-by-env-var rationale.

---

## Pending backlog

### MCP Maturity — next tickets (from [mcp-audit.md §6](mcp-audit.md))

Ranked by impact-in-our-context:

| # | Title | Why it matters |
|---|-------|----------------|
| MCP-03 | OAuth 2.1 resource-server client | Required to talk to any spec-compliant hosted MCP service. Registry already has `auth_mode='oauth_2_1'` column. |
| MCP-04 | HITL confirmation gate for destructive tools | Biggest production-risk gap today — we ignore `destructiveHint`. |
| MCP-05 | Structured tool output + `outputSchema` validation | Cheap; fixes lossy result parsing. |
| MCP-06 | Tool-definition fingerprinting + drift detection | Mitigates tool-poisoning. Side table already exists from migration 0019. |
| MCP-07 | Elicitation support (`elicitation/create` → HITL suspend) | Unlocks interactive MCP tools. |
| MCP-08 | `notifications/tools/list_changed` subscription + cache invalidation | Closes the 5-minute staleness window. |
| MCP-09 | `HTTP DELETE` session release on shutdown + resumability integration test | Politeness + confidence. |
| MCP-10 | Bump protocol version to `2025-11-25` | Non-breaking; picks up OIDC discovery, scope-minimization, icons. |

Deferred: sampling (MCP-11), resources / prompts primitives (MCP-12). See [mcp-audit.md §6](mcp-audit.md) for full rationale.

### Copilot intelligence upgrades — SMART-01 … SMART-06

Six follow-ups that turn the copilot from "reads + writes workflows" into "learns, warns, and improves over time." All six ship as **opt-out per-tenant feature flags** stored on `tenant_policies` so cost-conscious tenants can disable any subset (the flag is the single control — no scattered env vars). Default is on for zero-cost features (lints, telemetry reads, pattern retrieval), off for net-new cost features (regression re-runs, embedding indexing).

| # | Title | Default | Extra cost per turn | Payoff |
|---|-------|---------|---------------------|--------|
| SMART-01 | Scenario memory + strict promote-gate — every successful `execute_draft` auto-saves a scenario (deduped by payload hash); Promote re-runs them and refuses with 400 on regression. **Shipped** (migration 0028) — two independent flags (`smart_01_scenario_memory_enabled`, `smart_01_strict_promote_gate_enabled`) both default off so cost-conscious tenants opt in. | **off** (user opts in) | One scenario INSERT per successful run + one full draft execution per scenario at promote time | Stops "I built it once, then broke it" — biggest reliability jump. Pairs naturally with COPILOT-03's auto-heal. |
| SMART-02 | Per-tenant accepted-patterns library — every Promote stores the accepted `graph_json` + NL intent; next draft retrieves nearest 2–3 as few-shot examples | **on** | Two extra DB queries; negligible | Agent learns the tenant's conventions (naming, preferred MCP servers, memory profiles) without a prompt change. **Done** — see [copilot.md §3](copilot.md). |
| SMART-03 | Production-telemetry feedback loop — when a copilot-authored workflow shows anomalies in prod (timeouts ×3, error spike, cost outlier), surface the issue + a proposed fix at the top of the next chat | **on** | Aggregator runs on Beat schedule, not per-turn; surfacing is a single DB read | Closes the loop from authoring → operation; the copilot notices what the user would have taken days to flag. |
| SMART-04 | Proactive authoring lints — after every mutation, run cheap graph checks (no-trigger, disconnected-node, orphan-edge, missing-credential) + surface inline in the tool_result; `check_draft` runner tool supersedes `validate_graph` | **on** | Zero LLM calls; O(nodes + edges) graph pass | Catches the common shape bugs (dangling nodes, missing keys) before the first test run — huge authoring UX win. **Done** — see [copilot.md §3](copilot.md). |
| SMART-05 | Vector-backed docs upgrade — `search_docs` gains a cosine-similarity path over embeddings of the codewiki corpus (default `openai` / `text-embedding-3-small`, one-time embed per process restart + one embed per query). On embedding failure the call auto-falls back to word-overlap with a `vector_fallback` hint so enabling this never returns *fewer* results. **Shipped** (migration 0029) — internals-only; tool surface untouched. | **off** (user opts in) | One corpus embed per process restart + one query embed per copilot search | Semantic match — "classify incoming messages" finds Intent Classifier without exact-word overlap. |
| SMART-06 | MCP tool discovery for the agent — agent can call `list_tools` on the tenant's connected MCP servers (MCP-02 registry) and surface relevant ones proactively: "You're wiring a SOC analyst flow — `threat_intel.enrich_ip` on your connected MCP looks useful." | **on** | One cached `list_tools` call per session; zero LLM cost | Turns the MCP-02 registry into an agent capability, not just a config screen. Pairs with MCP-08 cache invalidation. **Done** — see [copilot.md §3](copilot.md). |

Design rule for the flag column: each SMART ticket adds one named boolean (e.g. `smart_04_lints_enabled`) to `tenant_policies` with `DEFAULT TRUE` or `DEFAULT FALSE` matching the table above. Resolver pattern mirrors ADMIN-01/02's `EffectivePolicy` dataclass so the runner's "is this feature on for this tenant?" check is a single method call.


### Workflow Authoring Copilot — COPILOT-01 … COPILOT-03

Expands roadmap entry [#24](#24-workflow-authoring-copilot-nl--draft-workflow--planned) into three shippable tickets. A Claude-Code-style conversational agent that can create, modify, test, and debug workflows through tool-calling — with a safety boundary so the copilot never mutates a live workflow directly.

| # | Title | Dep | Rough effort |
|---|-------|-----|--------------|
| COPILOT-01 | Draft-workspace model + agent tool surface (backend) | — | ~3 wks |
| COPILOT-02 | Chat pane + diff-apply UI (frontend) | COPILOT-01 | ~2 wks |
| COPILOT-03 | Debug / test-scenario / auto-heal loop | COPILOT-01 | ~2–3 wks |

**Why this order:** COPILOT-01 ships the safety boundary + tool layer before any user-facing surface exists. A copilot that can edit-and-execute a *published* workflow is too high blast radius — every mutation must pass through a draft that the human accepts before it lands in `workflow_definitions`. COPILOT-02 and -03 can be shipped in either order but pair naturally.

**Natural synergy with MCP-07 (elicitation):** the copilot's "ask a clarifying question" shape is structurally identical to MCP's `elicitation/create` flow. If MCP-07 ships first, the suspend/resume plumbing is reusable.

---

#### COPILOT-01 — Draft-workspace model + agent tool surface

**Goal.** An agent can safely create, patch, validate, and trial-run workflows through an exposed tool set, with all writes going to a draft layer that a human promotes before anything touches `workflow_definitions`.

**Schema (migration `0022`).**
- `workflow_drafts` — `id`, `tenant_id`, `base_workflow_id` (nullable; null when drafting net-new), `graph_json`, `title`, `created_by`, `created_at`, `updated_at`, `last_copilot_session_id`. RLS tenant-scoped like every other tenant table (see `RLS-01`).
- `copilot_sessions` — `id`, `tenant_id`, `draft_id`, `provider` (google/openai/anthropic, follows ADMIN-03), `created_at`. Chat-history container.
- `copilot_turns` — `id`, `session_id`, `tenant_id`, `role` (`user`/`assistant`/`tool`), `content_json`, `tool_calls_json`, `token_usage_json`, `created_at`.

**Backend surface.**
- `POST /api/v1/copilot/drafts` — create draft (from scratch or from a `base_workflow_id`).
- `GET /api/v1/copilot/drafts/{id}` — read current state + last validation result.
- `DELETE /api/v1/copilot/drafts/{id}` — abandon.
- `POST /api/v1/copilot/drafts/{id}/promote` — atomically merges draft into `workflow_definitions` as a new version (when `base_workflow_id` set) or creates a new workflow (null base).
- `POST /api/v1/copilot/sessions` — start a copilot session bound to a draft.
- `POST /api/v1/copilot/sessions/{id}/turn` — send user message; returns streamed assistant message + tool-call log (SSE).

**NL-first turn pipeline** (enforced by the system prompt):

1. **Intent extract** — before any tool call, emit a structured intent JSON: `{trigger, primary_operation, key_decisions, downstream_effects, ambiguities[]}`. Read-only on the user's prose; no drafting yet.
2. **Clarification loop** — for every item in `ambiguities[]`, ask one question at a time. Cannot advance to drafting until the list is empty. Same suspend-for-input shape as MCP-07 elicitation — reuse that plumbing if MCP-07 ships first.
3. **Pattern match** — call `search_docs` + `get_node_examples` to retrieve 2–3 nearest template patterns from the system KB (see below). Draft by adapting a known template, not by synthesising from nothing. Keeps hallucination low.
4. **Draft** — emit `add_node` / `connect` tool calls.
5. **Narrate** — plain-language summary of what was built before the user hits Apply, so the NL input gets a NL receipt.

**System knowledge base (RAG grounding).** A non-tenant-scoped KB ingested once at deploy time (or via a CLI re-index command) containing:
- `codewiki/*.md` — architecture, security, node types, memory model, automationedge, etc.
- Flattened `node_registry.json` — one doc page per node type, generated from the schema.
- `api-reference.md` — the orchestrator's own API.
- Template gallery descriptions — one doc per canonical pattern (classifier+router, RAG-over-KB, ReAct-with-MCP, etc.).

Reuses the existing RAG pipeline (pgvector + markdown chunker + embedding provider). Lives in a dedicated `kb_documents` row with a reserved tenant-id sentinel so the system KB is cross-tenant-readable but only re-index operations (admin) can write.

**Agent tool surface** (function-calling, passed to the configured LLM provider):
- `get_draft()` — returns `{graph_json, validation: {errors, warnings}}`.
- `list_node_types(category?: string)` — trimmed node-registry entries with id/category/short-description only. Copilot calls `get_node_schema(type)` to get the full schema for the one it picked — two-step flow prevents context blowup.
- `get_node_schema(type)` — full JSON-schema for one node type plus usage notes.
- `add_node(type, config, position)` → `{node_id, validation_delta}`.
- `update_node_config(node_id, partial)` → `{validation_delta}`.
- `delete_node(node_id)`.
- `connect(from_node, from_handle?, to_node, to_handle?)` → `{edge_id}`.
- `disconnect(edge_id)`.
- `validate()` — reuses `config_validator` / existing `/api/v1/workflows/{id}/validate` shape.
- `test_node(node_id, pins)` — reuses DV-02 single-node probe.
- `execute_draft(payload, sync=true)` — trial run against the draft graph by materialising a throwaway `WorkflowDefinition` internally (no engine fork).
- `get_execution_logs(instance_id, node_id?)` — structured log list for debugging.
- `search_docs(query, top_k=5)` — retrieves relevant chunks from the system KB. Used for "how does the Intent Classifier scope entities?" or debug-time "what does this error mean?".
- `get_node_examples(type)` — template snippets using a given node type, pulled from the gallery — concrete few-shot for config shape.

**Source-of-truth rule.** For schema-shaped questions (node types, config fields, endpoints) the copilot MUST prefer the live API (`list_node_types`, `get_node_schema`) over RAG-retrieved docs. Docs are for *concepts and patterns*; the API is source of truth for *structure*. Docs that contradict the live API surface as stale and should be re-indexed.

**Acceptance criteria.**
- Draft CRUD, promote, and all tool endpoints pass unit tests.
- Promote creates a new version of the base workflow OR a fresh workflow, atomically, and deletes the draft.
- RLS enforced: cross-tenant read/write denied. Regression test similar to `test_rls_dependency_wired.py`.
- `validate` matches the shape of the existing workflow validator (zero duplication).
- `execute_draft` routes through the existing engine path (same logs, same checkpoints).
- A synthetic end-to-end test drives an `anthropic.messages.create`-compatible function-calling loop through `add_node` → `connect` → `validate` → `execute_draft` and asserts the draft was built correctly.

**Out of scope.** UI (COPILOT-02), auto-heal loop (COPILOT-03), system-prompt tuning (ongoing — ship with a hand-written v1 prompt).

**Risks.**
- Registry context explosion: ~50 node types × ~200 tokens each = 10k tokens just for `list_node_types`. Mitigate with a two-step flow: copilot first calls `list_node_types({category})`, then `get_node_schema(type)` for the specific one.
- Draft/published schema drift: keep `graph_json` byte-identical between draft and published — same validator, same runner. No divergence allowed.
- Cost unbounded: add a per-session token budget (default 100k tokens) that suspends the session and prompts the user when exceeded.

---

#### COPILOT-02 — Chat pane + diff-apply UI

**Goal.** A canvas-side chat where the user describes intent, the copilot drafts + explains, and the user accepts/rejects at node-level granularity before anything lands in `workflow_definitions`.

**Frontend surface.**
- `CopilotPanel.tsx` — resizable right-side drawer. Toggled by a toolbar Sparkles icon. Coexists with `PropertyInspector` (mutually exclusive: opening the copilot hides the inspector, and vice versa — the right column is narrow, no sense fighting for it).
- `CopilotMessageList.tsx` — streaming assistant messages via SSE reusing the existing streaming infra. Tool calls render as inline pills (`🔧 add_node(llm_agent)` → click to expand arguments).
- `CopilotComposer.tsx` — textarea + model-picker (reuses ADMIN-03 credentials). Cmd+Enter to send.
- `DraftDiffOverlay.tsx` — when a draft is active, the canvas overlays a semi-transparent sparkle badge on nodes added/modified by the copilot; edges added by the copilot render dashed with a coloured stroke. A top bar shows "Draft mode — 3 changes unapplied" + Accept / Abandon buttons.
- `PromoteDialog.tsx` — side-by-side summary of draft vs. base (nodes added/removed/changed), confirms workflow name if net-new, confirms version bump if updating existing, then POSTs `/promote`.

**UX flow.**
1. User clicks Sparkles → panel opens with "What would you like to build?" prompt.
2. User types intent; copilot begins streaming. Tool calls surface as pills; draft starts rendering on canvas with sparkle badges.
3. Copilot asks clarifying questions inline; user answers in the same chat.
4. Before applying, user sees a diff summary; clicks Accept → promoted.
5. Abandon at any point discards the draft.

**Acceptance criteria.**
- From a blank canvas, "build a flow that reads a Slack message, summarises it, emails the summary" produces a draft that the user can accept and save.
- Draft preview matches the would-be post-promotion workflow exactly.
- Abandon deletes the draft cleanly (no orphan rows, no orphan nodes).
- Playwright E2E covers the three happy paths: create-new, edit-existing, abandon.
- Regen button retries the last assistant turn (reuses the same session, advances token counter).

**Out of scope.** Voice input, multi-user drafting (one draft = one user for v1), auto-heal on failed runs (COPILOT-03).

**Risks.**
- Token cost per turn grows with graph size: add a context-compression step that summarises older turns and transmits only the current diff to the LLM.
- "Who owns the canvas" ambiguity when a draft is active: banner must be unmissable; disable direct canvas edits while draft is mid-flight, or confirm "discard copilot's 3 pending changes?" if the user tries.

---

#### COPILOT-03 — Debug / test-scenario / auto-heal loop

**Goal.** When the user says "it's broken" or "test that it handles an empty payload," the copilot can trial-run, read logs, propose fixes, and remember named test scenarios — the Claude-Code-debugging-a-test shape applied to workflows.

**New tools exposed to the agent.**
- `run_debug_scenario(payload, pins={}, node_overrides={})` — trial-runs the draft with optional upstream pins (DV-01) for reproducible debugging.
- `get_instance_logs(instance_id)` — returns structured `[{node_id, timestamp, level, message, data}]`.
- `get_node_error(instance_id, node_id)` — narrows to the failed node: error message, stack (if available), the resolved `config_json` it ran with, the `context_json` it received.
- `suggest_fix(node_id, error)` — internal LLM subcall scoped to one node; returns a proposed config patch with a rationale. Never auto-applies — always round-trips through the user.
- `save_test_scenario(name, payload, pins, expected_output_contains?)` — persists a reusable debug scenario.
- `run_scenario(scenario_id)` — executes a saved scenario, diffs actual vs. `expected_output_contains`.

**Schema (migration `0023`).**
- `copilot_test_scenarios` — `id`, `tenant_id`, `draft_id` (or `workflow_id` for published-workflow scenarios), `name`, `payload_json`, `pins_json`, `expected_output_contains_json`, `created_at`. RLS tenant-scoped. When a draft promotes, scenarios either migrate to the new workflow_id or stay draft-scoped and get garbage-collected (decision during impl — lean toward migrate).

**Auto-heal loop.**
1. Copilot calls `execute_draft`. Run fails.
2. Copilot automatically calls `get_node_error` on the failed node.
3. Copilot calls `suggest_fix`, surfaces the proposal to the user ("The email node failed with `no auth configured`. Want me to wire it to the `SENDGRID_API_KEY` you already have in Secrets?").
4. On user approval, `update_node_config` patches the draft; copilot re-runs; confirms green.

**Promote-gate integration.**
- `PromoteDialog` (from COPILOT-02) now lists saved scenarios with pass/fail state: "✔ 3 of 4 scenarios pass. `empty-slack-message` fails — promote anyway?"

**Acceptance criteria.**
- End-to-end test: user asks "run it with an empty message"; copilot runs, surfaces the failure with the specific node + error, proposes a fix, applies it on approval, re-runs green.
- Three common failure modes covered explicitly: auth missing, schema mismatch (e.g. expression references a field not in upstream output), downstream node receives `null` where non-null expected.
- Saved scenario survives a draft edit and can be re-run; diff against `expected_output_contains` works.
- Scenario compatibility check: if a draft's schema change invalidates a scenario (e.g. deletes a node the scenario pins), the scenario surfaces as `stale` instead of silently failing.

**Out of scope.** Scheduled regression runs (separate eval harness — roadmap #13), sandboxing the copilot's own compute (v1 runs within the orchestrator process with full tenant scope).

**Risks.**
- `suggest_fix` hallucination: bounded by the "propose, never auto-apply" rule and by constraining the prompt to the node's config schema (copilot can only suggest changes that validate).
- Cost of auto-heal loop in a degenerate "flap" case (fix → fails differently → new fix → ...): cap the auto-heal depth at 3 retries per turn, then surface a "this is beyond auto-heal" message so the user takes over.

---

## Priority matrix

| Priority | # | Feature | Competitive Pressure | Status |
|----------|---|---------|---------------------|--------|
| **P0** | 1 | RAG / Knowledge Base / Vector Store | Dify, Flowise, n8n | **Done** |
| **P0** | 2 | Code Execution Node | Dify, n8n, LangGraph | **Done** |
| **P0** | 3 | Integration Ecosystem (native connectors) | n8n (400+), Dify, Flowise | **Partial** |
| **P0** | 4 | Credential Management UI | n8n, Dify | **Done** |
| **P1** | 5 | In-Process Multi-Agent Patterns | CrewAI, LangGraph, AutoGen | Planned |
| **P1** | 6 | Subgraphs / Nested Workflows | LangGraph, Dify | **Done** |
| **P1** | 7 | Cyclic Graph Support | LangGraph | Planned |
| **P1** | 8 | Built-in Observability Dashboard | Dify, LangSmith | Planned |
| **P1** | 9 | Per-Node Error Handling & Retry | n8n | Planned |
| **P1** | 10 | Dynamic Fan-Out Map-Reduce | LangGraph | Planned |
| **P1** | 21 | MCP Client Maturity (OAuth 2.1, elicitation, structured output, drift detection) | Claude Desktop, Cursor, 2026 ecosystem | **Partial** |
| **P1** | 22 | Tool Trust & Safety UX (annotation-driven destructive-tool gate) | Spec MUST; 2025 OWASP-AI flagged | Planned |
| **P2** | 11 | Advanced Memory (semantic, entity) | CrewAI, LangGraph | **Done** |
| **P2** | 12 | Data Transformation Nodes | n8n, Dify | Planned |
| **P2** | 13 | Evaluation / Testing Framework | LangSmith, Dify | Planned |
| **P2** | 14 | RBAC / Team Collaboration | Dify | Planned |
| **P2** | 15 | Marketplace / Community Nodes | n8n, Flowise | Planned |
| **P2** | 16 | Multi-Way Branching (Switch Node) | n8n, Dify | Planned |
| **P3** | 17 | Canvas UX (auto-layout, groups, comments) | Rivet, n8n | **Partial** |
| **P3** | 18 | API Playground / Embed Widgets | Dify, Flowise | **Partial** |
| **P3** | 19 | Environment / Variable Management UI | n8n, Dify | **Partial** |
| **P3** | 20 | Execution Analytics Dashboard | n8n, Dify | Planned |
| **P3** | 23 | Real-time Collaboration (presence, comments) | Dify, Figma-class | Planned |
| **P3** | 24 | Workflow Authoring Copilot (NL → draft) | Dify AI copilot, 2026 trend | Planned |
| **P3** | 25 | MCP Server Catalogue (curated + verified fingerprints) | Claude Desktop server registry | Planned |

---

## Detailed descriptions

### P0 — Critical gaps (table-stakes for competitive parity)

#### 1. RAG / Knowledge Base / Vector Store — Done

> Dify, Flowise, and n8n all have built-in knowledge base nodes with document ingestion, chunking, embedding, and retrieval. Dify supports 6+ vector store backends.

**Implemented:** Full RAG pipeline with pluggable vector stores (pgvector, FAISS), multiple embedding providers (OpenAI, Google GenAI, Google Vertex AI), four chunking strategies (recursive, token, markdown, semantic), async document ingestion, Knowledge Retrieval workflow node, and management UI.

See [RAG & Knowledge Base](rag-knowledge-base.md) for full documentation.

#### 2. Code Execution Node — Done

> Dify has sandboxed Python/JavaScript code nodes. n8n has a Code node. LangGraph nodes are arbitrary Python functions.

**Implemented:** Sandboxed Python code execution node ("Code") running user code in a separate subprocess with multiple security layers: restricted builtins (no `open`/`exec`/`eval`), import whitelist (30 safe stdlib modules), per-node timeout (max 120 s), 1 MB output cap, and clean environment (no app secrets). The frontend config panel renders a monospace code editor with tab support. Data flows in via an `inputs` dict and out via an `output` variable.

See [Node Types — Code](node-types.md) for full documentation.

#### 3. Integration Ecosystem / Pre-built Connectors — Partial

> n8n has 400+ pre-built nodes (Slack, Gmail, Google Sheets, Airtable, databases, CRMs, etc.). Dify and Flowise have native integrations for common services.

**Progress:** The **Notification node** adds native connectors for 8 channels: Slack (webhook), Microsoft Teams (webhook), Discord (webhook), Telegram (Bot API), WhatsApp (Meta Cloud API), PagerDuty (Events v2), Email (SendGrid, Mailgun, SMTP), and generic webhooks. All channels support three config value sources (static, vault secrets, runtime expressions) and Jinja2 message templating. See [Notification Guide](notification-guide.md).

**Remaining:** Database query nodes, file storage (S3, GCS), CRM connectors, Google Sheets, and other SaaS-specific nodes are not yet implemented. MCP tools remain the extensibility mechanism for uncommon integrations.

#### 4. Credential / Secret Management UI — Done

> n8n and Dify have full credential management UIs (add, test, reuse across workflows).

**Implemented:** Full REST API (`/api/v1/secrets`) for CRUD on tenant secrets with Fernet encryption. Frontend `SecretsDialog` accessible from the toolbar (KeyRound icon) with create, update, delete views. Secret values are never exposed after creation — only `{{ env.KEY_NAME }}` references are shown. Fixed the missing `get_tenant_secret` function so `{{ env.* }}` resolution now works at runtime.

See [API Reference](api-reference.md) and [Security](security.md) for details.

---

### P1 — Major gaps (strong differentiators in competing tools)

#### 5. In-Process Multi-Agent Patterns — Planned

> LangGraph supports supervisor/worker patterns and multi-agent swarms within a single graph. CrewAI has role-based teams with sequential, hierarchical, and consensus-driven coordination. AutoGen has structured multi-agent conversations.

We have A2A delegation (remote agents) but no native in-process multi-agent patterns (supervisor, swarm, debate, voting, hierarchical delegation within one workflow). Implementing supervisor and team coordination nodes would close this gap.

#### 6. Subgraphs / Nested Workflows — Done

> LangGraph has composable subgraphs (a compiled graph used as a node inside a larger graph). Dify supports nested workflow calls.

**Implemented:** Sub-Workflow logic node (`sub_workflow`) that executes another saved workflow as a single step. Child workflows run synchronously inline, creating a separate `WorkflowInstance` linked via `parent_instance_id` / `parent_node_id`. Input mapping via `safe_eval` expressions builds the child's `trigger_payload`; output filtering restricts which child node outputs are returned. Version policy: `latest` (live definition) or `pinned` (specific snapshot version). Recursion protection via `_parent_chain` prevents cycles and enforces configurable `maxDepth` (default 10, max 20). Cancellation cascades from parent to child instances. Frontend includes custom `WorkflowSelect`, `InputMappingEditor`, and `OutputNodePicker` widgets, canvas version-policy badge, and drill-down child execution logs.

See [Node Types — Sub-Workflow](node-types.md) for full config reference.

#### 7. Cyclic Graph Support — Planned

> LangGraph explicitly supports cyclic state machines — agents can loop back to previous nodes based on conditions. This is its core selling point.

We enforce DAG-only (Kahn cycle detection rejects cycles). Loop/ForEach are workarounds, not true cyclic graph support. This limits the expressiveness of agent reasoning loops. Supporting cycles would require rethinking the execution engine to handle state machines.

#### 8. Built-in Observability Dashboard — Planned

> Dify has built-in token counts, node-level latency, cost tracking, and execution analytics in the UI. LangGraph has deep integration with LangSmith.

We have optional Langfuse + SSE logs, but no in-app cost tracking, token counting, latency metrics, or analytics dashboard. Adding per-node timing, token usage, and cost estimation to execution logs and a summary view in the UI would address this.

#### 9. Per-Node Error Handling & Retry — Planned

> n8n has per-node "Retry on Fail" configuration (count, delay), dedicated Error Trigger workflows, and fallback paths.

We have instance-level retry from a failed node but no per-node retry policies, error fallback edges, or error trigger workflows. Adding retry config to the node schema and fallback edge support to the DAG runner would close this gap.

#### 10. Dynamic Fan-Out Map-Reduce — Planned

> LangGraph's `Send()` API dynamically spawns parallel workers based on runtime state and reduces results.

ForEach runs downstream nodes per-element but lacks dynamic fan-out where the number of parallel branches is determined at runtime by data shape, not graph structure. True parallel map-reduce with configurable concurrency limits would be valuable.

#### 21. MCP Client Maturity — Partial

> The MCP ecosystem standardized OAuth 2.1 resource-server auth in March 2025, added structured tool output and elicitation in 2025-06-18, and is shifting to OIDC discovery + scope-minimization in 2025-11-25. Claude Desktop, Cursor, and Continue all ship most of these; we ship tools/call + tools/list only.

**Progress (MCP-01, MCP-02 done):** Full audit of our client against the 2025-06-18 spec lives in [MCP Audit](mcp-audit.md). Per-tenant MCP server registry (MCP-02) replaces the single-server env-var config with `tenant_mcp_servers` — each row captures URL, auth mode (`none` / `static_headers` / `oauth_2_1`), and optional `{{ env.KEY }}` header placeholders resolved through the Secrets vault. Session pool + list-tools cache are now keyed by `(tenant, server)` so tenants can't share warm connections.

**Remaining (MCP-03..MCP-10):** OAuth 2.1 resource-server client (MCP-03; column exists, runtime raises today) · structured tool output + outputSchema validation (MCP-05) · elicitation → HITL suspend (MCP-07) · tool-definition fingerprint drift (MCP-06; empty side table already exists from migration 0019) · `notifications/tools/list_changed` cache invalidation (MCP-08) · `HTTP DELETE` session release + resumability test (MCP-09) · protocol-version bump to 2025-11-25 (MCP-10).

#### 22. Tool Trust & Safety UX — Planned

> The MCP spec declares that "applications **SHOULD** present confirmation prompts to the user for operations, to ensure a human is in the loop" and "clients **MUST** consider tool annotations to be untrusted unless they come from trusted servers." Today we ignore `destructiveHint` / `readOnlyHint` / `idempotentHint` / `openWorldHint` entirely. 2025-era advisories (tool poisoning, rug-pull via description mutation, confused-deputy via token passthrough) add pressure.

A `delete_customer` tool is currently one config field away from firing unattended on a scheduled trigger. Closing this means:

1. Surface tool annotations on the MCP Tool node at design time so workflow authors see the risk.
2. Gate destructive tool calls (`destructiveHint: true` AND the server is not on a trust-list) behind a HITL suspend when the parent workflow is not marked autonomous. Piggybacks on the existing `suspended_reason` / resume path.
3. Show the operator the tool call's inputs *before* it fires (spec: prevents accidental exfiltration).
4. Pair with MCP-06 fingerprint drift so a mutated tool description re-prompts for consent.

Together with MCP-06, this is the largest production-risk improvement available without new infrastructure.

---

### P2 — Moderate gaps (nice-to-have, common in mature tools)

#### 11. Advanced Memory Systems — Done

> CrewAI has short-term, long-term, and entity memory. LangGraph has typed state with reducers and cross-thread memory stores.

**Implemented:** Advanced Memory v1 hard cutover. Conversation transcripts are normalized into `conversation_messages`; `conversation_sessions` now stores summary metadata only. `memory_profiles` define tenant/workflow memory policy, `memory_records` store semantic and episodic memories across `session`, `workflow`, `tenant`, and `entity` scopes, and `entity_facts` stores relational entity memory with last-write-wins semantics. Agent/ReAct prompts are now turn-aware and token-budgeted, router/classifier history packing is shared, and operators can inspect the exact memories used by a run through `/api/v1/memory/instances/{instance_id}/resolved`.

See [Memory Management](memory-management.md) for the full design.

#### 12. Data Transformation Nodes — Planned

> n8n has Set, Split, Merge, Aggregate, Filter, Sort nodes. Dify has Template Transform.

No dedicated JSON transform, filter, aggregate, or template transform nodes. Users must resort to `safe_eval` expressions or code execution for data manipulation. Purpose-built transformation nodes would improve usability for non-technical users.

#### 13. Evaluation / Testing Framework — Planned

> LangSmith and Dify both offer prompt evaluation, A/B testing, and dataset-based testing.

No prompt evaluation, A/B testing, dataset-based testing, or regression testing for workflows. An evaluation framework that can run workflows against test datasets and compare outputs would be valuable for quality assurance.

#### 14. RBAC / Team Collaboration — Planned

> Dify has RBAC, team workspaces, and role-based access.

We have tenant isolation but no intra-tenant roles, team workspaces, or collaboration features (commenting, sharing, co-editing). Adding role-based permissions (viewer, editor, admin) within a tenant would support team use cases.

#### 15. Marketplace / Community Nodes — Planned

> n8n has a community node marketplace. Flowise has a component marketplace.

Template gallery exists but no community-contributed node types or workflow marketplace. A plugin/extension system for custom node types, combined with a sharing mechanism, would enable ecosystem growth.

#### 16. Multi-Way Branching (Switch Node) — Planned

> n8n and Dify support multi-way routing with switch/case patterns.

Only binary true/false Condition nodes exist. No multi-way switch/case routing. LLM Router helps but is non-deterministic and costs tokens. A deterministic Switch node with N output branches based on expression matching would fill this gap.

---

### P3 — Lower priority (polish and advanced features)

#### 17. Canvas UX Enhancements — Partial

> Rivet and n8n have advanced canvas features.

**Shipped (Sprint 2A):** Comment/annotation nodes via **Sticky Notes** (DV-03) with six preset colours, non-executable, filtered at parse time. **Hotkey cheatsheet** (DV-06) surfaces every canvas shortcut behind `?` with shared `isTextEditingTarget` guard. Minimap + pannable zoom exists on the main canvas. **Duplicate workflow** (DV-05) with collision-safe copy-suffix handling.

**Remaining:**
- Auto-layout algorithm for graphs
- Group/frame nodes for visually organising workflow sections
- Copy-paste of node groups across workflows (today: save as template, load as starter)
- Multi-select + bulk-edit on the canvas

#### 18. API Playground / Chatbot Embed — Partial

> Dify provides a built-in API playground for testing, plus embeddable chatbot widgets. Flowise offers one-click API deployment + embed widgets.

**API-18A — In-app API Playground — Shipped.** Toolbar **FlaskConical** icon → `ApiPlaygroundDialog.tsx`. JSON payload editor, sync / async toggle, sync-timeout input, deterministic-mode checkbox, one-click Run, live "Copy as curl" snippet that updates as the user types, and a per-workflow last-10-runs history persisted to `localStorage`. No new backend surface — the dialog goes through the existing `POST /api/v1/workflows/{id}/execute` endpoint so all existing auth, tenant scoping, and rate limits apply. Sync runs show the `SyncExecuteOut.output` context pretty-printed; async runs show the `InstanceOut` and point the operator at the main Execution Panel for streaming logs. Lives in the toolbar between the active-toggle and the Sync-run checkbox; disabled until a workflow is saved (needs a stored workflow id). See `src/lib/playgroundCurl.ts` (pure curl generator, 9 tests) and `src/lib/playgroundHistory.ts` (localStorage ring buffer, 9 tests).

**API-18B — Chatbot Embed Widget — Planned.** Security-sensitive follow-up: needs an unauthenticated-but-scoped access model (new `workflow_embeds` table with origin allowlist, signed short-lived tokens, per-embed rate limits), a public `/api/v1/embed/{id}/chat` endpoint with strict CORS / CSP, and a standalone Preact widget bundle so parent apps don't inherit the main React 19 bundle. Scoped separately from 18A because it's a brand-new attack surface — will need a written security design before code.

#### 19. Environment / Variable Management UI — Partial

> n8n and Dify have UIs for managing environment variables and workflow-level variables.

**Shipped:** `{{ env.KEY }}` resolution works end-to-end. The **Secrets dialog** (KeyRound icon) handles Fernet-encrypted tenant-scoped env vars. The **Tenant Integrations dialog** (Bot icon) and **MCP Servers dialog** (Globe icon, MCP-02) both surface `{{ env.* }}` placeholders in their connection configs so operators register a secret once and reference it from multiple places.

**Remaining:** No UI for workflow-scoped variables (today: pass via `trigger_payload` or derive in an early `safe_eval` node). No "variable autocomplete" in expression fields.

#### 20. Execution Analytics Dashboard — Planned

> n8n and Dify provide execution analytics with success rates, duration charts, and cost tracking.

No searchable/filterable audit log across all workflow executions. No execution analytics (success rates, average duration, cost over time). Adding aggregate metrics and a dashboard view would support operations and capacity planning.

#### 23. Real-time Collaboration (presence, comments) — Planned

> Dify has team workspaces but no real-time co-editing. Figma-class multiplayer on the canvas is the direction the category is heading.

Closely related to #14 (RBAC / Team Collaboration) but distinct: RBAC is *static* — who may edit. Collaboration is *live* — two editors see each other's cursors, per-node comment threads that persist across saves, "someone else is editing this node" locks. Requires a WebSocket broadcast layer (the SSE path is one-way) and per-node presence/comment storage. Would layer cleanly on top of the `workflow_snapshots` history — threads could anchor to a node id + version.

#### 24. Workflow Authoring Copilot (NL → draft workflow) — Partial

> Dify's AI Agent copilot can draft and refine workflows from a natural-language prompt. Anthropic's Claude Code has begun embedding this pattern for tool-chain generation.

**Progress (COPILOT-01a done, 2026-04-22):** Draft-workspace safety boundary + pure tool layer + API surface shipped. Migration `0022` adds `workflow_drafts`, `copilot_sessions`, `copilot_turns` (all tenant-scoped RLS). The tool layer at `app/copilot/tool_layer.py` exposes eight pure functions the (future) agent runner will call. Optimistic-concurrency `version` column guards concurrent tool calls; `base_version_at_fork` guards the promote race against a colleague editing the base. Full architecture + schema in [codewiki/copilot.md](copilot.md).

**Remaining (COPILOT-01b / 02 / 03):** agent runner + system-KB RAG ingestion (01b), chat pane + diff-apply UI (02), debug / test-scenario / auto-heal loop (03). See the [detailed breakdown](#workflow-authoring-copilot--copilot-01--copilot-03) above.

#### 25. MCP Server Catalogue — Planned

> Claude Desktop ships a curated list of official MCP servers (filesystem, brave-search, github, …). Cursor and Continue extend similar lists. Each is one click to install.

Natural pairing with MCP-02 (the registry is there; seed it). A catalogue UI that lists well-known public MCP servers, each with a verified fingerprint (pairs with MCP-06 drift detection), one-click "Add to tenant" action, and an indicator if the server requires OAuth (pairs with MCP-03). Lower-risk than a general node marketplace (#15) because each entry's surface area is bounded by the MCP protocol.

---

## Current strengths

Features where AEAIHubOrchestrator is competitive or ahead:

| Strength | Details |
|----------|---------|
| **A2A Protocol** | First-class inter-agent delegation that most competitors lack natively |
| **NLP Nodes** | Dedicated Intent Classifier (hybrid scoring) and Entity Extractor (rule-based + LLM fallback) with optional embedding caching |
| **Advanced Memory** | Normalized conversation storage, rolling summaries, semantic/episodic/entity memory, profile-driven prompt assembly, and inspection APIs |
| **MCP Integration** | Per-tenant server registry (MCP-02) with auth-mode discriminator + `{{ env.KEY }}` vault indirection. Remaining gaps (OAuth, elicitation, structured output, drift detection) are named in [MCP Audit](mcp-audit.md) and scheduled as MCP-03..MCP-10. |
| **HITL + Pause/Resume/Cancel** | Richer operator control than most visual builders |
| **Checkpointing + Debug Replay** | On par with LangGraph's checkpointing |
| **Deterministic Mode** | Unique feature for reproducible testing (sets LLM temperature to 0) |
| **Bridge Pattern** | Clean separation for embedding into parent apps |
| **Version History + Rollback** | On par with Dify |
| **Portable Architecture** | Easier to deploy than LangGraph Cloud; fully self-contained |
| **RAG / Knowledge Base** | Pluggable vector stores, multiple embedding providers, four chunking strategies |
| **AutomationEdge Async-External Integration** | Pattern A webhook + Pattern C Beat poll both resume through the same `finalize_terminal` path. Diverted pause-the-clock timeout model. Tenant-scoped connection defaults via `tenant_integrations`. |
| **Developer Velocity (Sprint 2A)** | Pin node outputs and test a single node in isolation (DV-01 + DV-02) — the edit→run→inspect loop no longer requires end-to-end runs. Plus 45 `safe_eval` expression helpers (DV-04), sticky notes (DV-03), duplicate workflow (DV-05), hotkey cheatsheet (DV-06), and active/inactive toggle (DV-07). See [Developer Workflow](dev-workflow.md). |
| **Tenant-scoped MCP Registry (MCP-02)** | Per-tenant MCP server registration with session-pool isolation across tenants and a forward-declared fingerprint side table for drift detection. Supports `none` and `static_headers` auth modes today; registry schema ready for OAuth without migration churn. |
