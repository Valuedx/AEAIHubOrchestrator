# Node Types

All node types are defined in `shared/node_registry.json`. The frontend reads this file to render the node palette and config forms; the backend validates configs and routes execution through `dispatch_node`.

---

## Categories

| Category | Label | Description | Color (UI) |
|----------|-------|-------------|------------|
| `trigger` | Triggers | Entry points that start a workflow execution | amber |
| `agent` | AI Agents | LLM-powered reasoning nodes | violet |
| `action` | Actions | Tool calls, HTTP requests, and side-effect operations | blue |
| `logic` | Logic | Branching, merging, and flow control nodes | emerald |
| `knowledge` | Knowledge | RAG knowledge base retrieval and management | teal |
| `notification` | Notifications | Send messages to external channels (Slack, Teams, email, etc.) | rose |
| `nlp` | NLP | Intent classification and entity extraction nodes | indigo |

---

## Trigger nodes

### Webhook Trigger (`webhook_trigger`)

Starts a workflow when an HTTP request is received.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `method` | enum | `POST` | `POST` or `PUT` |
| `path` | string | `/webhook` | URL path for this webhook endpoint |

**Output:** The trigger payload from the HTTP request body, accessible as `trigger.*` in downstream nodes.

### Schedule Trigger (`schedule_trigger`)

Starts a workflow on a cron schedule.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `cron` | string | `0 * * * *` | Cron expression in UTC |

**Output:** Trigger payload contains schedule metadata.

---

## AI Agent nodes

### LLM Agent (`llm_agent`)

Single-turn LLM call with optional turn-aware advanced memory assembly.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `provider` | enum | `google` | `google`, `openai`, `anthropic` |
| `model` | enum | `gemini-2.5-flash` | See model list below |
| `systemPrompt` | string | `""` | Jinja2 template. Use `{{ trigger.field }}` or `{{ node_2.response }}` |
| `temperature` | number | `0.7` | 0 = deterministic, 2 = very creative |
| `maxTokens` | integer | `4096` | Maximum output tokens |
| `historyNodeId` | string | `""` | Optional `Load Conversation State` node ID. Empty = auto-detect first upstream history node |
| `memoryEnabled` | boolean | `true` | Enable advanced memory assembly. False falls back to plain structured-context prompting |
| `memoryProfileId` | string | `""` | Optional explicit memory profile override |
| `memoryScopes` | string[] | `[]` | Optional scope override for semantic retrieval |
| `maxRecentTokens` | integer | `1200` | Token budget for recent raw turns |
| `maxSemanticHits` | integer | `4` | Max semantic or episodic memory hits to inject |
| `includeEntityMemory` | boolean | `true` | Include active entity facts from the resolved profile |

**Output:** `{ response, usage, provider, model, memory_debug }`

When advanced memory is enabled, the runtime assembles prompts from profile instructions, rolling summary, recent turns, entity facts, semantic hits, the latest user message, and non-memory workflow context.

### ReAct Agent (`react_agent`)

Iterative Reason-Act-Observe loop with MCP tool access.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `provider` | enum | `google` | `google`, `openai`, `anthropic` |
| `model` | enum | `gemini-2.5-flash` | |
| `systemPrompt` | string | `""` | Jinja2 template |
| `maxIterations` | integer | `10` | Max Reason-Act cycles |
| `tools` | string[] | `[]` | MCP tool names; empty = auto-discover all |
| `mcpServerLabel` | string | `""` | **MCP-02** — pick a server from the tenant's registry (Toolbar → Globe icon). Blank → tenant default → legacy `MCP_SERVER_URL` env-var fallback. |
| `temperature` | number | `0.7` | Sampling temperature used on each reasoning step |
| `maxTokens` | integer | `4096` | Maximum output tokens for each reasoning step |
| `historyNodeId` | string | `""` | Optional `Load Conversation State` node ID. Empty = auto-detect first upstream history node |
| `memoryEnabled` | boolean | `true` | Enable advanced memory assembly. False falls back to plain structured-context prompting |
| `memoryProfileId` | string | `""` | Optional explicit memory profile override |
| `memoryScopes` | string[] | `[]` | Optional scope override for semantic retrieval |
| `maxRecentTokens` | integer | `1200` | Token budget for recent raw turns |
| `maxSemanticHits` | integer | `4` | Max semantic or episodic memory hits to inject |
| `includeEntityMemory` | boolean | `true` | Include active entity facts from the resolved profile |

