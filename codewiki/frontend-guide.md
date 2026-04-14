# Frontend Guide

The frontend is a React 19 single-page application built with Vite, React Flow, Zustand, Tailwind CSS, and shadcn/ui components.

---

## Tech stack

| Library | Purpose |
|---------|---------|
| React 19 | UI framework |
| Vite | Build tool and dev server |
| @xyflow/react (React Flow) | DAG canvas with drag-and-drop |
| Zustand | State management (two stores) |
| Tailwind CSS | Utility-first styling |
| shadcn/ui | Pre-built UI primitives |
| Lucide React | Icon library |

---

## App layout

`App.tsx` renders a **single-screen** layout with no router. When `VITE_AUTH_MODE=oidc` and no token is stored, only the `LoginPage` is shown.

```
┌──────────────────────────────────────────────────────┐
│                    Toolbar                             │
├──────────────────────────────────────────────────────┤
│               WorkflowBanner (errors/notices)         │
├────────┬──────────────────────────────┬──────────────┤
│  Node  │                              │  Property    │
│ Palette│       FlowCanvas             │  Inspector   │
│ (left) │   ┌──────────────────────┐   │  (right)     │
│        │   │  ExecutionPanel      │   │              │
│        │   │  (bottom overlay)    │   │              │
│        │   └──────────────────────┘   │              │
└────────┴──────────────────────────────┴──────────────┘
```

---

## Component directory

### `components/auth/`

| File | Description |
|------|-------------|
| `LoginPage.tsx` | OIDC login page, shown when `VITE_AUTH_MODE=oidc` and no access token |

### `components/banner/`

| File | Description |
|------|-------------|
| `WorkflowBanner.tsx` | Dismissible error (red) or notice (blue) strip, driven by `workflowStore` |

### `components/canvas/`

| File | Description |
|------|-------------|
| `FlowCanvas.tsx` | React Flow graph — renders nodes/edges from `flowStore`, handles drag-drop from palette, undo/redo keyboard shortcuts, marks store dirty on changes |

### `components/nodes/`

| File | Description |
|------|-------------|
| `AgenticNode.tsx` | Custom node component for all node types. Shows category-colored border, icon, label, status dot during execution, validation rings (error/warning), debug replay cursor, and connection handles (including labeled Yes/No handles for Condition nodes) |

### `components/sidebar/`

| File | Description |
|------|-------------|
| `NodePalette.tsx` | Collapsible left panel. Groups nodes by category with color coding. Supports search filtering. Each node item is draggable with `application/reactflow` data transfer |
| `PropertyInspector.tsx` | Right panel. Shows selected node's ID, display name, engine label. Renders `DynamicConfigForm` based on the registry schema. Delete button |
| `DynamicConfigForm.tsx` | Schema-driven form generator. Renders enum selects, JSON editors, tool multi-selects, KB selectors, expression inputs based on field type from `node_registry.json` |
| `ExpressionInput.tsx` | Input field with autocomplete suggestions for expressions, node IDs, and Jinja2 template variables |
| `KBMultiSelect.tsx` | Knowledge base multi-select for the `knowledge_retrieval` node. Fetches available KBs from the API |

### `components/toolbar/`

| File | Description |
|------|-------------|
| `Toolbar.tsx` | Top bar with: undo/redo, inline workflow name editing, version badge, New/Template/Open/KB/History/Save buttons, sync run toggle, Run button, execution status chip |
| `ExecutionPanel.tsx` | Bottom overlay during execution. Shows per-node logs, live streaming tokens, pause/resume/stop controls, HITL resume, debug replay bar |
| `WorkflowListDialog.tsx` | Dialog to open saved workflows |
| `VersionHistoryDialog.tsx` | Dialog to browse and rollback to previous graph versions |
| `InstanceHistoryDialog.tsx` | Dialog to view past execution runs |
| `ValidationDialog.tsx` | Pre-run validation errors with "Run anyway" option |
| `TemplateGalleryDialog.tsx` | Template browser with import/export JSON |
| `KnowledgeBaseDialog.tsx` | KB management — create, edit, delete, upload documents, view status |
| `HITLResumeDialog.tsx` | Human-in-the-loop resume dialog with context display |
| `DebugReplayBar.tsx` | Checkpoint step-through UI for debug mode |

### `components/ui/`

shadcn/ui primitives: `badge`, `button`, `card`, `collapsible`, `dialog`, `dropdown-menu`, `input`, `label`, `scroll-area`, `select`, `separator`, `textarea`, `tooltip`.

---

## Zustand stores

### `useFlowStore` — graph state

Manages the React Flow graph (nodes, edges, selection, undo/redo).

**State:**

| Slice | Type | Description |
|-------|------|-------------|
| `nodes` | `Node[]` | React Flow node objects |
| `edges` | `Edge[]` | React Flow edge objects |
| `selectedNodeId` | `string \| null` | Currently selected node |
| `past` | `Array` | Undo history stack |
| `future` | `Array` | Redo history stack |

**Key actions:**

| Action | Description |
|--------|-------------|
| `onNodesChange` | React Flow node change handler |
| `onEdgesChange` | React Flow edge change handler |
| `onConnect` | Edge creation — Condition nodes get Yes/No labels with green/red styling |
| `addNode` | Create a new node from palette drop data |
| `selectNode` | Set selected node ID |
| `updateNodeData` | Update a node's data (config, label, etc.) |
| `deleteNode` | Remove a node and its edges |
| `replaceGraph` | Load a complete graph (e.g. from saved workflow) |
| `undo` / `redo` | Restore from history stacks |

### `useWorkflowStore` — workflow metadata & execution

Manages the saved workflow, execution state, SSE streaming, and debug replay.

**State:**

