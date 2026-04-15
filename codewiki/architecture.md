# Architecture

## System overview

AE AI Hub Orchestrator is a **no-code visual DAG workflow builder** for agentic AI. Users assemble workflows on a drag-and-drop canvas, connecting triggers, LLM agents, tool calls, logic gates, and knowledge retrieval nodes into directed acyclic graphs. The backend executes these graphs node-by-node, streaming progress to the frontend over SSE.

```
┌─────────────────────────────────────────────────────────┐
│                      Frontend                           │
│   React 19 · Vite · React Flow · Zustand · Tailwind    │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────────┐ │
│  │  Toolbar  │  │  Canvas  │  │  Property Inspector   │ │
│  │ (actions) │  │  (DAG)   │  │  (node config forms)  │ │
│  └──────────┘  └──────────┘  └───────────────────────┘ │
│  ┌──────────┐  ┌──────────────────────────────────────┐ │
│  │  Node    │  │       Execution Panel (SSE logs)     │ │
│  │  Palette │  └──────────────────────────────────────┘ │
│  └──────────┘                                           │
└──────────────────────────┬──────────────────────────────┘
                           │ REST + SSE
┌──────────────────────────▼──────────────────────────────┐
│                      Backend                            │
│              FastAPI · SQLAlchemy · Alembic              │
│                                                         │
│  ┌─────────┐  ┌───────────┐  ┌────────────────────────┐│
│  │   API   │  │ DAG Engine│  │     Node Handlers      ││
│  │ Routers │──│ dag_runner│──│ trigger/agent/action/   ││
│  │         │  │           │  │ logic/knowledge/nlp     ││
│  └─────────┘  └───────────┘  └────────────────────────┘│
│  ┌─────────┐  ┌───────────┐  ┌────────────────────────┐│
│  │ Workers │  │ LLM / MCP │  │   RAG Engine           ││
│  │ (Celery)│  │ Providers │  │ embed → chunk → store  ││
│  └─────────┘  └───────────┘  └────────────────────────┘│
└──────────────────────────┬──────────────────────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
   ┌───────────┐    ┌───────────┐    ┌──────────────┐
   │ PostgreSQL │    │   Redis   │    │  MCP Server  │
   │ (pgvector) │    │ (optional)│    │  (external)  │
   └───────────┘    └───────────┘    └──────────────┘
```

## Component responsibilities

### Frontend

| Component | File(s) | Role |
|-----------|---------|------|
| **App shell** | `App.tsx` | Single-page layout; optional OIDC gate |
| **Toolbar** | `toolbar/Toolbar.tsx` | Workflow save, run, templates, versions, knowledge bases |
| **Canvas** | `canvas/FlowCanvas.tsx` | React Flow graph; drag-drop from palette; edge creation |
| **Node Palette** | `sidebar/NodePalette.tsx` | Draggable node types grouped by category |
| **Property Inspector** | `sidebar/DynamicConfigForm.tsx` | Config form generated from `node_registry.json` schema |
| **Execution Panel** | `canvas/ExecutionPanel.tsx` | Live SSE stream of node logs and status |
| **Stores** | `store/flowStore.ts`, `store/workflowStore.ts` | Zustand stores for graph state and workflow metadata |
| **API client** | `lib/api.ts` | Typed fetch wrappers for every backend endpoint |

### Backend

| Component | File(s) | Role |
|-----------|---------|------|
| **App entrypoint** | `main.py` | FastAPI app, CORS, router mounting, lifespan |
| **Workflow API** | `api/workflows.py` | CRUD, execute, instances, versions, rollback |
| **SSE API** | `api/sse.py` | `GET /stream` — Server-Sent Events for execution |
| **Knowledge API** | `api/knowledge.py` | KB CRUD, document upload, search |
| **Tools API** | `api/tools.py` | MCP tool listing |
| **Conversations API** | `api/conversations.py` | Chat session management |
| **A2A API** | `api/a2a.py` | Agent-to-Agent protocol, keys, discovery |
| **DAG engine** | `engine/dag_runner.py` | Topological execution, loop/forEach, parallelism |
| **Node handlers** | `engine/node_handlers.py` | `dispatch_node` — routes to category/label-specific handler |
| **Notification handler** | `engine/notification_handler.py` | Channel-aware notification dispatch (Slack, Teams, Discord, Telegram, WhatsApp, PagerDuty, email, generic webhook) |
| **Intent Classifier** | `engine/intent_classifier.py` | Hybrid intent scoring (lexical + embedding + optional LLM fallback) |
| **Entity Extractor** | `engine/entity_extractor.py` | Rule-based entity extraction (regex, enum, number, date, free_text) with LLM fallback |
| **Embedding cache** | `engine/embedding_cache_helper.py` | DB-backed embedding cache with save-time precompute for intent vectors |
| **LLM providers** | `engine/llm_providers.py` | OpenAI, Anthropic, Google GenAI abstraction |
| **MCP client** | `engine/mcp_client.py` | Streamable HTTP MCP SDK client |
| **RAG engine** | `engine/chunker.py`, `embedding_provider.py`, `ingestor.py`, `retriever.py` | Document ingestion and retrieval pipelines |
| **Vector stores** | `engine/vector_store/` | Pluggable backends: pgvector, FAISS |
| **Workers** | `workers/tasks.py` | Celery tasks (workflow execution, document ingestion) |
| **Config** | `config.py` | Pydantic Settings with `ORCHESTRATOR_` prefix |
| **Database** | `database.py` | SQLAlchemy engine, session factory, RLS tenant setter |
| **Security** | `security/` | JWT, vault, rate limiter, tenant extraction |