**Output:** `{ response, provider, model, usage, iterations, total_iterations, memory_debug }`

### LLM Router (`llm_router`)

Classifies user intent into one of the configured labels. Pair with downstream Condition nodes to branch.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `provider` | enum | `google` | |
| `model` | enum | `gemini-2.5-flash` | |
| `intents` | string[] | `[]` | Valid intent names (e.g. `diagnose_server`, `casual_chat`) |
| `historyNodeId` | string | `""` | Node ID of Load Conversation State node |
| `userMessageExpression` | string | `trigger.message` | Expression resolving to user message |

**Output:** `{ intent, raw_response, usage, memory_debug }`

History is assembled with token budgets and rolling summaries, not a fixed message-count window.

### Reflection (`reflection`)

Calls an LLM with a summary of the workflow's execution history and returns a structured JSON assessment.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `provider` | enum | `google` | |
| `model` | enum | `gemini-2.5-flash` | |
| `reflectionPrompt` | string | `""` | Jinja2 template. Use `{{ execution_summary }}` for auto-built history |
| `outputKeys` | string[] | `[]` | Expected JSON keys (e.g. `["next_action", "confidence"]`) |
| `maxHistoryNodes` | number | `10` | Max recent node outputs in summary (cap: 25) |
| `temperature` | number | `0.3` | |
| `maxTokens` | number | `1024` | |

**Output:** Parsed JSON object with the specified output keys.

---

## Available LLM models

| Provider | Model |
|----------|-------|
| Google | `gemini-2.5-flash`, `gemini-2.5-pro` |
| OpenAI | `gpt-4o`, `gpt-4o-mini` |
| Anthropic | `claude-sonnet-4-20250514`, `claude-3-5-haiku-20241022` |

---

## Action nodes

### MCP Tool (`mcp_tool`)

Calls a tool on the resolved MCP server for this tenant.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `toolName` | string | `""` | MCP tool name |
| `parameters` | object | `{}` | Fixed parameters; runtime outputs can override |
| `mcpServerLabel` | string | `""` | **MCP-02** — pick a server from the tenant's registry (Toolbar → Globe icon). Blank → tenant default → legacy `MCP_SERVER_URL` env-var fallback. |

**Output:** Tool execution result. See [MCP Audit](mcp-audit.md) for the resolver precedence chain and the auth-mode semantics applied before the call leaves the orchestrator.

### HTTP Request (`http_request`)

Makes an external HTTP call.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `url` | string | `""` | Full URL |
| `method` | enum | `GET` | `GET`, `POST`, `PUT`, `PATCH`, `DELETE` |
| `headers` | object | `{}` | HTTP headers |
| `body` | string | `""` | Request body for POST/PUT/PATCH |

**Output:** `{ status_code: number, body: string, headers: object }`

### Human Approval (`human_approval`)

Suspends execution and waits for human approval via the callback endpoint.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `approvalMessage` | string | `""` | Message shown to the approver |
| `timeout` | integer | `3600` | Seconds before timeout |

**Output:** The `approval_payload` from the callback request.

### Bridge User Reply (`bridge_user_reply`)

Sets the text a sync caller receives when using the orchestrator bridge.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `messageExpression` | string | `""` | safe_eval expression for the reply |
| `responseNodeId` | string | `""` | Node ID whose response becomes the reply |

**Output:** `{ orchestrator_user_reply, text, source, memory_debug }`

If `responseNodeId` points at an upstream output that already contains `memory_debug`, the bridge forwards that metadata so the downstream save node can reuse the same memory policy on the write path.

### Load Conversation State (`load_conversation_state`)

Fetches multi-turn chat history from persistent storage.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `sessionIdExpression` | string | `trigger.session_id` | Expression resolving to session ID |

**Output:** `{ session_id, session_ref_id, messages, message_count, summary_text, summary_through_turn }`

The transcript is read from normalized `conversation_messages` rows. The session row stores metadata and rolling-summary state.

