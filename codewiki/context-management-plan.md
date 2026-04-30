# Context-Management Improvement Plan

**Status: in flight (kicked off 2026-05-01).** Living document — each item below is updated in place as work progresses. Change log at the bottom.

This plan addresses how `context: dict[str, Any]` flows across nodes, across workflows, and across turns. It exists because LangChain's [State of Agent Engineering 2026](https://eastondev.com/blog/en/posts/ai/20260424-langgraph-agent-architecture/) reports >60% of agent production incidents trace to state management — and our V8/V9/V10 work surfaced concrete instances of every category named in that report. The plan is grounded in [Anthropic's effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) guidance, [LangGraph's reducer/state-channel model](https://callsphere.ai/blog/langgraph-state-management-typeddict-reducers-state-channels), [Temporal's durable-execution principles](https://docs.temporal.io/workflow-execution), and the [practical agent-memory literature](https://towardsdatascience.com/a-practical-guide-to-memory-for-autonomous-llm-agents/).

> **Scope**. Engine-internal context plumbing (`app/engine/dag_runner.py`, `app/engine/memory_service.py`, `app/engine/prompt_template.py`, `app/engine/react_loop.py`) plus a small set of node-config schema additions and one or two new node types. Out of scope: copilot tooling beyond what's needed to lint authoring. Out of scope: replacing memory_service entirely — that's a separate workstream.

---

## §1. Today's model in one paragraph

A single mutable `dict[str, Any]` named `context` flows through the engine. Each node writes its full output to `context[node_id]`; the trigger payload lives at `context["trigger"]`; runtime keys (`_instance_id`, `_parent_chain`, `_loop_*`, `_cycle_iterations`, `_trace`, `hitl_pending_call`) are namespaced with a leading underscore and stripped before persistence to `instance.context_json`. Templates use Jinja2 with a permissive-undefined policy (missing variables render to empty string). Cross-workflow context flows in via `inputMapping → trigger_payload` and out via `output_node_ids`, never sharing the child's full state. Cross-turn context is reconstructed every turn by `assemble_agent_messages` from `ConversationMessage` rows — context_json itself does not survive turn boundaries (each turn is a fresh execution). This is simple, durable, and works well at small scale. The items below address where that simplicity stops scaling.

---

## §2. Priority order + status

The four items at the top are the highest-leverage, smallest-blast-radius changes. Aim to ship them first; the rest are valuable but second-tier.

| # | Item | Priority | Status | Owner | Last touched |
|---|---|---|---|---|---|
| **D** | `_runtime` resume-safe namespace for runtime keys | **P0** | **Shipped** (branch `ctx-mgmt-d-runtime`) | — | 2026-05-01 |
| **A** | Per-node output budget + overflow artifacts | **P0** | **Shipped** (branch `ctx-mgmt-a-overflow`) | — | 2026-05-01 |
| **L** | Reducer-per-channel state model (`outputReducer`) | **P0** | **Shipped** (branch `ctx-mgmt-l-reducers`) | — | 2026-05-01 |
| **K** | Compaction pass within a single workflow run | **P0** | **Shipped** (branch `ctx-mgmt-k-compaction`) | — | 2026-05-01 |
| **B** | `lint_jinja_dangling_reference` static lint | P1 | Not started | — | — |
| **C** | Per-node `dependsOn` / `exposeAs` scope declaration | P1 | Not started | — | — |
| **E** | Native `Coalesce` node + child-evidence promotion channel | P1 | Not started | — | — |
| **G** | ReAct iterations summary vs full split | P1 | Not started | — | — |
| **I** | `outputSchema` on every node (not just trigger) | P2 | Not started | — | — |
| **J** | First-class `distillBlocks` for context distillation | P2 | Not started | — | — |
| **F** | Scrub-secrets at write-time, not just log-time | P2 | Not started | — | — |
| **H** | `context_trace` runtime channel + copilot inspector | P2 | **Shipped** (branch `ctx-mgmt-h-context-trace`) | — | 2026-05-01 |
| **M** | Forgetting / decay (run-end pruning + checkpoint TTL) | P3 | Not started | — | — |

**Status vocabulary.** `Not started` → `In design` → `In progress` → `Shipped` → `Verified` (when post-merge eval / soak confirms the fix). A `Walked back` state exists for items the literature contradicts; we removed one (E.original — see §6).

---

## §3. P0 — ship first

### A. Per-node output budget + overflow artifacts — **Shipped 2026-05-01**

**Gap.** Every node's output stayed in `context[node_id]` for the entire run AND got persisted to DB on every node completion via `_save_checkpoint`. ReAct nodes write `iterations: [{tool_calls, tool_results, content}, ...]` — easily 10–50 kB per ReAct node; a 28-node V10-shape workflow could produce a `context_json` of several hundred kB. LangChain's *State of Agent Engineering 2026* identifies this as the #1 production-failure category in agent systems.

**Edge cases.** ForEach over 100 items writes per-downstream-node lists at peak. Sub-workflow `output_node_ids` merge into parent context. HTTP node response bodies stored verbatim. `InstanceCheckpoint` writes a full snapshot per node — 28 full copies for a 28-node graph.

**What shipped (branch `ctx-mgmt-a-overflow`).**
- New `node_output_artifacts` table (migration `0035`) — RLS-tenant-scoped, FK to `workflow_instances` with cascade delete. Carries `output_json`, `size_bytes`, `budget_bytes`, indexed by `(instance_id, node_id)` for the inspect-tool point lookup and `(tenant_id, created_at)` for retention sweeps.
- New module `app/engine/output_artifact.py` with pure helpers: `estimate_output_size` (bytes, `default=str` for non-JSON), `resolve_budget` (per-node `data.config.contextOutputBudget` with hard ceiling at 256 kB), `should_overflow`, `materialize_overflow_stub`, `persist_artifact`, `maybe_overflow` (the end-to-end one-call helper).
- Stub shape preserves canonical top-level scalar keys (`id`, `status`, `state`, `error`, `branch`, `result`, `code`, `name`, `session_id`, `instance_id`, `request_id`, `agent_id`, `workflow_id`, `case_id`) plus up to 8 other scalar fields and a sorted `top_level_keys` list — common downstream Jinja patterns (`{{ node_X.preview.status }}`, `{% if node_X._overflow %}…{% endif %}`) still resolve.
- `dag_runner._execute_single_node` calls `maybe_overflow(...)` after `dispatch_node` returns, on BOTH the sequential path and the parallel-branch executor (`_apply_result`). On overflow: artifact is INSERTed (with `flush()` for id allocation), the in-context value becomes the stub, the ExecutionLog row records the stub PLUS overflow metadata (`_overflow_artifact_id`, `size_bytes`, `budget_bytes`).
- `_promote_orchestrator_user_reply` still receives the FULL output (not the stub) — Bridge nodes are designed to produce small replies, but if a Bridge somehow exceeded budget the user still sees the original text in the promoted root key.
- New copilot runner tool `inspect_node_artifact(instance_id, node_id)` — same ephemeral-only safety as `get_execution_logs`. Returns `{instance_id, node_id, size_bytes, budget_bytes, output_json, created_at}` or a clean error if the artifact doesn't exist (the node didn't overflow).
- Defaults: `DEFAULT_OUTPUT_BUDGET_BYTES = 64 * 1024`, `HARD_CEILING_BYTES = 256 * 1024`. Per-node override via `data.config.contextOutputBudget`. Zero / negative / non-int values fall through to the default — never silently disable the overflow path.

