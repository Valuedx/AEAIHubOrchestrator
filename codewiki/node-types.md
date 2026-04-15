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

Single-turn LLM call with a system prompt.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `provider` | enum | `google` | `google`, `openai`, `anthropic` |
| `model` | enum | `gemini-2.5-flash` | See model list below |
| `systemPrompt` | string | `""` | Jinja2 template. Use `{{ trigger.field }}` or `{{ node_2.response }}` |
| `temperature` | number | `0.7` | 0 = deterministic, 2 = very creative |
| `maxTokens` | integer | `4096` | Maximum output tokens |

**Output:** `{ response: string }`

### ReAct Agent (`react_agent`)

Iterative Reason-Act-Observe loop with MCP tool access.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `provider` | enum | `google` | `google`, `openai`, `anthropic` |
| `model` | enum | `gemini-2.5-flash` | |
| `systemPrompt` | string | `""` | Jinja2 template |
| `maxIterations` | integer | `10` | Max Reason-Act cycles |
| `tools` | string[] | `[]` | MCP tool names; empty = auto-discover all |

**Output:** `{ response: string, tool_calls: [...], iterations: number }`

### LLM Router (`llm_router`)

Classifies user intent into one of the configured labels. Pair with downstream Condition nodes to branch.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `provider` | enum | `google` | |
| `model` | enum | `gemini-2.5-flash` | |
| `intents` | string[] | `[]` | Valid intent names (e.g. `diagnose_server`, `casual_chat`) |
| `historyNodeId` | string | `""` | Node ID of Load Conversation State node |
| `userMessageExpression` | string | `trigger.message` | Expression resolving to user message |

**Output:** `{ intent: string }`

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

Calls a tool on the configured MCP server.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `toolName` | string | `""` | MCP tool name |
| `parameters` | object | `{}` | Fixed parameters; runtime outputs can override |

**Output:** Tool execution result.

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

### Load Conversation State (`load_conversation_state`)

Fetches multi-turn chat history from persistent storage.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `sessionIdExpression` | string | `trigger.session_id` | Expression resolving to session ID |

**Output:** `{ messages: [...], message_count: number }`

### Save Conversation State (`save_conversation_state`)

Appends the current user/assistant exchange to persistent chat history.

| Config field | Type | Default | Description |
|-------------|------|---------|-------------|
| `sessionIdExpression` | string | `trigger.session_id` | |
| `responseNodeId` | string | `""` | Node whose `response` field is saved |
| `userMessageExpression` | string | `trigger.message` | Expression for user message |

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

**Output:** `{ intents: string[], confidence: number, fallback: boolean, scores: object, mode_used: string }`

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

## `{{ env.* }}` references

Any config value containing `{{ env.KEY }}` is resolved at runtime against the tenant's vault secrets (`tenant_secrets` table). This allows sensitive values like API keys to be stored encrypted and injected at execution time without appearing in the graph JSON.