### Save Conversation State (`save_conversation_state`)

Appends the current user/assistant exchange to persistent chat history and drives memory promotion.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `sessionIdExpression` | string | `trigger.session_id` | |
| `responseNodeId` | string | `""` | Node whose `response` field is saved |
| `userMessageExpression` | string | `trigger.message` | Expression for user message |

**Output:** `{ saved, session_id, session_ref_id, message_count, summary_updated, promoted_memory_records, promoted_entity_facts }`

Runtime behavior:

- writes normalized turn rows to `conversation_messages`
- updates `conversation_sessions` counters and rolling summary
- promotes episodic `memory_records` for successful outputs only
- promotes `entity_facts` from profile-defined mappings

### A2A Agent Call (`a2a_call`)

Delegates a task to an external A2A-compatible agent.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `agentCardUrl` | string | `""` | URL of remote `/.well-known/agent.json` |
| `skillId` | string | `""` | Remote skill ID; blank = first available |
| `messageExpression` | string | `trigger.message` | Message text to send |
| `apiKeySecret` | string | `""` | Vault reference: `{{ env.REMOTE_AGENT_KEY }}` |
| `timeoutSeconds` | integer | `300` | Max wait time |

**Output:** Remote agent's response.

### Code (`code_execution`)

Runs user-provided Python code in a sandboxed subprocess for data transformation, filtering, aggregation, or custom logic.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `language` | enum | `python` | Programming language (`python` currently supported) |
| `code` | string | *(see below)* | Python code to execute |
| `timeout` | integer | `30` | Max execution time in seconds (1–120) |

**Default code:**
```python
# 'inputs' contains upstream node outputs.
# Assign your result to 'output'.

output = {"result": inputs}
```

**Data flow:**
- **Input:** The `inputs` dict contains all upstream node outputs keyed by `node_<id>` plus the `trigger` payload.
- **Output:** The user must assign a dict to the `output` variable. If `output` is not a dict, it is wrapped as `{"result": output}`.

**Security — sandbox layers:**

| Layer | Description |
|-------|-------------|
| Process isolation | Code runs in a separate Python subprocess |
| Restricted builtins | `open`, `exec`, `eval`, `compile`, `__import__`, `globals`, `locals`, `vars`, `dir`, `breakpoint`, `exit`, `quit`, `input` are removed |
| Import whitelist | Only safe stdlib modules: `json`, `math`, `re`, `datetime`, `collections`, `itertools`, `functools`, `string`, `textwrap`, `hashlib`, `hmac`, `base64`, `urllib.parse`, `html`, `copy`, `decimal`, `fractions`, `statistics`, `operator`, `typing`, `uuid`, `random`, `time`, `enum`, `dataclasses` |
| Timeout | Subprocess killed after the configured timeout (max capped by `ORCHESTRATOR_CODE_SANDBOX_TIMEOUT_MAX`) |
| Output limit | Max 1 MB stdout (configurable via `ORCHESTRATOR_CODE_SANDBOX_OUTPUT_LIMIT_BYTES`) |
| Clean environment | Subprocess inherits only `PATH` — no app secrets |

**Configuration (env vars):**
- `ORCHESTRATOR_CODE_SANDBOX_ENABLED` — disable code execution entirely (default: `true`)
- `ORCHESTRATOR_CODE_SANDBOX_TIMEOUT_MAX` — hard cap on per-node timeout (default: `120`)
- `ORCHESTRATOR_CODE_SANDBOX_OUTPUT_LIMIT_BYTES` — max output size (default: `1048576`)

**Output:** The dict assigned to `output` in the user's code, plus `_sandbox_meta: { duration_ms, stderr }`.

---

## Logic nodes

### Condition (`condition`)

Branches the graph based on a boolean expression.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `condition` | string | `""` | safe_eval expression (e.g. `node_2.intent == "diagnose"`) |
| `trueLabel` | string | `Yes` | Edge label for true branch |
| `falseLabel` | string | `No` | Edge label for false branch |

**Routing:** Exactly one outgoing edge is followed based on the condition result.

### Merge (`merge`)

Joins multiple branches back together.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `strategy` | enum | `waitAll` | `waitAll` (all branches) or `waitAny` (first branch) |

