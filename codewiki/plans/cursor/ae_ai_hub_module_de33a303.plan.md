---
name: AE AI Hub Module
overview: Create a new feature branch from `main` and scaffold the AE AI Hub add-on module -- a React Flow visual orchestrator frontend and a FastAPI-based DAG execution backend, structured as a sidecar alongside the existing `AEAgenticSupport` codebase.
todos:
  - id: create-branch
    content: Create `feature/ae-ai-hub-orchestrator` branch from `main`
    status: completed
  - id: scaffold-frontend
    content: Initialize Vite + React + TS project in `orchestrator/frontend/` with Tailwind, shadcn/ui, @xyflow/react, and Zustand
    status: completed
  - id: build-layout
    content: "Build three-panel layout: NodePalette (left), FlowCanvas (center), PropertyInspector (right)"
    status: completed
  - id: zustand-store
    content: Create Zustand flowStore managing nodes, edges, selectedNode, and all CRUD operations
    status: completed
  - id: custom-nodes
    content: Build polymorphic AgenticNode component with category-based rendering (Trigger, Agent, Action, Logic)
    status: completed
  - id: drag-drop
    content: Implement drag-and-drop from sidebar palette onto React Flow canvas
    status: completed
  - id: scaffold-backend
    content: Initialize FastAPI project in `orchestrator/backend/` with SQLAlchemy, Alembic, and Celery stubs
    status: completed
  - id: data-models
    content: Create WorkflowDefinition, WorkflowInstance, ExecutionLog, and TenantToolOverride SQLAlchemy models with tenant_id
    status: completed
  - id: dag-engine
    content: Implement DAG parser (adjacency list from React Flow JSON), topological sort, and sequential node executor with suspend/resume
    status: completed
  - id: api-endpoints
    content: Build FastAPI CRUD and execution endpoints for workflows, plus MCP tool bridge endpoint
    status: completed
isProject: false
---

# AE AI Hub (Agentic Orchestrator) -- Add-on Module Development

## Branch Strategy

Create branch `**feature/ae-ai-hub-orchestrator**` from `main`. The current branch is `feature/langfuse-observability` (clean, up to date). We will switch to `main`, pull latest, then create the new branch.

```
git checkout main && git pull origin main
git checkout -b feature/ae-ai-hub-orchestrator
```

## Directory Structure (New Module)

All new code lives under a top-level `orchestrator/` directory to cleanly separate from the existing agent codebase:

```
orchestrator/
  frontend/               # React app (Vite + TypeScript)
    src/
      components/
        canvas/           # React Flow canvas wrapper
        sidebar/          # Left palette + Right inspector
        nodes/            # Custom AgenticNode, TriggerNode, etc.
      store/              # Zustand stores (nodes, edges, selectedNode)
      types/              # TypeScript types for graph, nodes, edges
      App.tsx
      main.tsx
    package.json
    vite.config.ts
    tailwind.config.ts
    tsconfig.json

  backend/                # FastAPI execution engine
    app/
      api/                # REST endpoints (workflow CRUD, execute, callback)
      models/             # SQLAlchemy: WorkflowDefinition, WorkflowInstance, ExecutionLog
      engine/             # DAG parser, topological sort, node executor
      workers/            # Celery task definitions
      security/           # tenant_id RLS, credential vault helpers
    alembic/              # DB migrations
    requirements.txt
    main.py

  shared/                 # Shared schemas (JSON specs for node types)
    node_registry.json
```

---

## Phase 1: UI Shell Scaffolding

### 1a. Initialize React Project

- Scaffold with **Vite + React + TypeScript** inside `orchestrator/frontend/`.
- Install core dependencies: `@xyflow/react`, `zustand`, `tailwindcss`, `@tailwindcss/vite`, and **shadcn/ui** (via `npx shadcn@latest init`).
- Configure Tailwind with shadcn preset and dark mode support.

### 1b. Main Layout (Three-Panel)

- `**App.tsx`** renders a flex layout: `<NodePalette />` | `<FlowCanvas />` | `<PropertyInspector />`.
- **Left Sidebar (`NodePalette`)**: Collapsible panel with categorized node types (Triggers, AI Agents, Logic, Actions). Each item is a draggable element with `onDragStart` setting `dataTransfer` with node type metadata.
- **Center Canvas (`FlowCanvas`)**: Wraps `<ReactFlow>` from `@xyflow/react` with `onDrop` / `onDragOver` handlers. Renders minimap and controls.
- **Right Sidebar (`PropertyInspector`)**: Displays config form for `selectedNode` -- fields vary by node type (LLM model selector for Agent nodes, JSON schema editor for Action nodes).

### 1c. Zustand Store