**What didn't ship (deferred sub-task).** The plan also called for `_save_checkpoint` to write deltas instead of full snapshots. Deferred — overflow artifacts already address the dominant cost (giant per-node outputs); checkpoint deltas are a separate optimisation that can land independently. Tracked as a sub-task in the change log.

**Tests (1057 passed, 1 skipped after this slice — was 1029).**
- New `tests/test_output_artifact.py` (28 tests):
  - `estimate_output_size` — None / small / UUID / unicode bytes / proportional growth.
  - `resolve_budget` — defaults, per-node override, hard-ceiling clamp, zero/negative/non-int fall-through.
  - `should_overflow` — None, small, big, exact-budget boundary.
  - `materialize_overflow_stub` — shape, canonical key preservation, `top_level_keys` cap (30, sorted), long-string truncation, nested-dict shape substitution, non-dict outputs (list, scalar), extra-scalar-keys cap (8).
  - `maybe_overflow` — hot path doesn't touch DB; overflow path persists artifact + returns stub + returns metadata; per-node budget override; hard ceiling kicks in for misconfigured budget.
  - End-to-end Jinja round-trip — `{{ node_X.preview.status }}` renders correctly; `{% if node_X._overflow %}` detects the marker; non-overflow output renders unchanged.

**Eval target (deferred to post-merge).** Re-run the V10 eval suite (28 nodes) with the budget enforced; compare `context_json` sizes pre/post. Target: <80 kB per turn (was 250–400 kB on V10 smoke).

**Refs.** Anthropic — *"keep state lean and store large data externally with references"*. LangGraph 2026 — *"Extremely large state objects will slow down serialization and increase memory usage."*

### D. `_runtime` resume-safe namespace — **Shipped 2026-05-01**

**Gap.** On suspend, `_get_clean_context` stripped ALL `_*` keys. On resume, only some were repopulated (`_instance_id`, `_workflow_def_id`, `_trace`). Lost: `_loop_item`, `_loop_index`, `_loop_iteration`, `_cycle_iterations`. Result: HITL inside a loop or cycle restarted the iteration counter on resume.