| Slice | Type | Description |
|-------|------|-------------|
| `currentWorkflow` | `WorkflowOut \| null` | Currently loaded workflow |
| `workflows` | `WorkflowOut[]` | List of tenant's workflows |
| `instances` | `InstanceOut[]` | Execution runs for current workflow |
| `isDirty` | `boolean` | Unsaved changes flag |
| `activeInstance` | `InstanceOut \| null` | Currently tracked execution |
| `isExecuting` | `boolean` | True while SSE stream is active |
| `instanceContext` | `object \| null` | Fetched execution context |
| `streamingTokens` | `Record<string, string>` | Live LLM token buffer per node |
| `runSync` | `boolean` | Sync execution mode toggle |
| `error` | `string \| null` | Error message for banner |
| `notice` | `string \| null` | Info message for banner |

**Debug replay state:**

| Slice | Description |
|-------|-------------|
| `isDebugMode` | Whether debug replay is active |
| `debugCheckpoints` | Checkpoint list for the instance |
| `activeCheckpointIdx` | Current checkpoint index |
| `activeCheckpointDetail` | Checkpoint context data |

**Key actions:**

| Action | Description |
|--------|-------------|
| `fetchWorkflows` | Load all workflows from API |
| `loadWorkflow` | Load a specific workflow and set canvas |
| `saveWorkflow` | Create or update workflow via API |
| `deleteWorkflow` | Delete workflow via API |
| `newWorkflow` | Reset to blank canvas |
| `executeWorkflow` | POST execute, then stream or poll |
| `streamInstance` | Open SSE stream — merges logs, tokens, status |
| `cancelInstance` / `pauseInstance` / `resumePausedInstance` | Instance control |
| `markDirty` | Flag unsaved changes |
| `enterDebugMode` / `exitDebugMode` | Debug replay |

---

## Node palette → canvas → config form flow

1. **Registry loading**: `lib/registry.ts` imports `shared/node_registry.json` and exports:
   - `REGISTRY_PALETTE` — palette items with `nodeCategory`, `label`, `description`, `icon`, `defaultConfig`
   - `getConfigSchema(label)` — looks up config schema by display label
   - `getRegistryNodeType(label)` — looks up node type string by display label
   - `schemaToDefaultConfig(schema)` — builds default config from schema

2. **Drag from palette**: `NodePalette` renders draggable items from `REGISTRY_PALETTE`, grouped by category. On drag start, `dataTransfer` is set with MIME type `application/reactflow` containing `{ nodeCategory, label, defaultConfig }`.

3. **Drop on canvas**: `FlowCanvas.onDrop` parses the transfer data, converts screen coordinates to flow position via `screenToFlowPosition`, and calls `flowStore.addNode` with the category, label, and default config.

4. **Select node**: Clicking a node sets `selectedNodeId` in `flowStore`. `PropertyInspector` reads the selected node and renders `DynamicConfigForm`.

5. **Config form**: `DynamicConfigForm` receives the `nodeType` (from `getRegistryNodeType`) and config schema (from `getConfigSchema`). It generates form fields based on schema types:
   - `enum` → Select dropdown
   - `string` → Text input (or `ExpressionInput` for expression fields)
   - `number` / `integer` → Number input with min/max
   - `boolean` → Checkbox
   - `object` → JSON editor
   - `array` → Multi-select (tools, KBs)
   - Special handling for `react_agent` tools, `mcp_tool` toolName, and `knowledge_retrieval` KBs

6. **Config updates**: Changes call `flowStore.updateNodeData`, which also calls `workflowStore.markDirty`.

---

## Execution and SSE streaming

1. **Run button**: Toolbar calls `workflowStore.executeWorkflow`, which POSTs to `/execute`. If sync mode, the response is shown directly. Otherwise, `streamInstance` opens an SSE connection.

2. **SSE events**:
   - `log` — Updates the execution log list in `ExecutionPanel`
   - `status` — Updates instance status and `current_node_id` (highlights active node on canvas)
   - `token` — Appends to `streamingTokens[node_id]` for live LLM output display
   - `done` — Clears streaming state, refetches instance detail

3. **ExecutionPanel**: Bottom overlay shows a scrollable log list. Each log entry shows node ID, status, timing. Running nodes display a "Generating..." block with live token text from `streamingTokens`. Completed nodes show input/output JSON.

4. **Node highlighting**: `AgenticNode` reads the active instance to show a status dot (green for running, check for completed, red for failed) on the currently executing node.

---

## Validation

- **Pre-run**: `validateWorkflow` checks for disconnected nodes, missing required config, invalid expressions, etc.
- **Per-node**: `useNodeValidation` hook on `AgenticNode` shows colored rings (red for errors, yellow for warnings).
- **ValidationDialog**: Lists all issues before execution with an option to "Run anyway".

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VITE_API_URL` | `http://localhost:8000` | Backend API base URL |
| `VITE_TENANT_ID` | `default` | Tenant ID for dev mode |
| `VITE_AUTH_MODE` | `dev` | `dev` (header-based) or `oidc` |
| `VITE_OIDC_AUTHORITY` | — | OIDC provider URL |
| `VITE_OIDC_CLIENT_ID` | — | OIDC client ID |

---

## Key files

| File | Purpose |
|------|---------|
| `src/App.tsx` | App shell layout |
| `src/main.tsx` | React DOM entry point |
| `src/index.css` | Tailwind imports and global styles |
| `src/types/nodes.ts` | TypeScript types for nodes, categories, palette items |
| `src/lib/api.ts` | Typed API client with all endpoint methods |
| `src/lib/registry.ts` | Bridge between `node_registry.json` and UI components |
| `src/store/flowStore.ts` | Graph state (nodes, edges, selection, undo) |
| `src/store/workflowStore.ts` | Workflow metadata, execution, SSE, debug |