File: `orchestrator/frontend/src/store/flowStore.ts`

```typescript
interface FlowState {
  nodes: Node[];
  edges: Edge[];
  selectedNodeId: string | null;
  onNodesChange: OnNodesChange;
  onEdgesChange: OnEdgesChange;
  onConnect: OnConnect;
  addNode: (type: string, position: XYPosition) => void;
  selectNode: (id: string | null) => void;
  updateNodeData: (id: string, data: Record<string, unknown>) => void;
}
```

---

## Phase 2: Custom React Flow Nodes

### 2a. Generic `AgenticNode` Component

File: `orchestrator/frontend/src/components/nodes/AgenticNode.tsx`

- A single polymorphic node component that renders differently based on `data.nodeCategory`:
  - **Trigger**: Webhook/schedule icon, single output handle.
  - **Agent**: LLM icon, model badge, input + output handles.
  - **Action**: Tool icon, input + output handles, status indicator.
  - **Logic**: Branch/merge icon, conditional output handles.
- Uses shadcn `Card`, `Badge`, and `Separator` for consistent styling.
- Registers via `nodeTypes={{ agenticNode: AgenticNode }}` on `<ReactFlow>`.

### 2b. Drag-and-Drop from Palette to Canvas

- Palette items set `event.dataTransfer.setData('application/reactflow', JSON.stringify({ nodeCategory, label, defaultConfig }))`.
- Canvas `onDrop` reads the data, calls `flowStore.addNode()` with the drop position (converted via `reactFlowInstance.screenToFlowPosition()`).

---

## Phase 3: Backend DAG Engine and Data Models

### 3a. FastAPI Application

File: `orchestrator/backend/main.py`

- FastAPI app with routers for:
  - `POST /api/v1/workflows` -- save/update workflow graph JSON.
  - `POST /api/v1/workflows/{id}/execute` -- enqueue graph execution.
  - `POST /api/v1/workflows/{id}/callback` -- resume suspended workflow.
  - `GET /api/v1/workflows/{id}/status` -- execution status + logs.
- All endpoints require `tenant_id` from JWT/header.

### 3b. SQLAlchemy Models (replacing hardcoded Django IT models)

File: `orchestrator/backend/app/models/`


| Model                  | Key Columns                                                                                                                              | Notes                         |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------- |
| **WorkflowDefinition** | `id`, `tenant_id`, `name`, `graph_json`, `version`, `created_at`                                                                         | Stores React Flow export JSON |
| **WorkflowInstance**   | `id`, `tenant_id`, `workflow_def_id`, `status` (queued/running/suspended/completed/failed), `context_json`, `started_at`, `completed_at` | One per execution run         |
| **ExecutionLog**       | `id`, `instance_id`, `node_id`, `status`, `input_json`, `output_json`, `error`, `started_at`, `completed_at`                             | Per-node execution trace      |
| **TenantToolOverride** | `id`, `tenant_id`, `tool_name`, `enabled`, `config_json`                                                                                 | MCP tool scoping per tenant   |


All tables include `tenant_id` for RLS. Alembic manages migrations.

### 3c. DAG Execution Engine

File: `orchestrator/backend/app/engine/dag_runner.py`

- **Parse**: Convert React Flow JSON (`nodes[]`, `edges[]`) into an adjacency list.
- **Topological Sort**: Kahn's algorithm to determine execution order.
- **Execute**: Iterate sorted nodes; for each node, resolve input variables from predecessor outputs, call the appropriate handler (LLM call, MCP tool invocation, logic branch), and store output.
- **Suspend/Resume**: If a node type is `human_approval`, serialize `WorkflowInstance.context_json` and set status to `suspended`. Resume via callback endpoint.

### 3d. MCP Tool Bridge

- Reuse existing `mcp_server/tool_specs.py` registry to dynamically populate the frontend node palette.
- New endpoint `GET /api/v1/tools` reads from `tool_specs.py` and returns JSON for the sidebar hydration.
- At execution time, the DAG runner invokes tools via the existing MCP server at `http://localhost:3000`.

---

## Key Architectural Decisions

- **Sidecar pattern**: The orchestrator runs on its own port (e.g., 8080 for frontend dev server, 8001 for backend API) and does NOT modify any existing files in the root `AEAgenticSupport` codebase.
- **MCP reuse**: The existing 106 tools are consumed, not duplicated. The orchestrator is a *client* of the existing MCP server.
- **Incremental migration**: The existing Flask agent server (`agent_server.py`) continues to serve the IT RCA use case. The orchestrator is additive.
- **Tech choices**: Vite over CRA (faster builds), FastAPI over Flask (async-native, OpenAPI docs), SQLAlchemy over Django ORM (decoupled from AI Studio's Django).

