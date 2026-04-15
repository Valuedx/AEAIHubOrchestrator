# Database Schema

PostgreSQL 16 with the `pgvector` extension. All tables use `UUID` primary keys, `timestamptz` for dates, and `tenant_id` for multi-tenant isolation via Row Level Security (see [Security](security.md)).

---

## Tables at a glance

| Table | Purpose | RLS |
|-------|---------|-----|
| `workflow_definitions` | Stored workflow DAGs | Yes |
| `workflow_instances` | Execution runs | Yes |
| `workflow_snapshots` | Versioned graph history | Yes |
| `execution_logs` | Per-node execution records | Yes |
| `instance_checkpoints` | Context snapshots after each node | Yes |
| `conversation_sessions` | Multi-turn chat history | Yes |
| `a2a_api_keys` | Hashed A2A inbound keys | Yes |
| `tenant_tool_overrides` | Per-tenant MCP tool visibility | Yes |
| `tenant_secrets` | Fernet-encrypted env vars | Yes |
| `knowledge_bases` | Knowledge base definitions | Yes |
| `kb_documents` | Uploaded documents | Yes |
| `kb_chunks` | Embedded text chunks (pgvector) | Yes |
| `embedding_cache` | Precomputed intent embeddings (pgvector) | Yes |

---

## Workflow tables

### `workflow_definitions`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `tenant_id` | `VARCHAR(64)` | NOT NULL, indexed | |
| `name` | `VARCHAR(256)` | NOT NULL | |
| `description` | `TEXT` | nullable | |
| `graph_json` | `JSONB` | NOT NULL | `{ nodes: [...], edges: [...] }` |
| `version` | `INTEGER` | NOT NULL, default 1 | Auto-incremented on graph update |
| `is_published` | `BOOLEAN` | NOT NULL, default false | Visible in A2A agent card when true |
| `created_at` | `TIMESTAMPTZ` | | |
| `updated_at` | `TIMESTAMPTZ` | | Auto-updated |

**Indexes:** `ix_wf_def_tenant_name` on `(tenant_id, name)`.

### `workflow_instances`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `tenant_id` | `VARCHAR(64)` | NOT NULL, indexed | |
| `workflow_def_id` | `UUID` | FK → `workflow_definitions.id` CASCADE | |
| `status` | `VARCHAR(32)` | NOT NULL, default `queued`, indexed | Values: `queued`, `running`, `completed`, `failed`, `suspended`, `paused`, `cancelled` |
| `trigger_payload` | `JSONB` | nullable | Input data from execute request |
| `definition_version_at_start` | `INTEGER` | nullable | Snapshot of definition version at queue time |
| `context_json` | `JSONB` | NOT NULL, default `{}` | Accumulated node outputs |
| `current_node_id` | `VARCHAR(128)` | nullable | |
| `started_at` | `TIMESTAMPTZ` | nullable | |
| `completed_at` | `TIMESTAMPTZ` | nullable | |
| `created_at` | `TIMESTAMPTZ` | | |
| `cancel_requested` | `BOOLEAN` | NOT NULL, default false | DAG runner checks between nodes |
| `pause_requested` | `BOOLEAN` | NOT NULL, default false | DAG runner checks between nodes |
| `parent_instance_id` | `UUID` | FK → `workflow_instances.id` CASCADE, nullable | Parent instance for sub-workflow children |
| `parent_node_id` | `VARCHAR(128)` | nullable | Node ID in the parent workflow that spawned this child |

**Indexes:** `ix_wf_inst_tenant_status` on `(tenant_id, status)`, `ix_wf_inst_parent` on `(parent_instance_id)`.

### `workflow_snapshots`

Immutable copy of a workflow graph, created before each overwrite.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `workflow_def_id` | `UUID` | FK → `workflow_definitions.id` CASCADE, indexed | |
| `tenant_id` | `VARCHAR(64)` | NOT NULL, indexed | |
| `version` | `INTEGER` | NOT NULL | |
| `graph_json` | `JSONB` | NOT NULL | |
| `saved_at` | `TIMESTAMPTZ` | | |

**Indexes:** `ix_snapshot_def_version` on `(workflow_def_id, version)` UNIQUE.

### `execution_logs`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `instance_id` | `UUID` | FK → `workflow_instances.id` CASCADE, indexed | |
| `node_id` | `VARCHAR(128)` | NOT NULL | |
| `node_type` | `VARCHAR(64)` | NOT NULL | |
| `status` | `VARCHAR(32)` | NOT NULL, default `pending` | |
| `input_json` | `JSONB` | nullable | |
| `output_json` | `JSONB` | nullable | |
| `error` | `TEXT` | nullable | |
| `started_at` | `TIMESTAMPTZ` | nullable | |
| `completed_at` | `TIMESTAMPTZ` | nullable | |