**Edge cases.** HITL inside a ForEach iteration → resume re-entered the loop from index 0. Cycle counter reset → potential runaway loop. The HITL re-fire pattern (V10) survived by accident because `hitl_pending_call` lived at the root, not under `_`.

**What shipped (branch `ctx-mgmt-d-runtime`).**
- `_get_clean_context` now preserves `_runtime` while stripping every other `_*` key.
- `_get_runtime(context)` helper — get-or-init the resume-safe sub-dict idempotently.
- `_hoist_legacy_runtime(context)` — backward-compat in-memory migration of any legacy flat keys (`_loop_*`, `_cycle_iterations`, `_parent_chain`, `hitl_pending_call`) into `_runtime.*`. Called at every `instance.context_json` load site in `dag_runner.py` (initial execute + resume paths). No DB migration; no in-flight instance breakage.
- All producers migrated:
  - `dag_runner._fire_loopbacks` writes `_runtime["cycle_iterations"][edge_id]`.
  - ForEach runner writes `_runtime["loop_item"]` / `loop_item_var` / `loop_index` and resumes from the persisted index.
  - Loop iteration runner writes `_runtime["loop_index"]` / `loop_iteration` / `loop_node_id` and resumes from the persisted index when re-entering the same loop node.
  - `react_loop.py` HITL-04 stamps `_runtime["hitl_pending_call"]` at suspend (instead of the previous "happens to not start with `_`" accident).
  - `_execute_sub_workflow` injects `child_instance.context_json["_runtime"]["parent_chain"]` (instead of flat `_parent_chain`).
- All consumers updated to read `_runtime.*` first, fall back to legacy flat keys for in-flight context that hasn't been hoisted yet (defence in depth — `prompt_template.build_structured_context_block`, `node_handlers._handle_for_each`, `node_handlers._handle_save_conversation_state` for `loop_iteration`, `node_handlers._execute_sub_workflow` for `parent_chain`, `dag_runner._build_node_input` for log payload, `react_loop` for `hitl_pending_call`).
- `_trace` stays top-level + ephemeral (rebuilt per invocation from the trace context) — explicitly excluded from the resume-safe namespace.

**Tests (1029 passed, 1 skipped after this slice — was 1011).**
- New `tests/test_runtime_namespace.py` (18 tests): `_get_runtime` create-vs-existing-vs-malformed, `_get_clean_context` strip + `_runtime` preservation, `_hoist_legacy_runtime` per-key + idempotency + canonical-wins-over-legacy, `_LEGACY_RUNTIME_KEYS` exhaustiveness guard, end-to-end clean→hoist round-trip preserves loop counters, legacy-context resume recovers `loop_index`, HITL-pending-call legacy→new round-trip.
- Existing cyclic test suite (`test_cyclic_loopback_execution.py` + `test_cyclic_e2e_patterns.py`) updated to assert against `context["_runtime"]["cycle_iterations"]` instead of flat `_cycle_iterations` — 24 tests pass post-migration.

**Refs.** Temporal — *"if your program restarts or the backend service goes down, your program will be in exactly the same state with all local variables and stack traces in exactly the same state"*.

### K. Compaction pass within a single workflow run — **Shipped 2026-05-01**

**Gap.** We ran rolling summarization on cross-session memory (`refresh_rolling_summary`) but NOT on within-run context. A 28-node workflow accumulated every node's full output for the whole run; nothing summarized early nodes once they were 20+ hops back.

**Edge cases.** Long-running graphs that loop through many iterations (cyclic graph support already shipped) compound the problem — each cycle adds another snapshot to checkpoints.

