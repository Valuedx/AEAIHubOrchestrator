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
| [RAG & Knowledge Base](rag-knowledge-base.md) | Vector stores, embedding providers, chunking strategies, ingestion and retrieval pipelines |
| [Frontend Guide](frontend-guide.md) | React component tree, Zustand stores, canvas, toolbar, and sidebar |
| [Deployment](deployment.md) | Docker Compose, environment variables, Celery, migrations |
| [Security](security.md) | Authentication modes, multi-tenancy, Row Level Security, vault, rate limits |
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
│   ├── alembic/        Migration scripts (0000 – 0012)
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
- **Tenant ID** is passed via `X-Tenant-Id` header in dev mode, or extracted from a JWT in production.
- **Memory docs** live in [Memory Management](memory-management.md); use that page for the normalized conversation and semantic/entity memory model introduced in migration `0012`.
