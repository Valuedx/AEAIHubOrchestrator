---
name: Tier 1 Feature Implementation
overview: "Implement three Tier 1 competitive gap features: Visual Debugging & Replay (step-through checkpoint debugger on canvas), Workflow Templates & Marketplace (importable JSON template gallery), and Webhook Response Sync Return (hold HTTP connection until DAG completes)."
todos:
  - id: templates-data
    content: Create template data model and define 5+ bundled templates in orchestrator/frontend/src/lib/templates/index.ts (migrate existing examples + add new ones)
    status: completed
  - id: templates-gallery-ui
    content: Build TemplateGalleryDialog.tsx with category tabs, search, template cards, and import/export JSON buttons
    status: completed
  - id: templates-toolbar
    content: Replace two example buttons in Toolbar.tsx with single Templates button; add loadTemplate and importGraphJson to workflowStore
    status: completed
  - id: sync-webhook-schema
    content: Add sync and sync_timeout to ExecuteRequest; create SyncExecuteOut response schema in schemas.py
    status: completed
  - id: sync-webhook-endpoint
    content: Implement sync branch in execute_workflow endpoint — run execute_graph directly, bypass Celery, enforce timeout, return 200 with output
    status: completed
  - id: sync-webhook-docs
    content: Document sync webhook usage in SETUP_GUIDE.md with curl examples
    status: completed
  - id: debug-api-types
    content: Add CheckpointOut/CheckpointDetailOut types and listCheckpoints/getCheckpointDetail to frontend api.ts
    status: completed
  - id: debug-store
    content: Add debug replay state (isDebugMode, debugCheckpoints, activeCheckpointIdx, etc.) and actions to workflowStore.ts
    status: completed
  - id: debug-replay-bar
    content: Build DebugReplayBar.tsx with step-through timeline, checkpoint dots, and context JSON viewer
    status: completed
  - id: debug-canvas-overlay
    content: Wire debug mode into AgenticNode.tsx — override status dots based on checkpoint replay position; add Debug button to ExecutionPanel header
    status: completed
isProject: false
---

# Tier 1 Feature Implementation

## Feature 1: Visual Debugging & Replay

The checkpoint infrastructure (v0.9.6) already stores per-node context snapshots in `InstanceCheckpoint`. Backend endpoints exist at `GET .../checkpoints` and `GET .../checkpoints/{id}`. The work is entirely frontend.

### Backend — No changes needed

- [workflows.py](orchestrator/backend/app/api/workflows.py) lines 493-565 already serve checkpoint list and detail endpoints
- [dag_runner.py](orchestrator/backend/app/engine/dag_runner.py) `_save_checkpoint` (line 1088) writes a snapshot after every successful node

### Frontend — API client additions

- Add `CheckpointOut` and `CheckpointDetailOut` types to [api.ts](orchestrator/frontend/src/lib/api.ts)
- Add `listCheckpoints(workflowId, instanceId)` and `getCheckpointDetail(workflowId, instanceId, checkpointId)` methods

### Frontend — Store state ([workflowStore.ts](orchestrator/frontend/src/store/workflowStore.ts))

- New state fields: `debugCheckpoints: CheckpointOut[]`, `activeCheckpointIdx: number | null`, `activeCheckpointDetail: CheckpointDetailOut | null`, `isDebugMode: boolean`
- Actions: `enterDebugMode(workflowId, instanceId)` — fetches checkpoints list; `selectCheckpoint(idx)` — fetches detail and updates node status overlays; `exitDebugMode()`
- When a checkpoint is selected, derive a `debugNodeStatuses: Record<string, "completed" | "active" | "idle">` from the checkpoint's `node_id` and the ordered checkpoint list

### Frontend — Canvas integration

- [AgenticNode.tsx](orchestrator/frontend/src/components/nodes/AgenticNode.tsx): read debug overlay status from a new store selector. When `isDebugMode`, override the `STATUS_DOT` with debug-derived status. The "active" checkpoint node gets a distinct pulsing ring (e.g., indigo)
- [flowStore.ts](orchestrator/frontend/src/store/flowStore.ts): no changes needed; node visual state is driven by `data.status` which we'll update via `updateNodeData`

### Frontend — New component: `DebugReplayBar.tsx`

- Rendered inside `ExecutionPanel` when `isDebugMode` is true
- Shows a horizontal timeline of checkpoint nodes (ordered by `saved_at`)
- Step-forward / step-back buttons, plus click-to-jump on any checkpoint dot
- Below the timeline: collapsible JSON viewer showing `activeCheckpointDetail.context_json`
- Button to open the checkpoint's Langfuse trace link (if `_checkpoint_id` is in log output)

### Frontend — Entry point

- Add a "Debug" button (Bug icon) in the [ExecutionPanel.tsx](orchestrator/frontend/src/components/toolbar/ExecutionPanel.tsx) header bar, visible when instance status is terminal (completed/failed/cancelled)
- Clicking it calls `enterDebugMode`, which fetches checkpoints and shows the `DebugReplayBar`