**What shipped (branch `ctx-mgmt-k-compaction`).**
- Migration `0037`: `tenant_policies.context_compaction_enabled BOOLEAN NOT NULL DEFAULT TRUE`. Compaction is a cost saver — default-on for the common case; tenants needing strict audit-trail replay can opt out (the full output stays available via the existing `node_output_artifacts` table either way).
- New module `app/engine/compaction.py`:
  - Tunables: `COMPACTION_THRESHOLD_BYTES` (128 kB trigger), `COMPACTION_TARGET_BYTES` (96 kB stop), `MIN_NODE_SIZE_FOR_COMPACTION` (1 kB — don't bother with tiny outputs), `RECONCILIATION_INTERVAL` (25 writes — periodic full size recompute to correct drift).
  - `is_compaction_enabled(context)` / `resolve_compaction_flag(db, tenant_id)` — same fast-path pattern as CTX-MGMT.H. Defaults TRUE on policy lookup error (cost saver, safe default opposite to trace's safe-off-when-uncertain).
  - `track_write_size(runtime, node_id, size)` — bookkeeping. Maintains `_runtime["context_size_bytes"]` (cumulative approximation), `_runtime["written_node_sizes"]` (per-node), `_runtime["written_node_order"]` (insertion order; rewrites move to end). Avoids re-encoding the full clean context on every write — that would be O(N²) in graph size.
  - `reconcile_size(context)` — drift correction every `RECONCILIATION_INTERVAL` writes.
  - `_pick_compaction_candidates(context, runtime)` — oldest-first, skipping already-stubbed (`_overflow` or `_compacted`), tiny outputs, non-`node_*` keys, and protected workflow-level slots (`trigger`, `approval`, `orchestrator_user_reply`).
  - `maybe_compact(db, context, ...)` — end-to-end. Fast no-op when disabled OR under threshold. When triggered: walks candidates oldest-first, persists each one's full output to `node_output_artifacts` (reusing CTX-MGMT.A's table), replaces `context[node_id]` with a stub, stops when under target.
- Stub shape upgrade — `materialize_overflow_stub` now accepts `kind: "overflow" | "compaction"` and spreads canonical scalar keys (`id`, `status`, `error`, `branch`, `result`, `code`, `name`, `session_id`, `instance_id`, `request_id`, `agent_id`, `workflow_id`, `case_id`) to BOTH top-level AND `preview`. `{{ node_X.id }}` renders the same pre- and post-stub regardless of which path produced the stub. Backward-compatible: existing CTX-MGMT.A tests still pass.
- `dag_runner._execute_single_node` (sequential + parallel) calls `track_write_size` + `maybe_compact` after each node assignment. Both fast no-ops when compaction is disabled or under threshold.
- Selection signal: oldest-write-first. The plan originally called for "haven't been read in the last K downstream executions"; read-event tracking was deferred to CTX-MGMT.H v2. Write-age is a defensible proxy and unblocks compaction now. When read tracking lands, the selection function flips to read-recency.

**Tests (1152 passed, 1 skipped after this slice — was 1120).**
- New `tests/test_compaction.py` (32 tests): `is_compaction_enabled` (5 fast-path cases), `resolve_compaction_flag` (default TRUE / explicit on/off / lookup-error defaults TRUE), `track_write_size` (seed, accumulate, rewrite-replaces, move-to-end), `_is_already_stubbed`, `_pick_compaction_candidates` (oldest-first, skip-already-compacted/overflowed/tiny/non-node/non-dict), `reconcile_size` (recompute from slots, reset counter), `maybe_compact` (disabled-no-op, under-threshold-no-op, compact-until-under-target, top-level keys preserved on stub, no double-compaction), stub Jinja round-trip (`{{ node_X.id }}` + `{% if node_X._compacted %}` both render).

**Refs.** Anthropic — *"Compaction is the practice of taking a conversation nearing the context window limit, summarizing its contents, and reinitiating a new context window with the summary. Compaction typically serves as the first lever in context engineering"*.

### L. Reducer-per-channel state model — **Shipped 2026-05-01**

**Gap.** Our context only did last-write-wins per node id. This broke down for parallel-branch aggregation, append-only audit trails, and counters. ForEach loop aggregation was hand-coded into the runner.

**Edge cases.** Multiple parallel branches each writing evidence — they overwrite each other today. ForEach aggregates via post-loop merge logic; could be a uniform `append` reducer.

**What shipped (branch `ctx-mgmt-l-reducers`).**
- New module `app/engine/reducers.py`. `KNOWN_REDUCERS` registry with six entries (`overwrite`, `append`, `merge`, `max`, `min`, `counter`); `DEFAULT_REDUCER = "overwrite"`. Pure helpers `resolve_reducer(node_data)` (with case-insensitive lookup + unknown-name fall-back-to-default + warn) and `apply_reducer(name, current, new)`.
- Each reducer is type-tolerant — type mismatches log a warning and fall back to overwrite for that specific write rather than raising. Strict guarantees are author-time concerns (validator).
- `dag_runner._execute_single_node` now resolves the per-node reducer and applies it when writing `context[node_id]`. Same wiring on the parallel-branch executor's `_apply_result`. Reducer fires AFTER the overflow check (CTX-MGMT.A) so an overflowed output gets stub-replaced before the reducer combines it with prior values.
- `app/engine/config_validator.py` rejects unknown reducer names + non-positive `contextOutputBudget` values at promote time so authors see a clear error rather than the silent runtime fall-back.
- `_reduce_append` wraps non-list `current` values when present so flipping a node from `overwrite` to `append` on the next run doesn't drop the existing slot value.
- All reducer types preserve numeric type invariants (counter on int + int returns int; mixed returns float).

**Reducer behavior summary.**

| Reducer | Use when | Type-mismatch fall-back |
|---|---|---|
| `overwrite` (default) | Single-write nodes (the common case). Identical to pre-CTX-MGMT.L. | n/a |
| `append` | Parallel-branch convergence, audit trails, multi-write aggregation. Auto-init from None to `[]`; wraps non-list current. | None |
| `merge` | Build up a structured payload across nodes. Dict.update semantics. | Logs + overwrites if either side is non-dict |
| `max` / `min` | Priority / score aggregation. | Logs + overwrites on non-numeric input |
| `counter` | Tally/sum across writes. Preserves int type on int+int. | Logs + overwrites on non-numeric input |

**What didn't ship (deferred sub-task).** Refactoring ForEach + Loop body aggregation to *use* reducers (instead of the runner's hand-coded merge into `{loop_results: [...], iterations: N}`) is deferred. It's invasive and the current loop semantics work; reducers add value for new patterns (the future Coalesce node from item E, audit trail channels) without forcing a loop refactor right now.