### `instance_checkpoints`

Point-in-time snapshot of execution context after each node completes.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `instance_id` | `UUID` | FK → `workflow_instances.id` CASCADE, indexed | |
| `node_id` | `VARCHAR(128)` | NOT NULL | |
| `context_json` | `JSONB` | NOT NULL | Internal `_`-prefixed keys stripped |
| `saved_at` | `TIMESTAMPTZ` | | |

**Indexes:** `ix_checkpoint_instance_node` on `(instance_id, node_id)`.

---

## Conversation tables

### `conversation_sessions`

Persistent multi-turn conversation history for the Stateful Re-Trigger Pattern.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `session_id` | `VARCHAR(256)` | NOT NULL | External identifier |
| `tenant_id` | `VARCHAR(64)` | NOT NULL, indexed | |
| `messages` | `JSONB` | NOT NULL, default `[]` | Array of `{ role, content, timestamp }` |
| `created_at` | `TIMESTAMPTZ` | | |
| `updated_at` | `TIMESTAMPTZ` | | Auto-updated |

**Indexes:** `ix_conv_session_tenant_session` on `(tenant_id, session_id)` UNIQUE.

---

## A2A tables

### `a2a_api_keys`

Hashed inbound API keys for external A2A agents. Raw key returned only at creation, never stored.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `tenant_id` | `VARCHAR(64)` | NOT NULL, indexed | |
| `label` | `VARCHAR(128)` | NOT NULL | Human-readable name |
| `key_hash` | `VARCHAR(64)` | NOT NULL | SHA-256 hex digest |
| `created_at` | `TIMESTAMPTZ` | | |

**Indexes:** `ix_a2a_key_hash` on `(key_hash)` UNIQUE.
**Constraints:** `uq_a2a_key_tenant_label` UNIQUE on `(tenant_id, label)`.

---

## Tenant tables

### `tenant_tool_overrides`

Per-tenant visibility and configuration overrides for MCP tools.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `tenant_id` | `VARCHAR(64)` | NOT NULL, indexed | |
| `tool_name` | `VARCHAR(256)` | NOT NULL | |
| `enabled` | `BOOLEAN` | NOT NULL, default true | |
| `config_json` | `JSONB` | nullable | |
| `created_at` | `TIMESTAMPTZ` | | |
| `updated_at` | `TIMESTAMPTZ` | | Auto-updated |

**Indexes:** `ix_tool_override_tenant_tool` on `(tenant_id, tool_name)` UNIQUE.

### `tenant_secrets`

Fernet-encrypted key-value secrets per tenant, exposed as `{{ env.KEY }}` in node configs.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `tenant_id` | `VARCHAR(64)` | NOT NULL, indexed | |
| `key` | `VARCHAR(256)` | NOT NULL | |
| `encrypted_value` | `TEXT` | NOT NULL | Fernet-encrypted |
| `created_at` | `TIMESTAMPTZ` | | |
| `updated_at` | `TIMESTAMPTZ` | | Auto-updated |

---

## Knowledge Base tables

### `knowledge_bases`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `tenant_id` | `VARCHAR(64)` | NOT NULL, indexed | |
| `name` | `VARCHAR(256)` | NOT NULL | |
| `description` | `TEXT` | nullable | |
| `embedding_provider` | `VARCHAR(32)` | default `openai` | `openai`, `google`, `vertex` |
| `embedding_model` | `VARCHAR(128)` | default `text-embedding-3-small` | |
| `embedding_dimension` | `INTEGER` | default 1536 | Set from registry at creation |
| `vector_store` | `VARCHAR(32)` | default `pgvector` | `pgvector` or `faiss` |
| `chunking_strategy` | `VARCHAR(32)` | default `recursive` | `recursive`, `token`, `markdown`, `semantic` |
| `chunk_size` | `INTEGER` | default 1000 | Characters (recursive/markdown) or tokens (token) |
| `chunk_overlap` | `INTEGER` | default 200 | |
| `semantic_threshold` | `FLOAT` | nullable | Cosine similarity threshold for semantic chunking |
| `document_count` | `INTEGER` | default 0 | Count of "ready" documents |
| `status` | `VARCHAR(32)` | default `ready` | |
| `created_at` | `TIMESTAMPTZ` | | |
| `updated_at` | `TIMESTAMPTZ` | | Auto-updated |

**Indexes:** `ix_kb_tenant_name` on `(tenant_id, name)`.

