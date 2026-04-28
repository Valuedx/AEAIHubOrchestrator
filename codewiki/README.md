# AE AI Hub Orchestrator — Code Wiki

Internal documentation for the AE AI Hub Orchestrator codebase. Start here and follow links to the topic you need.

## Contents

| Document | What it covers |
|----------|---------------|
| [Architecture](architecture.md) | System overview, component map, request lifecycle, DAG engine |
| [API Reference](api-reference.md) | Every REST endpoint — method, path, request/response schemas |
| [Database Schema](database-schema.md) | All tables, columns, indexes, RLS policies, migration history |
| [Memory Management](memory-management.md) | Advanced memory architecture, storage model, runtime assembly, profiles, and inspection APIs |
| [Node Types](node-types.md) | Registry of trigger, agent, action, logic, knowledge, notification, and NLP nodes with config schemas |
| [Notification Guide](notification-guide.md) | User guide for the Notification node — channel setup, config value sources, examples, troubleshooting |
| [AutomationEdge Node](automationedge.md) | Async-external RPA integration — setup, both completion modes (Beat poll / webhook), Diverted pause-the-clock, cancellation caveats, troubleshooting |
| [Developer Workflow](dev-workflow.md) | Sprint 2A developer-velocity features — data pinning (DV-01), test single node (DV-02), sticky notes (DV-03), expression helpers (DV-04), duplicate workflow (DV-05), hotkey cheatsheet (DV-06), active/inactive toggle (DV-07) |
| [MCP Audit](mcp-audit.md) | Sprint 2B — current MCP client vs. 2025-06-18 spec; ranked gap list and the per-tenant server registry (MCP-01 + MCP-02) |
| [Vertex AI Integration](vertex.md) | VERTEX-01 + VERTEX-02 end-to-end — ADC setup, per-tenant project routing, and the scope caveat around per-tenant *identity* (which is NOT what VERTEX-02 provides). **Read before making multi-tenant compliance decisions.** |
| [Tenant Policies](tenant-policies.md) | ADMIN-01 + ADMIN-02 — per-tenant override of execution quota, snapshot retention, MCP pool size, API rate limit. Env vars become fallbacks. Section 4 enumerates every other env var and why it was / wasn't moved. |
| [Startup Checks & Readiness](startup-checks.md) | STARTUP-01 — seven preflight checks (DB + migrations, Redis, Celery workers, RLS posture, auth-mode coherence, vault key, MCP default probe). Results surface in uvicorn logs, `/health/ready` JSON, and a UI banner. **Read before debugging a "my workflow sat at queued forever" problem.** |
| [Workflow Authoring Copilot](copilot.md) | COPILOT-01 — draft-workspace safety boundary (migration `0022`), pure tool layer (`add_node`, `connect_nodes`, `validate_graph`, …), optimistic-concurrency `version` column, `base_version_at_fork` race guard on `/promote`. Agent runner, chat pane, and debug-loop are COPILOT-01b/02/03. |
| [Cyclic Graphs](cyclic-graphs.md) | CYCLIC-01 — loopback edges (`type: "loopback"` + `maxIterations`) for agent↔tool, reflection, and retry patterns. Forward subgraph stays a DAG; validator + copilot lints keep cycles authorable; canvas auto-detects drag-to-ancestor. |
| [Human-in-the-Loop](hitl.md) | HITL-01 — approval audit log, claimed-identity capture, pending-approvals toolbar badge, timeout enforcement (planned). |
| [RAG & Knowledge Base](rag-knowledge-base.md) | Vector stores, embedding providers, chunking strategies, ingestion and retrieval pipelines |
| [Frontend Guide](frontend-guide.md) | React component tree, Zustand stores, canvas, toolbar, and sidebar |
| [Deployment](deployment.md) | Docker Compose, environment variables, Celery, migrations |
| [Security](security.md) | Authentication modes (dev / jwt / **local password LOCAL-AUTH-01** / OIDC), multi-tenancy, Row Level Security, vault, rate limits |
| [Feature Roadmap](feature-roadmap.md) | Gap analysis vs. competitors, 20 missing features with priority and status |

## Quick orientation

```
AEAIHubOrchestrator/
├── backend/            FastAPI app, Alembic, workers, engine
│   ├── app/
│   │   ├── api/        REST routers (workflows, tools, sse, conversations, memory, a2a, knowledge)
│   │   ├── engine/     DAG runner, node handlers, memory service, LLM providers, MCP client, RAG engine
│   │   ├── models/     SQLAlchemy ORM models, including advanced memory tables
│   │   ├── security/   JWT, vault, rate limiter, tenant helpers
│   │   └── workers/    Celery app, tasks, Beat scheduler
│   ├── alembic/        Migration scripts (0000 – 0033)
│   └── main.py         App entrypoint, router wiring
├── frontend/           React + Vite + React Flow
│   └── src/
│       ├── components/ Canvas, nodes, sidebar, toolbar, auth, UI primitives
│       ├── lib/        api.ts, registry, templates, validation
│       ├── store/      flowStore (graph), workflowStore (metadata)
│       └── types/      TypeScript type definitions
├── shared/             node_registry.json (canonical node definitions)
├── docker-compose.yml  PostgreSQL (pgvector) + Redis
└── codewiki/           ← you are here
```

## Conventions used in these docs

- **Env vars** use the prefix `ORCHESTRATOR_` (e.g. `ORCHESTRATOR_DATABASE_URL`).
- **API paths** are relative to the backend root (default `http://localhost:8000`).
- **Tenant ID** is passed via `X-Tenant-Id` header in dev mode, or extracted from a JWT in production. Local-password mode (`ORCHESTRATOR_AUTH_MODE=local`) mints the JWT via `POST /auth/local/login`; see [Security](security.md) §Local password mode.
- **Memory docs** live in [Memory Management](memory-management.md); use that page for the normalized conversation and semantic/entity memory model introduced in migration `0012`.
