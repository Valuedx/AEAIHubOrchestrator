> - **COPILOT-01b.ii.a — `test_node` runner tool (2026-04-22)**: The copilot can now run a single node in isolation to debug its config without paying for a full workflow run. No new env vars, no migration, no frontend surface changes. The tool reuses the same `dispatch_node` path as the existing `POST /workflows/{id}/nodes/{node_id}/test` endpoint so credential / MCP / LLM resolution all behave identically to runtime. Next 01b slices: `execute_draft` + `get_execution_logs` (needs a small migration for `is_ephemeral` on `workflow_definitions`), RAG docs grounding, OpenAI provider.
>
> - **COPILOT-01b.iv — Google AI Studio + Vertex AI providers (2026-04-22)**: The copilot agent now supports Anthropic, Google AI Studio, and Vertex AI. Default model for Google and Vertex is `gemini-3.1-pro-preview-customtools` (the tool-calling-optimised Gemini 3.x endpoint). Set `LLM_GOOGLE_API_KEY` tenant secret or `ORCHESTRATOR_GOOGLE_API_KEY` env for AI Studio; Vertex uses ADC + the tenant's default row in `tenant_integrations(system='vertex')` for project/location (VERTEX-02). No new env vars or service dependencies — `google-genai>=1.0.0` is already in `requirements.txt`. Pick provider/model at `POST /api/v1/copilot/sessions`; the `GET /providers` endpoint lists supported providers and default models dynamically.
>
> - **COPILOT-01b.i Agent runner + session streaming (2026-04-22)**: Adds the LLM-driven half of the copilot. New API router at `/api/v1/copilot/sessions` (CRUD + `/turns` SSE streaming + `/providers` metadata). No new environment variables — Anthropic API key is resolved per-tenant through the existing ADMIN-03 resolver (set the `LLM_ANTHROPIC_API_KEY` tenant secret or `ORCHESTRATOR_ANTHROPIC_API_KEY` env fallback). No service dependencies beyond the `anthropic>=0.30.0` package already in `requirements.txt`. User-facing UI lands in COPILOT-02; today's surface is back-end only and driven by the typed frontend bindings in `frontend/src/lib/api.ts`.
>
> - **COPILOT-01a Draft-workspace foundation (2026-04-22)**: First slice of the workflow authoring copilot. Adds migration `0022` with three new tenant-scoped tables (`workflow_drafts`, `copilot_sessions`, `copilot_turns`) and a new API router at `/api/v1/copilot/drafts`. Run `alembic upgrade head` after pulling. No new environment variables; no new service dependencies. The agent runner itself (that actually drives an LLM through the tool surface) and the system-KB RAG ingestion land in COPILOT-01b — so nothing user-facing has changed yet. Full architecture + schema + HTTP surface documented in `codewiki/copilot.md`.
>
> - **RLS-01 Systemic `get_tenant_db` cutover (2026-04-21)**: Fixed a latent bug where nearly every tenant-scoped API endpoint used the tenant-unaware `get_db` dependency instead of `get_tenant_db`, so the `app.tenant_id` GUC was never set on the request session. Postgres superusers silently bypass all RLS policies, so the breakage stayed hidden until a tenant followed STARTUP-01's `rls_posture` warn check and switched the app DB role to a non-superuser — at which point `POST /api/v1/workflows` immediately 500-ed with `InsufficientPrivilege: new row violates row-level security policy`. Header-based endpoints now use `Depends(get_tenant_db)`; path-based A2A endpoints keep `Depends(get_db)` plus an explicit `set_tenant_context(db, path_tenant_id)` call (they can't use `get_tenant_db` because it reads the `X-Tenant-Id` header, not the URL path). The change is mandatory for any non-superuser deployment — **run the app under a non-superuser role going forward** (STARTUP-01 will keep warning you otherwise). New `tests/test_rls_dependency_wired.py` guards the wiring.
>
> - **ADMIN-03 Per-tenant LLM provider credentials (2026-04-21)**: Google AI Studio / OpenAI / Anthropic API keys (and the OpenAI base URL) can now be per-tenant. Keys live in the existing Fernet-encrypted `tenant_secrets` vault under four well-known names (`LLM_GOOGLE_API_KEY`, `LLM_OPENAI_API_KEY`, `LLM_OPENAI_BASE_URL`, `LLM_ANTHROPIC_API_KEY`). New `engine/llm_credentials_resolver` threads tenant_id into all seven chat/agent/stream call sites with env fallback. Dialog lives behind the toolbar **Key** icon — password-masked inputs, per-field source badges (tenant override / env default / not configured), clear-to-reset buttons. New read-only `GET /api/v1/llm-credentials` endpoint returns per-provider status **without** ever exposing secret values. Vertex continues to use ADC + per-tenant project routing (VERTEX-02) — ADMIN-03 is for AI Studio / OpenAI / Anthropic. Embedding paths still use env keys — follow-up if tenants need per-tenant embedding billing.
>
> - **STARTUP-01 Preflight readiness checks (2026-04-21)**: FastAPI lifespan now runs seven preflight checks at boot — DB + alembic head, Redis `PING`, Celery worker heartbeat, RLS-posture (warn on superuser role), auth-mode coherence, vault key presence, MCP default server reachability. Each check returns a specific remediation string logged at INFO/WARNING/ERROR. New `/health/ready` endpoint runs the same checks live, returns **503** on any `fail`, **200** otherwise. Frontend `StartupHealthBanner` renders per-check remediation at the top of the UI. Tests gate the pass via `ORCHESTRATOR_SKIP_STARTUP_CHECKS=true` (set in `conftest.py`). **Would have caught the "workflow stuck at queued" issue** — `check_celery_workers` warns with the exact start command when `USE_CELERY=true` but no worker is connected. See `codewiki/startup-checks.md`.
>
> - **ADMIN-02 Per-tenant API rate limiting (2026-04-21)**: Turns on real per-tenant API rate limiting via a new `TenantRateLimitMiddleware` (Redis INCR+EXPIRE per `(tenant, time-bucket)`). Replaces the previously inert `slowapi.Limiter` which was instantiated but never wired into a middleware — the `RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW` env vars had no runtime effect pre-ADMIN-02. New columns `rate_limit_requests_per_window` + `rate_limit_window_seconds` on `tenant_policies` (migration `0021`); admin dialog gets two new rows. Old `ORCHESTRATOR_RATE_LIMIT_WINDOW` string ("1 minute") is deprecated in favour of a clean `ORCHESTRATOR_RATE_LIMIT_WINDOW_SECONDS: int`. Middleware fails open on Redis errors — a broken limit layer shouldn't hard-fail every endpoint. 429 response carries a `Retry-After: <seconds>` header.
>
> - **ADMIN-01 Per-tenant policy overrides (2026-04-21)**: Three operational knobs — `execution_quota_per_hour`, `max_snapshots`, `mcp_pool_size` — move from process-global env vars onto a new `tenant_policies` table (migration `0020`, one row per tenant). Operators override per-tenant via the toolbar **SlidersHorizontal** icon (``TenantPolicyDialog``). Env values remain the fallback when a column is null. New `engine/tenant_policy_resolver.get_effective_policy(tenant_id)` is the single read path; it degrades gracefully to env defaults on any DB error so quota checks can't 500 because of a transient `tenant_policies` outage. Resolver-returned `source` metadata lets the UI show which fields are overridden vs. inherited. **Scope caveats** in `codewiki/tenant-policies.md` §4: rate-limit / rate-window stay on env vars pending **ADMIN-02** (slowapi dynamic limits refactor); per-tenant LLM provider keys pending **ADMIN-03**.
>
> - **VERTEX-02 Per-tenant Vertex project override (2026-04-21)**: Vertex project + location are no longer process-global. Operators register per-tenant rows via the toolbar **Cloud** icon (``VertexProjectsDialog``). Rides on the existing ``tenant_integrations`` table with ``system='vertex'`` — ``config_json`` stores ``{project, location}``. Resolver precedence: tenant's ``is_default=true`` row → ``ORCHESTRATOR_VERTEX_PROJECT`` env fallback. Each tenant can bill Vertex usage to their own GCP project. **Caveat**: ADC (service-account identity) is still process-global — the orchestrator's service account needs ``aiplatform.user`` on every target project listed in the registry. Per-tenant service-account JSON uploads are not in scope here. No migration — reuses table from migration `0017`.
>
> - **VERTEX-01 Vertex AI support for LLM nodes (2026-04-21)**: Gemini models can now run through **Google Cloud Vertex AI** in addition to AI Studio. Adds ``vertex`` to the ``provider`` enum on LLM Agent, ReAct Agent, LLM Router, Reflection, and Intent Classifier nodes. Zero new dependencies — reuses the unified ``google-genai`` SDK via ``Client(vertexai=True, project, location)``. Auth uses Application Default Credentials (``GOOGLE_APPLICATION_CREDENTIALS`` env var pointing at a service-account JSON, or workload identity on GKE / Cloud Run). Reuses the existing ``ORCHESTRATOR_VERTEX_PROJECT`` + ``ORCHESTRATOR_VERTEX_LOCATION`` settings that were previously embeddings-only. See §7.1 below. Per-tenant Vertex project override tracked as VERTEX-02.
>
> - **API-18A In-app API Playground (2026-04-21)**: Toolbar **FlaskConical** button opens `ApiPlaygroundDialog` — JSON trigger-payload editor with inline parse errors, sync / async toggle, sync-timeout + deterministic-mode controls, live "Copy as curl" snippet that honours `VITE_API_URL` / `VITE_TENANT_ID` / `VITE_AUTH_MODE`, and a per-workflow last-10-runs history persisted to localStorage. Uses the existing `POST /api/v1/workflows/{id}/execute` endpoint end-to-end — no new backend surface. Disabled until a workflow is saved (needs a stored id). Pure helpers `lib/playgroundCurl.ts` (bash-safe curl generator) and `lib/playgroundHistory.ts` (localStorage ring buffer) have 18 vitest cases. Feature-roadmap item #18 → Partial; 18B embed widget deferred pending a written security design. See `codewiki/feature-roadmap.md` §18.
>
> - **Sprint 2B MCP Maturity (2026-04-21)**: MCP-01 audit of the client against the 2025-06-18 spec landed in `codewiki/mcp-audit.md` with a ranked gap list. MCP-02 per-tenant MCP server registry — new table `tenant_mcp_servers` (Alembic `0019`), `auth_mode` discriminator (`none` / `static_headers` / `oauth_2_1`), `{{ env.KEY }}` header placeholders resolved through the Secrets vault. Session pool + `list_tools` cache are re-keyed by `(tenant_id, server)` so tenants never share warm connections. Toolbar **Globe** icon → `McpServersDialog`. MCP Tool + ReAct Agent nodes accept an optional `mcpServerLabel` config field; blank → tenant default → legacy `MCP_SERVER_URL` env-var fallback. MCP-03..MCP-10 backlog tracked in `codewiki/feature-roadmap.md`. See `codewiki/mcp-audit.md`.
>
> - **Sprint 2A Developer Velocity (2026-04-20)**: DV-01..DV-07 shipped as seven incremental commits. **DV-01** data pinning — short-circuits `dispatch_node` on a pinned node output (pins live in `graph_json.nodes[*].data.pinnedOutput`, do NOT bump version). **DV-02** test single node — `POST …/nodes/{id}/test` runs one handler in isolation using upstream pins as synthetic context. **DV-03** sticky notes — non-executable canvas annotations filtered at `parse_graph`, `validateWorkflow`, and `computeNodeStatuses`. **DV-04** 45 expression helpers added to `safe_eval` (string / math / array / object / date / utility) plus `**` and `//` binary ops. **DV-05** duplicate workflow — deep-copies graph incl. pins with collision-safe `(copy N)` naming. **DV-06** hotkey cheatsheet — `?` opens modal; `Shift+S` / `1` / `Tab` registered with shared input-focus guard. **DV-07** active/inactive toggle — `workflow_definitions.is_active` (Alembic `0018`) filters Schedule Triggers; manual Run / PATCH / duplicate all still work. See `codewiki/dev-workflow.md`.
>
> - **AutomationEdge + async externals (2026-04-19)**: async-external node pattern with Beat-poll (Pattern C default) and webhook (Pattern A opt-in) completion, both resuming through `finalize_terminal`. New tables `async_jobs`, `tenant_integrations`, `scheduled_triggers` (Alembic `0015`, `0017`). `workflow_instances.suspended_reason` column distinguishes HITL-suspended (NULL) from async-external (`'async_external'`). Diverted pause-the-clock timeout model. See `codewiki/automationedge.md`.
>
> - **V0.9.13 Tier 1 UX (2026-04-10)**: Template gallery, sync execute (`§7.1.2`), debug replay in the Hub UI — no new migrations. See `TECHNICAL_BLUEPRINT.md` V0.9.13 and `HOW_IT_WORKS.md` Step 6.
>
> - **V0.9.11 Operator execution control (2026-03-22)**: `workflow_instances` gains `cancel_requested` and `pause_requested` (Alembic `0005`, `0006`). Run `alembic upgrade head` after pull. API: `POST …/pause`, `POST …/resume-paused`, `POST …/cancel` — see `TECHNICAL_BLUEPRINT.md` §6.11.
>
> - **V0.9 Execution Enhancements (2026-03-21)**: New env variables `ORCHESTRATOR_MAX_SNAPSHOTS` and `ORCHESTRATOR_MCP_POOL_SIZE`. ForEach loop node added to node_registry.json. MCP client upgraded with connection pooling. Retry-from-failed endpoint added. Snapshot pruning via Celery Beat. Safe expression evaluator enhanced with whitelisted function/method calls. Env variable mapping (`{{ env.SECRET_NAME }}`) for node configs.
> - **V0.8 Enterprise Features (2026-03-20)**:OIDC federation config + `VITE_AUTH_MODE`. New env variables for OIDC provider settings. `workflow_snapshots` table added (Alembic migration 0002). Project structure updated for new files. Troubleshooting table updated. Environment variable table expanded.
>
> - **Initial Setup (2026-03-20)**: V0.1 scaffold — frontend dev server, backend API, prerequisites, and configuration. See `TECHNICAL_BLUEPRINT.md` for architecture and `HOW_IT_WORKS.md` for runtime walkthrough.

