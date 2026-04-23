# Cyclic Graphs (LangGraph-style loops)

One-line summary: mark any edge as `type: "loopback"` and the engine re-enqueues the target when the source fires, gated by the source's condition branch and capped by `maxIterations`. The forward subgraph stays a DAG — Kahn's check excludes loopback edges — so execution semantics, logging, and debug tooling work unchanged for zero-loopback graphs.

## 1. Why this shape

Most agentic patterns need cycles:

| Pattern | Cycle shape |
|---|---|
| **Agent ↔ Tool** | `planner → tool → check ─true→ planner` (loop until check says "done") |
| **Reflection** | `writer → critic ─revise→ writer` (regenerate until critic accepts) |
| **Retry-on-failure** | `action → check ─retry→ action` (cap bounded) |

LangGraph supports these via a state-graph runtime with explicit entry/exit nodes. We went with the cheaper alternative: a single edge flag plus a per-edge iteration cap. The forward graph stays acyclic, so cycle detection, `parse_graph`, and every downstream consumer (validator, scheduler, logs, UI) don't need a parallel "is this a cyclic graph?" path.

## 2. Ticket timeline

| Ticket | Status | What it adds |
|---|---|---|
| **CYCLIC-01.a** | **Shipped** | Edge schema `type: "loopback"` + `maxIterations`; `_Edge.kind`/`max_iterations`; `_build_graph_structures` excludes loopbacks from forward adjacency. Zero-loopback graphs bit-identical. |
| **CYCLIC-01.b** | **Shipped** | Runtime: `_fire_loopbacks` after each node completes — evaluate gating, increment counter, clear cycle body from `context`, un-satisfy internal-cycle edges, un-prune exit-branch subtrees, write `loopback_iteration` / `loopback_cap_reached` log rows. Default 10, hard cap 100. |
| **CYCLIC-01.c** | **Shipped** | Save-time errors (invalid `maxIterations`, duplicate loopbacks per source, target-not-ancestor, no-forward-exit) + copilot lints (`loopback_no_exit` error, `loopback_no_cap` warn, `loopback_nested_deep` warn on ≥3 distinct cycles). Pure graph analysis in `app/engine/cyclic_analysis.py`. |
| **CYCLIC-01.d** | **Shipped** | Canvas UX: `LoopbackEdge.tsx` dashed-amber bezier + `↻ ×N` chip; `onConnect` auto-flags drag-to-ancestor; `EdgeInspector.tsx` for tuning `maxIterations`; graph_json round-trip via `serialiseEdgesForSave` / `hydrateEdgesFromLoad`. |
| **CYCLIC-01.e** | **Shipped** | `tests/test_cyclic_e2e_patterns.py` pins agent↔tool loop, reflection, retry, cap-hit patterns end-to-end. This document. |

## 3. Edge schema

A loopback edge looks like a normal React Flow edge plus two fields:

```json
{
  "id": "lb_check_planner",
  "source": "check",
  "sourceHandle": "true",
  "target": "planner",
  "type": "loopback",
  "maxIterations": 5
}
```

- `type: "loopback"` — the single flag that flips runtime semantics. Missing = regular forward edge.
- `sourceHandle` — when the source is a `Condition` node, the loopback only fires when `Condition.output.branch` equals this handle. `"true"`, `"false"`, or any custom branch name.
- `maxIterations` — integer in `[1, 100]`. Missing → default 10 (`LOOPBACK_DEFAULT_MAX_ITERATIONS`). Clamped to 100 (`LOOPBACK_HARD_CAP`) regardless of author-supplied value; defence-in-depth against typos like `999999`.

Authoring path on the canvas (CYCLIC-01.d):

1. **Drag-to-ancestor** — drag a connection from a node back to one of its upstream ancestors; `onConnect` auto-flags the new edge as loopback and seeds `maxIterations=10`.
2. **EdgeInspector** — click any edge to open the right-hand pane. Loopback edges show a `maxIterations` number input (clamped) plus a "Convert to forward edge" button. Forward edges show a "Convert to loopback" button.
3. **Visual differentiation** — loopback edges render as a dashed amber bezier with a `↻ ×N` chip showing the current cap; forward edges keep the default solid grey.

## 4. Runtime semantics

