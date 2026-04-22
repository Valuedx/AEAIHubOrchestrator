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
| `PATCH` | `/api/v1/workflows/{workflow_id}` | 200 | Update name, description, graph, or is_active (auto-snapshots on graph change) |
| `DELETE` | `/api/v1/workflows/{workflow_id}` | 204 | Delete a workflow and all related data |
| `POST` | `/api/v1/workflows/{workflow_id}/duplicate` | 201 | **DV-05** — clone into a new row (`<name> (copy)`, graph deep-copied incl. pins, version=1, is_active=True) |

**WorkflowCreate** (request body for POST):

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `name` | string (1–256) | Yes | |
| `description` | string | No | |
| `graph_json` | object | Yes | `{ nodes: [...], edges: [...] }` |

**WorkflowUpdate** (request body for PATCH):

All fields optional: `name`, `description`, `graph_json`, `is_active`. When `graph_json` is updated, the previous version is snapshotted and `version` is incremented. Toggling **`is_active`** alone does NOT bump version or snapshot — it's a runtime switch only, matching the pins / DV-07 design.

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
| `is_active` | bool — DV-07; when false, Schedule Triggers skip this workflow. Manual Run / PATCH / duplicate all still work. |
| `created_at` | ISO datetime |
| `updated_at` | ISO datetime |

### Developer probes — pin / test (DV-01, DV-02)

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| `POST` | `.../{workflow_id}/nodes/{node_id}/pin` | 200 | **DV-01** — pin a dict to `graph_json.nodes[].data.pinnedOutput`. Subsequent runs short-circuit `dispatch_node` and return the pin. Does NOT bump version. |
| `DELETE` | `.../{workflow_id}/nodes/{node_id}/pin` | 200 | Clear the pin (idempotent). |
| `POST` | `.../{workflow_id}/nodes/{node_id}/test` | 200 | **DV-02** — run one node in isolation using upstream pins as synthetic context. Handler exceptions are caught and returned as `error`. No `workflow_instances` / `execution_logs` rows are written. |

**PinNodeRequest:** `{ "output": { ... } }` — stored verbatim under `pinnedOutput`.

**TestNodeRequest:** `{ "trigger_payload": { ... } }` (optional).

**TestNodeResponse:** `{ "output": dict | null, "elapsed_ms": int, "error": string | null }`.

See [Developer Workflow](dev-workflow.md) for the full UX + edge cases.

### Execution

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| `POST` | `/api/v1/workflows/{workflow_id}/execute` | 202 / 200 / 504 | Execute a workflow |

> **UI shortcut (API-18A):** the toolbar **FlaskConical** icon opens an **API Playground** dialog that hits this endpoint with an arbitrary JSON payload (sync or async), shows the result inline, and offers a one-click "Copy as curl" for the exact request. No new API surface — it's purely a UI over this endpoint.

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
| `parent_instance_id` | UUID or null |
| `parent_node_id` | string or null |

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

**InstanceDetailOut:** extends `InstanceOut` with `logs` and `children`:

**`children`** — list of `ChildInstanceSummary` (sub-workflow instances spawned during execution):

| Field | Type |
|-------|------|
| `id` | UUID |
| `workflow_def_id` | UUID |
| `parent_node_id` | string |
| `status` | string |
| `started_at` | ISO datetime or null |
| `completed_at` | ISO datetime or null |

**`logs`** — list of `ExecutionLogOut`:

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
| `GET` | `/api/v1/tools?server_label={label}` | 200 | List available MCP tools from the resolved server (respects tenant overrides). `server_label` is optional — blank picks the tenant default, or env-var fallback if no default is set. |
| `POST` | `/api/v1/tools/invalidate-cache` | 204 | Clear this tenant's cached tool list. |

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

## Tenant MCP Servers — `/api/v1/tenant-mcp-servers`