---

## Feature 2: Workflow Templates & Marketplace

`graph_json` is already portable (`{nodes, edges}`). Two hardcoded examples exist in [exampleComplexWorkflow.ts](orchestrator/frontend/src/lib/exampleComplexWorkflow.ts) and [exampleMainAppWorkflow.ts](orchestrator/frontend/src/lib/exampleMainAppWorkflow.ts). The toolbar has two separate buttons for them.

### Template data model

- Create `orchestrator/frontend/src/lib/templates/index.ts` exporting a `WorkflowTemplate[]` array
- Each template: `{ id, name, description, category, tags, nodeCount, graph: {nodes, edges} }`
- Categories: "customer-support", "operations", "research", "getting-started"
- Move existing examples into this array; add 2-3 new templates:
  - **Document Review with HITL** — webhook trigger, LLM summarizer, human approval, save
  - **Multi-Agent Research** — webhook, parallel LLM agents (researcher + critic), merge, final summary
  - **Customer Onboarding** — webhook, condition (new vs returning), personalized welcome agent, save state

### Template gallery UI: `TemplateGalleryDialog.tsx`

- Dialog opened from toolbar, replacing the two separate example buttons
- Category filter tabs across the top
- Grid of template cards with: name, description, tag badges, node count
- "Use Template" button loads the graph onto canvas (same as current `loadExampleComplexWorkflow`)
- "Export Current" button at bottom to download current canvas as JSON
- "Import JSON" button to upload a template file

### Toolbar changes ([Toolbar.tsx](orchestrator/frontend/src/components/toolbar/Toolbar.tsx))

- Replace the `Layers` and `Cpu` icon buttons (lines 188-225) with a single `LayoutTemplate` icon button labeled "Templates"
- Add the template gallery dialog

### Store changes ([workflowStore.ts](orchestrator/frontend/src/store/workflowStore.ts))

- Replace `loadExampleComplexWorkflow` and `loadAutomationEdgeMainWorkflow` with a generic `loadTemplate(templateId: string)` action
- Add `importGraphJson(graph: {nodes, edges})` for file import

---

## Feature 3: Webhook Response (Sync Return)

Current flow: `POST /execute` returns 202 + `InstanceOut`, caller must poll or SSE-stream. The goal is a synchronous mode that holds the connection open and returns the final output.

### Backend — Schema changes ([schemas.py](orchestrator/backend/app/api/schemas.py))

- Add `sync: bool = False` and `sync_timeout: int = 120` fields to `ExecuteRequest`
- New response schema `SyncExecuteOut`:
  - `instance_id`, `status`, `started_at`, `completed_at`, `output: dict` (the final `context_json` with `_` keys stripped)

### Backend — Endpoint changes ([workflows.py](orchestrator/backend/app/api/workflows.py))

- In `execute_workflow` (line 157): branch on `body.sync`
  - **Async (default)**: existing behavior — dispatch to Celery, return 202
  - **Sync**: import and call `execute_graph` directly (bypass Celery `delay`), wrap in a timeout. On completion, return `SyncExecuteOut` with status 200
- Use `asyncio.wait_for` or a simple threading timeout to enforce `sync_timeout`
- The sync path must use its own DB session to avoid blocking the FastAPI thread pool. Use `run_in_threadpool` from Starlette to execute the synchronous `execute_graph` in a thread while keeping the async handler alive

### Backend — Implementation detail

The sync path in `execute_workflow`:

```python
if body.sync:
    from starlette.concurrency import run_in_threadpool
    from app.engine.dag_runner import execute_graph
    
    async def _run_sync():
        db_sync = SessionLocal()
        try:
            execute_graph(db_sync, str(instance.id), body.deterministic_mode)
            db_sync.refresh(instance)
            return instance
        finally:
            db_sync.close()
    
    result = await asyncio.wait_for(
        run_in_threadpool(_run_sync_wrapper, str(instance.id), body.deterministic_mode),
        timeout=body.sync_timeout,
    )
    # Return 200 with full output
```

- The endpoint signature changes from sync `def` to `async def` to support `await` in the sync branch
- For the async branch, wrap the existing Celery dispatch in `run_in_threadpool` to maintain compatibility

### Frontend — Optional UI support

- Add a "Sync Mode" toggle in the execute workflow dialog (if one exists) or as a checkbox before Run
- Lower priority since the primary use case is API-first callers

### API documentation

- Update [SETUP_GUIDE.md](orchestrator/SETUP_GUIDE.md) with sync webhook usage examples:

```
  curl -X POST .../execute \
    -d '{"trigger_payload": {...}, "sync": true, "sync_timeout": 60}'
  

```

  Returns 200 with `{"instance_id": "...", "status": "completed", "output": {...}}`

---

## Execution order

1. **Templates & Marketplace** (lowest risk, no backend changes, unblocks adoption)
2. **Webhook Sync Return** (backend-focused, clean scope, high API-first value)
3. **Visual Debugging & Replay** (most complex frontend work, builds on existing APIs)

