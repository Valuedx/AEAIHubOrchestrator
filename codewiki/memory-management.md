# Memory Management

Advanced Memory v1 is now the default memory subsystem for conversational and agentic workflows. The old `conversation_sessions.messages` JSONB transcript is gone after migration `0012`; conversation turns, summaries, semantic memories, and entity facts all have dedicated storage and runtime assembly paths.

---

## What is implemented

- Normalized conversation storage in `conversation_messages`
- Session metadata and rolling summaries on `conversation_sessions`
- Tenant- and workflow-scoped memory policies in `memory_profiles`
- Semantic and episodic memory in `memory_records`
- Relational entity memory in `entity_facts`
- Turn-aware prompt assembly for `LLM Agent` and `ReAct Agent`
- Token-budgeted history assembly for `LLM Router` and `Intent Classifier`
- Memory inspection APIs for operators and execution debugging

This system supports four retrieval scopes:

- `session`
- `workflow`
- `tenant`
- `entity`

---

## Storage Model

### `conversation_sessions`

Session rows now store metadata, not the transcript itself:

- `session_id`
- `tenant_id`
- `message_count`
- `last_message_at`
- `summary_text`
- `summary_updated_at`
- `summary_through_turn`

### `conversation_messages`

Append-only turn rows:

- stable `turn_index` ordering per session
- `role`, `content`, `message_at`
- provenance: `workflow_def_id`, `instance_id`, `node_id`
- retry dedupe via `idempotency_key`

The unique indexes enforce:

- one row per `(session_ref_id, turn_index)`
- one row per `(session_ref_id, idempotency_key, role)`

### `memory_profiles`

Profiles define advanced-memory policy. They can be:

- tenant defaults (`workflow_def_id IS NULL`)
- workflow-specific defaults
- explicitly selected per node via `memoryProfileId`

Profiles store:

- enabled scopes
- shared instructions
- recent-turn token budget
- semantic-hit cap
- summary thresholds
- `history_order` (`summary_first` or `recent_first`)
- embedding provider/model/vector store
- entity promotion mappings

### `memory_records`

Semantic and episodic memory rows:

- `scope` and `scope_key`
- `kind` (`episode` today; schema also supports future memory kinds)
- text payload in `content`
- provenance fields
- embedding metadata and vector column
- dedupe via `dedupe_key`

The vector embedding is stored on the same row and indexed with pgvector HNSW.

### `entity_facts`

Entity facts are relational, not free-form semantic memory:

- `entity_type`, `entity_key`
- `fact_name`, `fact_value`
- `confidence`
- validity range: `valid_from`, `valid_to`
- `superseded_by`
- provenance fields

Conflict policy is `last_write_wins`. The DB enforces a single active fact per:

- `(tenant_id, entity_type, entity_key, fact_name)` where `valid_to IS NULL`

When a new fact wins, the previously active fact is closed by setting `valid_to` and `superseded_by`.

---

## Runtime Assembly

Advanced memory is assembled in two related paths:

### Classifier-style history assembly

Used by:

- `LLM Router`
- `Intent Classifier` LLM mode / fallback

`assemble_history_text()` loads:

1. rolling summary, if present
2. recent raw turns packed by token budget

The order is controlled by `history_order`:

- `summary_first`
- `recent_first`

### Agent/ReAct message assembly

Used by:

- `LLM Agent`
- `ReAct Agent`

`assemble_agent_messages()` builds the prompt in this order:

1. tenant/workflow/profile instructions into the `system` message
2. summary message and recent raw turns, ordered by `history_order`
3. entity facts
4. semantic and episodic memory hits
5. latest user message
6. structured workflow context from non-memory upstream nodes

If `memoryEnabled` is `false`, the node falls back to the legacy-style prompt shape:

- rendered system prompt
- one structured user block from upstream context

---

## Memory Policy Resolution

Policy resolution happens in `resolve_memory_policy()` and follows this precedence:

1. node-level `memoryEnabled=false` disables advanced memory entirely
2. explicit `memoryProfileId`
3. workflow default profile
4. tenant default profile
5. built-in defaults

Additional rules:

- `historyNodeId` wins if present; otherwise the first upstream `Load Conversation State` output is auto-detected
- node-level `memoryScopes`, `maxRecentTokens`, `maxSemanticHits`, `includeEntityMemory`, and `historyOrder` override profile values
- tenant instructions are always prepended ahead of workflow/selected-profile instructions

Built-in defaults are:

- scopes: `session`, `workflow`, `tenant`, `entity`
- recent-turn budget: `1200` tokens
- semantic hits: `4`
- summary trigger: `12` new turns
- summary recency window: `6` turns
- summary budget: `400` tokens
- history order: `summary_first`
- embeddings: `openai` / `text-embedding-3-small` / `pgvector`

---

## Node Behavior

### Load Conversation State

Loads or creates the referenced session and returns:

