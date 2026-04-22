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
| `conversation_sessions` | Conversation session metadata + rolling summary state | Yes |
| `conversation_messages` | Normalized append-only conversation turns | Yes |
| `memory_profiles` | Tenant/workflow memory policies | Yes |
| `memory_records` | Semantic and episodic memory rows | Yes |
| `entity_facts` | Relational entity memory | Yes |
| `a2a_api_keys` | Hashed A2A inbound keys | Yes |
| `tenant_tool_overrides` | Per-tenant MCP tool visibility | Yes |
| `tenant_secrets` | Fernet-encrypted env vars | Yes |
| `knowledge_bases` | Knowledge base definitions | Yes |
| `kb_documents` | Uploaded documents | Yes |
| `kb_chunks` | Embedded text chunks (pgvector) | Yes |
| `embedding_cache` | Precomputed intent embeddings (pgvector) | Yes |
| `conversation_episodes` | Issue-scoped conversation memory with checkpointed summaries (0013) | Yes |
| `scheduled_triggers` | Atomic claim rows for Beat schedule-fire dedupe (0015) | No (cross-tenant) |
| `async_jobs` | External-system job tracking for AE-style suspended nodes (0017) | No (FK-scoped via instance) |
| `tenant_integrations` | Per-tenant connection defaults for external systems (0017) | Yes |
| `tenant_mcp_servers` | Per-tenant MCP server registry (0019) | Yes |
| `tenant_mcp_server_tool_fingerprints` | Forward-declared side table for MCP-06 drift detection (0019) | No (FK-scoped) |
| `tenant_policies` | Per-tenant operational knobs + SMART-XX feature flags (0020 / 0021 / 0024 / 0025) | Yes |
| `workflow_drafts` | Copilot draft workspace — ephemeral graph being edited, promoted into `workflow_definitions` on accept (COPILOT-01a, migration 0022) | Yes |
| `copilot_sessions` | One chat session per draft; holds provider + model for the agent loop (0022) | Yes |
| `copilot_turns` | Ordered user / assistant / tool messages replayed on reopen (0022) | Yes |
| `copilot_accepted_patterns` | SMART-02 — snapshot of every promoted draft (graph + NL intent + tags) so the agent can retrieve nearest prior patterns as few-shot (0026) | Yes |

**DV-07 (migration 0018):** `workflow_definitions.is_active BOOLEAN NOT NULL DEFAULT TRUE` — when false, Schedule Triggers skip the workflow. Manual Run, PATCH, and duplicate all stay active.

**COPILOT-01b.ii.b (migration 0023):** `workflow_definitions.is_ephemeral BOOLEAN NOT NULL DEFAULT FALSE` — the copilot's `execute_draft` runner tool creates transient WorkflowDefinition rows marked `is_ephemeral=true` so the engine can run them. Filtered out of `list_workflows`, scheduler scan, and A2A agent card. Reaped by `cleanup_ephemeral_workflows`.

**SMART-04 + SMART-06 + SMART-02 (migrations 0024 + 0025 + 0026):** `tenant_policies.smart_04_lints_enabled`, `smart_06_mcp_discovery_enabled`, and `smart_02_pattern_library_enabled` — all BOOLEAN NOT NULL DEFAULT TRUE — per-tenant opt-out flags for the three copilot intelligence features shipped so far. Same column template applies to SMART-01/03/05 as they ship.

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

Persistent session metadata for the Stateful Re-Trigger Pattern.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `session_id` | `VARCHAR(256)` | NOT NULL | External identifier |
| `tenant_id` | `VARCHAR(64)` | NOT NULL, indexed | |
| `message_count` | `INTEGER` | NOT NULL, default `0` | Total normalized turns in `conversation_messages` |
| `last_message_at` | `TIMESTAMPTZ` | nullable | Timestamp of newest turn |
| `summary_text` | `TEXT` | nullable | Rolling summary of older turns |
| `summary_updated_at` | `TIMESTAMPTZ` | nullable | |
| `summary_through_turn` | `INTEGER` | NOT NULL, default `0` | Highest turn index already summarized |
| `created_at` | `TIMESTAMPTZ` | | |
| `updated_at` | `TIMESTAMPTZ` | | Auto-updated |

**Indexes:** `ix_conv_session_tenant_session` on `(tenant_id, session_id)` UNIQUE.

### `conversation_messages`