### ForEach (`forEach`)

Iterates over an array, executing downstream nodes once per element.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `arrayExpression` | string | `""` | Expression resolving to an array |
| `itemVariable` | string | `item` | Variable name for current element |

**Output:** Aggregated results from all iterations.

### Loop (`loop`)

Repeats downstream nodes while a condition is true.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `continueExpression` | string | `""` | safe_eval expression; empty = run for maxIterations |
| `maxIterations` | integer | `10` | Max iterations (hard cap: 25) |

**Special variables:** `_loop_index` is available in expressions.

### Sub-Workflow (`sub_workflow`)

Executes another saved workflow as a single step within the current workflow. Creates a child `WorkflowInstance` with its own execution logs and checkpoints.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `workflowId` | string | `""` | ID of the workflow definition to execute as a sub-workflow |
| `versionPolicy` | enum | `latest` | `latest` (current live definition) or `pinned` (specific snapshot version) |
| `pinnedVersion` | integer | `1` | Version number when `versionPolicy` is `pinned` |
| `inputMapping` | object | `{}` | Map of child trigger keys to parent context expressions (e.g. `{"message": "node_2.response", "user_id": "trigger.user_id"}`) |
| `outputNodeIds` | string[] | `[]` | Only return outputs from these child node IDs; empty = all outputs |
| `maxDepth` | integer | `10` | Maximum nesting depth for recursion protection (1–20) |

**Execution model:**

- The child workflow runs **synchronously** within the parent's execution thread (not a separate Celery task).
- A new `WorkflowInstance` row is created for the child, linked to the parent via `parent_instance_id` and `parent_node_id`.
- **Input mapping** evaluates each expression against the parent's context via `safe_eval` and builds the child's `trigger_payload`.
- **Output filtering** returns either all child node outputs (`node_*` + `trigger`) or a subset matching `outputNodeIds`.

**Output:** `{ child_instance_id: string, child_workflow_name: string, child_status: string, outputs: object }`

**Recursion protection:** The engine tracks a `_parent_chain` of ancestor workflow definition IDs. If a sub-workflow would create a cycle (e.g. A → B → A) or exceed `maxDepth`, execution fails with a descriptive error.

**Version pinning:** Use `latest` during development (always picks the current graph). Pin to a specific version in production for stability — the child uses the snapshot at that version number.

**Limitations (v1):** Child workflows containing Human Approval nodes cause the parent sub-workflow node to fail. HITL bubbling from child to parent will be supported in a future release.

---

## Knowledge nodes

### Knowledge Retrieval (`knowledge_retrieval`)

Searches knowledge bases and returns relevant document chunks for RAG.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `knowledgeBaseIds` | string[] | `[]` | KB UUIDs to search |
| `queryExpression` | string | `trigger.message` | Expression resolving to search query |
| `topK` | integer | `5` | Max chunks to return (1–20) |
| `scoreThreshold` | number | `0.0` | Min similarity score (0–1) |

**Output:** `{ chunks: [...], context_text: string, query: string, chunk_count: number }`

The `context_text` field concatenates all chunk contents separated by `\n\n---\n\n`, ready to inject into an LLM Agent's system prompt via `{{ node_X.context_text }}`.

See [RAG & Knowledge Base](rag-knowledge-base.md) for the full ingestion and retrieval pipeline details.

---

## Notification nodes

### Notification (`notification`)

Sends a formatted message to an external channel. Supports 8 channels with channel-specific payload construction.

See [Notification Guide](notification-guide.md) for a full user guide with setup instructions per channel.

**Common config fields (always visible):**

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `channel` | enum | `slack_webhook` | Target channel (see table below) |
| `destination` | string | `""` | Webhook URL, bot token, or API key. Supports all three value sources |
| `messageTemplate` | string | `""` | Jinja2 message body. Use `{{ trigger.field }}` or `{{ node_2.response }}` |

**Supported channels:**

