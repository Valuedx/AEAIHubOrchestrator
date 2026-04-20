# Developer Workflow — Testing and Iteration

Sprint 2A bundles the **developer-velocity** features that shorten the edit → run → inspect loop. Each section below covers one capability, what it solves, how to use it from the UI, and the backend surface it sits on. Tickets are referenced as `DV-NN` for cross-linking with the roadmap.

> This guide expands as each DV ticket lands. Today only **DV-01 (data pinning)** is documented; DV-02..DV-07 sections will arrive with their respective commits.

---

## DV-01 — Data pinning

**What it solves**: during workflow iteration every test run re-executes every node end-to-end — expensive when the DAG contains LLM agents, MCP tool calls, or external HTTP services. Pinning a node's output lets subsequent runs short-circuit that node and return the pinned payload without calling the handler.

### Using it from the UI

1. Run the workflow at least once so the node you want to pin has a completed execution log.
2. Click the node on the canvas.
3. In the **Property Inspector** (right panel), scroll to the **Pin last output** section.
4. Click **Pin output**. The button is disabled when no completed log for this node exists on the active instance.
5. A 📌 icon appears on the node's card header. Subsequent runs of the workflow return the pinned payload for that node without invoking the handler. Every downstream node still runs normally, reading the pinned value through the shared context.
6. To re-run the node live, click **Unpin output** in the Property Inspector.

### Where the pin lives

Pins are stored inside the workflow itself (`graph_json.nodes[*].data.pinnedOutput`), which means they:

* survive a save, a version snapshot, a restore, and a duplicate.
* are visible to anyone opening the workflow — the 📌 badge makes this obvious.
* are NOT tied to a specific execution. Clearing the active instance (`X` button on the ExecutionPanel) leaves pins intact.
* do NOT bump the workflow's `version` — toggling pins during iteration doesn't churn `workflow_snapshots`. A regular **Save** creates the next version.

### Backend surface

Two endpoints, both tenant-scoped via the standard `get_tenant_id` + `get_db` pair:

```http
POST   /api/v1/workflows/{workflow_id}/nodes/{node_id}/pin
Body:  { "output": { ... arbitrary dict ... } }
→ 200  WorkflowOut with graph_json.nodes[*].data.pinnedOutput set
→ 404  workflow or node not found

DELETE /api/v1/workflows/{workflow_id}/nodes/{node_id}/pin
→ 200  WorkflowOut with pinnedOutput removed (idempotent — no-op if absent)
→ 404  workflow or node not found
```

### Dispatch short-circuit

`app/engine/node_handlers.py::dispatch_node` is the entry point every node goes through during execution. The first thing it does now is:

```python
pinned = node_data.get("pinnedOutput")
if isinstance(pinned, dict):
    logger.info("Node %r returning pinned output (skipping execution)", ...)
    return {**pinned, "_from_pin": True}
```

So pinning takes effect **without** invoking the handler, resolving `{{ env.* }}` variables, opening a DB session, or touching the LLM/MCP clients. The `_from_pin` key is an underscore-prefixed breadcrumb — it flows through the dispatch return value and lands in `execution_logs.output_json` so operators can see "this output came from a pin". `_get_clean_context` strips underscore-prefixed keys before persisting to `workflow_instances.context_json`, so pinned runs don't leak the flag into the long-term context.

Downstream nodes reading `node_X.somekey` see the same shape whether `node_X` ran live or returned from a pin — that's the whole point.

### Edge cases handled

* Pin value is non-dict (e.g. an accidental string) → silently falls through to live dispatch.
* Pin value is `{}` (empty dict) → treated as a valid pin; node returns `{"_from_pin": true}`.
* Pin on a node that no longer exists in the graph → ignored at dispatch time; pin/unpin endpoints return 404 at mutation time.
* Pin on a ForEach body node — the pin returns the same output for every iteration. If you actually need per-iteration outputs, unpin and run live.

### What pinning does NOT do

* **Not a cache.** It doesn't auto-invalidate when inputs change — operators pin deliberately and unpin deliberately.
* **Not stored per-execution.** The pin lives on the workflow definition, not on a workflow_instance.
* **Doesn't affect downstream nodes.** They still run against the pinned value as if it were a fresh output.

### Related tests

* `backend/tests/test_dispatch_pinned_output.py` — 7 unit tests covering the short-circuit: wholesale key preservation, fallthrough when the pin is a non-dict, empty-dict edge case, env-var resolution skip, downstream context shape invariants.
* `backend/tests/test_pin_endpoints.py` — 8 API tests covering set / clear / overwrite / unknown-node 404 / unknown-workflow 404 / idempotent unpin / non-version-bumping.

---

## (reserved) DV-02 — Test single node

_Planned. Ships in the next commit._ This section will document the `POST /workflows/{id}/nodes/{node_id}/test` endpoint and the **Test node** button in Property Inspector that executes one node against upstream pins + a trigger payload without writing a new workflow instance.

## (reserved) DV-03 — Sticky notes on canvas
## (reserved) DV-04 — Expression helpers library
## (reserved) DV-05 — Duplicate workflow
## (reserved) DV-06 — Hotkey cheatsheet
## (reserved) DV-07 — Active / Inactive toggle
