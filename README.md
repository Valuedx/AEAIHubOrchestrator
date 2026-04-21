# AE AI Hub Orchestrator

Portable visual workflow orchestration service built as:

- `frontend/`: React + Vite + React Flow canvas
- `backend/`: FastAPI execution engine + optional Celery workers
- `shared/`: node registry consumed by both frontend and backend

Key capabilities: multi-provider LLM agents (Google AI Studio, **Google Cloud Vertex AI with per-tenant project routing**, OpenAI, Anthropic), ReAct tool-calling loops, advanced memory management (normalized conversation storage, rolling summaries, semantic/episodic/entity memory, memory profiles, and inspection APIs), RAG knowledge bases, NLP nodes (Intent Classifier with hybrid scoring, Entity Extractor with rule-based + LLM fallback), sub-workflows / nested workflow execution, A2A protocol, HITL approval gates, operator pause/resume/cancel, version history with rollback, 8-channel notifications, **per-tenant MCP server registry** (MCP-02), **AutomationEdge async-external integration** (Pattern A webhook + Pattern C Beat poll, Diverted pause-the-clock), **Sprint 2A developer velocity** (data pinning, test single node, sticky notes, 45 expression helpers, duplicate workflow, hotkey cheatsheet, active/inactive toggle), **in-app API Playground** (API-18A — JSON payload editor + sync/async toggle + live "Copy as curl" + last-10-runs history), and visual debug replay.

This subtree is designed to be lift-and-shift into its own repository. It does not import code from any parent application. External integrations are runtime contracts only:

- PostgreSQL for workflow state
- Optional Redis for Celery, token streaming, and OIDC PKCE state
- Optional MCP server for tool discovery and tool execution
- Optional upstream callers that trigger workflows over HTTP

## Quick Start

0. Local services

```powershell
docker compose up -d
```

1. Backend

```powershell
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
Copy-Item .env.example .env
python scripts/ensure_db.py
alembic upgrade head
uvicorn main:app --reload --port 8001
```

2. Frontend

```powershell
cd frontend
npm ci
Copy-Item .env.example .env
npm run dev
```

3. Open `http://localhost:8080`

4. Optional worker

```powershell
cd backend
venv\Scripts\activate
celery -A app.worker worker --loglevel=info
```

## Optional Integrations

- MCP tools: point `ORCHESTRATOR_MCP_SERVER_URL` at any Streamable HTTP MCP server.
- Auth: use `ORCHESTRATOR_AUTH_MODE=dev` for local `X-Tenant-Id` auth, or `jwt` for Bearer auth.
- Upstream callers: see `examples/python_client.py` for a minimal execute + poll client.
- Local infra: `docker-compose.yml` starts PostgreSQL and Redis with the defaults used by `backend/.env.example`.
- CI: `.github/workflows/ci.yml` runs backend portability tests plus a frontend production build.

## Docs

- `SETUP_GUIDE.md`
- `HOW_IT_WORKS.md`
- `TECHNICAL_BLUEPRINT.md`
- `DEVELOPER_GUIDE.md`
- `codewiki/memory-management.md`
- `codewiki/automationedge.md` — AutomationEdge async-external node (Pattern C / Pattern A, Diverted handling, operator setup)
- `codewiki/vertex.md` — Google Cloud Vertex AI integration end-to-end. ADC setup, per-tenant project routing (VERTEX-02), and the **scope caveat** around per-tenant *identity* (which VERTEX-02 does NOT provide). Read before multi-tenant compliance decisions.
- `codewiki/tenant-policies.md` — ADMIN-01 per-tenant overrides for execution quota, snapshot retention, and MCP pool size. §4 enumerates every other `ORCHESTRATOR_*` env var and why it's moveable / deferred / permanently not.
- `codewiki/dev-workflow.md` — Sprint 2A developer-velocity features (DV-01 pinning, DV-02 test-single-node, DV-03 sticky notes, DV-04 expression helpers, DV-05 duplicate workflow, DV-06 hotkey cheatsheet, DV-07 active/inactive toggle)
- `codewiki/mcp-audit.md` — Sprint 2B MCP maturity (MCP-01 audit against the 2025-06-18 spec + MCP-02 per-tenant MCP server registry; MCP-03..MCP-10 backlog)
- `codewiki/feature-roadmap.md` — Gap analysis vs. LangGraph / Dify / n8n / Flowise / CrewAI / Rivet with 25 ranked items