**MCP-02** — per-tenant registry of MCP servers an operator wants to route tool calls to. Each row captures URL + auth config. Nodes resolve by label (MCP Tool / ReAct Agent `mcpServerLabel` config field); blank label → tenant `is_default` row → legacy `settings.mcp_server_url` env-var fallback.

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| `POST` | `/api/v1/tenant-mcp-servers` | 201 | Register a server |
| `GET` | `/api/v1/tenant-mcp-servers` | 200 | List servers for the tenant |
| `GET` | `/api/v1/tenant-mcp-servers/{server_id}` | 200 | Get one |
| `PATCH` | `/api/v1/tenant-mcp-servers/{server_id}` | 200 | Update label / url / auth_mode / config / is_default |
| `DELETE` | `/api/v1/tenant-mcp-servers/{server_id}` | 204 | Delete a server |

**TenantMcpServerCreate** (also the PATCH body, all fields optional):

| Field | Type | Notes |
|-------|------|-------|
| `label` | string (1–128) | Unique per tenant — collision returns 409 |
| `url` | string | Streamable-HTTP MCP endpoint |
| `auth_mode` | `"none"` \| `"static_headers"` \| `"oauth_2_1"` | `oauth_2_1` is accepted but runtime raises `not yet implemented` (MCP-03) |
| `config_json` | object | For `static_headers`: `{ "headers": { "Authorization": "Bearer {{ env.MY_TOKEN }}", ... } }`. `{{ env.KEY }}` placeholders resolve through the Secrets vault at call time. |
| `is_default` | bool | At most one per tenant — partial unique index flips the prior default automatically on create / update |

**TenantMcpServerOut** (response):

| Field | Type |
|-------|------|
| `id` | string (UUID) |
| `tenant_id` | string |
| `label` | string |
| `url` | string |
| `auth_mode` | string |
| `config_json` | object |
| `is_default` | bool |
| `created_at` | ISO datetime |
| `updated_at` | ISO datetime |

See [MCP Audit — §8 Per-tenant MCP server registry](mcp-audit.md) for the resolver precedence chain and auth-mode semantics.

---

## Tenant Integrations — `/api/v1/tenant-integrations`

External-system connection defaults (currently AutomationEdge only). Same CRUD shape as Tenant MCP Servers but keyed by `(tenant_id, system, label)`. See [AutomationEdge Node](automationedge.md) for the AE-specific `config_json` shape.

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| `POST` | `/api/v1/tenant-integrations` | 201 | Register an integration |
| `GET` | `/api/v1/tenant-integrations?system={name}` | 200 | List (optional `system` filter) |
| `GET` | `/api/v1/tenant-integrations/{id}` | 200 | Get one |
| `PATCH` | `/api/v1/tenant-integrations/{id}` | 200 | Update |
| `DELETE` | `/api/v1/tenant-integrations/{id}` | 204 | Delete |

---

## Async Jobs — `/api/v1/async-jobs`

External-system job tracking (currently AutomationEdge). See [AutomationEdge Node](automationedge.md) for the pattern (Pattern A webhook callback vs. Pattern C Beat poll) and the shared `finalize_terminal` resume path.

| Method | Path | Auth | Status | Description |
|--------|------|------|--------|-------------|
| `POST` | `/api/v1/async-jobs/{job_id}/complete` | Token or HMAC (in `metadata_json`) | 200 | External system posts terminal state back — resumes the parent `WorkflowInstance`. |

---

## Conversations — `/api/v1/conversations`

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| `GET` | `/api/v1/conversations` | 200 | List conversation sessions (max 100) |
| `GET` | `/api/v1/conversations/{session_id}` | 200 | Get session with messages |
| `DELETE` | `/api/v1/conversations/{session_id}` | 204 | Delete a session |

These endpoints still return the same transcript-focused payload shape as before, but reads now come from normalized `conversation_messages` rows and session metadata from `conversation_sessions`.

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

## Memory Profiles — `/api/v1/memory-profiles`

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| `GET` | `/api/v1/memory-profiles` | 200 | List tenant and workflow memory profiles |
| `POST` | `/api/v1/memory-profiles` | 201 | Create a memory profile |
| `GET` | `/api/v1/memory-profiles/{profile_id}` | 200 | Get one memory profile |
| `PUT` | `/api/v1/memory-profiles/{profile_id}` | 200 | Update a memory profile |
| `DELETE` | `/api/v1/memory-profiles/{profile_id}` | 204 | Delete a memory profile |

**MemoryProfileOut:**