Normalized conversation turns. This replaced the legacy `conversation_sessions.messages` JSONB transcript in migration `0012`.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `session_ref_id` | `UUID` | FK -> `conversation_sessions.id` CASCADE, indexed | |
| `tenant_id` | `VARCHAR(64)` | NOT NULL, indexed | |
| `session_id` | `VARCHAR(256)` | NOT NULL, indexed | External session ID copied for lookup/debug |
| `turn_index` | `INTEGER` | NOT NULL | Stable ordering within the session |
| `role` | `VARCHAR(32)` | NOT NULL | `user`, `assistant`, or future roles |
| `content` | `TEXT` | NOT NULL | |
| `message_at` | `TIMESTAMPTZ` | NOT NULL | |
| `workflow_def_id` | `UUID` | nullable, indexed | Provenance |
| `instance_id` | `UUID` | nullable, indexed | Provenance |
| `node_id` | `VARCHAR(128)` | nullable | Provenance |
| `idempotency_key` | `VARCHAR(128)` | nullable | Server-derived retry dedupe key |
| `created_at` | `TIMESTAMPTZ` | | |

**Indexes:**

- `ix_conv_msg_session_turn` on `(session_ref_id, turn_index)` UNIQUE
- `ix_conv_msg_session_idem_role` on `(session_ref_id, idempotency_key, role)` UNIQUE

### `memory_profiles`

Tenant- or workflow-scoped advanced-memory policy.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `tenant_id` | `VARCHAR(64)` | NOT NULL, indexed | |
| `workflow_def_id` | `UUID` | FK -> `workflow_definitions.id` CASCADE, nullable, indexed | Null = tenant profile |
| `name` | `VARCHAR(256)` | NOT NULL | |
| `description` | `TEXT` | nullable | |
| `is_default` | `BOOLEAN` | NOT NULL, default false | At most one tenant default and one workflow default |
| `instructions_text` | `TEXT` | nullable | Auto-injected system instructions |
| `enabled_scopes` | `JSONB` | NOT NULL | Any of `session`, `workflow`, `tenant`, `entity` |
| `max_recent_tokens` | `INTEGER` | NOT NULL | Token budget for raw turns |
| `max_semantic_hits` | `INTEGER` | NOT NULL | Max retrieved memory rows |
| `include_entity_memory` | `BOOLEAN` | NOT NULL | |
| `summary_trigger_messages` | `INTEGER` | NOT NULL | |
| `summary_recent_turns` | `INTEGER` | NOT NULL | |
| `summary_max_tokens` | `INTEGER` | NOT NULL | |
| `history_order` | `VARCHAR(32)` | NOT NULL | `summary_first` or `recent_first` |
| `semantic_score_threshold` | `FLOAT` | NOT NULL | |
| `embedding_provider` | `VARCHAR(32)` | NOT NULL | |
| `embedding_model` | `VARCHAR(128)` | NOT NULL | |
| `vector_store` | `VARCHAR(32)` | NOT NULL | |
| `entity_mappings_json` | `JSONB` | NOT NULL | Structured entity-promotion config |
| `created_at` | `TIMESTAMPTZ` | | |
| `updated_at` | `TIMESTAMPTZ` | | Auto-updated |

**Indexes:**

- `ix_mem_profile_tenant_name` on `(tenant_id, name)`
- `ix_mem_profile_tenant_wf_default` on `(tenant_id, workflow_def_id, is_default)`
- `ux_mem_profile_tenant_default` partial UNIQUE on `(tenant_id)` where `workflow_def_id IS NULL AND is_default = true`
- `ux_mem_profile_workflow_default` partial UNIQUE on `(tenant_id, workflow_def_id)` where `workflow_def_id IS NOT NULL AND is_default = true`

### `memory_records`

Semantic and episodic memory. Embeddings are stored inline and indexed with pgvector.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `tenant_id` | `VARCHAR(64)` | NOT NULL, indexed | |
| `scope` | `VARCHAR(32)` | NOT NULL, indexed | `session`, `workflow`, `tenant`, `entity` |
| `scope_key` | `VARCHAR(256)` | NOT NULL, indexed | Scope-specific key |
| `kind` | `VARCHAR(32)` | NOT NULL, indexed | `episode` today |
| `content` | `TEXT` | NOT NULL | Text stored and embedded |
| `metadata_json` | `JSONB` | NOT NULL | |
| `session_ref_id` | `UUID` | FK -> `conversation_sessions.id` CASCADE, nullable, indexed | |
| `workflow_def_id` | `UUID` | nullable, indexed | |
| `entity_type` | `VARCHAR(128)` | nullable, indexed | |
| `entity_key` | `VARCHAR(256)` | nullable, indexed | |
| `source_instance_id` | `UUID` | nullable, indexed | |
| `source_node_id` | `VARCHAR(128)` | nullable | |
| `dedupe_key` | `VARCHAR(128)` | nullable | Retry/concurrency dedupe |
| `embedding_provider` | `VARCHAR(32)` | NOT NULL | |
| `embedding_model` | `VARCHAR(128)` | NOT NULL | |
| `vector_store` | `VARCHAR(32)` | NOT NULL | |
| `embedding` | `VECTOR` | nullable | pgvector embedding |
| `created_at` | `TIMESTAMPTZ` | | |

