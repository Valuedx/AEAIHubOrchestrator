# AE AI Hub — Agentic Orchestrator Developer Guide

> - **V0.9.14 (2026-04-15):** **§26** — NLP nodes (Intent Classifier + Entity Extractor). `intent_classifier.py` — hybrid scoring (lexical + embedding cosine + LLM fallback); three modes (`hybrid`, `heuristic_only`, `llm_only`). `entity_extractor.py` — rule-based extraction (regex/enum/number/date/free_text) with LLM fallback for missing required entities; intent-entity scoping via `scopeFromNode`. `embedding_cache_helper.py` — DB-backed embedding cache with `get_or_embed()` (batch query + upsert) and `precompute_node_embeddings()` called at save time. New `EmbeddingCache` model + Alembic `0010_add_embedding_cache.py` (pgvector VECTOR column, HNSW index, RLS). Frontend: `IntentListEditor` / `EntityListEditor` custom components in `DynamicConfigForm.tsx`; `nlp` category (indigo) in palette and canvas. `visibleWhen` supports boolean values. See `codewiki/node-types.md` §NLP nodes.
>
> - **V0.9.13 (2026-04-10):** **Templates** — add or edit entries in `frontend/src/lib/templates/index.ts` (import graphs from `example*.ts` or inline `nodes`/`edges`). **Sync execute** — `app/api/workflows.py` `execute_workflow` async branch; `schemas.py` `SyncExecuteOut`, `ExecuteRequest.sync` / `sync_timeout`. **Debug replay** — `workflowStore` checkpoint actions; `DebugReplayBar.tsx`; `api.listCheckpoints` / `getCheckpointDetail`. See `TECHNICAL_BLUEPRINT.md` V0.9.13.
>
> - **V0.9.12 (2026-04-07):** **§25** — A2A (Agent-to-Agent) protocol. Per-tenant agent card (`GET /tenants/{id}/.well-known/agent.json`), JSON-RPC 2.0 dispatcher (`POST /tenants/{id}/a2a`) with `tasks/send`, `tasks/get`, `tasks/cancel`, `tasks/sendSubscribe`. Outbound **A2A Agent Call** node delegates tasks to remote A2A agents. Inbound key management (`POST/GET/DELETE /api/v1/a2a/keys`). Workflow publish toggle (`PATCH /api/v1/workflows/{id}/publish`). New `A2AApiKey` model, `is_published` on `WorkflowDefinition`. Alembic migration `0007_a2a_support.py`. `WorkflowInstance` status → A2A task state mapping (`suspended` → `input-required`).
>
> - **V0.9.11 (2026-03-22):** **§24** — operator pause / cancel / resume (`cancel_requested`, `pause_requested`, migrations `0005`/`0006`); **§4** clarified HITL `suspended` vs operator `paused`; **§21** SSE terminal statuses. **§23** — Bridge User Reply + `displayName` pointers (V0.9.10).
>
> - **Earlier sections:** Custom nodes (§1), `safe_eval` (§2), ReAct (§3), ForEach / retry / HITL (§4), vault (§5), conversational memory (§6), through Loop node (§22).

**Version:** 0.9.14
**Last updated:** 2026-04-15

Welcome to the Developer Guide! 🚀 

**Doc map:** Architecture and API reference → `TECHNICAL_BLUEPRINT.md`. Setup, migrations, env → `SETUP_GUIDE.md`. End-user runtime walkthrough → `HOW_IT_WORKS.md`. This file focuses on **how to extend and debug** the orchestrator as a developer.

If you are a fresher or new to this codebase, you are in the right place. This guide is written specifically to help you understand how the **Agentic Orchestrator** works under the hood, step-by-step, with plain English explanations and heavily commented code examples.

---

## 📚 Core Concepts (The Basics)

Before we write code, let's understand the vocabulary:

*   **Orchestrator:** A system that manages a sequence of tasks. Think of it like a factory manager ensuring every machine does its job in the right order.
*   **Node:** A single "box" or "step" on the visual canvas. A node might send an email, ask an AI a question, or check if a condition is true.
*   **DAG (Directed Acyclic Graph):** A fancy computer science term for a flowchart. "Directed" means the arrows have a direction. "Acyclic" means it doesn't loop infinitely back on itself. It always moves forward through the workflow.
*   **Context:** The highly secured "memory" of the workflow. Every time a node finishes running, it drops its results into the Context. The nodes downstream can then read those results.
*   **MCP (Model Context Protocol):** A standard way for our AI agents to securely connect to external tools (like a tool to check server status or query a database).
*   **Jinja2:** A "fill-in-the-blanks" text system. If you write `"Hello {{ user_name }}"`, Jinja2 will look inside the Context for `user_name` and replace it, resulting in `"Hello Alice"`.

---

## 🛠️ 1. Let's Build Your First Custom Node

The most common task you will do as a developer is adding a new type of Node. 
The magical part? **You don't need to write any React/Frontend code.** The UI builds itself based on a JSON file!

Let's pretend we want to build a **Slack Notification Node** that sends a message to a team channel.

### Step 1: Tell the UI about your Node (`node_registry.json`)

Open the `shared/node_registry.json` file. This is the source of truth for all nodes. We will add our new node here.

```json
{
  "type": "slack_notification",
  "category": "action",
  "label": "Slack Notification",
  "description": "Sends a message to a Slack channel",
  "icon": "MessageSquare",
  "color": "bg-blue-100 border-blue-300",
  
  // This is where the magic happens! The UI reads this config_schema
  // and automatically generates the textboxes and checkboxes for the user.
  "config_schema": {
    "channel": {
      "type": "string",
      "description": "Enter the Slack channel name (e.g., #alerts)"
    },
    "messageTemplate": {
      "type": "string",
      "description": "What to say! You can use variables like {{ context.user }}"
    },
    "urgent": {
      "type": "boolean",
      "description": "Check this box to flag it as high priority",
      "default": false
    }
  }
}
```

### Step 2: Write the Python Logic (`node_handlers.py`)

Now that the UI can place the node, we need to tell the backend what to do when the workflow actually runs.
Open `backend/app/engine/node_handlers.py`.

```python
from app.engine.prompt_template import render_template

# This function receives the user's config, the current memory (context), 
# and the tenant_id (who is running this workflow)
async def _handle_slack_notification(node_data: dict, context: dict, tenant_id: str) -> dict:
    
    # 1. Safely grab the settings the user typed into the UI
    config = node_data.get("config", {})
    channel = config.get("channel", "#general")
    template = config.get("messageTemplate", "No message provided.")
    urgent = config.get("urgent", False)
    
    # 2. Fill in the blanks! Let's render the Jinja2 template.
    # If template is "Server {{ trigger.server }} failed"
    # and context has a trigger.server value of "Web-01", 
    # message becomes "Server Web-01 failed".
    message = render_template(template, context)
    
    # 3. Apply basic business logic
    if urgent:
        message = f"🚨 *URGENT* 🚨\n{message}"
        
    # 4. Do the actual work! (e.g., call a Slack API hook)
    print(f"I am sending this to {channel}: {message}")
    
    # 5. Return a dictionary. Whatever you return here is permanently 
    # saved into the workflow's Context memory for the next nodes to use.
    return {
        "status": "success",
        "delivered_to": channel,
        "final_text": message
    }
```

Finally, at the bottom of `node_handlers.py`, just route the traffic to your new function:

```python
# Inside the dispatch_node function:
if node_category == "action":
    if label == "Slack Notification":  # MUST match the label in the JSON!
        return await _handle_slack_notification(node_data, context, tenant_id)
```
Congratulations! You just built a fully functional distributed workflow node! 🎉

---

## 🧠 2. Writing Logic Rules (`safe_eval`)

Workflows often need to make decisions like, *"If the AI found a virus, go left. If the file is safe, go right."* We do this using **Condition Nodes**.

Because letting users run random Python code is a huge security risk, we built a very strict expression evaluator called `safe_eval`. It acts like a mini-language.

### Reading from Memory (The Context)
If a previous node with the ID `node_2` returned `{"user": {"age": 25, "name": "Bob"}}`, you can check his age like this:
```python
node_2.user.age >= 18
```

