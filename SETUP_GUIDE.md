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

**Version:** 0.9.16
**Last updated:** 2026-04-15

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

- **MCP Server** (optional): The orchestrator connects via MCP SDK over Streamable HTTP transport. Point `ORCHESTRATOR_MCP_SERVER_URL` at any compatible Streamable HTTP MCP endpoint. If unavailable, the tools endpoint returns an empty list and the frontend uses its built-in palette. The ReAct agent will have no tools to call.

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
│           ├── toolbar/
│           │   ├── Toolbar.tsx             # Save/Run/History buttons
│           │   ├── WorkflowListDialog.tsx  # Saved workflows browser
│           │   ├── VersionHistoryDialog.tsx # Snapshot history + rollback
│           │   └── ExecutionPanel.tsx      # SSE execution log viewer
│           └── ui/                         # shadcn/ui components
│
├── backend/                        # FastAPI execution engine
│   ├── main.py                     # App entry point (v0.8.0)
│   ├── requirements.txt            # Python dependencies
│   ├── alembic.ini                 # Migration config
│   ├── alembic/versions/           # 0001 … 0012 — see §5.2
│   └── app/
│       ├── config.py               # Settings from env (incl. OIDC)
│       ├── database.py             # SQLAlchemy setup
│       ├── observability.py        # Langfuse tracing
│       ├── api/
│       │   ├── workflows.py        # CRUD + execute + pause/resume/cancel + versions
│       │   ├── tools.py            # MCP palette + cache invalidation
│       │   ├── sse.py              # Server-Sent Events stream
│       │   ├── schemas.py          # Pydantic request/response models
│       │   ├── conversations.py    # Conversation session inspection
│       │   ├── memory.py           # Memory profile CRUD + memory inspection
│       │   └── auth.py             # OIDC Authorization Code + PKCE flow
│       ├── engine/
│       │   ├── dag_runner.py       # Ready-queue DAG executor
│       │   ├── node_handlers.py    # Per-type dispatch
│       │   ├── memory_service.py   # Advanced memory policy, summaries, retrieval, promotion
│       │   ├── llm_providers.py    # Google/OpenAI/Anthropic abstraction
│       │   ├── react_loop.py       # ReAct tool-calling loop
│       │   ├── mcp_client.py       # MCP SDK client (TTL cache)
│       │   ├── prompt_template.py  # Jinja2 prompt templating
│       │   ├── safe_eval.py        # AST-based expression evaluator (whitelisted functions V0.9)
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
| `ORCHESTRATOR_MCP_SERVER_URL` | No | `http://localhost:8000/mcp` | MCP server Streamable HTTP endpoint |
| `ORCHESTRATOR_SECRET_KEY` | Yes | `change-me-in-production` | JWT signing key |
| `ORCHESTRATOR_CORS_ORIGINS` | No | `["http://localhost:8080"]` | Allowed CORS origins (JSON array) |
| `ORCHESTRATOR_GOOGLE_API_KEY` | No | `""` | Google AI API key for Gemini models |
| `ORCHESTRATOR_GOOGLE_PROJECT` | No | `""` | GCP project ID (optional for Vertex AI) |
| `ORCHESTRATOR_GOOGLE_LOCATION` | No | `us-central1` | GCP region |
| `ORCHESTRATOR_OPENAI_API_KEY` | No | `""` | OpenAI API key for GPT models |
| `ORCHESTRATOR_OPENAI_BASE_URL` | No | `https://api.openai.com/v1` | OpenAI-compatible base URL |
| `ORCHESTRATOR_ANTHROPIC_API_KEY` | No | `""` | Anthropic API key for Claude models |
| `ORCHESTRATOR_AUTH_MODE` | No | `dev` | `dev` (X-Tenant-Id header) or `jwt` (Bearer token) |
| `ORCHESTRATOR_VAULT_KEY` | No | `""` | Fernet encryption key for credential vault |
| `ORCHESTRATOR_RATE_LIMIT_REQUESTS` | No | `100` | Max API requests per tenant per window |
| `ORCHESTRATOR_RATE_LIMIT_WINDOW` | No | `1 minute` | Rate limit time window |
| `ORCHESTRATOR_EXECUTION_QUOTA_PER_HOUR` | No | `50` | Max workflow executions per tenant per hour |
| `ORCHESTRATOR_USE_CELERY` | No | `false` | If `true`, dispatches execution/resume/retry via Celery (requires Redis + worker). If `false`, runs tasks in-process in background threads (local dev-friendly). |
| `ORCHESTRATOR_OIDC_ENABLED` | No | `false` | Enable OIDC Authorization Code + PKCE flow |
| `ORCHESTRATOR_OIDC_ISSUER` | No | `""` | OIDC provider issuer URL (e.g. `https://accounts.google.com`) |
| `ORCHESTRATOR_OIDC_CLIENT_ID` | No | `""` | OIDC application client ID |
| `ORCHESTRATOR_OIDC_CLIENT_SECRET` | No | `""` | OIDC application client secret |
| `ORCHESTRATOR_OIDC_REDIRECT_URI` | No | `http://localhost:8001/auth/oidc/callback` | Callback URL registered with the OIDC provider |
| `ORCHESTRATOR_OIDC_TENANT_CLAIM` | No | `email` | ID token claim used as `tenant_id` (e.g. `email`, `sub`, `org_id`) |
| `ORCHESTRATOR_OIDC_SCOPES` | No | `openid email profile` | OIDC scopes to request |
| `ORCHESTRATOR_MAX_SNAPSHOTS` | No | `20` | Max snapshots to keep per workflow (0 = unlimited). Pruned daily by Celery Beat |
| `ORCHESTRATOR_MCP_POOL_SIZE` | No | `4` | Number of warm MCP client sessions in the connection pool |

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
| `{{ env.SECRET }}` not resolved | Secret not in vault | Add the secret via the vault API first |

---

**Document version:** 0.9.16
**Last updated:** 2026-04-15