**Indexes:**

- `ix_mem_record_scope_lookup` on `(tenant_id, scope, scope_key)`
- `ix_mem_record_entity_lookup` on `(tenant_id, entity_type, entity_key)`
- `ux_mem_record_tenant_dedupe` on `(tenant_id, dedupe_key)` UNIQUE
- `ix_memory_records_embedding` HNSW index on `embedding vector_cosine_ops`

### `entity_facts`

Authoritative relational entity memory.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `tenant_id` | `VARCHAR(64)` | NOT NULL, indexed | |
| `entity_type` | `VARCHAR(128)` | NOT NULL, indexed | |
| `entity_key` | `VARCHAR(256)` | NOT NULL, indexed | |
| `fact_name` | `VARCHAR(128)` | NOT NULL, indexed | |
| `fact_value` | `TEXT` | NOT NULL | |
| `confidence` | `FLOAT` | NOT NULL | |
| `valid_from` | `TIMESTAMPTZ` | NOT NULL | |
| `valid_to` | `TIMESTAMPTZ` | nullable | Null = active fact |
| `superseded_by` | `UUID` | nullable | Row id of the winning replacement fact |
| `session_ref_id` | `UUID` | FK -> `conversation_sessions.id` SET NULL, nullable | |
| `workflow_def_id` | `UUID` | nullable, indexed | |
| `source_instance_id` | `UUID` | nullable, indexed | |
| `source_node_id` | `VARCHAR(128)` | nullable | |
| `metadata_json` | `JSONB` | NOT NULL | Includes resolution strategy metadata |
| `created_at` | `TIMESTAMPTZ` | | |

**Indexes:**

- `ix_entity_fact_active_lookup` on `(tenant_id, entity_type, entity_key, fact_name, valid_to)`
- `ux_entity_fact_active_unique` partial UNIQUE on `(tenant_id, entity_type, entity_key, fact_name)` where `valid_to IS NULL`

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

## Copilot tables

### `workflow_drafts`

Ephemeral graph being edited by a copilot session (or a human editor). Every mutation through the agent tool layer bumps `version` so concurrent tool calls race-safely via optimistic concurrency. Promoted into `workflow_definitions` on accept; the draft is deleted in the same transaction.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `tenant_id` | `VARCHAR(64)` | NOT NULL, indexed | |
| `base_workflow_id` | `UUID` | FK → `workflow_definitions.id` (SET NULL) | null = net-new draft; set = editing an existing workflow |
| `base_version_at_fork` | `INTEGER` | nullable | `WorkflowDefinition.version` at fork time; promote refuses if the base has advanced since (colleague-saved-in-another-tab guard) |
| `title` | `VARCHAR(256)` | NOT NULL | Seeded from the first NL intent in COPILOT-01b.i |
| `graph_json` | `JSONB` | NOT NULL, server_default `{"nodes": [], "edges": []}` | Same shape as `workflow_definitions.graph_json` |
| `version` | `INTEGER` | NOT NULL, default 1 | Optimistic-concurrency token; bumped on every successful mutation; 409 on stale write |
| `created_by` | `VARCHAR(128)` | nullable | User identifier when available |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | | |

**Indexes:** `ix_draft_tenant_updated` on `(tenant_id, updated_at)`.

### `copilot_sessions`

One chat session per draft (optionally many sequential sessions if a user abandons and reopens). Carries the LLM provider + model so the UI can show "drafted via Claude" etc.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `tenant_id` | `VARCHAR(64)` | NOT NULL, indexed | |
| `draft_id` | `UUID` | FK → `workflow_drafts.id` (CASCADE) | |
| `provider` | `VARCHAR(32)` | NOT NULL | `anthropic` / `google` / `vertex` / `openai` (pending) |
| `model` | `VARCHAR(128)` | NOT NULL | e.g. `claude-sonnet-4-6`, `gemini-3.1-pro-preview-customtools` |
| `status` | `VARCHAR(16)` | NOT NULL, default `active` | `active` / `completed` / `abandoned` |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | | |

**Indexes:** `ix_session_tenant_draft` on `(tenant_id, draft_id)`.

### `copilot_turns`

