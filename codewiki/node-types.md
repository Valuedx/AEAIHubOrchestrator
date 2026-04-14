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
