# AE AI Hub Orchestrator

Portable visual workflow orchestration service built as:

- `frontend/`: React + Vite + React Flow canvas
- `backend/`: FastAPI execution engine + optional Celery workers
- `shared/`: node registry consumed by both frontend and backend

Key capabilities: multi-provider LLM agents, ReAct tool-calling loops, RAG knowledge bases, NLP nodes (Intent Classifier with hybrid scoring, Entity Extractor with rule-based + LLM fallback), A2A protocol, HITL approval gates, operator pause/resume/cancel, version history with rollback, 8-channel notifications, MCP tool integration, and visual debug replay.

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