**Tests (1101 passed, 1 skipped after this slice — was 1057).**
- New `tests/test_reducers.py` (44 tests): every reducer's happy path + type-mismatch behavior, registry sanity, `resolve_reducer` (default / case-insensitive / unknown fall-back), validator integration (bad reducer name caught, valid name accepted, missing field skipped, negative/non-int budget caught), simulated engine round-trip (append accumulates across two writes; overwrite preserves last-write-wins).
- All 1057 prior tests pass unchanged — default `overwrite` keeps every existing graph identical.

**Refs.** LangGraph — *"key is operator.add, which tells LangGraph to append new messages to the existing list instead of overwriting"*. LangChain *State of Agent Engineering 2026*.

---

## §4. P1 — second slice

### B. `lint_jinja_dangling_reference`

**Gap.** Jinja's `_PermissiveUndefined` renders missing variables to empty strings — typos and broken cross-references ship silently.

**Plan.**
- New SMART-04 lint. Walk every templated string in every node config (systemPrompt, body, url, expression, headers, value).
- Parse Jinja AST, collect `node_*.foo.bar` references.
- Cross-check against:
  - Does `node_*` exist in graph?
  - Is it on a control-flow path that's pruned when the referencing node runs (Switch/Condition reachability)?
  - Does its handler ever produce the field being read?
- Warn for unreachable refs; error for non-existent ids.

**Status note (2026-05-01).** This is the cheapest item in the plan — pure static analysis, no runtime change. Recommend shipping alongside D so the V10 lessons it codifies (especially the "Switch arm pruned → downstream `node_4r.json.id` empty" edge case) are in the lint vocabulary.

### C. Per-node `dependsOn` / `exposeAs` scope declaration

**Gap.** A node 25 hops downstream sees every upstream node's output. Privacy concern (PII flows transitively) and cost concern (agents read outputs nobody references).

**Plan.**
- `data.config.dependsOn: list[str]`: explicit list of upstream node ids this node reads. Engine builds the read-context from listed deps + `trigger` + always-globals (`_runtime.*`, `approval`).
- `data.config.exposeAs: str`: rename for downstream visibility (default = node_id). Lets a node be referenced as something semantic (`{{ case }}` vs `{{ node_4r }}`).
- Default unset = current behavior (full read). Authors opt in.
- Lint: warn when Jinja references a node not in `dependsOn`.

**Refs.** Anthropic — *"each agent operates with scoped instructions and context"*. The principle generalizes from sub-agents to nodes.

### E. `Coalesce` node + child-evidence promotion (replaces walked-back proposal)

**Gap.** Switch/Condition `_prune_subtree` blocks fan-in. Sub-workflows can't surface partial evidence to parents during the run.

