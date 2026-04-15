# API Reference

All endpoints are served by the FastAPI backend (default `http://localhost:8000`). Unless noted, every endpoint requires tenant authentication — see [Security](security.md) for details.

---

## Health & Auth

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | None | Returns `{ "status": "ok" }` |
| `GET` | `/auth/token?tenant_id=<id>` | None | Dev-mode only — returns a signed JWT for the given tenant |

---

## Workflows — `/api/v1/workflows`

### Definitions

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| `POST` | `/api/v1/workflows` | 201 | Create a workflow |
| `GET` | `/api/v1/workflows` | 200 | List all workflows for the tenant |
| `GET` | `/api/v1/workflows/{workflow_id}` | 200 | Get a single workflow |
| `PATCH` | `/api/v1/workflows/{workflow_id}` | 200 | Update name, description, or graph (auto-snapshots on graph change) |
| `DELETE` | `/api/v1/workflows/{workflow_id}` | 204 | Delete a workflow and all related data |

**WorkflowCreate** (request body for POST):

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `name` | string (1–256) | Yes | |
| `description` | string | No | |
| `graph_json` | object | Yes | `{ nodes: [...], edges: [...] }` |

**WorkflowUpdate** (request body for PATCH):

All fields optional: `name`, `description`, `graph_json`. When `graph_json` is updated, the previous version is snapshotted and `version` is incremented.

**Save-time side effects:** Both POST and PATCH validate node configs against the registry schema (returning warnings in logs). If the graph contains Intent Classifier nodes with `cacheEmbeddings=true`, embeddings for their intent definitions are precomputed and stored in the `embedding_cache` table. This is transparent to the caller — the API response is unchanged.

**WorkflowOut** (response):

| Field | Type |
|-------|------|
| `id` | UUID |
| `tenant_id` | string |
| `name` | string |
| `description` | string or null |
| `graph_json` | object |
| `version` | integer |
| `created_at` | ISO datetime |
| `updated_at` | ISO datetime |

### Execution

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| `POST` | `/api/v1/workflows/{workflow_id}/execute` | 202 / 200 / 504 | Execute a workflow |

**ExecuteRequest** (request body):

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `trigger_payload` | object | `{}` | Data passed to the trigger node |
| `deterministic_mode` | bool | `false` | Sets LLM temperature to 0 |
| `sync` | bool | `false` | If true, blocks until completion |
| `sync_timeout` | int (5–3600) | `120` | Seconds before returning 504 in sync mode |

**Response variants:**

- **Async** (`sync: false`) — `202` with `InstanceOut`
- **Sync success** — `200` with `SyncExecuteOut`: `instance_id`, `status`, `started_at`, `completed_at`, `output`
- **Sync timeout** — `504` with `detail`, `instance_id`, `hint`

### Instance control

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| `POST` | `.../{instance_id}/callback` | 200 | Resume a suspended (HITL) instance |
| `POST` | `.../{instance_id}/retry` | 200 | Retry a failed instance, optionally from a specific node |
| `POST` | `.../{instance_id}/pause` | 200 | Pause a running/queued instance |
| `POST` | `.../{instance_id}/resume-paused` | 200 | Resume a paused instance |
| `POST` | `.../{instance_id}/cancel` | 200 | Cancel an instance |

**CallbackRequest:** `approval_payload` (object), `context_patch` (optional object).

**RetryRequest:** `from_node_id` (optional string).

**ResumePausedRequest:** `context_patch` (optional object).

All return **InstanceOut**:

| Field | Type |
|-------|------|
| `id` | UUID |
| `tenant_id` | string |
| `workflow_def_id` | UUID |
| `status` | string (`queued`, `running`, `completed`, `failed`, `suspended`, `paused`, `cancelled`) |
| `current_node_id` | string or null |
| `started_at` | ISO datetime or null |
| `completed_at` | ISO datetime or null |
| `created_at` | ISO datetime |
| `definition_version_at_start` | integer or null |