### Safe Functions You Can Use
Instead of standard Python, you can only use these safe functions (added in V0.9):
*   **Math:** `len()`, `min()`, `max()`, `abs()`
*   **Types:** `str()`, `int()`, `float()`, `bool()`
*   **Text Checkers:** `startswith()`, `endswith()`, `contains()` (checks if an item is in a list)
*   **Text Changers:** `lower()`, `upper()`, `strip()`

### Examples for Freshers
Here is how you would type these inside a Condition Node on the visual canvas:

**Example A: Simple text check**
Wait, did the AI respond with an error? Let's check:
```python
lower(node_1.status) == "error"
```

**Example B: Making sure an array isn't empty**
Did the database give us any results?
```python
len(node_3.database_rows) > 0
```

**Example C: Complex security condition**
Is the user part of the `@admin.com` domain AND is this an urgent request?
```python
trigger.email.endswith("@admin.com") and trigger.priority == "High"
```

---

## 🤖 3. The ReAct Agent (AI that uses Tools)

Normally, if you ask ChatGPT a question, it just replies with text. 
But a **ReAct Agent** (Reasoning + Acting) is special. You give it a goal, and you hand it a backpack full of tools (like a tool to restart a server, or a tool to read logs).

### How to configure it:
1.  Drag a **ReAct Agent** onto the canvas.
2.  In the `tools` dropdown, you can select specific tools you want to allow it to use.
3.  **Pro Tip:** If you leave the tools dropdown completely empty, the backend will auto-discover **every single tool** available on the MCP server and hand them all to the AI.

### How it thinks:
The backend code (`react_loop.py`) runs a loop that goes like this:
1. **AI:** "I need to check the server status. I will use the `get_status` tool."
2. **Backend:** *Pauses the AI, runs the `get_status` tool, gets the result, hands the result back to the AI.*
3. **AI:** "Okay, the server is down. I will now use the `restart_server` tool."
4. **Backend:** *Runs the tool, returns the result.*
5. **AI:** "The server is back up! Here is my final summary for the user."

---

## 🔄 4. Advanced Tricks: Loops, Retries, and Suspensions

> **Operator pause / cancel / resume** (Execution panel **Pause**, **Stop**, **Resume**) is a separate feature — cooperative stops **between nodes**, statuses `paused` / `cancelled`. See **§23**.  
> **HITL** below uses **`suspended`** and **`POST …/callback`** — do not confuse the two.

### The "ForEach" Loop (Doing things repeatedly)
Introduced in V0.9, the ForEach node takes a list, and runs every node attached to it *once per item* in the list.

If your list is `["Alice", "Bob"]`:
*   `_loop_item` will be "Alice" for the first run.
*   `_loop_item` will be "Bob" for the second run.

### The Retry Button (Oops, API failed!)
If a workflow runs 10 steps successfully, but fails on step 11 because the internet blinked, you don't want to start over from step 1!
The backend now tracks `current_node_id`. If it fails, a user can hit **Retry** in the UI. The backend deletes the error log, loads the memory right before step 11, and simply presses 'play' again.

### Human-in-the-Loop — approval gate (status `suspended`)
Sometimes it is too dangerous to let an AI delete a database automatically. It needs human approval.
If a Node's config contains an `approvalMessage` (e.g., `"Approve deletion?"`), the engine stops after that node, marks the instance as **`suspended`** (not `paused`), and persists context.
When a human approves via the hub UI, **`POST /api/v1/workflows/{workflow_id}/instances/{instance_id}/callback`** runs `resume_graph` with `approval_payload` / optional `context_patch` — the workflow continues from the same graph position.

#### HITL Review UI (V0.9.4)

The orchestrator now includes a built-in review UI so operators don't need external webhooks for simple approvals.

**How it works for the operator:**

1. When a workflow suspends, the **Execution Panel** shows a yellow **Review & Resume** button.
2. Clicking it fetches the current context from `GET /instances/{id}/context` (internal keys like `_trace` are stripped before display).
3. The `HITLResumeDialog` opens showing:
   - The node's configured **approval message** (e.g., *"About to delete 500 production records — confirm?"*).
   - A read-only **JSON viewer** of every node's output up to the suspension point.
   - An editable **Context Patch** textarea (JSON object) where the operator can inject corrected values — for example, overriding a specific node's output before the workflow continues.
4. **Approve & Resume** merges the patch and calls `POST /callback` — the workflow continues.
5. **Reject** sends `{rejected: true}` in the approval payload — downstream Condition nodes can branch on `approval.rejected`.

**How to make a node require approval:**

In `shared/node_registry.json`, add `approvalMessage` to the node's `config_schema`:

```json
"config_schema": {
  "approvalMessage": {
    "type": "string",
    "default": "",
    "description": "If non-empty, execution pauses here for human approval before continuing."
  }
}
```

Set it on any action node in the Properties panel. Leave it empty to skip the approval gate.

**Context patch use cases:**

| Scenario | Patch |
|----------|-------|
| Override a condition result | `{"node_5": {"branch": "true"}}` |
| Inject corrected data | `{"node_3": {"score": 0.95, "label": "approved"}}` |
| Add a manual flag | `{"manual_override": true}` |

---

## 🔐 5. Security: The Vault

**Golden Rule:** NEVER hardcode passwords or API keys in the visual builder text boxes.

Instead, an admin saves an API key in the Database Vault (encrypted) under a name like `AWS_PROD_KEY`.
When a developer configures a node (like an HTTP request), they just type:
`{{ env.AWS_PROD_KEY }}`

When the workflow runs, exactly 1 millisecond before the node executes, `resolve_config_env_vars()` (in `prompt_template.py`) intercepts that string, safely fetches the encrypted key from the database, decrypts it in RAM, and hands it to the node. Safe and sound!

---

## 💬 6. Stateful Conversational Memory

By default, an Orchestrator DAG is acyclic and stateless. But what if you want to build a chatbot that remembers context over 10 messages? You use the **Stateful Re-Trigger Pattern** (introduced in V0.9.1).

Instead of making the DAG loop infinitely, we let each user message trigger a **fresh DAG instance**. We use two "bookend" nodes to fetch and save memory to a PostgreSQL database (`conversation_sessions`).

### How to build a conversational DAG

The canonical graph for any chat-enabled workflow is:

```text
[Webhook Trigger]
       ↓
[Load Conversation State]   ← config: sessionIdExpression = "trigger.session_id"
       ↓
[LLM Router]                ← config: intents = ["diagnose_server", "casual_chat", "escalate"]
                                       historyNodeId = "node_2"
       ↓
[Condition]                 ← condition: node_3.intent == "diagnose_server"
    ↙         ↘
[Branch A]  [Branch B]  ...  (any action/agent nodes)
    ↘         ↙
[Save Conversation State]   ← config: responseNodeId = "node_X"
                                       userMessageExpression = "trigger.message"
```

### Key design points:
1. **The DAG stays acyclic** — each user message simply fires a fresh execution instance.
2. **Load at the start / Save at the end** bookend every instance with memory fetch/store.
3. **LLM Router** reads the full history passed from the Load State node, handling pivots and follow-ups contextually.
4. **The intent value** flows dynamically into standard Condition nodes — keeping routine routing out of arbitrary Python code.

---

## 📋 7. Execution Log — Copy & Expand

After running a workflow, every node's **Input** and **Output** JSON blocks in the execution panel now have two action buttons in the top-right corner:

| Button | What it does |
|--------|-------------|
| **Copy** (clipboard icon) | Copies the full JSON string to the clipboard. The icon turns green with a ✓ for 2 seconds to confirm. |
| **Expand** (maximize icon) | Opens a full-size dialog showing the complete JSON (no height cap) with its own Copy button. |

This is especially useful for large LLM responses or deeply nested tool outputs that are truncated in the 128px preview area.

---

## 🔍 8. Palette Search

The Node Palette has a **search box** at the top. Type any part of a node's label or description (e.g., "http", "loop", "approval") to instantly filter the list.

- Categories with zero matches are hidden
- Matching categories auto-expand
- Category headers show `matched/total` while searching
- ✕ button clears the filter