**Plan.**
- New `Coalesce` node type — fires when ANY input has data (vs default ALL). Native fan-in.
- Sub-workflow gains a structured "child evidence channel": child workflows can append to `_runtime.shared_evidence: list` (visible to the parent's downstream nodes via the append reducer from L). Parent decides what to do with accumulated evidence.
- New `lint_unreachable_node_after_switch` — flag fan-in shapes that pruning will break.

**Note.** The earlier draft of this item (`outputContext: "all"` to expose child's full state to parent) was walked back — see §6. The literature is firm that scoped per-agent context is the right pattern; child-evidence promotion preserves isolation while enabling the legitimate use case.

### G. ReAct iterations summary vs full split

**Gap.** `node_worker.iterations` exposes full LLM reasoning + tool args + tool results. Downstream Jinja can pull the whole list into another prompt; downstream LLMs see private reasoning state.

**Plan.**
- `iterations` (default exposure) → summary list `[{iteration, action, tool_name?, status}]`.
- `iterations_full` (only when `data.config.exposeFullIterations: true`) → current verbose shape.
- Run `scrub_secrets` on `iterations` write, not just log write.

**Refs.** Anthropic — *"each agent has a specific role and context window... avoids overloading any single agent with too much responsibility or information"*.

---

## §5. P2 / P3 — defer until P0/P1 land

### I. `outputSchema` on every node

**Gap.** Trigger has implicit shape; no validation across webhook / scheduler / A2A / sub-workflow callers. Generalize: every node could declare an output schema.

**Plan.** Pydantic-v3 `outputSchema: dict` (JSON Schema) on each node config. Engine validates at write-time, surfaces clean errors. Copilot lint catches unsafe Jinja reads against the schema.

**Refs.** LangGraph 2026 — *"Pydantic v3 is used for state definitions because it provides runtime validation"*.

### J. First-class `distillBlocks`

**Gap.** Manual Jinja distillation (V10 RECENT TOOL FINDINGS pattern) doesn't generalize across workflows.

**Plan.** `data.config.distillBlocks: [{label, fromPath, limit, project, format}]` on any LLM/ReAct node. Engine renders distilled blocks as a separate cacheable user message.

**Refs.** Anthropic — *"structured note-taking"* as a context-pollution mitigation.

### F. Scrub-secrets at write-time

**Gap.** `scrub_secrets` only runs on log writes; in-memory context can carry secrets that downstream LLM nodes can pull via Jinja.

**Plan.** Run `scrub_secrets` on `context[node_id] = output` write, in `_execute_single_node` (line 1220). Adds CPU cost but closes the leak vector. Pair with V10's redaction patterns (`runner_tools._SECRET_PATTERNS`) so the engine and copilot share one secret dictionary.

### H. `context_trace` runtime channel — **Shipped 2026-05-01 (v1: writes only)**

**Gap.** No introspection — when context was unexpectedly empty, debugging meant eyeballing `context_json` by hand. With multi-node graphs (V10 has 28 nodes) and reducer support (CTX-MGMT.L), the question "where did node_4r come from?" needed a structured answer.

**What shipped (branch `ctx-mgmt-h-context-trace`).**
- New `instance_context_trace` table (migration `0036`) — `{instance_id, node_id, op, key, size_bytes, reducer, overflowed, ts}`. RLS tenant-scoped, cascade-delete from `workflow_instances`. Two indexes: `(instance_id, key, ts)` for the inspect-tool point lookup, `(tenant_id, ts)` for retention sweeps.
- New tenant-policy column `context_trace_enabled` (default FALSE).
- New module `app/engine/context_trace.py` with pure-fast-path helpers:
  - `is_trace_enabled(context)` — single dict lookup of `_runtime["context_trace_enabled"]`.
  - `resolve_trace_flag(db, tenant_id, is_ephemeral)` — returns True for ephemeral instances unconditionally; consults `tenant_policies.context_trace_enabled` for production; defaults False on policy lookup error so prod never traces by accident.
  - `record_write(db, context, ...)` — fast no-op when disabled; INSERT one row + `flush()` when enabled. Failures are logged but never raised.
  - `fetch_events_for_instance(db, ...)` — read API with optional exact-key or prefix-key (`node_*`) filter, ordered ts ASC, capped at 200.
- Per-instance row cap: 500 events. When exceeded, oldest 50 rows dropped in one DELETE. Cap-check itself runs every 50th write to keep per-write overhead bounded.
- `dag_runner.execute_graph` resolves the trace flag once at start and stamps `_runtime["context_trace_enabled"]` (which survives suspend/resume via CTX-MGMT.D's hoist).
- `dag_runner._execute_single_node` calls `record_write(...)` after each context assignment on BOTH the sequential and parallel-branch paths. Each event records the configured reducer (CTX-MGMT.L) and whether the write overflowed (CTX-MGMT.A).
- New copilot runner tool `inspect_context_flow(instance_id, key?)` — same ephemeral-only safety as `get_execution_logs`. Returns `{instance_id, key_filter, event_count, events: [...]}`. Supports exact match and `key_prefix*` filtering.

**v2 (deferred).** Read-event tracking and miss-event tracking. Reads were intentionally skipped — one Jinja prompt can hit hundreds of attribute reads, and the volume blows the 500-cap easily. Misses need Jinja AST work (track template variable resolution failures); the missing-key story is better served by `lint_jinja_dangling_reference` (CTX-MGMT.B) at promote time, complementary to runtime tracing. v2 will add read-event tracking with sampling + the lint at promote time.

**Tests (1120 passed, 1 skipped after this slice — was 1101).**
- New `tests/test_context_trace.py` (19 tests):
  - `is_trace_enabled` (six cases: missing context, missing runtime, flag unset, flag explicit-false, flag explicit-true, malformed runtime).
  - `resolve_trace_flag` (ephemeral always on without consulting policy; prod honors policy on/off; prod stays off on policy lookup error).
  - `record_write` disabled fast-path (zero DB touches).
  - `record_write` enabled path (one row added with all fields, flush called, overflowed flag propagates, never raises on DB error).
  - Trim behavior (cap-check fires every TRACE_TRIM_BATCH writes; doesn't trim when under cap).
  - `fetch_events_for_instance` (serialised event shape, ts isoformat, prefix filter applies LIKE match).

**Refs.** Anthropic — *"context engineering is the set of strategies for curating and maintaining the optimal set of tokens"*. Observability is what enables that curation in practice.

### M. Forgetting / decay

**Gap.** Context grows unbounded within a run; checkpoints accumulate forever.

**Plan.**
- `data.config.retainOutputAfterRun: bool` per node — when False, engine clears `context[node_id]` at run completion before persisting.
- Beat task `prune_aged_checkpoints` — `InstanceCheckpoint` rows older than N days deleted (configurable via `tenant_policies.checkpoint_retention_days`).

**Refs.** Agent-memory literature — time-based decay, relevance scoring, explicit deletion.

---

## §6. Walk-backs (literature contradicted my draft)

**Original sub-workflow `outputContext: "all"` (Issue E in the review).** Proposed letting parents read children's full `context_json`. Anthropic's multi-agent guidance: *"each agent operates with scoped instructions and context, the system runs in parallel, sustains accuracy, and avoids overloading any single agent with too much responsibility or information"* — directly contradicts. **Replaced** with structured child-evidence promotion (item E above) — same outcome, preserves scope isolation.

---

## §7. Cross-cutting principles (literature-validated)

These hold across every item; reference them in PRs to keep the surface coherent.

1. **Context is one budget.** Tool descriptions, allowed tool categories, node outputs, system prompt — they all consume the model's attention. Optimize them as one budget, not three. (Anthropic.)
2. **Compaction is the first lever**, not the last. When state grows, summarize early; don't wait for OOM. (Anthropic.)
3. **Scoped, not shared.** Default sub-agent / sub-node context to scoped; opt in to sharing. (Anthropic, LangChain.)
4. **Reducers > overwrites.** Declare how state merges; don't let last-write-wins be the answer to every question. (LangGraph.)
5. **Durable runtime state.** Resume-safe execution is a correctness requirement, not a feature. (Temporal.)
6. **Lean state, external blobs.** Store large data with references, not inline. (LangChain, LangGraph.)
7. **Forgetting is first-class.** Time-based decay + explicit deletion are part of the design, not afterthoughts. (Agent-memory literature.)
8. **Fail loud at authoring, soft at runtime.** Lints catch schema/reference bugs before they ship; runtime renders missing values to empty strings rather than 500s. (Our existing pattern; reinforce don't loosen.)

---

## §8. References

**Anthropic engineering**
- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [Building Effective AI Agents — Architecture Patterns and Implementation Frameworks (PDF)](https://resources.anthropic.com/hubfs/Building%20Effective%20AI%20Agents-%20Architecture%20Patterns%20and%20Implementation%20Frameworks.pdf)

**LangChain / LangGraph**
- [Context Engineering for Agents — LangChain blog](https://blog.langchain.com/context-engineering-for-agents/)
- [LangGraph State Management in Practice: 2026 Agent Architecture Best Practices](https://eastondev.com/blog/en/posts/ai/20260424-langgraph-agent-architecture/)
- [LangGraph State Management: TypedDict, Reducers, and State Channels](https://callsphere.ai/blog/langgraph-state-management-typeddict-reducers-state-channels)
- [Mastering LangGraph State Management in 2025](https://sparkco.ai/blog/mastering-langgraph-state-management-in-2025)

**Temporal**
- [Temporal Workflow Execution overview](https://docs.temporal.io/workflow-execution)
- [Workflow Engine Design Principles with Temporal](https://temporal.io/blog/workflow-engine-principles)

**Agent memory**
- [A Practical Guide to Memory for Autonomous LLM Agents — TDS](https://towardsdatascience.com/a-practical-guide-to-memory-for-autonomous-llm-agents/)
- [Agent Memory in LangChain: Short-Term, Long-Term, and Episodic — Propelius](https://propelius.ai/blogs/agent-memory-patterns-langchain/)

**In-repo**
- [copilot.md](copilot.md) — copilot tool surface (V2 lints already include `prompt_cache_breakage` and `react_role_no_category_restriction`, both in this lineage)
- [cyclic-graphs.md](cyclic-graphs.md) — loopback edge model, related to item D
- [database-schema.md](database-schema.md) — migration index
- [feature-roadmap.md](feature-roadmap.md) — canonical status index

**Project memory (auto-loaded)**
- `feedback_agent_design_principles.md` — STATIC + DYNAMIC prompt split (related to item A overflow + B linting)
- `feedback_v9_adoption_targets_from_legacy.md` — RECENT TOOL FINDINGS distillation pattern (related to item J)
- `project_ae_ops_support_demo.md` — V8/V9/V10 series; the empirical ground truth that motivated this plan

---

## §9. Change log

| Date | Change |
|---|---|
| 2026-05-01 | Plan created. All items at `Not started`. Priority order set (D, A, L, K as P0). Walk-back recorded for original Issue E sub-workflow `outputContext: "all"` after Anthropic-multi-agent literature contradiction. |
| 2026-05-01 | **CTX-MGMT.D shipped** on branch `ctx-mgmt-d-runtime`. `_runtime` namespace + `_hoist_legacy_runtime` backward-compat migration in `dag_runner.py`. Producers migrated in `dag_runner.py` (cycle counters, ForEach + Loop iteration runners), `react_loop.py` (HITL pending call), `node_handlers.py` (sub-workflow `parent_chain`). Consumers updated with new-then-legacy fall-through in `prompt_template.py`, `node_handlers.py`, `react_loop.py`. 18 new unit tests + 24 existing cyclic tests migrated. Full backend suite 1029 passed (was 1011). HITL-inside-ForEach now resumes at the correct iteration index instead of restarting at 0. |
| 2026-05-01 | **CTX-MGMT.A shipped** on branch `ctx-mgmt-a-overflow` (stacked on `ctx-mgmt-d-runtime`). New `node_output_artifacts` table (migration 0035) + RLS + indexes. `app/engine/output_artifact.py` with pure helpers (`estimate_output_size`, `resolve_budget`, `should_overflow`, `materialize_overflow_stub`, `persist_artifact`, `maybe_overflow`). Wired into `dag_runner._execute_single_node` on both sequential and parallel-branch paths — on overflow the artifact is INSERTed and the in-context value becomes a small stub preserving canonical scalar keys (`id`, `status`, `error`, `branch`, etc.) so downstream Jinja still resolves. New copilot runner tool `inspect_node_artifact` (same ephemeral-only safety as `get_execution_logs`). 28 new unit tests + Jinja round-trip tests. Full backend suite 1057 passed (was 1029). Defaults: 64 kB per-node budget, 256 kB hard ceiling, per-node `contextOutputBudget` override. Deferred sub-task: `_save_checkpoint` delta writes — separate optimisation that can land independently. |
| 2026-05-01 | **CTX-MGMT.K shipped** on branch `ctx-mgmt-k-compaction` (stacked on `ctx-mgmt-h-context-trace`). All four P0 items now done. Migration 0037: `tenant_policies.context_compaction_enabled` (DEFAULT TRUE — opposite of trace flag's default-off). New `app/engine/compaction.py` with running-size approximation tracker, oldest-first candidate selection, and `maybe_compact` end-to-end pass that reuses CTX-MGMT.A's `node_output_artifacts` table for storage. Stub shape upgrade — `materialize_overflow_stub` accepts `kind: "overflow"|"compaction"` and now spreads canonical scalar keys to top level so `{{ node_X.id }}` renders the same pre/post-stub. Wired into `dag_runner._execute_single_node` on both paths. Selection signal is write-age (oldest-first) until CTX-MGMT.H v2 adds read-recency. 32 new unit tests; 1152 backend tests pass (was 1120). Per-tenant opt-out for strict audit-trail replay; full output remains in artifacts either way. |
| 2026-05-01 | **CTX-MGMT.H shipped** (v1: writes only) on branch `ctx-mgmt-h-context-trace` (stacked on `ctx-mgmt-l-reducers`). New `instance_context_trace` table (migration 0036) + `tenant_policies.context_trace_enabled` flag. `app/engine/context_trace.py` with fast-path no-op helpers. Wired into `dag_runner._execute_single_node` on both paths so every context write records `{instance_id, node_id, op, key, size_bytes, reducer, overflowed, ts}` when tracing is on. Ephemeral (copilot-initiated) instances always trace; production opts in via the new tenant policy. New copilot runner tool `inspect_context_flow(instance_id, key?)` with exact-match + prefix (`node_*`) filtering. Per-instance cap of 500 events with batched 50-row trim. 19 new unit tests; 1120 backend tests pass (was 1101). Read-event + miss-event tracking deferred to v2 — volume is template-dependent and the missing-key story is better served by `lint_jinja_dangling_reference` (CTX-MGMT.B) at promote time. |
| 2026-05-01 | **CTX-MGMT.L shipped** on branch `ctx-mgmt-l-reducers` (stacked on `ctx-mgmt-a-overflow`). New `app/engine/reducers.py` with `KNOWN_REDUCERS` registry (6 entries: `overwrite`/`append`/`merge`/`max`/`min`/`counter`) + pure helpers `resolve_reducer` and `apply_reducer`. Wired into `dag_runner._execute_single_node` (sequential) and `_apply_result` (parallel-branch) — fires AFTER the overflow check so the overflow stub composes correctly with reducers. `app/engine/config_validator.py` gains `_validate_output_reducer` (rejects unknown names) and `_validate_output_budget` (rejects non-positive budgets) at promote time. Default `overwrite` keeps every existing graph identical; new reducers unlock parallel-branch aggregation, audit trails, counters, max/min trackers without ad-hoc handler code. 44 new unit tests; full backend suite 1101 passed (was 1057). Deferred sub-task: refactoring ForEach + Loop body aggregation to use reducers — invasive, current semantics work, current reducers already add value for new patterns. |