- `session_id`
- `session_ref_id`
- `messages`
- `message_count`
- `summary_text`
- `summary_through_turn`

### LLM Agent and ReAct Agent

Both nodes now expose advanced-memory config fields:

- `historyNodeId`
- `memoryEnabled`
- `memoryProfileId`
- `memoryScopes`
- `maxRecentTokens`
- `maxSemanticHits`
- `includeEntityMemory`

Outputs now include `memory_debug`, which records what memory was actually used:

- selected profile
- session id
- summary usage
- recent turn ids
- entity fact ids
- memory record ids
- token budget and retrieval settings

### LLM Router and Intent Classifier

These nodes no longer use hard-coded "last 10 messages" windows. They now share the same token-budgeted history packer and emit `memory_debug`.

### Bridge User Reply

When `responseNodeId` points at an agent output, the bridge now preserves upstream `memory_debug` so the downstream save node can apply the same policy that was used on the read path.

### Save Conversation State

The save node now does more than append a transcript:

1. appends normalized user/assistant turns to `conversation_messages`
2. updates session counters
3. refreshes the rolling summary when thresholds are crossed
4. promotes entity facts from profile-defined mappings
5. promotes episodic memory records for successful assistant outputs only

The return payload includes:

- `saved`
- `session_id`
- `session_ref_id`
- `message_count`
- `summary_updated`
- `promoted_memory_records`
- `promoted_entity_facts`

Save-time behavior uses the producing node's `memory_debug` when available, so a selected profile affects both prompt assembly and promotion.

### Entity Extractor

Entity memory is promoted through `memory_profiles.entity_mappings_json`. In practice this usually points at `Entity Extractor` outputs, but promotion is explicit and profile-driven rather than inferred from arbitrary chat text.

---

## Summaries

Rolling summaries are deterministic string compactions, not separate LLM calls.

- `summary_through_turn` records the highest covered turn index
- only turns after that cursor are candidates for the next refresh
- summaries are refreshed once the number of uncovered turns reaches `summary_trigger_messages`
- the retained tail is controlled by `summary_recent_turns`
- final summary text is truncated by token budget, not characters

This is a v1 rolling-summary design. It leaves room for future checkpointed or hierarchical summaries without changing the session contract.

---

## Semantic and Episodic Memory

`promote_memory_records()` currently writes episodic memories from successful save-node outputs across the enabled scopes:

- session
- workflow
- tenant
- entity

Each promoted record:

- stores embedding metadata on the row
- gets embedded through the configured provider/model
- is indexed in the configured memory vector store

Retry and concurrency safety are provided by:

- conversation turn idempotency keys
- memory-record `dedupe_key`
- DB uniqueness on `(tenant_id, dedupe_key)`

---

## Inspection and Debugging

Operator APIs:

- `GET /api/v1/memory-profiles`
- `POST /api/v1/memory-profiles`
- `GET /api/v1/memory-profiles/{profile_id}`
- `PUT /api/v1/memory-profiles/{profile_id}`
- `DELETE /api/v1/memory-profiles/{profile_id}`
- `GET /api/v1/memory/records`
- `GET /api/v1/memory/entity-facts`
- `GET /api/v1/memory/instances/{instance_id}/resolved`

The resolved-memory endpoint uses `memory_debug` from execution logs and rehydrates the exact recent turns, entity facts, and memory records that influenced a run. All lookups are tenant-filtered before rows are returned.

---

## Migration Notes

Migration `0012_advanced_memory_hard_cutover.py` performs the storage cutover:

1. add summary metadata columns to `conversation_sessions`
2. create `conversation_messages`, `memory_profiles`, `memory_records`, `entity_facts`
3. backfill legacy `conversation_sessions.messages` into normalized turn rows
4. compute `message_count` and `last_message_at`
5. drop the old `messages` JSONB column

This is a hard cutover. Fresh databases should simply run `alembic upgrade head`.

---

## Frontend Notes

The workflow editor exposes profile selection and per-node overrides for Agent and ReAct nodes through `node_registry.json` and `DynamicConfigForm.tsx`.

Current validation nuance:

- `validateWorkflow.ts` cross-validates `historyNodeId` for `LLM Router` and `Intent Classifier`
- Agent/ReAct `historyNodeId` fields are present in the UI but are not yet cross-validated client-side before run

The expression picker surfaces memory-aware outputs from conversation nodes:

- `Load Conversation State`: `session_id`, `session_ref_id`, `messages`, `message_count`, `summary_text`, `summary_through_turn`
- `Save Conversation State`: `saved`, `session_id`, `session_ref_id`, `message_count`, `summary_updated`, `promoted_memory_records`, `promoted_entity_facts`

---

## Related docs

- [Architecture](architecture.md)
- [API Reference](api-reference.md)
- [Database Schema](database-schema.md)
- [Node Types](node-types.md)
- [Security](security.md)