## AE AI Hub — Orchestrator Setup Guide

**Advanced Memory note:** Advanced Memory v1 adds normalized conversation storage, memory profiles, semantic or episodic memory, relational entity facts, and new memory APIs. Fresh installs should simply run `alembic upgrade head`, which includes migration `0012`.

**Version:** 0.9.18 (Sprint 2A + 2B)
**Last updated:** 2026-04-21

---

### Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Project Structure](#2-project-structure)
3. [Frontend Setup](#3-frontend-setup)
4. [Backend Setup](#4-backend-setup)
5. [Database Setup](#5-database-setup)
6. [Running the Services](#6-running-the-services)
7. [Environment Variables](#7-environment-variables)
8. [Verifying the Installation](#8-verifying-the-installation)
9. [Development Workflow](#9-development-workflow)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Prerequisites

### 1.1 Software Requirements

| Software | Version | Purpose |
|----------|---------|---------|
| **Node.js** | >= 18.x | Frontend build and dev server |
| **npm** | >= 9.x | Frontend package manager |
| **Python** | >= 3.11 | Backend API and worker |
| **PostgreSQL** | >= 15 | Workflow state and execution logs |
| **Redis** | >= 7.x | Celery message broker, result backend, and OIDC PKCE state |

### 1.2 External Services

The orchestrator can run on its own. The only external runtime contracts are:

- **MCP Server(s)** (optional): Operators register zero or more per-tenant MCP servers in the `tenant_mcp_servers` table via the **Globe** icon in the toolbar (MCP-02). Each entry captures a Streamable-HTTP MCP URL + optional auth headers (static, with `{{ env.KEY }}` indirection through the Secrets vault). Nodes pick a server by its `mcpServerLabel` config field; blank → tenant `is_default` row → the legacy `ORCHESTRATOR_MCP_SERVER_URL` env-var fallback so pre-MCP-02 tenants keep working untouched. See `codewiki/mcp-audit.md`.
- **AutomationEdge / other async-external systems** (optional): Any node type that submits work to an external RPA / job-queue system (AutomationEdge today) uses the `async_jobs` table + `suspended_reason='async_external'` pattern. Beat polls terminal status by default; optional webhook callback via `POST /api/v1/async-jobs/{job_id}/complete`. See `codewiki/automationedge.md`.
- **Google Cloud Vertex AI** (optional): when nodes use `provider: "vertex"` (chat + ReAct + streaming via VERTEX-01) or Vertex-backed embeddings. Auth is ADC (`GOOGLE_APPLICATION_CREDENTIALS` or workload identity). Operators can register per-tenant GCP projects via the **Cloud** toolbar icon (VERTEX-02) — tenants bill to their own projects. **Scope caveat**: service-account identity is still process-global; see `codewiki/vertex.md` §5 before assuming per-tenant isolation is total.

### 1.3 Network Ports

| Service | Default Port | Configurable Via |
|---------|-------------|-----------------|
| Frontend (Vite dev server) | 8080 | `frontend/vite.config.ts` |
| Backend (FastAPI) | 8001 | `uvicorn` CLI argument |
| PostgreSQL | 5432 | `ORCHESTRATOR_DATABASE_URL` |
| Redis | 6379 | `ORCHESTRATOR_REDIS_URL` |
| MCP Server | 8000 | `ORCHESTRATOR_MCP_SERVER_URL` |

---

## 2. Project Structure

```
.
├── TECHNICAL_BLUEPRINT.md          # Architecture documentation
├── SETUP_GUIDE.md                  # This file
├── HOW_IT_WORKS.md                 # Runtime walkthrough
├── DEVELOPER_GUIDE.md              # Extend nodes, debugging, API deep dives
│
├── frontend/                       # React + TypeScript visual builder
│   └── src/
│       ├── App.tsx                 # Three-panel layout + OIDC auth gate
│       ├── store/
│       │   ├── flowStore.ts        # Zustand canvas state
│       │   └── workflowStore.ts    # Zustand workflow CRUD + execution
│       ├── types/nodes.ts          # Node types (palette sourced from registry)
│       ├── lib/
│       │   ├── api.ts              # Backend API client (Bearer + X-Tenant-Id)
│       │   ├── registry.ts         # node_registry.json consumer + helpers
│       │   └── utils.ts            # Tailwind cn() utility
│       └── components/
│           ├── auth/
│           │   └── LoginPage.tsx   # OIDC SSO login screen
│           ├── canvas/
│           │   └── FlowCanvas.tsx  # React Flow canvas
│           ├── nodes/
│           │   └── AgenticNode.tsx # Polymorphic custom node component
│           ├── sidebar/
│           │   ├── NodePalette.tsx         # Draggable node list
│           │   ├── PropertyInspector.tsx   # Selected-node config panel
│           │   └── DynamicConfigForm.tsx   # Schema-driven form renderer
│           ├── nodes/
│           │   ├── AgenticNode.tsx         # Polymorphic executable node
│           │   └── StickyNote.tsx          # DV-03 non-executable annotation node
│           ├── toolbar/
│           │   ├── Toolbar.tsx             # Save/Run/History/Active-toggle + dialog openers
│           │   ├── WorkflowListDialog.tsx  # Saved workflows — duplicate (DV-05) + inactive pills
│           │   ├── VersionHistoryDialog.tsx # Snapshot history + rollback
│           │   ├── ExecutionPanel.tsx      # SSE execution log viewer
│           │   ├── HotkeyCheatsheet.tsx    # DV-06 "?" modal
│           │   ├── IntegrationsDialog.tsx  # AutomationEdge tenant_integrations CRUD
│           │   ├── McpServersDialog.tsx    # MCP-02 tenant_mcp_servers CRUD
│           │   └── SecretsDialog.tsx       # {{ env.KEY }} vault CRUD
│           └── ui/                         # shadcn/ui components
│
├── backend/                        # FastAPI execution engine
│   ├── main.py                     # App entry point (v0.8.0)
│   ├── requirements.txt            # Python dependencies
│   ├── alembic.ini                 # Migration config
│   ├── alembic/versions/           # 0001 … 0019 — see §5.2
│   └── app/
│       ├── config.py               # Settings from env (incl. OIDC)
│       ├── database.py             # SQLAlchemy setup
│       ├── observability.py        # Langfuse tracing
│       ├── api/
│       │   ├── workflows.py        # CRUD + execute + pause/resume/cancel + versions + DV-01/02/05/07
│       │   ├── tools.py            # MCP palette + cache invalidation (tenant-scoped MCP-02)
│       │   ├── tenant_integrations.py  # AutomationEdge connection defaults
│       │   ├── tenant_mcp_servers.py   # MCP-02 per-tenant MCP server registry
│       │   ├── async_jobs.py       # Pattern A webhook callback (AE + future systems)
│       │   ├── sse.py              # Server-Sent Events stream
│       │   ├── schemas.py          # Pydantic request/response models
│       │   ├── conversations.py    # Conversation session inspection
│       │   ├── memory.py           # Memory profile CRUD + memory inspection
│       │   └── auth.py             # OIDC Authorization Code + PKCE flow
│       ├── engine/
│       │   ├── dag_runner.py       # Ready-queue DAG executor (sticky-note filter in parse_graph)
│       │   ├── node_handlers.py    # Per-type dispatch (pin short-circuit, mcpServerLabel)
│       │   ├── memory_service.py   # Advanced memory policy, summaries, retrieval, promotion
│       │   ├── llm_providers.py    # Google/OpenAI/Anthropic abstraction
│       │   ├── react_loop.py       # ReAct tool-calling loop
│       │   ├── mcp_client.py       # MCP SDK client, session pool keyed by (tenant, server)
│       │   ├── mcp_server_resolver.py  # MCP-02 label → URL + headers + auth-mode dispatch
│       │   ├── automationedge_client.py  # AE REST client (session-token + bearer modes)
│       │   ├── async_job_poller.py      # Diverted pause-the-clock timeout helpers
│       │   ├── async_job_finalizer.py   # Shared terminal-resume path (Pattern A + C)
│       │   ├── integration_resolver.py  # tenant_integrations label resolver (AE)
│       │   ├── prompt_template.py  # Jinja2 prompt templating
│       │   ├── safe_eval.py        # AST-based expression evaluator
│       │   ├── expression_helpers.py    # DV-04 — 45 safe_eval functions
│       │   └── config_validator.py # Graph config validation
│       ├── models/
│       │   ├── workflow.py         # WorkflowDefinition, Instance, Snapshot, Log
│       │   └── tenant.py           # TenantToolOverride, TenantSecret
│       ├── workers/
│       │   ├── celery_app.py       # Celery configuration
│       │   ├── tasks.py            # execute, resume, retry, resume_paused tasks
│       │   └── scheduler.py        # Celery Beat cron scheduler + snapshot pruning
│       └── security/
│           ├── jwt_auth.py         # JWT creation + validation
│           ├── vault.py            # Fernet-encrypted credential vault
│           ├── rate_limiter.py     # Per-tenant rate limiting
│           └── tenant.py           # get_tenant_id dependency
│
└── shared/
    └── node_registry.json          # Canonical node type schemas (source of truth for forms)
```

---

## 3. Frontend Setup

### 3.1 Install Dependencies

```bash
cd frontend
npm install
```

This installs React 19, `@xyflow/react`, Zustand, Tailwind CSS v4, shadcn/ui, and Lucide icons.

### 3.2 Start the Dev Server

```bash
npm run dev
```

The Vite dev server starts on **http://localhost:8080** with hot module replacement.

### 3.3 Production Build

```bash
npm run build
```

Output goes to `frontend/dist/`. Serve with any static file server or configure Vite preview:

```bash
npm run preview
```

### 3.4 Hub UI quick reference (V0.9.13)

| Feature | Where | Notes |
|---------|--------|--------|
| **Templates** | Toolbar (layout icon) | Starter DAGs, import/export JSON |
| **Sync run** | Checkbox next to **Run** | Same as `POST …/execute` with `sync: true` |
| **Debug** | Execution panel (after terminal run) | Checkpoint timeline + context replay |

Details: `HOW_IT_WORKS.md` Step 6, `TECHNICAL_BLUEPRINT.md` §4.5 / §6.10.

### 3.5 Type Checking

```bash
npx tsc -b --noEmit
```

---

## 4. Backend Setup

### 4.1 Create a Virtual Environment

```bash
cd backend
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate
```

### 4.2 Install Dependencies

```bash
pip install -r requirements.txt
```

This installs FastAPI, Celery, SQLAlchemy, MCP SDK, Langfuse, LLM provider SDKs, `authlib` (OIDC), `redis` (PKCE state), and all other dependencies.

### 4.3 Configure Environment

Create a `.env` file in `backend/` or set environment variables with the `ORCHESTRATOR_` prefix:

```env
# Required
ORCHESTRATOR_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ae_orchestrator
ORCHESTRATOR_REDIS_URL=redis://localhost:6379/0
ORCHESTRATOR_SECRET_KEY=your-secret-key-here

# Optional — MCP server
ORCHESTRATOR_MCP_SERVER_URL=http://localhost:8000/mcp

# Optional — LLM providers (at least one required for agent nodes)
ORCHESTRATOR_GOOGLE_API_KEY=your-google-key
ORCHESTRATOR_OPENAI_API_KEY=your-openai-key
ORCHESTRATOR_ANTHROPIC_API_KEY=your-anthropic-key

# Optional — OIDC federation (leave unset for dev mode)
ORCHESTRATOR_OIDC_ENABLED=false
ORCHESTRATOR_OIDC_ISSUER=https://accounts.google.com
ORCHESTRATOR_OIDC_CLIENT_ID=
ORCHESTRATOR_OIDC_CLIENT_SECRET=
ORCHESTRATOR_OIDC_REDIRECT_URI=http://localhost:8001/auth/oidc/callback
ORCHESTRATOR_OIDC_TENANT_CLAIM=email
```

---

## 5. Database Setup

### 5.1 Create the Database

```bash
psql -U postgres -c "CREATE DATABASE ae_orchestrator;"
```

### 5.2 Run Migrations

```bash
cd backend

# Apply all migrations (creates all tables including workflow_snapshots)
alembic upgrade head
```

This applies all revisions under `alembic/versions/`, including (among others):

- **0001** — PostgreSQL Row-Level Security policies for tenant isolation (workflow, tenant-secrets, tenant-tool-override tables)
- **0002** — `workflow_snapshots` table for version history
- **0003** — `conversation_sessions` (stateful DAG pattern)
- **0004** — `instance_checkpoints`
- **0005** — `workflow_instances.cancel_requested`
- **0006** — `workflow_instances.pause_requested`
- **0012** — advanced memory hard cutover: `conversation_messages`, `memory_profiles`, `memory_records`, `entity_facts`, and normalized conversation storage
- **0014** — RLS policies on the memory, conversation, A2A-key, and workflow-snapshot tables (closes the gap left by 0001)
- **0015** — `scheduled_triggers` table for atomic Beat schedule-fire dedupe (replaces the 55-second wall-clock guard)
- **0016** — pin pgvector embedding columns to `vector(1536)` and rebuild HNSW indexes — matches `text-embedding-3-small`; operators on a different embedding model must adjust before running
- **0017** — `async_jobs` (AutomationEdge poll queue with Diverted pause-the-clock accounting) + `tenant_integrations` (per-tenant external-system connection defaults) + `workflow_instances.suspended_reason` column
- **0018** — **DV-07** — `workflow_definitions.is_active BOOLEAN NOT NULL DEFAULT TRUE`. Existing rows backfill to active; Schedule Triggers skip `is_active=false` workflows (manual Run / PATCH / duplicate still work)
- **0019** — **MCP-02** — `tenant_mcp_servers` (per-tenant MCP registry with `auth_mode` discriminator + partial unique index enforcing one default per tenant) + empty `tenant_mcp_server_tool_fingerprints` side table forward-declared for MCP-06 drift detection

Use `alembic current` to verify the DB revision after upgrading.

### 5.2a PostgreSQL Row-Level Security — production hardening

**RLS is enforced only when the application connects as a non-superuser role.** Superusers bypass every RLS policy silently, so a misconfigured `DATABASE_URL` will leave cross-tenant reads wide open without any error.

For a production deployment:

```sql
-- One-time setup: create a dedicated application role.
CREATE ROLE ae_orchestrator_app WITH LOGIN PASSWORD 'change-me';
GRANT CONNECT ON DATABASE ae_orchestrator TO ae_orchestrator_app;
GRANT USAGE ON SCHEMA public TO ae_orchestrator_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ae_orchestrator_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ae_orchestrator_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ae_orchestrator_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO ae_orchestrator_app;
```

Then set `ORCHESTRATOR_DATABASE_URL=postgresql://ae_orchestrator_app:...@host/ae_orchestrator`.

Run `alembic upgrade head` as the Postgres superuser (it needs DDL privileges to ALTER TABLE). Run the application as the non-superuser role above.

The runtime sets `app.tenant_id` per-request via `get_tenant_db` (see `app/database.py`). Every `SessionLocal()` site in request, task, and engine code paths now calls `set_tenant_context(db, tenant_id)` immediately after opening the session.

**Celery Beat** (the scheduler in `app/workers/scheduler.py`) is the one exception — its tasks (`check_scheduled_workflows`, `prune_old_snapshots`, `archive_stale_conversation_episodes`) are inherently cross-tenant and cannot set a single `app.tenant_id`. Run Beat under a dedicated role that bypasses RLS:

```sql
CREATE ROLE ae_orchestrator_beat WITH LOGIN PASSWORD 'change-me' BYPASSRLS;
GRANT CONNECT ON DATABASE ae_orchestrator TO ae_orchestrator_beat;
GRANT USAGE ON SCHEMA public TO ae_orchestrator_beat;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ae_orchestrator_beat;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ae_orchestrator_beat;
```

Start Beat with its own `ORCHESTRATOR_DATABASE_URL` pointing at that role; keep uvicorn and Celery worker processes on the regular non-superuser app role.

### 5.2b Deferred Sprint 1 hardening — tracked, not yet implemented

Two Sprint 1 tickets ship as follow-up work because they need live infrastructure (a running Beat + Postgres, and Docker in CI respectively) to validate meaningfully:

- **S1-12 — Postgres-fixture integration tests.** Scaffold landed: `tests/integration/` with a `testcontainers`-driven pgvector container + non-superuser role provisioning + Alembic upgrade. Live tests cover the cross-tenant RLS breach on `memory_records` / `conversation_messages` and the end-to-end `scheduled_triggers` dedupe (matching the S1-02 unit tests at the DB level). Three stubs remain in `test_pending_followups.py` for the HITL `context_patch`, sub-workflow parent-instance cascade, and the Beat end-to-end race — each requires an LLM stub and more elaborate setup. Runs in the `backend-integration` CI job; auto-skips locally when Docker is unavailable.

- **Operator action carried over from this PR**: run the non-superuser DDL in §5.2a and the Beat BYPASSRLS DDL in §5.2a before pointing a new `ORCHESTRATOR_DATABASE_URL` at them. RLS enforcement only activates once this is done; until then migrations 0001 + 0014 remain silently bypassed (policies exist but the role is a superuser).

### 5.3 Schema Overview

```
workflow_definitions     1 ──── * workflow_instances     1 ──── * execution_logs
  id (PK, UUID)                   id (PK, UUID)                   id (PK, UUID)
  tenant_id                       tenant_id                       instance_id (FK)
  name                            workflow_def_id (FK)            node_id
  graph_json (JSONB)              status                          node_type
  version (bumped on save)        context_json (JSONB)            status
  created_at                      current_node_id                 input_json (JSONB)
  updated_at                      started_at                      output_json (JSONB)
                                  completed_at                    error
                                  cancel_requested (0005)
                                  pause_requested (0006)

workflow_definitions     1 ──── * workflow_snapshots
                                  id (PK, UUID)
                                  workflow_def_id (FK)
                                  tenant_id
                                  version (snapshot of)
                                  graph_json (JSONB)
                                  saved_at

conversation_sessions            conversation_messages
  id (PK, UUID)                  id (PK, UUID)
  session_id                     session_ref_id (FK)
  tenant_id                      tenant_id
  message_count                  session_id
  summary_text                   turn_index
  summary_through_turn           role, content, message_at

memory_profiles                  memory_records                  entity_facts
  id (PK, UUID)                  id (PK, UUID)                  id (PK, UUID)
  tenant_id                      tenant_id                      tenant_id
  workflow_def_id                scope, scope_key               entity_type, entity_key
  enabled_scopes                 kind, content                  fact_name, fact_value
  max_recent_tokens              embedding                      valid_from, valid_to
  history_order                  provenance                     provenance

tenant_tool_overrides            tenant_secrets
  id (PK, UUID)                  id (PK, UUID)
  tenant_id                      tenant_id
  tool_name                      key_name
  enabled                        encrypted_value
  config_json (JSONB)

scheduled_triggers               async_jobs                   tenant_integrations
  id (PK, UUID)                  id (PK, UUID)                id (PK, UUID)
  workflow_def_id (FK)           instance_id (FK)             tenant_id
  scheduled_for (minute-aligned) system, external_job_id      system, label
  instance_id (FK, nullable)     status, metadata_json        config_json (JSONB)
  created_at                     diverted_since, total_ms     is_default (partial unique)
                                 next_poll_at

tenant_mcp_servers               tenant_mcp_server_tool_fingerprints  (MCP-06; empty at MCP-02)
  id (PK, UUID)                  id (PK, UUID)
  tenant_id                      server_id (FK)
  label (unique per tenant)      tool_name
  url                            fingerprint_sha256
  auth_mode                      last_seen_at
  config_json (JSONB)
  is_default (partial unique)
```

---

## 6. Running the Services

### 6.1 Start All Services

Open separate terminals for each service:

**Terminal 1 — Frontend:**
```bash
cd frontend
npm run dev
```

**Terminal 2 — Backend API:**
```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

**Terminal 3 — Celery Worker:**
```bash
cd backend
celery -A app.workers.celery_app worker --loglevel=info
```

**Terminal 4 — Celery Beat (schedule triggers):**
```bash
cd backend
celery -A app.workers.celery_app beat --loglevel=info
```

**Terminal 5 — Redis** (if not already running):
```bash
redis-server
```

### 6.1.1 Local dev shortcut (no Celery / no Redis required)

By default, the backend can run workflow execution **in-process** (background threads) without Celery/Redis:

- Set `ORCHESTRATOR_USE_CELERY=false` (default).
- You still need PostgreSQL.
- Redis is only required if you enable features that depend on it (e.g. OIDC PKCE state, token streaming, or if you explicitly enable Celery).

```env
ORCHESTRATOR_USE_CELERY=false
```

Start only:

```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

### 6.2 Quick Start (Frontend Only)

If you just want to use the visual builder without backend execution:

```bash
cd frontend
npm run dev
```

Open **http://localhost:8080**. You can drag nodes, connect them, and configure properties. Workflow execution requires the backend services.

---

## 7. Environment Variables

Backend settings use the `ORCHESTRATOR_` prefix; frontend uses `VITE_` variables.

### 7.1 Backend Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ORCHESTRATOR_DATABASE_URL` | Yes | `postgresql://postgres:postgres@localhost:5432/ae_orchestrator` | PostgreSQL connection string |
| `ORCHESTRATOR_REDIS_URL` | Yes | `redis://localhost:6379/0` | Redis for Celery + OIDC PKCE state |
| `ORCHESTRATOR_MCP_SERVER_URL` | No | `http://localhost:8000/mcp` | Fallback MCP endpoint used only when a tenant has no `tenant_mcp_servers` row (per-tenant registry from MCP-02 takes precedence). |
| `ORCHESTRATOR_SECRET_KEY` | Yes | `change-me-in-production` | JWT signing key |
| `ORCHESTRATOR_CORS_ORIGINS` | No | `["http://localhost:8080"]` | Allowed CORS origins (JSON array) |
| `ORCHESTRATOR_GOOGLE_API_KEY` | No | `""` | **Fallback** Google AI Studio API key for `provider: "google"` nodes when the tenant has no `LLM_GOOGLE_API_KEY` vault row (ADMIN-03). Tenant overrides take precedence. |
| `ORCHESTRATOR_GOOGLE_PROJECT` | No | `""` | GCP project ID (legacy — not used by the Gemini path today) |
| `ORCHESTRATOR_GOOGLE_LOCATION` | No | `us-central1` | GCP region (legacy) |
| `ORCHESTRATOR_VERTEX_PROJECT` | When using `provider: "vertex"` without a tenant registry row, or Vertex embeddings | `""` | **Fallback** GCP project for Vertex AI (Gemini + embeddings). Per-tenant registry rows (VERTEX-02) override this. Set ADC via `GOOGLE_APPLICATION_CREDENTIALS` or workload identity — no API key. Full guide incl. scope caveats: `codewiki/vertex.md`. |
| `ORCHESTRATOR_VERTEX_LOCATION` | No | `us-central1` | Fallback Vertex AI region (overridden by the `location` field on a registry row) |
| `ORCHESTRATOR_OPENAI_API_KEY` | No | `""` | **Fallback** OpenAI API key when the tenant has no `LLM_OPENAI_API_KEY` vault row (ADMIN-03). |
| `ORCHESTRATOR_OPENAI_BASE_URL` | No | `https://api.openai.com/v1` | **Fallback** OpenAI-compatible base URL when the tenant has no `LLM_OPENAI_BASE_URL` vault row. |
| `ORCHESTRATOR_ANTHROPIC_API_KEY` | No | `""` | **Fallback** Anthropic API key when the tenant has no `LLM_ANTHROPIC_API_KEY` vault row (ADMIN-03). |
| `ORCHESTRATOR_AUTH_MODE` | No | `dev` | `dev` (X-Tenant-Id header) or `jwt` (Bearer token) |
| `ORCHESTRATOR_VAULT_KEY` | No | `""` | Fernet encryption key for credential vault |
| `ORCHESTRATOR_RATE_LIMIT_REQUESTS` | No | `100` | **Fallback** max API requests per tenant per window when no `tenant_policies.rate_limit_requests_per_window` override exists (ADMIN-02). |
| `ORCHESTRATOR_RATE_LIMIT_WINDOW` | No | `1 minute` | **DEPRECATED** — slowapi-format string, not actually consumed post-ADMIN-02. Use `ORCHESTRATOR_RATE_LIMIT_WINDOW_SECONDS` instead. |
| `ORCHESTRATOR_RATE_LIMIT_WINDOW_SECONDS` | No | `60` | **Fallback** rate-limit window duration in seconds when no `tenant_policies.rate_limit_window_seconds` override exists (ADMIN-02). Integer. |
| `ORCHESTRATOR_EXECUTION_QUOTA_PER_HOUR` | No | `50` | **Fallback** default when a tenant has no `tenant_policies.execution_quota_per_hour` override (ADMIN-01). Max workflow executions per tenant per hour. |
| `ORCHESTRATOR_USE_CELERY` | No | `false` | If `true`, dispatches execution/resume/retry via Celery (requires Redis + worker). If `false`, runs tasks in-process in background threads (local dev-friendly). |
| `ORCHESTRATOR_OIDC_ENABLED` | No | `false` | Enable OIDC Authorization Code + PKCE flow |
| `ORCHESTRATOR_OIDC_ISSUER` | No | `""` | OIDC provider issuer URL (e.g. `https://accounts.google.com`) |
| `ORCHESTRATOR_OIDC_CLIENT_ID` | No | `""` | OIDC application client ID |
| `ORCHESTRATOR_OIDC_CLIENT_SECRET` | No | `""` | OIDC application client secret |
| `ORCHESTRATOR_OIDC_REDIRECT_URI` | No | `http://localhost:8001/auth/oidc/callback` | Callback URL registered with the OIDC provider |
| `ORCHESTRATOR_OIDC_TENANT_CLAIM` | No | `email` | ID token claim used as `tenant_id` (e.g. `email`, `sub`, `org_id`) |
| `ORCHESTRATOR_OIDC_SCOPES` | No | `openid email profile` | OIDC scopes to request |
| `ORCHESTRATOR_MAX_SNAPSHOTS` | No | `20` | **Fallback** retention cap when a tenant has no `tenant_policies.max_snapshots` override (ADMIN-01). 0 = unlimited. Pruned daily by Celery Beat. |
| `ORCHESTRATOR_MCP_POOL_SIZE` | No | `4` | **Fallback** warm-session count per `(tenant, server)` MCP pool when a tenant has no `tenant_policies.mcp_pool_size` override (ADMIN-01). Applies at pool construction; existing pools keep their size. |

### 7.1.1 Langfuse observability (optional)

The orchestrator backend supports optional **Langfuse** tracing (workflow traces, per-node spans, LLM generations, and tool spans).

- **Enablement model**: Langfuse uses the shared `LANGFUSE_*` variables (no `ORCHESTRATOR_` prefix). If `LANGFUSE_ENABLED` is unset or falsey, the backend uses no-op stubs (no tracing overhead).
- **Where to set these**: add them to `backend/.env` (recommended) or export them in the shell before starting `uvicorn` / `celery`.

```env
# Langfuse Observability (Optional)
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com   # or your self-hosted URL (e.g. http://localhost:3000)
# LANGFUSE_RELEASE=0.9.11
```

**Verify it’s working:**

1. Start the backend (`uvicorn`) and worker (`celery`) with `LANGFUSE_ENABLED=true`.
2. Run any workflow from the UI.
3. Open your Langfuse project and confirm you see a trace for the workflow execution with nested node spans and (when applicable) LLM/tool observations.

### 7.1.2 Synchronous execution (API hold-open)

By default, `POST /api/v1/workflows/{workflow_id}/execute` returns **202 Accepted** with an `InstanceOut` and runs the DAG via Celery (or the in-process worker when `ORCHESTRATOR_USE_CELERY=false`). Callers poll `GET …/instances/{id}` or subscribe to SSE.

For **API-first** integrations that cannot poll, set **`sync: true`** on the execute body. The server runs `execute_graph` inline (in a worker thread), waits until the instance reaches a terminal status (`completed`, `failed`, `suspended`, `cancelled`, or `paused`), and returns **HTTP 200** with the final context:

| Field | Meaning |
|-------|---------|
| `instance_id` | Same as async `InstanceOut.id` |
| `status` | Terminal workflow status |
| `started_at` / `completed_at` | From `workflow_instances` |
| `output` | `context_json` with internal `_…` keys stripped (same rule as HITL context) |

**Limits:** `sync_timeout` (default **120**, max **3600** seconds) bounds the wait; exceeding it returns **504**. Long-running or HITL-heavy flows should stay async. Sync mode **bypasses Celery** for that request even when Celery is enabled.

**Example:**

```bash
curl -sS -X POST "http://localhost:8001/api/v1/workflows/$WORKFLOW_ID/execute" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: default" \
  -d '{"trigger_payload":{"message":"hello"},"sync":true,"sync_timeout":60}'
```

The AE AI Hub toolbar also exposes a **Sync run** checkbox next to **Run** for quick testing from the UI.

### 7.3 Step-by-step recipes

#### 7.3.1 Local development (single machine)

1. **PostgreSQL**: create DB and run migrations:

```bash
psql -U postgres -c "CREATE DATABASE ae_orchestrator;"
cd backend
alembic upgrade head
```

2. **Backend `.env`**: create `backend/.env`:

```env
ORCHESTRATOR_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ae_orchestrator
ORCHESTRATOR_SECRET_KEY=dev-secret

# Local dev: no Celery required
ORCHESTRATOR_USE_CELERY=false

# Optional LLM provider keys
ORCHESTRATOR_GOOGLE_API_KEY=
ORCHESTRATOR_OPENAI_API_KEY=
ORCHESTRATOR_ANTHROPIC_API_KEY=
```

3. **Start backend**:

```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

4. **Start frontend**:

```bash
cd frontend
npm run dev
```

5. **Optional: enable Langfuse** by adding `LANGFUSE_*` to `backend/.env` and restarting the backend.

#### 7.3.2 Production (recommended)

Use Celery for durable async execution and scheduling.

1. **Provision dependencies**:
   - PostgreSQL (persistent)
   - Redis (persistent; shared by API + worker + beat)
   - Optional: Langfuse (Cloud or self-hosted)

2. **Configure env** (prefer an OS secret store or an `.env` file only readable by the service account):

```env
ORCHESTRATOR_DATABASE_URL=postgresql://...
ORCHESTRATOR_REDIS_URL=redis://redis:6379/0
ORCHESTRATOR_SECRET_KEY=change-me-in-production
ORCHESTRATOR_USE_CELERY=true

# Security hardening
ORCHESTRATOR_AUTH_MODE=jwt
ORCHESTRATOR_VAULT_KEY=...
ORCHESTRATOR_CORS_ORIGINS=["https://your-orchestrator-ui.example.com"]
```

3. **Run migrations** (once per deploy):

```bash
cd backend
alembic upgrade head
```

4. **Run services** (separate processes/containers):
   - **API**: `uvicorn main:app --host 0.0.0.0 --port 8001`
   - **Worker**: `celery -A app.workers.celery_app worker --loglevel=info`
   - **Beat**: `celery -A app.workers.celery_app beat --loglevel=info`

5. **Put a reverse proxy in front** (TLS termination + request limits). Ensure the frontend points at the proxy via `VITE_API_URL`.

### 7.2 Frontend Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `VITE_API_URL` | No | `http://localhost:8001` | Backend base URL |
| `VITE_TENANT_ID` | No | `default` | Tenant ID sent as `X-Tenant-Id` in dev mode |
| `VITE_AUTH_MODE` | No | `""` | Set to `oidc` to show the SSO login gate and use Bearer tokens |

---

## 8. Verifying the Installation

### 8.1 Frontend

1. Open **http://localhost:8080**.
2. You should see a three-panel layout: Node Palette (left), Canvas (center), Properties (right).
3. Drag a "Webhook Trigger" from the palette onto the canvas.
4. Drag an "LLM Agent" and connect the Trigger's output handle to the Agent's input handle.
5. Click the Agent node — the Property Inspector should show dynamically generated fields: Provider dropdown, Model dropdown, System Prompt textarea, Temperature input, Max Tokens input.
6. If a workflow has been saved, the Toolbar shows a **History** (clock) button. Click it to view saved snapshots.

### 8.2 Backend API

```bash
# Health check
curl http://localhost:8001/health
# Expected: {"status":"ok","service":"ae-ai-hub-orchestrator"}

# OpenAPI docs
# Open http://localhost:8001/docs in a browser
```

### 8.3 API Smoke Test

```bash
# Create a workflow
curl -X POST http://localhost:8001/api/v1/workflows \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: test-tenant" \
  -d '{
    "name": "Hello World",
    "graph_json": {
      "nodes": [
        {"id": "node_1", "type": "agenticNode", "position": {"x": 0, "y": 0},
         "data": {"label": "Webhook Trigger", "nodeCategory": "trigger", "config": {"method": "POST", "path": "/webhook"}}},
        {"id": "node_2", "type": "agenticNode", "position": {"x": 300, "y": 0},
         "data": {"label": "LLM Agent", "nodeCategory": "agent", "config": {"provider": "google", "model": "gemini-2.5-flash", "systemPrompt": "You are helpful."}}}
      ],
      "edges": [
        {"id": "e1-2", "source": "node_1", "target": "node_2"}
      ]
    }
  }'

# List workflows
curl http://localhost:8001/api/v1/workflows \
  -H "X-Tenant-Id: test-tenant"

# List version history (after saving the workflow a second time)
curl http://localhost:8001/api/v1/workflows/{workflow_id}/versions \
  -H "X-Tenant-Id: test-tenant"

# Invalidate the MCP tool cache (after deploying new MCP tools)
curl -X POST http://localhost:8001/api/v1/tools/invalidate-cache \
  -H "X-Tenant-Id: test-tenant"

# DV-05 — duplicate a workflow (creates a "(copy)"-suffixed clone)
curl -X POST http://localhost:8001/api/v1/workflows/$WORKFLOW_ID/duplicate \
  -H "X-Tenant-Id: test-tenant"

# DV-07 — toggle a workflow inactive (Schedule Triggers will stop firing)
curl -X PATCH http://localhost:8001/api/v1/workflows/$WORKFLOW_ID \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: test-tenant" \
  -d '{"is_active": false}'

# DV-01 — pin a node output
curl -X POST "http://localhost:8001/api/v1/workflows/$WORKFLOW_ID/nodes/node_2/pin" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: test-tenant" \
  -d '{"output": {"response": "canned", "usage": {"tokens": 5}}}'

# DV-02 — probe a single node's handler in isolation
curl -X POST "http://localhost:8001/api/v1/workflows/$WORKFLOW_ID/nodes/node_2/test" \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: test-tenant" \
  -d '{"trigger_payload": {"message": "hi"}}'

# MCP-02 — register a per-tenant MCP server (static-headers auth with vault indirection)
curl -X POST http://localhost:8001/api/v1/tenant-mcp-servers \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: test-tenant" \
  -d '{
    "label": "github",
    "url": "https://mcp.github.com/mcp",
    "auth_mode": "static_headers",
    "config_json": {"headers": {"Authorization": "Bearer {{ env.GH_TOKEN }}"}},
    "is_default": true
  }'
```

### 8.4 OIDC Login (when enabled)

```bash
# Redirect URL to initiate login flow
open http://localhost:8001/auth/oidc/login

# After callback, returns:
# {"access_token": "eyJ...", "token_type": "bearer", "tenant_id": "user@example.com"}
```

---

## 9. Development Workflow

### 9.1 Frontend Development

- **Hot reload:** Vite automatically reloads on file changes.
- **Adding shadcn components:** `npx shadcn@latest add <component-name>` inside `frontend/`.
- **Import alias:** Use `@/` to reference `src/` (e.g. `import { cn } from "@/lib/utils"`).

### 9.2 Backend Development

- **Auto-reload:** `uvicorn main:app --reload` watches for file changes.
- **Adding models:** Define in `app/models/`, import in `app/models/__init__.py`, then run `alembic revision --autogenerate -m "description"` and `alembic upgrade head`.
- **OpenAPI docs:** Available at `http://localhost:8001/docs` (Swagger) and `http://localhost:8001/redoc`.

### 9.3 Adding a New Node Type

With V0.8 dynamic forms, adding a new node type only requires changes in two places:

1. **Shared schema:** Add the node type to `shared/node_registry.json` — define `type`, `category`, `label`, `description`, `icon`, and `config_schema`. The frontend property form is generated automatically from the schema. Use `enum` for dropdowns, `min`/`max` for number fields.

2. **Backend handler:** Add or extend a handler in `backend/app/engine/node_handlers.py` to implement the node's execution logic.

The frontend palette and property forms update automatically — no frontend code changes needed.

### 9.4 Generating a Vault Key

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Set the output as `ORCHESTRATOR_VAULT_KEY`.

### 9.5 Generating a Dev JWT

```bash
curl "http://localhost:8001/auth/token?tenant_id=my-tenant"
# Returns: {"access_token": "eyJ...", "token_type": "bearer", "tenant_id": "my-tenant"}
```

Only works when `ORCHESTRATOR_AUTH_MODE=dev`. Use the token as `Authorization: Bearer <token>` in subsequent requests.

---

## 10. Troubleshooting

| Problem | Likely Cause | Solution |
|---------|-------------|----------|
| Frontend blank page | CSS not loading | Ensure `index.css` has `@import "tailwindcss"` at top |
| `Module not found: @/...` | Path alias misconfigured | Check `tsconfig.json` has `"paths": {"@/*": ["./src/*"]}` and `vite.config.ts` has `resolve.alias` |
| shadcn init fails | Missing Tailwind or alias | Run `npm install tailwindcss @tailwindcss/vite` and ensure tsconfig has path alias |
| Backend import error | Missing dependencies | Run `pip install -r requirements.txt` in the venv |
| `401 Missing X-Tenant-Id` | No tenant header in request | Set `VITE_TENANT_ID` for the frontend or add `-H "X-Tenant-Id: your-tenant"` to curl |
| Celery tasks not executing | Redis not running | Start Redis with `redis-server` |
| Migration fails | DB doesn't exist | Create it: `psql -U postgres -c "CREATE DATABASE ae_orchestrator;"` |
| MCP tools endpoint empty | MCP server not running or URL incorrect | Start any compatible Streamable HTTP MCP server and set `ORCHESTRATOR_MCP_SERVER_URL` |
| Canvas nodes not appearing | Drag-and-drop broken | Check browser console for JS errors; ensure `ReactFlowProvider` wraps the app |
| Property form shows no fields | Label not in node_registry.json | Verify `data.label` matches a `label` value in `shared/node_registry.json` |
| Version History button missing | Workflow not saved yet | Save the workflow first — the History button only appears for persisted workflows |
| `POST /rollback/{v}` returns 404 | Snapshot not found | The version must be a snapshot (saved before a previous overwrite). Check `GET /versions` first |
| OIDC login redirects to error | Wrong redirect_uri | Ensure `ORCHESTRATOR_OIDC_REDIRECT_URI` matches exactly what is registered in the IdP |
| OIDC state expired | User took >5 minutes | Retry login — PKCE state TTL is 5 minutes |
| ReAct agent has no tools | MCP server offline at startup | Cache empty — restart backend after starting MCP server, or hit `POST /api/v1/tools/invalidate-cache` |
| Retry returns 404 | Instance not in `failed` status | Only failed instances can be retried. Check `GET /instances/{id}` status |
| ForEach does nothing | `arrayExpression` resolves to empty | Ensure the upstream node outputs an array at the expected path |
| Snapshot pruning not running | Celery Beat not started | Start Celery Beat: `celery -A app.workers.celery_app beat --loglevel=info` |
| `{{ env.SECRET }}` not resolved | Secret not in vault | Add the secret via the Secrets dialog or `POST /api/v1/secrets` first |
| MCP Tool node returns `error: "No MCP server named '<label>' for tenant ..."` | Registry row missing or wrong label | Open the Globe dialog and register a server with that label, or clear `mcpServerLabel` to use the tenant default |
| MCP calls fail with `auth_mode=oauth_2_1 not yet implemented` | Registry row was created with an auth mode the runtime doesn't support yet | OAuth is tracked as MCP-03. Change the row to `none` or `static_headers`, or wait for MCP-03 |
| Schedule Trigger not firing after a cron change | Workflow is `is_active=false` | Toolbar → Active/Inactive toggle (next to the version badge); Beat filter skips inactive workflows (DV-07) |
| Sticky note is treated as a real node and fails validation | Old client cached | Reload the page — the canvas uses a `stickyNote` React Flow type introduced in DV-03 |
| `POST …/nodes/{id}/test` returns `Node suspended on external system 'automationedge'` | AE-style node was probed — `async_jobs` row written as a side effect | Expected for DV-02 probes of async-external nodes. Beat's poller will resume or abandon the orphan row through normal channels; no cleanup needed |

---

**Document version:** 0.9.18 (Sprint 2A + 2B)
**Last updated:** 2026-04-21