No code changes needed when you add a new node to `node_registry.json` — the search automatically covers its `label` and `description` fields.

---

## 🔴 9. Validation Highlighting on Node Cards

Node cards show red or yellow visual indicators **in real time** as you edit the canvas — no need to click Run to discover problems.

### How it works

**Files:** `frontend/src/lib/useNodeValidation.ts`, `frontend/src/components/nodes/AgenticNode.tsx`

The `useNodeValidation()` hook subscribes to `nodes` and `edges` from the Zustand store and runs `validateWorkflow()` inside `useMemo`. It returns two sets:

```ts
const { errorIds, warningIds } = useNodeValidation();
// errorIds  → Set of node IDs with hard errors (broken config)
// warningIds → Set of node IDs with warnings (e.g. disconnected)
```

`AgenticNode` checks `errorIds.has(id)` and `warningIds.has(id)` to decide which ring to show.

### Visual priority (highest → lowest)

1. **Blue ring** — node is selected (always wins)
2. **Red ring + `AlertCircle`** — configuration error
3. **Yellow ring + `AlertTriangle`** — disconnected from trigger
4. **Coloured status dot** — runtime execution status (default)

### Adding validation rules automatically updates the highlighting

Because `useNodeValidation` calls the same `validateWorkflow()` function used by the Run button, any rule you add to `REQUIRED_FIELDS` in `validateWorkflow.ts` will **automatically light up** the corresponding node card in red — no extra code needed.

---

## 🔧 10. MCP Tool Node — Visual Tool Picker

When you drop an **MCP Tool** node onto the canvas and click it, the `toolName` field is rendered as a searchable visual picker instead of a plain text input.

### What you see
- A search box to filter by tool name, title, or description
- Tools grouped by category, each card showing: **title**, **safety tier badge**, description snippet, and the exact `tool.name` in monospace
- Clicking a card selects it and shows it in a highlighted "selected" bar with a ✕ clear button
- The selected tool's exact API name is stored in `config.toolName` — no typos possible

### Why it matters
Previously you had to know the exact internal tool name (e.g., `get_server_status`) and type it correctly. Now you browse the live MCP tool registry the same way you pick tools for a ReAct Agent.

### Offline fallback
If the MCP server is unreachable, the component shows a message and you can fall back to typing the tool name manually.

---

## ⚡ 11. Expression Variable Picker — Autocomplete in Config Fields

Whenever you click a Condition node, a ForEach, a Save Conversation State, or any node with a **systemPrompt**, the property panel automatically shows an autocomplete dropdown as you type in expression fields.

### How to use it

- **Condition → `condition` field**: Type `node` and a dropdown appears showing all upstream node outputs (e.g., `node_3.intent`, `node_2.response`). Arrow keys to navigate, Enter/Tab to insert.
- **systemPrompt fields**: Type `{{` and you'll get Jinja2 suggestions like `{{ trigger.message }}` or `{{ node_2.response }}`.
- **responseNodeId / historyNodeId**: Typing shows only node IDs (`node_1`, `node_2`) — no path, just the ID.

The picker is **cursor-aware**: if your expression already has `node_2.intent == "` and you position the cursor back on `node_2`, the picker will replace only that token, not the whole line.

### How to add output fields for your new node

Open `frontend/src/lib/expressionVariables.ts` and find `NODE_OUTPUT_FIELDS`:

```ts
const NODE_OUTPUT_FIELDS: Record<string, string[]> = {
  "LLM Agent":   ["response", "input_tokens", "output_tokens"],
  "LLM Router":  ["intent"],
  // 👉 Add your node label and what fields it outputs at runtime:
  "Slack Notification": ["delivered_to", "final_text", "status"],
};
```

That's it — the autocomplete will immediately suggest `node_X.delivered_to`, `node_X.final_text`, etc. for any Slack Notification node on the canvas.

### How to add a new expression field

If your new node type has a field that should get autocomplete (e.g., a `filterExpression`), open `DynamicConfigForm.tsx` and add the key to the appropriate set:

```ts
const EXPRESSION_KEYS = new Set([
  "condition", "arrayExpression", "sessionIdExpression", "userMessageExpression",
  "filterExpression",  // 👈 add here for dot-path expressions
]);
```

---

## ↩️ 12. Undo / Redo — Canvas History

The workflow canvas supports full undo/redo with **Ctrl+Z** (undo) and **Ctrl+Y** or **Ctrl+Shift+Z** (redo). Toolbar buttons show the same actions with disabled state when history is empty.

### How it works

**File:** `frontend/src/store/flowStore.ts`

The store maintains two history stacks: `past[]` and `future[]`, each capped at 50 snapshots. A snapshot is `{ nodes: Node[], edges: Edge[] }`.

`_pushHistory()` is called automatically **before** every destructive action:

| Action | When snapshot is taken |
|--------|----------------------|
| `addNode()` | Before the node is added |
| `deleteNode()` | Before the node and its edges are removed |
| `onConnect()` | Before the new edge is created |
| `onNodesChange()` with drag | On first `dragging: true` event per drag (once per gesture) |
| `onNodesChange()` with remove | Before a node is removed via Delete key |
| `onEdgesChange()` with remove | Before an edge is removed via Delete key |

> `updateNodeData()` (property panel edits) is **not** snapshotted because it fires on every keystroke. Config changes can be reverted by simply editing the field back.

### Loading a workflow resets history

Calling `replaceGraph()` (used by load, new workflow, and example loaders) always resets both `past` and `future` to empty arrays — this prevents confusing undo across different workflows.

---

## 🛡️ 13. Pre-Run Validation — Catching Mistakes Before They Run

The orchestrator validates your workflow **in the browser** the moment you hit **Run**. This prevents common mistakes without wasting an API call.

### What gets checked?

**File:** `frontend/src/lib/validateWorkflow.ts`

| Check | What it catches | Severity |
|-------|----------------|----------|
| No trigger | Canvas has no Webhook or Schedule Trigger | Error |
| Disconnected node | A node exists on canvas but nothing connects it to a trigger | Warning |
| Empty required field | e.g., Condition has no expression, HTTP Request has no URL | Error |
| LLM Router: no intents | The `intents` array is empty | Error |
| Broken node reference | `responseNodeId` or `historyNodeId` points to a non-existent node | Error |