Ordered user / assistant / tool messages. Replayed on session reopen and as the history the agent runner reconstructs for each LLM call.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `UUID` | PK | |
| `tenant_id` | `VARCHAR(64)` | NOT NULL, indexed | Denormalised from the parent session so the RLS policy is a simple equality check |
| `session_id` | `UUID` | FK → `copilot_sessions.id` (CASCADE) | |
| `turn_index` | `INTEGER` | NOT NULL, UNIQUE(session_id, turn_index) | Chronological order |
| `role` | `VARCHAR(16)` | NOT NULL | `user` / `assistant` / `tool` |
| `content_json` | `JSONB` | NOT NULL | Role-specific — text for user/assistant, `{name, args, result}` for tool |
| `tool_calls_json` | `JSONB` | nullable | Populated on assistant turns that emit function-calling requests |
| `token_usage_json` | `JSONB` | nullable | `{input_tokens, output_tokens}` |
| `created_at` | `TIMESTAMPTZ` | | |

**Indexes:** `ix_turn_tenant_session` on `(tenant_id, session_id)`, unique `(session_id, turn_index)`.

**RLS:** each copilot table has a `tenant_isolation_*` policy on `current_setting('app.tenant_id')`.

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
| 0012 | `0012_advanced_memory_hard_cutover.py` | Hard-cutover to normalized conversation rows; add session summary metadata, `conversation_messages`, `memory_profiles`, `memory_records`, `entity_facts`, vector indexes, and backfill/drop legacy JSONB transcripts |
| 0013 | `0013_conversation_episodes_checkpointed_summaries.py` | `conversation_episodes` table + checkpointed summary metadata for issue-scoped conversation memory |
| 0014 | `0014_rls_memory_tables.py` | Enable RLS on the memory tables added in 0012 |
| 0015 | `0015_scheduled_triggers.py` | `scheduled_triggers` table — DB-enforced dedupe for Celery Beat fires (UNIQUE `(workflow_def_id, scheduled_for)`) |
| 0016 | `0016_pgvector_dim_and_hnsw.py` | Pin vector column dimensions to 1536 and rebuild HNSW indexes |
| 0017 | `0017_async_jobs_and_tenant_integrations.py` | `async_jobs` (AutomationEdge poll queue, Diverted pause-the-clock), `tenant_integrations` (per-tenant connection defaults), `workflow_instances.suspended_reason` column |
| 0018 | `0018_workflow_is_active.py` | **DV-07** — `workflow_definitions.is_active BOOLEAN` (default TRUE; existing rows backfill active). Schedule Triggers skip `is_active=false` workflows. |
| 0019 | `0019_tenant_mcp_servers.py` | **MCP-02** — `tenant_mcp_servers` (per-tenant MCP registry with `auth_mode` discriminator + partial unique index enforcing one default per tenant) + empty `tenant_mcp_server_tool_fingerprints` side table forward-declared for MCP-06 drift detection |
| 0020 | `0020_tenant_policies.py` | **ADMIN-01** — `tenant_policies` table (execution_quota_per_hour, max_snapshots, mcp_pool_size) with RLS |
| 0021 | `0021_tenant_policies_rate_limit.py` | **ADMIN-02** — add `rate_limit_requests_per_window` + `rate_limit_window_seconds` to `tenant_policies` |
| 0022 | `0022_copilot_drafts.py` | **COPILOT-01a** — `workflow_drafts` (with `version` optimistic-concurrency + `base_version_at_fork` race guard), `copilot_sessions`, `copilot_turns`; all tenant-scoped RLS |
| 0023 | `0023_workflow_definitions_is_ephemeral.py` | **COPILOT-01b.ii.b** — add `is_ephemeral BOOLEAN NOT NULL DEFAULT FALSE` to `workflow_definitions` (marks the copilot's throwaway trial-run rows) |
| 0024 | `0024_tenant_policies_smart_flags.py` | **SMART-04** — add `smart_04_lints_enabled BOOLEAN NOT NULL DEFAULT TRUE` to `tenant_policies` |
| 0025 | `0025_tenant_policies_smart_06.py` | **SMART-06** — add `smart_06_mcp_discovery_enabled BOOLEAN NOT NULL DEFAULT TRUE` to `tenant_policies` |
| 0026 | `0026_copilot_accepted_patterns.py` | **SMART-02** — `copilot_accepted_patterns` table (snapshot of promoted drafts + NL intent + tags, tenant-scoped RLS, `ix_accepted_pattern_tenant_created`) + `smart_02_pattern_library_enabled BOOLEAN NOT NULL DEFAULT TRUE` on `tenant_policies` |

### Running migrations

```bash
cd backend
alembic upgrade head
```

Each migration uses a linear revision chain (`revises` points to the previous migration). `0012` is intentionally destructive with respect to the legacy `conversation_sessions.messages` column because it performs the advanced-memory hard cutover.