| Field | Type |
|-------|------|
| `id` | UUID |
| `tenant_id` | string |
| `name` | string |
| `description` | string or null |
| `workflow_def_id` | UUID or null |
| `is_default` | boolean |
| `instructions_text` | string or null |
| `enabled_scopes` | string[] |
| `max_recent_tokens` | integer |
| `max_semantic_hits` | integer |
| `include_entity_memory` | boolean |
| `summary_trigger_messages` | integer |
| `summary_recent_turns` | integer |
| `summary_max_tokens` | integer |
| `history_order` | `summary_first` or `recent_first` |
| `semantic_score_threshold` | number |
| `embedding_provider` | string |
| `embedding_model` | string |
| `vector_store` | string |
| `entity_mappings_json` | object[] |
| `created_at` | ISO datetime |
| `updated_at` | ISO datetime |

Profiles are optional. Agent/ReAct nodes can reference one explicitly via `memoryProfileId`, otherwise the runtime resolves workflow default, tenant default, then built-in defaults.

## Memory Inspection — `/api/v1/memory`

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| `GET` | `/api/v1/memory/records` | 200 | List semantic or episodic memory rows |
| `GET` | `/api/v1/memory/entity-facts` | 200 | List active and historical entity facts |
| `GET` | `/api/v1/memory/instances/{instance_id}/resolved` | 200 | Resolve the exact memory inputs used by logged agent/classifier runs |

### `GET /api/v1/memory/records`

Query params:

- `scope` optional
- `scope_key` optional
- `kind` optional
- `workflow_def_id` optional
- `entity_type` optional
- `entity_key` optional
- `limit` optional, default `100`, max `500`

**MemoryRecordOut:**

| Field | Type |
|-------|------|
| `id` | UUID |
| `tenant_id` | string |
| `scope` | string |
| `scope_key` | string |
| `kind` | string |
| `content` | string |
| `metadata_json` | object |
| `session_ref_id` | UUID or null |
| `workflow_def_id` | UUID or null |
| `entity_type` | string or null |
| `entity_key` | string or null |
| `source_instance_id` | UUID or null |
| `source_node_id` | string or null |
| `embedding_provider` | string |
| `embedding_model` | string |
| `vector_store` | string |
| `created_at` | ISO datetime |

### `GET /api/v1/memory/entity-facts`

Query params:

- `entity_type` optional
- `entity_key` optional
- `include_inactive` optional, default `false`
- `limit` optional, default `100`, max `500`

**EntityFactOut:**

| Field | Type |
|-------|------|
| `id` | UUID |
| `tenant_id` | string |
| `entity_type` | string |
| `entity_key` | string |
| `fact_name` | string |
| `fact_value` | string |
| `confidence` | number |
| `valid_from` | ISO datetime |
| `valid_to` | ISO datetime or null |
| `superseded_by` | UUID or null |
| `session_ref_id` | UUID or null |
| `workflow_def_id` | UUID or null |
| `source_instance_id` | UUID or null |
| `source_node_id` | string or null |
| `metadata_json` | object |
| `created_at` | ISO datetime |

### `GET /api/v1/memory/instances/{instance_id}/resolved`

Returns `ResolvedMemoryLogOut[]` keyed by execution log entry. Each item contains:

- `node_id`
- `node_type`
- `completed_at`
- `memory_debug`
- `recent_turns`
- `entity_facts`
- `memory_records`

The backend rehydrates these rows from `memory_debug` recorded during execution and tenant-filters the resolved turn, memory-record, and entity-fact lookups before returning them.

---

## A2A (Agent-to-Agent) — `/tenants/{tenant_id}/...`

### Public agent card (no auth)