### Status, versions, graph history

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| `GET` | `.../{workflow_id}/status` | 200 | Last 50 instances |
| `GET` | `.../{workflow_id}/versions` | 200 | List version snapshots |
| `GET` | `.../{workflow_id}/graph-at-version/{version}` | 200 | Get graph JSON for a specific version |
| `POST` | `.../{workflow_id}/rollback/{version}` | 200 | Rollback to a previous version |

### Instance detail & checkpoints

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| `GET` | `.../{instance_id}` | 200 | Instance with execution logs |
| `GET` | `.../{instance_id}/context` | 200 | Current execution context |
| `GET` | `.../{instance_id}/checkpoints` | 200 | List HITL checkpoints |
| `GET` | `.../{instance_id}/checkpoints/{checkpoint_id}` | 200 | Checkpoint detail with context |

**InstanceDetailOut:** extends `InstanceOut` with `logs` — list of `ExecutionLogOut`:

| Field | Type |
|-------|------|
| `id` | UUID |
| `instance_id` | UUID |
| `node_id` | string |
| `node_type` | string |
| `status` | string |
| `input_json` | object or null |
| `output_json` | object or null |
| `error` | string or null |
| `started_at` | ISO datetime |
| `completed_at` | ISO datetime or null |

### Publishing

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| `PATCH` | `/api/v1/workflows/{workflow_id}/publish` | 200 | Set `is_published` for A2A discovery |

**WorkflowPublishRequest:** `is_published` (bool).

---

## SSE — `/api/v1/workflows`

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `.../{workflow_id}/instances/{instance_id}/stream` | Server-Sent Events stream |

**Event types:**

| Event | Payload | When |
|-------|---------|------|
| `log` | Execution log fields | Each node start/complete/error |
| `status` | `instance_status`, `current_node_id` | Status transitions |
| `token` | Streaming LLM token data | During LLM generation (via Redis pub/sub) |
| `done` | Terminal status | Instance reaches a terminal state |

---

## Tools — `/api/v1/tools`

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| `GET` | `/api/v1/tools` | 200 | List available MCP tools (respects tenant overrides) |
| `POST` | `/api/v1/tools/invalidate-cache` | 204 | Clear cached tool list |

**ToolOut:**

| Field | Type |
|-------|------|
| `name` | string |
| `title` | string |
| `description` | string |
| `category` | string |
| `safety_tier` | string |
| `tags` | list of strings |

---

## Conversations — `/api/v1/conversations`

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| `GET` | `/api/v1/conversations` | 200 | List conversation sessions (max 100) |
| `GET` | `/api/v1/conversations/{session_id}` | 200 | Get session with messages |
| `DELETE` | `/api/v1/conversations/{session_id}` | 204 | Delete a session |

**ConversationSessionOut:**

| Field | Type |
|-------|------|
| `session_id` | string |
| `tenant_id` | string |
| `messages` | list of `{ role, content, timestamp }` |
| `message_count` | integer |
| `created_at` | ISO datetime |
| `updated_at` | ISO datetime |

---

## A2A (Agent-to-Agent) — `/tenants/{tenant_id}/...`

### Public agent card (no auth)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/tenants/{tenant_id}/.well-known/agent.json` | Agent card with capabilities and published skills |

### JSON-RPC endpoint (A2A API key auth)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/tenants/{tenant_id}/a2a` | JSON-RPC dispatch |

**Request body** (`A2AJsonRpcRequest`):

| Field | Type | Notes |
|-------|------|-------|
| `jsonrpc` | string | Always `"2.0"` |
| `id` | int or string | Optional, echoed in response |
| `method` | string | See below |
| `params` | object | Method-specific |

**Methods:**