After each node completes, `_execute_ready_queue` calls `_fire_loopbacks(node_id, ...)`. For each loopback edge originating at `node_id`:

1. **Gate** — if the source is a `Condition` node and the edge has a `sourceHandle`, the loopback only fires when `output.branch == sourceHandle`. Non-Condition sources fire unconditionally.
2. **Cap check** — read `context["_cycle_iterations"][edge.id]`. If at or above `maxIterations`, write a `loopback_cap_reached` log row and fall through to forward edges. **Cap-hit is a clean termination, not an error.**
3. **Bump counter** — `context["_cycle_iterations"][edge.id] += 1`. The underscore prefix keeps this counter out of persisted context (`_get_clean_context` strips it).
4. **Log iteration** — write a `loopback_iteration` `ExecutionLog` row on the target node so the debug UI shows per-iteration progress.
5. **Clear cycle body** — compute `cycle_body = forward-descendants(target) ∩ ancestors(source)`. Pop each body node's output from `context` and discard it from `pruned`. The loopback target is included — it's the re-entry point.
6. **Un-prune exit subtrees** — for each forward edge out of a cycle-body node whose target is *not* in the body, walk the target's forward subtree and drop each node from `pruned`. **This is what makes the cycle actually exit cleanly** after iterations that chose the loopback branch: the exit branch was pruned by each prior `_propagate_edges`, and needs to be re-opened so the final iteration's Condition can re-propagate fresh. Also clears stale satisfaction entries from cycle-body sources.
7. **Un-satisfy internal edges** — for each forward edge whose both endpoints are in the cycle body, discard the source from `satisfied[target]`. Cycle nodes now wait for their upstream to re-fire.

Then `_execute_ready_queue` re-scans `_find_ready_nodes` and the target node runs again. Because the loopback target's incoming forward edges from *outside* the cycle are still satisfied (their sources weren't re-executed), the target becomes ready on the next pass.

```
trigger → planner → tool → check ─false→ exit
                    ↑                ╲
                    └─── loopback ────┘ (true branch)
```

On iteration N+1: cycle body `{planner, tool, check}` cleared; `trigger`'s satisfaction of `planner`'s incoming edge stays intact; `planner` is ready again.

## 5. Save-time validation (CYCLIC-01.c)

`validate_graph_configs` blocks save on four classes of error:

| Rule | Error message fragment |
|---|---|
| Invalid `maxIterations` (not int, ≤ 0, > 100) | `'maxIterations' must be an integer`, `must be ≥ 1`, `must be ≤ 100` |
| Duplicate loopbacks from the same source | `already has another loopback` |
| Target not a forward ancestor of source | `not reachable` |
| Cycle body has no forward exit path (LOOPBACK_NO_EXIT) | `no forward exit path` |

Missing `maxIterations` is *not* an error — the runtime fills in 10 — but the copilot's `loopback_no_cap` lint surfaces a warning so authors know they're relying on the default.

## 6. Copilot lints (CYCLIC-01.c / SMART-04)

`app/copilot/lints.py::run_lints` runs these after every mutation:

| Lint code | Severity | Trigger | Why |
|---|---|---|---|
| `loopback_no_exit` | **error** | Cycle body has no forward edge leaving it | The loopback is the only way out — termination is entirely cap-driven, which is almost never what the author meant. |
| `loopback_no_cap` | **warn** | Loopback edge has no explicit `maxIterations` | The default 10 is almost always wrong for the author's intent — warn so they pick intentionally. |
| `loopback_nested_deep` | **warn** | ≥ 3 distinct cycles in the graph (overlap-aware dedup) | Deeply nested cycles are hard to reason about and debug; propose flattening or pulling logic into sub-workflows. |

Zero-loopback graphs pick up no loopback-related lints — regression-tested.

## 7. Debug + observability

Every loopback fire writes two log row types on the loopback target node:

```
{
  "node_id": "<target>",
  "node_type": "loopback",
  "status": "loopback_iteration",
  "output_json": {
    "edge_id": "lb1",
    "source_node_id": "check",
    "iteration": 3,
    "max_iterations": 5
  }
}
```

When the cap is hit:

```
{
  "status": "loopback_cap_reached",
  "output_json": {
    "iterations_used": 5,
    "max_iterations": 5,
    ...
  }
}
```