| Channel | Transport | Destination | Channel-specific fields |
|---------|-----------|-------------|------------------------|
| `slack_webhook` | POST to Incoming Webhook URL | Webhook URL | `username`, `iconEmoji` |
| `teams_webhook` | POST to Connector URL | Webhook URL | `title`, `themeColor` |
| `discord_webhook` | POST to Discord Webhook URL | Webhook URL | `username`, `avatarUrl` |
| `telegram` | POST to Telegram Bot API | Bot token (`{{ env.* }}`) | `chatId`, `parseMode` |
| `whatsapp` | POST to Meta Cloud API | Access token (`{{ env.* }}`) | `phoneNumber`, `phoneNumberId`, `templateName` |
| `pagerduty` | POST to Events API v2 | Routing key (`{{ env.* }}`) | `severity`, `eventAction`, `pdSource` |
| `email` | SendGrid / Mailgun API or SMTP | API key or SMTP host | `to`, `subject`, `from`, `emailProvider`, `smtpPort`, `smtpUser`, `smtpPass` |
| `generic_webhook` | POST/PUT/PATCH to any URL | URL | `httpMethod`, `httpHeaders` |

**Channel-specific fields** are only shown in the sidebar when the matching channel is selected, using the `visibleWhen` schema property (see [Frontend Guide](frontend-guide.md)).

**Three-source config resolution:**

All string config fields support three ways to provide a value:

1. **Static** — Type the value directly (e.g. `https://hooks.slack.com/services/T00/B00/xxx`)
2. **Vault secret** — Use `{{ env.SECRET_NAME }}` to pull from the tenant secrets configured in the Secrets UI
3. **Runtime expression** — Use `{{ trigger.field }}` or `{{ node_N.field }}` to resolve from the execution context

Resolution order: `{{ env.* }}` is resolved first (by `dispatch_node`), then remaining `{{ trigger.* }}` / `{{ node_*.* }}` expressions are resolved by the notification handler. Static values with no `{{ }}` pass through unchanged.

**Output:** `{ success: boolean, channel: string, status_code: number, message_preview: string, response_body: string }`

Downstream nodes can check `node_X.success` to branch on delivery status, or inspect `node_X.status_code` for HTTP-level diagnostics.

---

## NLP nodes

### Intent Classifier (`intent_classifier`)

Classifies user intent using hybrid scoring (lexical + embedding + optional LLM fallback). A production-grade upgrade over LLM Router with configurable intents, examples, and confidence thresholds.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `utteranceExpression` | string | `trigger.message` | Expression resolving to the user utterance text |
| `intents` | array of objects | `[]` | Each object: `{ name, description, examples, priority }` |
| `allowMultiIntent` | boolean | `false` | Return multiple intents when scores are close |
| `mode` | enum | `hybrid` | `hybrid`, `llm_only`, `heuristic_only` |
| `provider` | enum | `google` | LLM provider (visible when mode is `hybrid` or `llm_only`) |
| `model` | enum | `gemini-2.5-flash` | LLM model (visible when mode is `hybrid` or `llm_only`) |
| `embeddingProvider` | enum | `openai` | Embedding provider (visible when mode uses embeddings) |
| `embeddingModel` | enum | `text-embedding-3-small` | Embedding model (visible when mode uses embeddings) |
| `cacheEmbeddings` | boolean | `false` | Precompute and persist intent embeddings at save time. Recommended for large intent lists (15+). When false, embeddings are computed on-the-fly at runtime |
| `confidenceThreshold` | number | `0.6` | Heuristic confidence threshold; below this, hybrid mode falls back to LLM |
| `historyNodeId` | string | `""` | Node ID of Load Conversation State node for LLM prompt context |

**Modes:**

| Mode | Behaviour | Cost |
|------|-----------|------|
| `heuristic_only` | Lexical substring matching + embedding cosine similarity. Zero LLM calls | Embedding only |
| `hybrid` (default) | Heuristic first; if confidence < threshold, falls back to LLM classification | Embedding + conditional LLM |
| `llm_only` | Skips embeddings entirely; sends all intents to LLM with conversation history | LLM per request |

**Scoring (heuristic/hybrid):**
- Lexical: +2.0 if intent name is a substring of the utterance, +1.0 per matching example
- Embedding: `max(0, cosine_similarity) × 4.0`
- Multi-intent: picks all intents within 1.0 of the best score

**Output:** `{ intents: string[], confidence: number, fallback: boolean, scores: object, mode_used: string, memory_debug }`