| Method | Params | Behavior |
|--------|--------|----------|
| `tasks/send` | `skillId`, optional `sessionId`, `message` (with `parts[].text`) | Create and enqueue a task |
| `tasks/get` | `id` (instance UUID), optional `sessionId` | Get task status |
| `tasks/cancel` | `id` (instance UUID) | Request cancellation |
| `tasks/sendSubscribe` | Same as `tasks/send` | Create task + SSE stream |

### A2A API key management — `/api/v1/a2a/keys`

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| `POST` | `/api/v1/a2a/keys` | 201 | Create key (returns `raw_key` once) |
| `GET` | `/api/v1/a2a/keys` | 200 | List keys (no raw key) |
| `DELETE` | `/api/v1/a2a/keys/{key_id}` | 204 | Revoke a key |

---

## Knowledge Bases — `/api/v1/knowledge-bases`

Full reference in [RAG & Knowledge Base](rag-knowledge-base.md). Summary:

### Options (no side effects)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/knowledge-bases/embedding-options` | Available embedding providers/models |
| `GET` | `/api/v1/knowledge-bases/chunking-strategies` | Available chunking strategies |
| `GET` | `/api/v1/knowledge-bases/vector-stores` | Available vector store backends |

### KB CRUD

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| `POST` | `/api/v1/knowledge-bases` | 201 | Create a knowledge base |
| `GET` | `/api/v1/knowledge-bases` | 200 | List all KBs |
| `GET` | `/api/v1/knowledge-bases/{kb_id}` | 200 | Get a single KB |
| `PUT` | `/api/v1/knowledge-bases/{kb_id}` | 200 | Update name/description |
| `DELETE` | `/api/v1/knowledge-bases/{kb_id}` | 204 | Delete KB and all vectors |

### Documents

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| `POST` | `/api/v1/knowledge-bases/{kb_id}/documents` | 202 | Upload a document (multipart) |
| `GET` | `/api/v1/knowledge-bases/{kb_id}/documents` | 200 | List documents in a KB |
| `DELETE` | `/api/v1/knowledge-bases/{kb_id}/documents/{doc_id}` | 204 | Delete a document and its chunks |

### Search

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/knowledge-bases/{kb_id}/search` | Vector similarity search |

**SearchRequest:** `query` (string), `top_k` (1–50, default 5), `score_threshold` (0–1, default 0).

**Response:** list of `ChunkOut` — `content`, `score`, `chunk_index`, `document_id`, `document_filename`, `metadata`.

---

## Secrets (Credential Vault) — `/api/v1/secrets`

Manage encrypted tenant secrets used for `{{ env.KEY_NAME }}` references in node configs. Secret values are encrypted with Fernet at rest and **never returned** by any endpoint after creation.

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| `POST` | `/api/v1/secrets` | 201 | Create a new secret |
| `GET` | `/api/v1/secrets` | 200 | List all secrets (metadata only) |
| `GET` | `/api/v1/secrets/{secret_id}` | 200 | Get one secret (metadata only) |
| `PUT` | `/api/v1/secrets/{secret_id}` | 200 | Update a secret's value |
| `DELETE` | `/api/v1/secrets/{secret_id}` | 204 | Delete a secret |

**SecretCreate** (request body for POST):

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `key_name` | string | 1–256 chars, `^\w+$` | Letters, digits, underscores only |
| `value` | string | min 1 char | The secret value (encrypted before storage) |

**SecretUpdate** (request body for PUT):

| Field | Type | Description |
|-------|------|-------------|
| `value` | string | New secret value |

**SecretOut** (response — all endpoints):

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | |
| `key_name` | string | The secret's name |
| `created_at` | ISO datetime | |
| `updated_at` | ISO datetime | |

**Errors:** `400` (invalid key_name format), `404` (secret not found), `409` (duplicate key_name), `422` (invalid UUID).

---

## Error conventions

All errors follow this pattern:

```json
{
  "detail": "Human-readable error message"
}
```

Common status codes: `400` (validation), `401` (auth), `404` (not found), `409` (conflict), `413` (file too large), `422` (invalid input), `429` (rate limited), `504` (timeout).
