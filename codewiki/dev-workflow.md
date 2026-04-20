# Developer Workflow — Testing and Iteration

Sprint 2A bundles the **developer-velocity** features that shorten the edit → run → inspect loop. Each section below covers one capability, what it solves, how to use it from the UI, and the backend surface it sits on. Tickets are referenced as `DV-NN` for cross-linking with the roadmap.

> This guide expands as each DV ticket lands. Currently documented: **DV-01 (data pinning)**, **DV-02 (test single node)**, **DV-03 (sticky notes)**, **DV-04 (expression helpers)**, **DV-06 (hotkey cheatsheet)**. DV-05, DV-07 sections will arrive with their respective commits.

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

## DV-02 — Test single node

**What it solves**: iterating on one node's config without rerunning the entire workflow. Pairs with DV-01: pin the predecessors once, then probe the node under test with different configs until it does what you want.

### Using it from the UI

1. (Optional) Pin any upstream nodes whose outputs you want to reuse (DV-01).
2. Select the node you want to probe.
3. In the **Property Inspector**, under **Test this node**, click **Test node**.
4. The result appears inline: either a green OK badge + JSON output, or a red Error badge + message. Elapsed time is shown in ms.
5. Edit config, click **Test node** again, repeat until satisfied.

### What the test run does

* Runs only the target node's handler via `dispatch_node`.
* Populates the context with every pinned node's output as `node_X` keys. Upstream nodes without pins are absent from the context — the handler may fail loudly, which is the correct UX (tells you to pin the predecessor first).
* Injects a synthetic `_instance_id` (random UUID), `_current_node_id`, and `_workflow_def_id` so handlers that read these invariants work as expected.
* Uses an empty dict for `trigger` unless a payload is passed (the v1 UI doesn't expose the payload knob; the API does).

### What the test run does NOT do

* **No `workflow_instances` row.** No `execution_logs` row. No checkpoints.
* **No SSE events.** The live-status canvas overlays (FV-01) stay untouched.
* **No RLS bypass.** The handler still runs under the caller's tenant.

One deliberate side effect exists — **`NodeSuspendedAsync`** (AutomationEdge) genuinely creates an `async_jobs` row when submitted, because that's the only way to verify the AE connection actually works. The response surfaces this explicitly:

> `Node suspended on external system 'automationedge' (external_job_id=2968). An async_job row was created as a side effect; this is expected for test runs of AutomationEdge-style nodes.`

The Beat poller will then resume / terminate the orphan row through normal channels; no cleanup needed on the operator side.

### Backend surface

```http
POST /api/v1/workflows/{workflow_id}/nodes/{node_id}/test
Body (optional): { "trigger_payload": { ... } }
→ 200 { "output": { ... }, "elapsed_ms": 123, "error": null }
→ 200 { "output": null,    "elapsed_ms": 87,  "error": "bad config: ..." }
→ 404 workflow or node not found
```

The endpoint always returns 200 when the workflow / node exist. Handler exceptions → 200 + `error` string. 404 is reserved for "the target isn't in the graph at all". This keeps the UI's error-handling code simple: single branch on `error !== null`.

### Error-catching semantics

Every exception the handler raises (except `HTTPException`) is caught and converted to the `error` field. The endpoint logs at `INFO` level so operators' test failures don't pollute `WARNING` dashboards. Stack traces stay server-side; the string surfaced to the UI is just `str(exc)` — good enough for "config is wrong" feedback, not a full debugger.

### Related tests

* `backend/tests/test_test_node_endpoint.py` — 9 tests: happy-path context shape (pins + trigger + synthetic keys), default empty trigger, pin-on-target dispatch short-circuit, handler-raise caught as error, `NodeSuspendedAsync` message format, 404 for unknown workflow / node, context isolation (no pin → key absent), no-side-effect assertion (no `.add()` on the session).

---

## DV-03 — Sticky notes on canvas

**What it solves**: in-situ documentation for non-trivial DAGs. Instead of operators switching to Notion or a Confluence page to describe "why does this branch loop back through the classifier?", they drop a sticky note next to the cluster and keep the context with the workflow.

### Using it from the UI

* **Shift + S** — add a sticky at the current viewport centre.
* Toolbar **sticky** icon (Keyboard shortcut: same) — same thing, for mouse users.
* Click a sticky to edit its text inline. Blur commits the edit.
* Palette icon on the sticky header cycles through six preset colours (yellow → blue → green → pink → purple → grey → yellow …).
* Drag the corners/edges to resize (via React Flow's `NodeResizer`, min 120×80).
* **Delete** / **Backspace** with the sticky selected, or click the trash icon on the header.

### Storage semantics

Sticky notes are ordinary entries in `flowStore.nodes`, discriminated by `Node.type === "stickyNote"`. They ride in `graph_json` alongside agentic nodes, so they survive saves, version snapshots, restores, and workflow duplicates the same way regular nodes do. Their data shape is:

```ts
{ text: string, color: "yellow" | "blue" | "green" | "pink" | "purple" | "grey" }
```

IDs use a `sticky_<timestamp>_<random>` format so they can't collide with the palette's `node_N` counter — and they stand out when grepping `graph_json`.

### Execution semantics

Stickies **never run**. Every place that iterates the graph for execution purposes filters them out:

* `backend/app/engine/dag_runner.py::parse_graph` — only entries with `type == "agenticNode"` are admitted to `nodes_map`; edges touching a sticky are dropped. Legacy workflows whose nodes omit `type` default to `agenticNode`, so no migration is needed.
* `frontend/src/lib/validateWorkflow.ts::validateWorkflow` — strips stickies before the trigger / reachability / required-field checks. A workflow containing only stickies still fails with "no trigger found", as it should.
* `frontend/src/lib/executionStatus.ts::computeNodeStatuses` — excludes stickies from both the initial idle fill and the terminal-run "skipped" sweep. Sticky IDs never appear in the returned status map.
* `frontend/src/components/sidebar/PropertyInspector.tsx` — short-circuits to a small help panel when a sticky is selected (they have no config schema to render).

The live-status overlays on `AgenticNode` and the read-only Flow view (FV-02) both work unchanged — stickies are rendered by their own `stickyNote` type registered in the React Flow `nodeTypes` map alongside `agenticNode`.

### Related tests

* `backend/tests/test_dag_parse.py::TestParseGraphStickyNoteFiltering` — 4 tests: stickies dropped, edges touching stickies dropped, legacy `type`-less nodes default to agenticNode, pure-sticky graph yields empty map.
* `frontend/src/lib/validateWorkflow.test.ts::sticky notes` — 2 tests: unreachable-node check ignores stickies, a sticky alone still fails the trigger check.
* `frontend/src/lib/executionStatus.test.ts::computeNodeStatuses — sticky notes` — 2 tests: stickies omitted from the status map, no "skipped" assignment on terminal runs.

---

---

## DV-04 — Expression helpers library

**What it solves**: Condition / Loop / ForEach / Reflection / Memory / trigger-payload builder all evaluate user-authored expressions through the same `safe_eval` whitelist. Pre-DV-04 that whitelist held 15 functions — enough for comparisons, not enough for real data-shaping. DV-04 adds **45 helpers** covering strings, math, arrays, objects, date/time, and null/JSON utilities so operators rarely need to reach for a separate Code node.

### Where expressions live

The helpers are available anywhere `safe_eval` runs:

* Condition nodes (`condition` field)
* Loop (`continueExpression`)
* ForEach (`arrayExpression`)
* Reflection (`reflectionPrompt` evaluated fragments)
* Sub-Workflow trigger payload builder (`{{ expr }}` bindings)
* HTTP Request / MCP Tool config fields that pass through `safe_eval`
* Intent Classifier + Entity Extractor dynamic scope resolution
* Memory service retrieval expressions

One whitelist, one source of truth — fix a helper once, every consumer gets the fix.

### Registered helpers

Grouped by category. Every helper is pure, deterministic, and has size-caps / type-guards to keep a bad expression from OOM-ing the worker.

**Strings (18)** — `trim`, `replace`, `split`, `join`, `substring`, `left`, `right`, `pad_left`, `pad_right`, `repeat`, `reverse`, `truncate`, `title_case`, `snake_case`, `camel_case`, `regex_replace`, `regex_extract`, `slugify`

**Math (7)** — `round`, `ceil`, `floor`, `sum`, `avg`, `median`, `clamp`

**Arrays (11)** — `first`, `last`, `unique`, `sort_list`, `sort_by`, `flatten`, `chunk`, `slice`, `pluck`, `filter_by_key`, `count_where`

**Objects (4)** — `has_key`, `pick`, `omit`, `get_path`

**Date / time (11)** — `now`, `today`, `parse_date`, `format_date`, `add_days`, `add_hours`, `add_minutes`, `days_between`, `hours_between`, `is_past`, `is_future`

**Utility (7)** — `is_null`, `is_empty`, `default`, `coalesce`, `to_json`, `parse_json`, `safe_number`

Two AST operations were also added to `_BIN_OPS`: `**` (power) and `//` (floor division). Existing operators (`+`, `-`, `*`, `/`, `%`) already worked.

### Example expressions

```python
# Bulk-shape list of dicts into a summary
sum(pluck(orders, "amount")) > 1000

# Date arithmetic without reaching for a Code node
days_between(parse_date(trigger.created_at), now()) > 7

# Safe optional field access with fallback
default(get_path(output.node_1, "user.address.city"), "unknown")

# Slug-friendly workflow IDs
slugify(trigger.title) + "-" + str(trigger.id)

# Null-safe aggregation
avg(pluck(filter_by_key(items, "status", "ok"), "score"))
```

### Safety model

Helpers inherit `safe_eval`'s sandbox — no I/O, no env access, no `__import__`. A few additional guards specific to DV-04:

* `repeat(s, n)` caps `n ≤ 10_000` and total result size ≤ 1 MB
* `chunk(arr, n)` requires `n ≥ 1`
* `parse_json(s)` caps input size ≤ 1 MB
* `clamp(x, lo, hi)` validates `lo ≤ hi`
* `regex_replace` / `regex_extract` catch `re.error` and surface it as `SafeEvalError`
* `ExpressionHelperError` (raised for bad args — empty `avg`, negative `repeat`, unparseable `parse_date`) is translated to `SafeEvalError` at the call site so existing callers keep their single `except SafeEvalError` branch.

Regex catastrophic backtracking is NOT mitigated (Python's `re` module has no built-in timeout). Tenant data is tenant-trusted; if this becomes a vector in the future, move regex into a subprocess with a hard walltime.

### Where to add the next helper

1. Implement in `backend/app/engine/expression_helpers.py`. Raise `ExpressionHelperError` on bad input; never return a wrong-type silent result.
2. Add it to the `EXPRESSION_HELPERS` dict at the bottom of that module.
3. Add a test class or case in `backend/tests/test_safe_eval.py`.

No changes needed in `safe_eval.py` — the merge happens once via `**EXPRESSION_HELPERS`.

### Related tests

* `backend/tests/test_safe_eval.py` — 113 tests total; the DV-04 suite (70 new cases) covers every helper, every size cap, every error path, plus combined-helper expressions mirroring real condition strings.

---

## (reserved) DV-05 — Duplicate workflow

---

## DV-06 — Hotkey cheatsheet

**What it solves**: the canvas has accumulated keyboard shortcuts (undo/redo, delete, pan/zoom, now sticky-add and fit-view). Without a discoverability surface, operators either never learn them or have to read source. The cheatsheet puts every canvas-level shortcut one keystroke away.

### Using it from the UI

* Press **`?`** anywhere on the page, or click the **Keyboard** icon in the toolbar.
* The dialog groups shortcuts by section (Canvas / History / Help).
* Close with **Esc** or the dialog's close button.

Single-key shortcuts (`?`, `S`, `1`, `Tab`) all share a common guard (`frontend/src/lib/keyboardUtils.ts::isTextEditingTarget`) so they never fire while the user is typing in an `input`, `textarea`, `select`, or contenteditable region. Typing "?" into a Property Inspector field is typing — not a help request.

### Registered shortcuts

| Context | Keys | Action |
| ------- | ---- | ------ |
| Canvas  | `Shift` + `S` | Add sticky note at viewport centre (DV-03) |
| Canvas  | `1` | Fit view to whole workflow |
| Canvas  | `Tab` | Toggle node palette |
| Canvas  | `Delete` / `Backspace` | Delete selected node(s) or edge(s) — native React Flow |
| History | `Ctrl` + `Z` | Undo |
| History | `Ctrl` + `Y` / `Ctrl` + `Shift` + `Z` | Redo |
| Help    | `?` | Open the cheatsheet |
| Help    | `Esc` | Close dialogs / deselect — native browser / dialog |

Adding a new shortcut? Register the handler in the component that owns the relevant state (FlowCanvas for canvas shortcuts, App for layout-level toggles, Toolbar for help dialogs) AND add a row to the `SECTIONS` constant in `frontend/src/components/toolbar/HotkeyCheatsheet.tsx` so it shows up in the dialog.

### Where each handler lives

* `App.tsx` — `Tab` (owns `paletteCollapsed`)
* `FlowCanvas.tsx` — `Ctrl+Z`, `Ctrl+Y`, `Ctrl+Shift+Z`, `Shift+S`, `1` (owns the React Flow instance + undo/redo / `addStickyNote`)
* `Toolbar.tsx` — `?` (owns `cheatsheetOpen`) + mounts `HotkeyCheatsheet`

The toolbar "Add sticky" button dispatches a `CustomEvent("aeai:add-sticky")` that `FlowCanvas` listens for, so the canvas — which has the only access to the React Flow instance needed for viewport→flow-coord translation — owns the actual insert logic in one place.

---

## (reserved) DV-07 — Active / Inactive toggle