We serve the agent card at **both** paths so A2A v0.2.x and v1.0 clients both discover us (A2A-01.a):

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/tenants/{tenant_id}/.well-known/agent-card.json` | **A2A v1.0** agent card (spec-required path) |
| `GET` | `/tenants/{tenant_id}/.well-known/agent.json` | **A2A v0.2.x** legacy alias — identical body |

**Card body (A2A-01.b — v1.0 complete):**

| Field | Type | Notes |
|-------|------|-------|
| `name` | string | `AE Orchestrator — {tenant_id}` |
| `description` | string | Short blurb |
| `url` | string | JSON-RPC dispatch endpoint |
| `version` | string | Currently `1.0` |
| `defaultInputModes` | `["text"]` | v1.0 required |
| `defaultOutputModes` | `["text", "data"]` | v1.0 required — workflows can emit structured context plus prose |
| `capabilities` | object | `{streaming: true, pushNotifications: false, stateTransitionHistory: false, extendedAgentCard: false}` |
| `securitySchemes` | object | `{bearer: {type: "http", scheme: "bearer"}}` — v1.0 clients auto-negotiate from here |
| `security` | array | `[{bearer: []}]` — bearer required on the main endpoint |
| `provider` | object | `{organization, url?, email?}` — sourced from `ORCHESTRATOR_A2A_PROVIDER_*` env vars; optional fields suppressed when unset |
| `documentationUrl` | string? | `ORCHESTRATOR_A2A_DOCUMENTATION_URL` env, omitted when empty |
| `skills` | array | One entry per published workflow: `{id, name, description, inputModes, outputModes}` |

**Operator config (all optional):**

```bash
ORCHESTRATOR_A2A_PROVIDER_ORGANIZATION="Your Org"      # default: "AE AI Hub Orchestrator"
ORCHESTRATOR_A2A_PROVIDER_URL="https://your.example"
ORCHESTRATOR_A2A_PROVIDER_EMAIL="platform@your.example"
ORCHESTRATOR_A2A_DOCUMENTATION_URL="https://your.example/docs"
```

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

**Methods:** we accept both v0.2.x and v1.0 method names as aliases so clients on either spec version work without a shim (A2A-01.a).

| Method (v0.2.x) | Alias (v1.0) | Params | Behavior |
|-----------------|--------------|--------|----------|
| `tasks/send` | `message/send` | `skillId` (top-level OR `message.metadata.skillId`), optional `sessionId`, `message` (with `parts[].text`) | Create and enqueue a task |
| `tasks/sendSubscribe` | `message/sendStreaming` | Same as send | Create task + SSE stream |
| `tasks/get` | — | `id` (instance UUID), optional `sessionId` | Get task status |
| `tasks/cancel` | — | `id` (instance UUID) | Request cancellation |

**Skill routing note:** v1.0's `message/send` has no top-level `skillId`; the Google ADK / LangGraph convention puts the id inside `message.metadata.skillId`. Our dispatcher reads from both locations so either param shape works.

**Message Part types (A2A-01.c):** messages and artifacts carry a list of parts; each part is one of four variants discriminated by field presence (Google ADK / LangGraph convention).

| Variant | Shape | Example |
|---|---|---|
| TextPart | `{text: string}` | `{"text": "please summarise"}` |
| DataPart | `{data: any JSON, mimeType?: string}` | `{"data": {"priority": "high"}, "mimeType": "application/vnd.slack.message+json"}` |
| FilePart (bytes) | `{file: {name?, mimeType?, bytes: base64}}` | `{"file": {"name": "logs.txt", "mimeType": "text/plain", "bytes": "aGVsbG8="}}` |
| FilePart (uri) | `{file: {name?, mimeType?, uri: string}}` | `{"file": {"uri": "https://ex.com/doc.pdf", "mimeType": "application/pdf"}}` |

**Inbound (`message/send` / `tasks/send`):** parts are fanned out into the workflow's `trigger_payload`:

- `trigger_payload.message` — concatenated text (back-compat string, unchanged).
- `trigger_payload.message_parts.text` — same as above.
- `trigger_payload.message_parts.data` — list of DataPart payloads, in order.
- `trigger_payload.message_parts.files` — list of FilePart references (bytes stay base64 on the inbound side; workflows decode as needed).

**Outbound (completed task artifacts):** every completed task emits a single artifact with up to two parts — a TextPart carrying the final LLM response (prose for the user) plus a DataPart carrying every non-internal key in the workflow context (`_`-prefixed keys are stripped so internal markers don't leak). Workflows calling the **A2A Agent Call** node handler get `{response, data, files}` in the node output; `response` stays a plain string for back compat while `data`/`files` expose the richer payload — file bytes are base64-decoded to raw `bytes` on the outbound client side.

**Task state mapping (A2A-01.b):** A2A v1.0 defines 8 states. Our `WorkflowInstance.status + suspended_reason` pair maps onto them:

| WorkflowInstance `status` | `suspended_reason` | A2A state |
|---|---|---|
| `queued` | — | `submitted` |
| `running` | — | `working` |
| `completed` | — | `completed` |
| `failed` | `rejected` | `rejected` (pre-execution policy refusal) |
| `failed` | anything else | `failed` |
| `cancelled` | — | `canceled` |
| `suspended` | None or `async_external` | `input-required` |
| `suspended` | `auth_required` | `auth-required` |
| anything else / null | — | `unknown` |

`unknown` is the intentional fallback — a spec-required enum is better than silently claiming `working`.

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

## Workflow Copilot — `/api/v1/copilot`

The conversational authoring + debug surface (COPILOT-01 / 02 / SMART-04 / SMART-06). Every mutation flows through a **draft** — nothing touches `workflow_definitions` until the human promotes.

### Drafts — `/api/v1/copilot/drafts`

| Method | Endpoint | Status | Description |
|--------|----------|--------|-------------|
| `POST` | `/api/v1/copilot/drafts` | 201 | Create a draft (optionally forked from a `base_workflow_id`) |
| `GET` | `/api/v1/copilot/drafts` | 200 | List drafts for the tenant |
| `GET` | `/api/v1/copilot/drafts/{id}` | 200 | Read draft + live validation result |
| `PATCH` | `/api/v1/copilot/drafts/{id}` | 200 | Manual graph / title update; accepts `expected_version` for optimistic concurrency |
| `DELETE` | `/api/v1/copilot/drafts/{id}` | 204 | Abandon |
| `POST` | `/api/v1/copilot/drafts/{id}/tools/{tool_name}` | 200 / 400 / 409 | Dispatch one of the agent tools (`add_node`, `connect_nodes`, `check_draft`, `test_node`, `execute_draft`, `search_docs`, `discover_mcp_tools`, …). Body `{args, expected_version?}` |
| `POST` | `/api/v1/copilot/drafts/{id}/promote` | 201 / 400 / 404 / 409 | Atomically merge into `workflow_definitions` (net-new or new version of base). Refuses on validation errors or `base.version != base_version_at_fork` |

### Sessions — `/api/v1/copilot/sessions`

| Method | Endpoint | Status | Description |
|--------|----------|--------|-------------|
| `GET` | `/api/v1/copilot/sessions/providers` | 200 | Supported providers + default model + declared tool surface |
| `POST` | `/api/v1/copilot/sessions` | 201 | Create session bound to a draft; pick provider (anthropic / google / vertex) |
| `GET` | `/api/v1/copilot/sessions` | 200 | List sessions (optional `?draft_id=` filter) |
| `GET` | `/api/v1/copilot/sessions/{id}` | 200 | Read session metadata |
| `DELETE` | `/api/v1/copilot/sessions/{id}` | 204 | Mark `abandoned` — turns are preserved |
| `GET` | `/api/v1/copilot/sessions/{id}/turns` | 200 | Chronological turn list (user / assistant / tool) |
| `POST` | `/api/v1/copilot/sessions/{id}/turns` | 200 (SSE) | Send a user message and stream the agent's response as `text/event-stream` |

**SSE event shapes** (see `codewiki/copilot.md` §4 for the full contract):

- `{type: "assistant_text", text}`
- `{type: "tool_call", id, name, args}`
- `{type: "tool_result", id, name, result, validation, draft_version, error}`
- `{type: "error", message, recoverable}`
- `{type: "done", turns_added, final_text}`

**Errors:** `404` (draft/session not found), `409` (session abandoned, or `expected_version` mismatch), `422` (invalid UUID or empty message).

---

## Error conventions

All errors follow this pattern:

```json
{
  "detail": "Human-readable error message"
}
```

Common status codes: `400` (validation), `401` (auth), `404` (not found), `409` (conflict), `413` (file too large), `422` (invalid input), `429` (rate limited), `504` (timeout).