**Errors** block execution entirely. **Warnings** allow you to click **"Run Anyway"** (useful when you intentionally have a disconnected utility branch you're testing).

### How to add a validation rule for your new node

Open `frontend/src/lib/validateWorkflow.ts` and find `REQUIRED_FIELDS`:

```ts
const REQUIRED_FIELDS: Record<string, string[]> = {
  "Condition":               ["condition"],
  "HTTP Request":            ["url"],
  "MCP Tool":                ["toolName"],
  "ForEach":                 ["arrayExpression"],
  "Save Conversation State": ["responseNodeId"],
  // 👉 Add your new node label and required field names here:
  "Slack Notification":      ["channel", "messageTemplate"],
};
```

That's it! The validator will automatically show an error if those fields are empty when a user tries to run a workflow containing your node.

If your node has a **node-ID reference field** (a field where the user types another node's ID like `node_4`), also add it to `NODE_ID_REF_FIELDS`:

```ts
const NODE_ID_REF_FIELDS: Record<string, string[]> = {
  "Save Conversation State": ["responseNodeId"],
  "LLM Router":              ["historyNodeId"],
  // 👉 Add reference fields for your node:
  "Data Aggregator":         ["sourceNodeId"],
};
```

The validator will cross-check that the referenced node ID actually exists on the canvas.

## 🪪 14. Node ID Visibility — Copy a Node's ID from the Properties Panel

Every node on the canvas has a **machine ID** (`node_1`, `node_2`, …) that is separate from its human-readable label. When you write expressions like `node_3.intent` or set `responseNodeId` to `node_5`, you need this ID — but it was previously invisible unless you opened DevTools.

**File:** `frontend/src/components/sidebar/PropertyInspector.tsx`

### What was added

A grey `bg-muted` chip is now rendered at the very top of the Properties panel (above the Label field). It shows:

```
ID  node_3  [copy icon]
```

Clicking the copy icon writes the ID to the clipboard. The icon swaps to a green checkmark for 2 seconds as confirmation, then reverts.

### How it works

```tsx
const [idCopied, setIdCopied] = useState(false);
const handleCopyId = useCallback(() => {
  navigator.clipboard.writeText(selectedNode.id).then(() => {
    setIdCopied(true);
    setTimeout(() => setIdCopied(false), 2000);
  });
}, [selectedNode.id]);
```

The chip renders `selectedNode.id` (e.g., `node_3`) in a `font-mono` `<code>` span. The `Copy` / `Check` icons from `lucide-react` toggle based on `idCopied`.

### Typical workflow

1. Drop a **Save Conversation State** node onto the canvas.
2. Drop a **Webhook Trigger** node and connect it.
3. Click the **Webhook Trigger** node → Properties panel opens → ID chip shows `node_1`.
4. Click the copy icon next to `node_1`.
5. Click the **Save Conversation State** node → find the `responseNodeId` field → paste `node_1`.

The expression picker autocomplete on nodeId fields also surfaces this, but the chip is faster when you already know the node you want.

## 📝 15. Inline Field Help Text — Schema Descriptions in Config Forms

Every field in the Properties panel can now show a small grey hint line below the input. The hint comes directly from the `description` property in `shared/node_registry.json`.

**Files involved:**
- `shared/node_registry.json` — source of truth for all `description` strings
- `frontend/src/components/sidebar/DynamicConfigForm.tsx` — renders `<FieldHint>`

### Adding a description to an existing field

Open `shared/node_registry.json` and find the field you want to document:

```json
"config_schema": {
  "myField": {
    "type": "string",
    "default": "",
    "description": "Explain what this field does and give a concrete example"
  }
}
```

That's it — the frontend reads `description` from the schema and renders it automatically. No TypeScript changes needed.

### Adding a description to a new node's fields

When you add a new node type (see §1), add a `description` to every `config_schema` entry from the start. Good descriptions:
- Explain *what* the field controls
- Include a concrete example value (e.g., `e.g. trigger.session_id`)
- Mention units for numeric fields (e.g., `seconds`, `0–2 range`)
- Explain the difference between enum options when it isn't obvious

### How `DynamicConfigForm` renders hints

The `FieldHint` component is a single line:

```tsx
function FieldHint({ text }: { text: string }) {
  return (
    <p className="text-[10px] text-muted-foreground leading-snug">{text}</p>
  );
}
```

Every renderer branch in `DynamicConfigForm` ends with:

```tsx
{field.description && <FieldHint text={field.description} />}
```

This covers all nine field types: enum/Select, array/ToolMultiSelect, array/JSON textarea, object/JSON textarea, boolean/checkbox, number/Input, ToolSingleSelect, ExpressionInput, and plain string/Input.

## 🔁 16. ForEach & Merge — Canvas-Level UX Clarity

These two logic nodes now surface key config on the canvas card so users don't have to open the Properties panel to understand what they do.

**File:** `frontend/src/components/nodes/AgenticNode.tsx`

### Merge — strategy badge

The `waitAll`/`waitAny` strategy is shown as a secondary badge next to the category pill:

```tsx
{label === "Merge" && config?.strategy != null && (
  <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
    {String(config.strategy)}
  </Badge>
)}
```

This mirrors the pattern already used for agent model badges. If you add a new strategy option to `node_registry.json`, it appears automatically.

### ForEach — array expression hint

When `arrayExpression` is set, a small `↻ expr` line appears below the badge row:

```tsx
{label === "ForEach" && config?.arrayExpression && (
  <p className="text-[10px] font-mono text-muted-foreground truncate mt-1 leading-tight"
     title={String(config.arrayExpression)}>
    ↻ {String(config.arrayExpression)}
  </p>
)}
```

The line is `truncate` (with full text in `title` for hover) so it doesn't blow out the card width. When the field is empty (node just dropped, not configured) the line is hidden entirely.

### Adding similar hints for your own node

Follow the same pattern — guard with `label === "YourNodeLabel" && config?.yourField` and render a `<p>` or `<Badge>` inside `CardHeader` after the badge `<div>`. Keep the text short and `truncate` anything that could be long.

---

## ⚙️ 17. Deterministic Batch Execution — Reproducible Log Ordering

**Introduced in V0.9.3**

By default, when multiple nodes in a workflow are ready at the same time (e.g., two parallel branches after a fan-out), they are submitted to a `ThreadPoolExecutor` and their results are processed as each thread completes (`as_completed`). This maximises throughput but means execution logs may appear in a different order on each run.

For debugging, testing, or replay scenarios where you need the **same log sequence every time**, you can enable **deterministic mode**.

### How to enable it

Pass `"deterministic_mode": true` in the execute request body:

```json
POST /api/v1/workflows/{workflow_id}/execute
{
  "trigger_payload": { "input": "test value" },
  "deterministic_mode": true
}
```

Or from the frontend API client:

```ts
await api.executeWorkflow(workflowId, triggerPayload, /* deterministicMode */ true);
```

### What changes when it's on

| Aspect | Default (`false`) | Deterministic (`true`) |
|--------|-------------------|------------------------|
| Submission order | Arbitrary | Sorted by node ID |
| Result processing | `as_completed` (fastest thread first) | `.result()` in sorted order |
| Log write order | Non-deterministic | Stable across every run |
| Langfuse tag | — | `"deterministic"` tag added |
| Throughput | Maximum | Slightly lower for large parallel batches |

### When to use it

- **Integration tests** — assert exact log sequences without flaky ordering.
- **Replay / debugging** — compare two runs of the same workflow and diff their logs.
- **On-call investigations** — reproduce the exact execution sequence that caused a failure.
- Leave **off** (`false`) for all production hot-paths.

### Code path (for contributors)

**`backend/app/api/schemas.py`** — `ExecuteRequest.deterministic_mode: bool`

**`backend/app/api/workflows.py`** — passes the flag to `execute_workflow_task.delay()`

**`backend/app/workers/tasks.py`** — `execute_workflow_task(instance_id, deterministic_mode)` forwards it to `execute_graph`

**`backend/app/engine/dag_runner.py`** — `_execute_parallel` reads `deterministic_mode`:
- `True`: sorts `ready_nodes` → creates log entries in sorted order → submits in sorted order → calls `future.result()` in sorted order
- `False` (default): original `as_completed` path, unchanged

---

## 🧠 18. Reflection Node — Workflow Self-Assessment

**Introduced in V0.9.5**

The Reflection node lets a workflow "look back" at everything that has happened so far and ask an LLM to produce a structured JSON decision. A downstream Condition node then routes based on that decision.

### When to use it

- **Quality gate**: after several agent nodes, ask "is the output good enough, or should we escalate?"
- **Loop controller**: after a ForEach, ask "did enough items succeed, or do we retry?"
- **Routing decision**: given the full execution history, pick the next department/queue/action.

### How it works

**File:** `backend/app/engine/reflection_handler.py`

```
Reflection node executes
        │
        ▼
_build_execution_summary(context, max_history_nodes)
  ├── Collects last N node_* keys from context (insertion order = execution order)
  ├── Hard cap at 25 nodes regardless of config
  ├── Truncates each to 800 chars (prevents token explosion)
  └── Prepends trigger payload if present
        │
        ▼
render_prompt(reflectionPrompt, {**context, "execution_summary": summary})
  └── Jinja2 template — {{ execution_summary }} injects the history block
        │
        ▼
call_llm(provider, model, system_prompt, user_message, temperature=0.3)
  └── user_message always ends with "respond ONLY with a valid JSON object"
        │
        ▼
_parse_json_response(raw)
  ├── Strip ```json ... ``` fences
  ├── json.loads() → if dict, return; if primitive, wrap {"reflection": value}
  ├── Regex {…} extraction fallback
  └── Last resort: {"reflection": raw, "parse_error": True}
        │
        ▼
Returns {**parsed, "_usage": usage, "_raw_response": raw_response}
  └── dag_runner stores this under context["node_X"]
```

### Configuring a Reflection node

| Field | Default | What it does |
|-------|---------|-------------|
| `provider` | `google` | LLM provider |
| `model` | `gemini-2.5-flash` | Model variant |
| `reflectionPrompt` | *(required)* | Jinja2 system prompt; use `{{ execution_summary }}` |
| `outputKeys` | `[]` | Expected top-level keys in the JSON response — warns if absent |
| `maxHistoryNodes` | `10` | How many recent node outputs to include in the summary |
| `temperature` | `0.3` | Lower = more deterministic JSON output |
| `maxTokens` | `1024` | Enough for structured JSON; increase for verbose responses |

### Example prompt template

```jinja2
You are a quality-control engine for an IT support workflow.
Review the execution history and decide whether the issue has been resolved.

{{ execution_summary }}

Respond with a JSON object with exactly these keys:
- "resolved": true or false
- "confidence": 0.0–1.0
- "next_action": one of "close_ticket", "escalate", "retry_diagnosis"
- "reason": one-sentence explanation
```

### Example downstream condition

```
node_5.resolved == True          → close ticket branch
node_5.next_action == "escalate" → escalate branch
```

### Key design constraint: read-only

The Reflection node **never mutates `context`**. It only returns a value. The dag_runner stores that value under the node's own key. This means:

- Earlier node outputs are never overwritten
- There is no dynamic graph mutation (the DAG is Kahn-sorted upfront)
- The pattern is fully composable with ForEach, HITL, and Condition nodes

### Code path (for contributors)

1. `node_handlers.dispatch_node()` matches `label == "Reflection"` and imports `_handle_reflection` from `reflection_handler.py`
2. `_handle_reflection()` reads config, builds summary, renders prompt, calls LLM
3. `_parse_json_response()` normalises the raw text to a dict
4. `record_generation()` logs the call to Langfuse under `reflection:{provider}/{model}`
5. dag_runner receives `{**parsed, "_usage": ..., "_raw_response": ...}` and stores it in context

### Frontend integration

- `shared/node_registry.json` — `reflection` type under `agent` category with full `config_schema`
- `validateWorkflow.ts` — `"Reflection": ["reflectionPrompt"]` in `REQUIRED_FIELDS` blocks execution if prompt is empty
- `expressionVariables.ts` — `"Reflection": ["_raw_response"]` in `NODE_OUTPUT_FIELDS`; user-defined `outputKeys` fields (e.g., `node_X.next_action`) are also accessible at runtime but can't be statically enumerated

---

## 💾 19. Checkpointing — Per-Node Context Snapshots

**Introduced in V0.9.6**

Every time a node completes successfully, the engine automatically saves a **checkpoint** — a full snapshot of the execution context at that exact moment. This lets you inspect what the workflow "knew" after each step, without having to run it again.

### What a checkpoint contains

A checkpoint stores the `context_json` minus all internal runtime keys (anything starting with `_` — like `_trace`, `_loop_item`, `_loop_index`). What remains is:
- `trigger` — the original webhook/schedule payload
- `node_1`, `node_2`, … — outputs from every node that has completed up to that point

### Where checkpoints are written

**File:** `backend/app/engine/dag_runner.py` → `_save_checkpoint(db, instance_id, node_id, context)`

```python
# After a single node completes (execute_single_node):
log_entry.completed_at = _utcnow()
db.commit()
_save_checkpoint(db, instance.id, node_id, context)   # ← here

# After a parallel batch node completes (_apply_result):
context[node_id] = output
log_entry.status = "completed"
log_entry.completed_at = _utcnow()
_save_checkpoint(db, instance.id, node_id, context)   # ← here
```

**ForEach iterations** are covered automatically because they call `_execute_single_node` for each iteration — one checkpoint per iteration per downstream node.

### Non-fatal design

```python
def _save_checkpoint(db, instance_id, node_id, context):
    try:
        clean_context = {k: v for k, v in context.items() if not k.startswith("_")}
        db.add(InstanceCheckpoint(instance_id=instance_id, node_id=node_id,
                                  context_json=clean_context, saved_at=_utcnow()))
        db.commit()
    except Exception as exc:
        logger.warning("Failed to save checkpoint: %s", exc)
        db.rollback()   # ← never propagated upward
```

If the checkpoint write fails (e.g., transient DB error), execution continues uninterrupted. Only a warning appears in the logs.

### Reading checkpoints via the API

```
GET /api/v1/workflows/{workflow_id}/instances/{instance_id}/checkpoints
```
Returns a list ordered by `saved_at` — each entry has `id`, `instance_id`, `node_id`, `saved_at`. No context payload.

```
GET /api/v1/workflows/{workflow_id}/instances/{instance_id}/checkpoints/{checkpoint_id}
```
Returns the full checkpoint including `context_json`.

### Database

**Table:** `instance_checkpoints`
**Migration:** `alembic/versions/0004_instance_checkpoints.py`
**Model:** `app/models/workflow.py` → `InstanceCheckpoint`

Rows are cascade-deleted when the parent `WorkflowInstance` is deleted.

---

## 🔬 20. Checkpoint-aware Langfuse — Linking Traces to DB Snapshots

**Introduced in V0.9.7**

After Item 4 introduced DB checkpoints, Item 5 connects them to Langfuse so that every node span in the Langfuse UI carries a direct reference to its DB context snapshot.

### How it works

**`_save_checkpoint` now returns the checkpoint UUID:**

```python
# Before (returned None):
_save_checkpoint(db, instance.id, node_id, context)

# After (returns str UUID or None):
checkpoint_id = _save_checkpoint(db, instance.id, node_id, context)
```

**For sequential nodes** (`_execute_single_node`), the span is still open when the checkpoint is saved. The checkpoint_id is passed directly to `span.update()`:

```python
checkpoint_id = _save_checkpoint(db, instance.id, node_id, context)
span_meta = {"status": "completed", "has_output": output is not None}
if checkpoint_id:
    span_meta["checkpoint_id"] = checkpoint_id
span.update(output=span_meta)
```

In Langfuse, the node's span now shows `checkpoint_id: "abc123-..."` in its output metadata. You can copy this UUID and look up the exact context snapshot via:
```
GET /api/v1/workflows/{wf_id}/instances/{inst_id}/checkpoints/{checkpoint_id}
```

**For parallel nodes** (`_apply_result`), the Langfuse span has already exited by the time `_apply_result` runs. Instead, the checkpoint_id is embedded in the execution log entry's `output_json`:

```python
checkpoint_id = _save_checkpoint(db, instance.id, node_id, context)
log_entry.output_json = (
    {**(output or {}), "_checkpoint_id": checkpoint_id}
    if checkpoint_id else output
)
```

This means the checkpoint_id is accessible via `GET /instances/{id}` → `logs[i].output_json._checkpoint_id`.

### `span_node` signature update

`observability.py` → `span_node()` now accepts an optional `checkpoint_id` kwarg:

```python
@contextmanager
def span_node(
    parent,
    *,
    node_id: str,
    node_type: str,
    node_label: str = "",
    input_data: Any = None,
    checkpoint_id: str | None = None,   # ← new
) -> Generator:
```

When `checkpoint_id` is provided at span creation time, it is written into the Langfuse span's metadata immediately. This kwarg is available for any future caller that has the checkpoint_id before the span opens (e.g., resume-from-checkpoint scenarios in Item 7).

### Debugging workflow: sequential node

1. Open Langfuse → find the workflow trace
2. Click a node span
3. In **Output metadata**, find `checkpoint_id`
4. Call `GET .../checkpoints/{checkpoint_id}` → get exact context snapshot at that point
5. Compare with the next checkpoint to see exactly what the node added

### Debugging workflow: parallel node

1. Call `GET .../instances/{id}` → find the node's log entry
2. Read `output_json._checkpoint_id`
3. Call `GET .../checkpoints/{checkpoint_id}` → full snapshot

### Why different for sequential vs parallel?

In `_execute_single_node`, the node runs inside a `with span_node(...) as span:` block. The checkpoint is saved AFTER `dispatch_node` returns but BEFORE the `with` block exits — so the span is still live.

In `_execute_parallel`, each node runs in a `ThreadPoolExecutor` thread. The thread's `_run_node` function creates its own `with span_node(...)` block, which exits when the thread returns. The main thread then collects the future result in `_apply_result` — by then the span is already committed to Langfuse. We embed the checkpoint_id in the execution log as a fallback linkage mechanism.

---

## 🌊 21. Rich Token Streaming — Live LLM Output in the Browser

**Introduced in V0.9.8**

LLM Agent nodes stream tokens to the browser in real time as the model generates them — no waiting for the full response.

### Architecture

```
Celery worker (LLM call)               Redis                FastAPI SSE
────────────────────────               ─────                ──────────
  stream_google / stream_openai
  / stream_anthropic
        │
        │ each token arrives
        ▼
  publish_token(instance_id, node_id, token)
        │                              │
        └──────────▶ PUBLISH ─────────▶ orch:stream:{instance_id}
                                       │
                                       │ SUBSCRIBE
                                       ◀─────────── _subscribe_tokens task
                                                         │
                                                   asyncio.Queue
                                                         │
                                                   event_generator loop
                                                         │
                                              event: token
                                              data: {"node_id": "node_2",
                                                     "token": "The ",
                                                     "done": false}
                                                         │
                                                    Browser SSE
```

### File: `backend/app/engine/streaming_llm.py`

Three streaming functions — `stream_google`, `stream_openai`, `stream_anthropic` — each:
1. Call the provider's streaming API
2. Accumulate the full text
3. Call `publish_token(instance_id, node_id, token)` for each chunk
4. Call `publish_stream_end(instance_id, node_id)` after the last chunk
5. Return the same `{response, usage, model, provider}` dict as the non-streaming path

Redis publish failures are caught and logged as warnings — execution is never blocked.

### File: `backend/app/engine/llm_providers.py`

`call_llm_streaming(...)` routes to the streaming variants when `instance_id` and `node_id` are non-empty. Falls back to `call_llm` silently if either is empty (e.g., Reflection node calls, ReAct loop).

### How node_id gets into the handler

```python
# execute_graph — once per execution
context["_instance_id"] = str(instance.id)

# _execute_single_node — before each sequential node
context["_current_node_id"] = node_id

# _handle_agent reads:
instance_id = context.get("_instance_id", "")
node_id = context.get("_current_node_id", "")
result = call_llm_streaming(..., instance_id=instance_id, node_id=node_id)
```

### File: `backend/app/api/sse.py`

```python
token_queue: asyncio.Queue = asyncio.Queue()
redis_task = asyncio.create_task(_subscribe_tokens(instance_id, token_queue))

while True:
    # Drain token queue (non-blocking, no sleep needed)
    while not token_queue.empty():
        token_msg = token_queue.get_nowait()
        yield f"event: token\ndata: {json.dumps(token_msg)}\n\n"

    # DB poll every 1s for log/status/done events
    ...
    await asyncio.sleep(1.0)
```

`_subscribe_tokens` uses `redis.asyncio` (bundled in `redis>=5.0.0` — no new dependency) and terminates cleanly when the asyncio task is cancelled.

### Frontend

| Layer | Change |
|-------|--------|
| `api.ts` | `streamInstance` gains optional `onToken` callback for `event: token` events |
| `workflowStore.ts` | `streamingTokens: Record<string, string>` state; accumulated per `node_id`; cleared on execution start and done |
| `ExecutionPanel.tsx` | `LogEntry` receives `streamingText` prop; running nodes show a pulsing blue dot + live text in expanded view |

### Adding streaming support to a new node type

1. In your handler (`node_handlers.py`), read `instance_id` and `node_id` from context
2. Call `call_llm_streaming(...)` instead of `call_llm(...)`
3. The streaming infrastructure handles Redis publish automatically

For node types that should **not** stream (e.g., LLM Router which needs a deterministic 64-token classification response), continue using `call_llm` directly — `call_llm_streaming` is not called unless `instance_id` and `node_id` are provided.

### SSE terminal statuses (execution stream)

The SSE loop (`app/api/sse.py`) ends with `event: done` when the instance reaches a terminal or wait state, including **`completed`**, **`failed`**, **`suspended`** (HITL), **`cancelled`**, and **`paused`** (operator). The client then refreshes instance detail; for **`paused`**, the operator can call **`POST …/resume-paused`** and open a **new** SSE stream after the worker sets status back to **`running`**.

---

## 🔁 22. Loop Node — Controlled Agentic Cycles (V0.9.9)

The **Loop** node repeats its body (directly-connected downstream nodes) while a condition holds, up to a hard cap of 25 iterations. It is the controlled-cycle complement to ForEach: ForEach iterates over a known array; Loop iterates until a condition changes.

### Typical use cases

- **Quality gate**: call an LLM, score the output, loop until score > threshold.
- **Retry with backoff**: attempt an HTTP call, loop on failure up to N times.
- **Agentic refinement**: generate → critique → refine, repeated until satisfied.

### Architecture

```
node_handlers._handle_loop()          ← evaluates config; returns metadata
dag_runner._run_loop_iterations()     ← drives body node re-execution
```

`_handle_loop` only validates config and returns:
```python
{"continueExpression": "<expr>", "maxIterations": N}
```

`_execute_ready_queue` detects `label == "Loop"` after single-node execution and calls `_run_loop_iterations`, which:

1. Evaluates `continueExpression` via `safe_eval` (pre-check — False = don't enter)
2. Clears body node context keys from previous iteration
3. Sets `context["_loop_index"]` (0-based) and `context["_loop_iteration"]` (1-based)
4. Calls `_execute_single_node` for each body node
5. Appends per-node output to `all_iteration_results`
6. Repeats from step 1

After the loop ends (condition False or `maxIterations` reached), stores:

```python
context[body_node_id] = {
    "loop_results": [<iter-0-output>, <iter-1-output>, ...],
    "iterations": N,
}
```

### Expression context

Inside `continueExpression`, all of the following are available:

| Variable | Meaning |
|----------|---------|
| `_loop_index` | Current iteration number (0-based) |
| `_loop_iteration` | Current iteration number (1-based) |
| `node_X.field` | Output from any upstream node (updated after each body iteration) |
| `trigger.*` | Trigger payload |

Example: `node_3.score < 0.9 and _loop_index < 5`

### File changes

| File | Change |
|------|--------|
| `backend/app/engine/node_handlers.py` | `_handle_loop()` + dispatch (`label == "Loop"`) |
| `backend/app/engine/dag_runner.py` | `_run_loop_iterations()` + wired in `_execute_ready_queue` |
| `shared/node_registry.json` | `loop` type, `logic` category, `continueExpression` + `maxIterations` schema |
| `frontend/src/components/nodes/AgenticNode.tsx` | `RefreshCw` icon (`"refresh-cw"` key); `≤N×` badge; `⟳ expr` preview line |
| `frontend/src/lib/validateWorkflow.ts` | `"Loop": ["continueExpression"]` in `REQUIRED_FIELDS`; `maxIterations > 25` warning |

### Validation

- Missing `continueExpression` → **error** (blocks execution)
- `maxIterations > 25` → **warning** (execution allowed; backend silently caps at 25)

### Adding a new "Loop-aware" node type

Nodes run inside a Loop body behave identically to any other node — they read from context and write their output back. No special handling is needed. Inside their `systemPrompt` or `condition`, use `{{ _loop_index }}` (Jinja2) or `_loop_index` (safe_eval expressions) to reference the current iteration.

---

## 🌉 23. Bridge User Reply + Canvas `displayName` (V0.9.10)

When an external caller completes a DAG run synchronously, it often needs a single user-facing string. The **Bridge User Reply** action node (`bridge_user_reply` in `shared/node_registry.json`) sets `orchestrator_user_reply` in the node output; `dag_runner._promote_orchestrator_user_reply()` copies it to **context root** so `GET …/instances/{id}/context` exposes it for the gateway.

**Files to read:**
- `backend/app/engine/node_handlers.py` — `_handle_bridge_user_reply`
- `backend/app/engine/dag_runner.py` — `_promote_orchestrator_user_reply`
- `examples/python_client.py` — minimal execute + poll pattern for external callers
- Frontend: `displayName` on node data (`types/nodes.ts`, `AgenticNode.tsx`, `nodeCanvasTitle()`) — registry **`label`** remains the engine key; **`displayName`** is UI-only for canvas titles and validation messages

Add Bridge nodes on **each terminal branch** when multiple LLM paths exist so chat text is explicit.

---

## 🎛️ 24. Operator Pause, Cancel, and Resume (V0.9.11)

These controls are **cooperative**: the runner observes flags **between nodes** (after the current node’s handler returns). There is **no** mid-token cancellation inside an LLM call.

### How it differs from HITL (§4)

| | Operator (this section) | HITL approval (§4) |
|--|-------------------------|---------------------|
| **Status** | `paused` or `cancelled` | `suspended` |
| **Resume API** | `POST …/resume-paused` + optional `context_patch` | `POST …/callback` + `approval_payload` / `context_patch` |
| **Trigger** | User clicks Pause / Stop in Execution panel | Node has `approvalMessage` in config |

### Database (run `alembic upgrade head`)

| Column | Migration | Meaning |
|--------|-----------|---------|
| `cancel_requested` | `0005_workflow_cancel_requested.py` | Set by `POST …/cancel` while instance is `queued` or `running` |
| `pause_requested` | `0006_workflow_pause_requested.py` | Set by `POST …/pause` while instance is `queued` or `running` |

### Backend implementation

- **`dag_runner.py`:** `_finalize_cancelled`, `_finalize_paused`, and `_abort_if_cancel_or_pause` (single refresh — **cancel is checked before pause**).
- **Early exit:** If the worker sees `cancel_requested` or `pause_requested` immediately after setting `running`, it finalizes without executing the graph.
- **Resume:** `resume_paused_graph(db, instance_id, context_patch=None)` — loads `context_json`, pops stale `_trace`, re-injects `_instance_id`, rebuilds skipped set from context keys, calls `_execute_ready_queue`. Task: `resume_paused_workflow_task` in `workers/tasks.py`.
- **Abandon paused run:** `POST …/cancel` when status is **`paused`** sets **`cancelled`** synchronously (no Celery round-trip).

### REST endpoints (prefix `/api/v1/workflows`)

| Method | Path | Notes |
|--------|------|------|
| `POST` | `/{workflow_id}/instances/{instance_id}/pause` | Sets `pause_requested` |
| `POST` | `/{workflow_id}/instances/{instance_id}/resume-paused` | Body: `ResumePausedRequest` — optional `context_patch` |
| `POST` | `/{workflow_id}/instances/{instance_id}/cancel` | Queued/running: `cancel_requested`; **paused**: immediate `cancelled` |

### Frontend

- **`frontend/src/lib/api.ts`:** `pauseInstance`, `resumePausedInstance`, `cancelInstance`
- **`frontend/src/store/workflowStore.ts`:** same three actions; `resumePausedInstance` re-attaches SSE via `streamInstance`
- **`frontend/src/components/toolbar/ExecutionPanel.tsx`:** **Pause** / **Stop** while `queued` or `running`; **Resume** + **Stop** (discard) when `paused`

### Example external client

**`examples/python_client.py`:** minimal execute + poll example. Custom clients can call the same pause/cancel/resume/context endpoints directly.

### Further reading

- `TECHNICAL_BLUEPRINT.md` §4.5 (API table), §5.2 (`WorkflowInstance` columns), §6.11 (full semantics)
- `HOW_IT_WORKS.md` — Step 6 (Execution), Pause / Stop / Resume subsection

---

## 🔗 25. A2A Protocol — Agent-to-Agent Communication (V0.9.12)

The orchestrator now speaks the **Google A2A protocol v0.2**, letting external agents discover and invoke your published workflows — and letting DAG nodes call out to remote A2A-capable agents.

---

### 25.1 What A2A Gives You

| Before (V0.9.11) | After (V0.9.12) |
|---|---|
| External agents call raw `POST /execute` (fire-and-forget) | Structured `tasks/send` → poll `tasks/get` → receive `completed` with artifacts |
| No way for the caller to know if the workflow is waiting on a human | `suspended` maps to `input-required` — the caller knows to wait |
| Cross-DAG calls require hand-wired HTTP Request nodes | **A2A Agent Call** node handles discovery, auth, polling, and result injection |
| No discovery — caller must know the workflow UUID | `GET /.well-known/agent.json` lists all published workflows as named skills |

---

### 25.2 Inbound A2A — Making Your Workflow Callable

**Step 1: Publish the workflow**

```
PATCH /api/v1/workflows/{workflow_id}/publish
Body: {"is_published": true}
```

This flips `WorkflowDefinition.is_published = True`. The workflow now appears as a **skill** in the tenant's agent card.

**Step 2: Issue an API key to the external agent**

```
POST /api/v1/a2a/keys
Body: {"label": "teams-bot"}

← {"id": "...", "label": "teams-bot", "raw_key": "abc123...", "created_at": "..."}
```

The `raw_key` is shown **exactly once** and never stored. Hand it to the external agent. It lives in their vault as `Bearer abc123...` on all A2A requests to this tenant.

**Step 3: External agent discovers the tenant's skills**

```
GET /tenants/{tenant_id}/.well-known/agent.json
(no auth required — this is a public discovery endpoint)
```

Returns:
```json
{
  "name": "AE Orchestrator — acme",
  "url": "https://orch.example.com/tenants/acme/a2a",
  "capabilities": {"streaming": true},
  "skills": [
    {"id": "wf-uuid-1", "name": "Server Diagnostics", "description": "..."}
  ]
}
```

**Step 4: External agent sends a task**

```
POST /tenants/{tenant_id}/a2a
Authorization: Bearer abc123...
Content-Type: application/json

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tasks/send",
  "params": {
    "skillId": "wf-uuid-1",
    "sessionId": "thread-abc",
    "message": {"role": "user", "parts": [{"text": "Server us-east-1 is down"}]}
  }
}
```

Returns immediately with a `Task` object (`state: submitted`). The external agent then polls `tasks/get` or uses `tasks/sendSubscribe` for streaming updates.

**Status mapping** — `WorkflowInstance.status` → A2A `state`:

| Instance status | A2A state | Notes |
|---|---|---|
| `queued` | `submitted` | |
| `running` | `working` | |
| `completed` | `completed` | Final response in `artifacts[0]` |
| `suspended` | `input-required` | Human Approval node waiting — approval message surfaced in `status.message` |
| `failed` | `failed` | |
| `cancelled` | `canceled` | |

---

### 25.3 Outbound A2A — Calling Remote Agents from a DAG

Drop an **A2A Agent Call** node onto the canvas.

| Config field | What to put | Example |
|---|---|---|
| `agentCardUrl` | Full URL to the remote agent's discovery doc | `https://other.example.com/tenants/acme/.well-known/agent.json` |
| `skillId` | Skill ID from the agent card (leave blank for first skill) | `wf-uuid-1` |
| `messageExpression` | safe_eval expression for the message text | `node_2.response` or `trigger.message` |
| `apiKeySecret` | Vault reference to the remote agent's A2A key | `{{ env.REMOTE_AGENT_KEY }}` |
| `timeoutSeconds` | Max seconds to wait for the task to complete | `300` |

The node returns:

```json
{
  "task_id": "...",
  "state": "completed",
  "response": "The server was restarted successfully.",
  "skill_id": "wf-uuid-1",
  "agent": "AE Orchestrator — partner-org",
  "task": { ... full A2A Task object ... }
}
```

Downstream nodes access the response via `node_X.response` in Condition or systemPrompt fields.

> **Error handling:** If the remote agent is unreachable, the task times out, or it returns `failed`, the node returns `{"state": "failed"|"timeout", "error": "..."}`. Wire a Condition node on `node_X.state == "completed"` to handle the failure path.

---

### 25.4 Key Management

```
# Create — raw key shown once, store in the external agent's vault
POST /api/v1/a2a/keys           {"label": "teams-bot"}

# List — safe summary, no key material
GET  /api/v1/a2a/keys

# Revoke — external agents using this key get 401 immediately
DELETE /api/v1/a2a/keys/{key_id}
```

Keys use the existing `get_tenant_id` auth dependency — your normal API credentials manage them. Only the SHA-256 hash is stored in `a2a_api_keys`.

---

### 25.5 Streaming — `tasks/sendSubscribe`

For real-time updates, external agents can use `tasks/sendSubscribe` instead of `tasks/send`. It creates the instance and immediately starts an SSE stream of A2A-formatted events:

```
event: task
data: {"id": "...", "status": {"state": "submitted", ...}}

event: task
data: {"id": "...", "status": {"state": "working", ...}}

event: task
data: {"id": "...", "status": {"state": "completed", ...}}

event: artifact
data: {"id": "...", "artifact": {"parts": [{"text": "Final answer here"}]}}
```

The stream closes automatically when the task reaches a terminal state.

---

### 25.6 Files Added / Changed

| File | Change |
|---|---|
| `backend/app/api/a2a.py` | New — agent card, JSON-RPC dispatcher, key CRUD, publish endpoint |
| `backend/app/engine/a2a_client.py` | New — `fetch_agent_card`, `send_task`, `poll_until_done`, `extract_response_text` |
| `backend/alembic/versions/0007_a2a_support.py` | New — `is_published` column + `a2a_api_keys` table |
| `backend/app/models/workflow.py` | `is_published` on `WorkflowDefinition` + `A2AApiKey` ORM model |
| `backend/app/engine/node_handlers.py` | `_handle_a2a_call` + dispatch line |
| `backend/app/api/schemas.py` | A2A Pydantic models + `WorkflowPublishRequest` |
| `shared/node_registry.json` | `a2a_call` action node |
| `backend/main.py` | `a2a_router` registered, version → `0.9.2` |

**Run migration:** `alembic upgrade head`

**Add expression autocomplete for the new node** — open `frontend/src/lib/expressionVariables.ts` and add:

```ts
"A2A Agent Call": ["task_id", "state", "response", "agent", "skill_id"],
```

---

## 🎯 26. NLP Nodes — Intent Classifier and Entity Extractor (V0.9.14)

Two dedicated NLP nodes provide structured text understanding as native, configurable workflow steps — ported from the IntentEdge service.

### 26.1 Intent Classifier

**File:** `backend/app/engine/intent_classifier.py`

The Intent Classifier combines three scoring strategies:

| Mode | Strategy | When to use |
|------|----------|-------------|
| `heuristic_only` | Lexical substring + embedding cosine similarity | Zero LLM cost; good for well-defined intents with clear examples |
| `hybrid` (default) | Heuristic first; LLM fallback if confidence < threshold | Best accuracy-to-cost ratio |
| `llm_only` | Send all intents + conversation history to LLM | Maximum accuracy; highest cost |

**Heuristic scoring:**
1. Lexical: +2.0 if the intent name appears as a substring in the utterance, +1.0 per matching example
2. Embedding: `max(0, cosine(utterance_vec, intent_vec)) × 4.0`
3. Confidence: `min(0.95, 0.5 + best_score × 0.1)`

**Embedding cache (`cacheEmbeddings`):**

When `cacheEmbeddings=true`, saving the workflow triggers `precompute_node_embeddings()` in `embedding_cache_helper.py`. This:
1. Iterates nodes with `label == "Intent Classifier"` and `cacheEmbeddings=true`
2. Builds embedding text from intent `name + description + examples`
3. Calls `get_or_embed()` which checks the `embedding_cache` table by SHA-256 content hash
4. Embeds missing texts in batch and upserts them

At runtime, the handler reads cached vectors from the DB instead of recomputing. When `cacheEmbeddings=false` (default), embeddings are computed on-the-fly using `embed_batch_transient()` — no DB interaction.

**LLM classification:** Builds a structured prompt with available intents and conversation history, requests strict JSON (`{"intents": [...], "confidence": 0.0-1.0}`), validates returned intent names against the configured set, and falls back to `"fallback_intent"` if parsing fails.

### 26.2 Entity Extractor

**File:** `backend/app/engine/entity_extractor.py`

Rule-based entity extraction supporting five types:

| Type | Strategy | Needs |
|------|----------|-------|
| `regex` | `re.search(pattern, text, IGNORECASE)` — returns group(1) or group(0) | `pattern` in config |
| `enum` | Word-boundary match (`\b{value}\b`) for each configured enum value | `enum_values` list |
| `number` | First integer or decimal in text | — |
| `date` | First `YYYY-MM-DD` match | — |
| `free_text` | `entity_name: value` pattern | — |

**Intent-entity scoping:** When `scopeFromNode` references an upstream Intent Classifier, the handler reads `intents` from its output. If `intentEntityMapping` maps any matched intent to specific entity names, only those entities are extracted. Unmapped intents pass all entities through.

**LLM fallback:** When `llmFallback=true` and required entities are missing after rule-based extraction, the handler sends a structured prompt to the LLM requesting only the missing entity names. Responses are validated and merged into the extraction result.

### 26.3 Frontend custom editors

**File:** `frontend/src/components/sidebar/DynamicConfigForm.tsx`

Two custom React components handle the complex array-of-objects configuration:

- **`IntentListEditor`** — renders one card per intent with fields for name (required), description, examples (comma-separated input), and priority
- **`EntityListEditor`** — renders one card per entity with name (required), type dropdown, conditional pattern/enum_values fields (shown only for `regex`/`enum` types), description, and required checkbox

Both are wired into `DynamicConfigForm` before the generic array/JSON fallback, matching on `nodeType === "intent_classifier"` / `"entity_extractor"` and `key === "intents"` / `"entities"`.

### 26.4 Validation

**Server-side** (`config_validator.py`):
- Intent Classifier: non-empty `intents` array, each intent must have a non-empty `name`
- Entity Extractor: non-empty `entities` array, each entity must have a non-empty `name`; `regex` type requires `pattern`; `enum` type requires non-empty `enum_values`

**Client-side** (`validateWorkflow.ts`):
- Same rules as server-side — intents/entities array must have ≥1 entry, each with a `name`
- `historyNodeId` and `scopeFromNode` are cross-validated against existing canvas node IDs

### 26.5 Files added / changed

| File | Change |
|---|---|
| `backend/app/models/embedding_cache.py` | New — `EmbeddingCache` SQLAlchemy model |
| `backend/alembic/versions/0010_add_embedding_cache.py` | New — migration with pgvector VECTOR column, HNSW index, RLS |
| `backend/app/models/__init__.py` | Added `EmbeddingCache` export |
| `backend/app/engine/embedding_cache_helper.py` | New — `get_or_embed()`, `embed_batch_transient()`, `precompute_node_embeddings()` |
| `backend/app/engine/intent_classifier.py` | New — `_handle_intent_classifier()`, `_llm_classify()`, scoring logic |
| `backend/app/engine/entity_extractor.py` | New — `_handle_entity_extractor()`, `_llm_extract()`, rule-based extraction |
| `backend/app/engine/node_handlers.py` | Dispatch lines for Intent Classifier and Entity Extractor |
| `backend/app/engine/config_validator.py` | `_validate_intent_classifier()`, `_validate_entity_extractor()` |
| `backend/app/api/workflows.py` | `precompute_node_embeddings()` call in create/update |
| `shared/node_registry.json` | `nlp` category + `intent_classifier` and `entity_extractor` entries |
| `frontend/src/types/nodes.ts` | `"nlp"` added to `NodeCategory` |
| `frontend/src/components/sidebar/DynamicConfigForm.tsx` | `IntentListEditor`, `EntityListEditor`, boolean `visibleWhen` support |
| `frontend/src/components/sidebar/NodePalette.tsx` | `Target`, `ListFilter` icons; `nlp` category in palette |
| `frontend/src/components/nodes/AgenticNode.tsx` | `Target`, `ListFilter` icons; `nlp` category styles |
| `frontend/src/lib/expressionVariables.ts` | Output fields for both nodes |
| `frontend/src/lib/validateWorkflow.ts` | Validation rules for both nodes |

**Run migration:** `alembic upgrade head`