### Shared

| File | Role |
|------|------|
| `node_registry.json` | Canonical definition of every node type — category, label, config schema, defaults |

### Infrastructure

| Service | Image | Purpose |
|---------|-------|---------|
| PostgreSQL | `pgvector/pgvector:pg16` | Primary data store with vector extension |
| Redis | `redis:7-alpine` | Celery broker/result backend (optional) |
| MCP Server | External | Tool execution server the orchestrator calls as a client |

## Request lifecycle

### Workflow execution (async)

```
Frontend                        Backend                              Workers
   │                               │                                    │
   │  POST /execute                │                                    │
   │  { trigger_payload }          │                                    │
   │──────────────────────────────▶│                                    │
   │                               │  Create WorkflowInstance           │
   │                               │  (status: pending)                 │
   │       202 { instance_id }     │                                    │
   │◀──────────────────────────────│                                    │
   │                               │  Dispatch to Celery / thread       │
   │                               │─────────────────────────────────▶  │
   │  GET /stream (SSE)            │                                    │
   │──────────────────────────────▶│   dag_runner.run_dag()             │
   │                               │      │                             │
   │  ◀─── event: node_start ─────│◀─────│  dispatch_node()            │
   │  ◀─── event: node_done  ─────│◀─────│  (LLM / MCP / RAG / ...)   │
   │  ◀─── event: node_start ─────│◀─────│  next node...              │
   │  ...                          │      │                             │
   │  ◀─── event: completed  ─────│◀─────│  done                      │
   │                               │                                    │
```

### Workflow execution (sync)

When `sync: true` is passed to the execute endpoint, the backend runs the DAG in-process and returns the final output directly in the HTTP response (with configurable timeout, default 120s, returns 504 on timeout).

### Document ingestion (async)

```
Frontend                        Backend API                     Worker / In-process
   │                               │                                │
   │  POST /documents              │                                │
   │  (multipart file upload)      │                                │
   │──────────────────────────────▶│                                │
   │                               │  Create KBDocument (pending)   │
   │       202 { document }        │                                │
   │◀──────────────────────────────│                                │
   │                               │  ingest_document_task.delay()  │
   │                               │──────────────────────────────▶ │
   │                               │                                │  parse_document()
   │                               │                                │  chunk_text()
   │                               │                                │  get_embeddings_batch_sync()
   │                               │                                │  vector_store.add_embeddings()
   │  (polls GET /documents)       │                                │  doc.status = "ready"
   │──────────────────────────────▶│◀───────────────────────────────│
   │       200 [{ status: ready }] │                                │
   │◀──────────────────────────────│                                │
```

## DAG engine

The DAG engine (`engine/dag_runner.py`) performs **topological execution** of the workflow graph:

1. **Parse** the `graph_json` into nodes and edges.
2. **Topological sort** to determine execution order.
3. **Execute** each node via `dispatch_node`, passing in a context dict containing outputs from upstream nodes.
4. **Special control flow**: ForEach (fan-out / fan-in), Loop (iteration with break condition), Merge (join), Condition (branching).
5. **Stream** execution events (node start, node done, errors) to SSE subscribers.
6. **Persist** execution logs to the `execution_logs` table.
7. **Checkpointing**: HITL nodes (Human Approval, Bridge User Reply) pause the instance and await callback.

## Multi-tenancy

Every request carries a `tenant_id`. The backend sets `SET LOCAL app.tenant_id` on the database session, activating PostgreSQL Row Level Security policies on all tables. This provides defense-in-depth: even if application-level filters are bypassed, the database enforces tenant isolation.

See [Security](security.md) for full details.