These show up inline with per-node logs in the debug panel, so the operator sees "check → planner loopback fired iteration 2/5" alongside `planner`'s own completion log. `iterations_used == max_iterations` is the signal that the cap bounded termination.

## 8. Invariants + regression fences

These properties are pinned by `tests/test_cyclic_loopback_execution.py` + `tests/test_cyclic_e2e_patterns.py`:

- **Zero-loopback hot path** — graphs with no loopback edges produce bit-identical execution order and context shape as before CYCLIC-01.b. Regression-fenced by `TestZeroLoopbackHotPath` (linear + diamond).
- **Counter internal** — `_cycle_iterations` has the `_` prefix so `_get_clean_context` strips it; never leaks into persisted `workflow_instances.context_json`.
- **Hard cap supremacy** — author can set `maxIterations=999`; the runtime still caps at 100. Parse-time clamp + `_fire_loopbacks` defence-in-depth.
- **Condition gating** — a loopback with `sourceHandle="true"` does NOT fire when the Condition chose `"false"`. Loopbacks without a handle fire unconditionally regardless of Condition output.
- **Cap-hit → completed** — reaching the cap is not an error; the instance status transitions to `completed`, a `loopback_cap_reached` log row is written, and the forward exit path fires normally.
- **Exit subtree recovery** — after iterations N-1 where the Condition chose the loopback branch (pruning the exit), iteration N choosing the exit branch must actually exit. Un-pruning the exit subtree inside `_fire_loopbacks` is what makes this work.

## 9. What's *not* built

- **Streaming cycles** — the iteration counter bumps as a side-effect of `_fire_loopbacks`; there's no streaming `loopback_iteration` SSE event to the frontend yet. The UI picks up the log row on the next `ExecutionLog` refresh.
- **Nested-scope variable shadowing** — context is flat; `context["planner"]` is overwritten on each iteration. There's no way to ask for "the planner output from iteration 2". If you need that, `append` outputs into a shared `context["history"]` list inside the node's handler.
- **Full state-graph runtime** — we considered a LangGraph-style state-machine with explicit entry/exit nodes (Tier 3 of the original plan); the loopback-edge approach won on simplicity + backwards compatibility. Revisit if loopback edges prove insufficient for a real workload.
- **Push to the copilot** — the copilot can read lint findings via `check_draft`, but there's no explicit "add a cycle here" runner tool. Authors get auto-flagging via `onConnect` instead.

## 10. Related files

| File | Role |
|---|---|
| `backend/app/engine/dag_runner.py` | `_Edge`, `_build_loopback_map`, `_compute_cycle_body`, `_should_fire_loopback`, `_fire_loopbacks`, `_execute_ready_queue` integration |
| `backend/app/engine/cyclic_analysis.py` | Pure graph helpers shared by validator + lints (loopback_edges, cycle_body, has_forward_exit, is_forward_ancestor, deduped_bodies, get_loopback_max_iterations) |
| `backend/app/engine/config_validator.py` | `_validate_loopback_edges` — the four save-time errors |
| `backend/app/copilot/lints.py` | `lint_loopback_no_exit` / `lint_loopback_no_cap` / `lint_loopback_nested_deep` |
| `backend/tests/test_cyclic_loopback_execution.py` | Helper-level unit tests (`_fire_loopbacks` semantics, cap clamping) |
| `backend/tests/test_cyclic_validator_and_lints.py` | Validator + lint coverage, hot-path regression guards |
| `backend/tests/test_cyclic_e2e_patterns.py` | End-to-end pattern tests (agent↔tool, reflection, retry, cap-hit, zero-loopback) |
| `frontend/src/types/edges.ts` | `GraphEdgeKind`, `isLoopbackEdge`, `clampLoopbackMaxIterations`, `serialiseEdgesForSave`, `hydrateEdgesFromLoad` |
| `frontend/src/components/canvas/LoopbackEdge.tsx` | Dashed-amber bezier + `↻ ×N` chip |
| `frontend/src/components/sidebar/EdgeInspector.tsx` | Right-pane `maxIterations` tuner + convert-between-edge-types |
| `frontend/src/store/flowStore.ts` | `onConnect` auto-loopback detection; `selectEdge`/`updateEdge`; graph_json interop wiring |