### `kb_documents`

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `kb_id` | `UUID` | FK → `knowledge_bases.id` CASCADE | |
| `tenant_id` | `VARCHAR(64)` | NOT NULL, indexed | |
| `filename` | `VARCHAR(512)` | NOT NULL | |
| `content_type` | `VARCHAR(128)` | NOT NULL | |
| `file_size` | `INTEGER` | NOT NULL | |
| `chunk_count` | `INTEGER` | | Populated after ingestion |
| `status` | `VARCHAR(32)` | | `pending` → `processing` → `ready` / `failed` |
| `error` | `TEXT` | nullable | Error message if ingestion failed |
| `metadata_json` | `JSONB` | nullable | |
| `created_at` | `TIMESTAMPTZ` | | |

**Indexes:** `ix_kb_doc_tenant_kb` on `(tenant_id, kb_id)`.

### `kb_chunks`

Used by the **pgvector** backend. FAISS stores vectors in local files instead.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `document_id` | `UUID` | FK → `kb_documents.id` CASCADE | |
| `kb_id` | `UUID` | FK → `knowledge_bases.id` CASCADE | |
| `tenant_id` | `VARCHAR(64)` | NOT NULL, indexed | |
| `content` | `TEXT` | NOT NULL | Chunk text |
| `chunk_index` | `INTEGER` | NOT NULL | Sequential index within document |
| `embedding` | `VECTOR` | | pgvector column, added in migration |
| `metadata_json` | `JSONB` | nullable | Chunker metadata (e.g. heading path) |
| `created_at` | `TIMESTAMPTZ` | | |

**Indexes:** `ix_kb_chunks_embedding` HNSW index on `embedding vector_cosine_ops`.

---

## Embedding Cache

### `embedding_cache`

Generic tenant-scoped vector cache for precomputed embeddings (used by Intent Classifier nodes with `cacheEmbeddings=true`).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `tenant_id` | `VARCHAR(64)` | NOT NULL | |
| `text_hash` | `VARCHAR(64)` | NOT NULL | SHA-256 hex digest of normalized text |
| `text` | `TEXT` | NOT NULL | Original text that was embedded |
| `provider` | `VARCHAR(32)` | NOT NULL | Embedding provider (`openai`, `google`, `vertex`) |
| `model` | `VARCHAR(128)` | NOT NULL | Embedding model name |
| `embedding` | `VECTOR` | | pgvector column, added in migration |
| `created_at` | `TIMESTAMPTZ` | | |

**Indexes:**
- `ix_emb_cache_lookup` on `(tenant_id, provider, model, text_hash)` UNIQUE — fast cache lookup
- `ix_emb_cache_embedding` HNSW index on `embedding vector_cosine_ops` — similarity search

**RLS:** `tenant_isolation_embedding_cache` policy (same pattern as all other tables).

**Design notes:** Keyed by content hash, not by workflow or node ID. The same text embedded with the same provider/model is cached once and reused across all workflows in the tenant. When intents change, new hashes are computed; old entries remain (harmless, content-addressed).

---

## Migration history

| # | File | What it does |
|---|------|-------------|
| 0000 | `0000_initial_schema.py` | `workflow_definitions`, `workflow_instances`, `execution_logs`, `tenant_tool_overrides`, `tenant_secrets` |
| 0001 | `0001_enable_rls_policies.py` | Enable RLS + `tenant_isolation` policies on all existing tables |
| 0002 | `0002_workflow_snapshots.py` | `workflow_snapshots` table |
| 0003 | `0003_conversation_sessions.py` | `conversation_sessions` table |
| 0004 | `0004_instance_checkpoints.py` | `instance_checkpoints` table |
| 0005 | `0005_workflow_cancel_requested.py` | Add `cancel_requested` column to `workflow_instances` |
| 0006 | `0006_workflow_pause_requested.py` | Add `pause_requested` column to `workflow_instances` |
| 0007 | `0007_a2a_support.py` | `a2a_api_keys` table + `is_published` on `workflow_definitions` |
| 0008 | `0008_instance_definition_version_at_start.py` | Add `definition_version_at_start` to `workflow_instances` |
| 0009 | `0009_add_knowledge_base_tables.py` | `CREATE EXTENSION vector`, `knowledge_bases`, `kb_documents`, `kb_chunks` with HNSW index + RLS |
| 0010 | `0010_add_embedding_cache.py` | `embedding_cache` table with pgvector `VECTOR` column, HNSW cosine index, and RLS |
| 0011 | `0011_add_subworkflow_parent_tracking.py` | Add `parent_instance_id` (FK), `parent_node_id` columns and `ix_wf_inst_parent` index to `workflow_instances` |

### Running migrations

```bash
cd backend
alembic upgrade head
```

Each migration uses a linear revision chain (`revises` points to the previous migration). All are **non-destructive** upgrades; downgrade functions exist but should be used with caution in production.