When the LLM fallback fires in hybrid mode, `mode_used` is `"hybrid_llm_fallback"` and `heuristic_scores` contains the pre-fallback scoring.

**Downstream usage example:**

```
Condition: node_X.intents[0] == "book_flight"
Condition: node_X.fallback == true    → fallback branch
```

### Entity Extractor (`entity_extractor`)

Extracts structured entities from text using rule-based patterns (regex, enum, number, date, free_text) with optional LLM fallback for missing required entities.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `sourceExpression` | string | `trigger.message` | Expression resolving to the source text |
| `entities` | array of objects | `[]` | Each object: `{ name, type, pattern, enum_values, description, required }` |
| `scopeFromNode` | string | `""` | Node ID of an upstream Intent Classifier for intent-based entity scoping |
| `intentEntityMapping` | object | `{}` | Map of intent name → entity name array. Example: `{"book_flight": ["destination", "date"]}` |
| `llmFallback` | boolean | `false` | Use LLM to extract required entities that rule-based extraction missed |
| `provider` | enum | `google` | LLM provider for fallback (visible when `llmFallback` is true) |
| `model` | enum | `gemini-2.5-flash` | LLM model for fallback (visible when `llmFallback` is true) |

**Entity types:**

| Type | Matching strategy | Config required |
|------|-------------------|-----------------|
| `regex` | Python regex with `re.IGNORECASE`. Uses group(1) if capture groups exist | `pattern` |
| `enum` | Word-boundary match against each value in the list | `enum_values` |
| `number` | Matches first integer or decimal number in text | — |
| `date` | Matches `YYYY-MM-DD` format | — |
| `free_text` | Matches `entity_name: value` pattern | — |

**Intent-entity scoping:** When `scopeFromNode` references an upstream Intent Classifier and `intentEntityMapping` is configured, only entities mapped to the matched intents are extracted. If no mapping exists for a matched intent, all entities pass through.

**Output:** `{ entities: object, missing_required: string[], extraction_method: string }` plus each extracted entity as a top-level key (e.g., `node_X.destination`).

**Downstream usage example:**

```
Expression: node_X.destination           → extracted value
Condition: len(node_X.missing_required) > 0    → ask user for more info
```

---

## Annotation nodes

### Sticky Note (`stickyNote`)

**DV-03** — inline documentation for the canvas. Not executable. Sticky nodes live in `graph_json` alongside agenticNode entries but are filtered out by `dag_runner.parse_graph`, `validateWorkflow`, and `computeNodeStatuses`, so they never enter the execution ready queue and never produce logs.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `text` | string | `""` | Inline-editable note text |
| `color` | enum | `"yellow"` | `yellow`, `blue`, `green`, `pink`, `purple`, `grey` |

**Storage shape:** `{ id: "sticky_<ts>_<rand>", type: "stickyNote", position, width, height, data: { text, color } }`.

Created via **Shift+S** (viewport centre) or the toolbar sticky-note icon. Resizable via React Flow's `NodeResizer`. No ports, no handles, no dispatch. See [Developer Workflow — DV-03](dev-workflow.md) for the full design.

---

## Expression syntax

Many config fields accept **expressions** that reference data from the execution context:

- `trigger.field` — data from the trigger payload
- `node_X.response` — output from a previous node (X = numeric ID)
- `node_X.field` — any field from a previous node's output
- `item.field` — current element inside a ForEach loop
- `_loop_index` — current iteration index inside a Loop

Expressions are evaluated via `safe_eval` — a restricted Python subset that prevents code execution while allowing property access, comparisons, and basic operations.

Fields named `systemPrompt` and `reflectionPrompt` use **Jinja2 templating** instead, supporting `{{ variable }}` syntax with filters and conditionals.

Fields named with `Expression` suffix (e.g. `messageExpression`, `queryExpression`) use `safe_eval`.

See [Memory Management](memory-management.md) for the storage model, policy resolution order, promotion rules, and inspection APIs behind the advanced-memory fields documented above.

## `{{ env.* }}` references

Any config value containing `{{ env.KEY }}` is resolved at runtime against the tenant's vault secrets (`tenant_secrets` table). This allows sensitive values like API keys to be stored encrypted and injected at execution time without appearing in the graph JSON.
