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
| **B** | `lint_jinja_dangling_reference` static lint | P1 | **Shipped** (branch `ctx-mgmt-b-jinja-lint`) | — | 2026-05-01 |
| **C** | Per-node `dependsOn` / `exposeAs` scope declaration | P1 | **Shipped (v1: aliasing + lint)** (branch `ctx-mgmt-c-scope`) | — | 2026-05-01 |
| **E** | Native fan-in primitive (Merge waitAny) + child-evidence promotion + reachability lint | P1 | **Shipped** (branch `ctx-mgmt-e-coalesce`) | — | 2026-05-01 |
| **G** | ReAct iterations summary vs full split | P1 | **Shipped** (branch `ctx-mgmt-g-iterations-split`) | — | 2026-05-01 |
| **I** | `outputSchema` on every node (not just trigger) | P2 | **Shipped** (branch `ctx-mgmt-i-output-schema`) | — | 2026-05-01 |
| **J** | First-class `distillBlocks` for context distillation | P2 | **Shipped** (branch `ctx-mgmt-j-distill`) | — | 2026-05-01 |
| **F** | Scrub-secrets at write-time, not just log-time | P2 | **Shipped** (branch `ctx-mgmt-f-write-scrub`) | — | 2026-05-01 |
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

### B. `lint_jinja_dangling_reference` — **Shipped 2026-05-01**

**Gap.** Jinja's `_PermissiveUndefined` rendered missing variables to empty strings — typos and broken cross-references shipped silently.

**What shipped (branch `ctx-mgmt-b-jinja-lint`).**
- New module `app/copilot/jinja_refs.py`:
  - `extract_node_refs(text)` — regex extracts `node_X` and `node_X.foo.bar` references from any string. Covers BOTH Jinja `{{ ... }}` and safe_eval expressions in one pass — the dotted-attribute syntax is identical.
  - `walk_node_strings(obj)` — recursive iterator yielding every string value in a JSON-shaped object. Lets the lint sweep every templated field of a node config without maintaining a list of "fields that may contain templating" that drifts as new node types ship.
  - `collect_refs_from_config(config)` — combines the two: returns a deduped sorted list of every `(node_id, attr_path)` reference in a node's config.
- New SMART-04 lint `lint_jinja_dangling_reference` in `app/copilot/lints.py`:
  - For each node, walks `data.config` and extracts every `node_X` reference.
  - Cross-checks against the graph's actual node ids.
  - Emits two lint codes:
    - `jinja_dangling_node_ref` (**error**) — referenced node id doesn't exist; the permissive-undefined policy will render to empty string at runtime, silently broken. Fix-hint surfaces the existing node ids for the typo case.
    - `jinja_node_self_ref` (**warn**) — node references its own slot; that slot is empty when the handler runs (the engine populates it AFTER the handler returns). Probably a typo or copy-paste bug.
  - Wired into `run_lints` so `check_draft` surfaces it via the existing copilot lint pipeline.

**v2 deferral — what's NOT checked yet.**
- **Reachability.** A node can legally reference a `node_X` that's pruned by an upstream Switch arm; that produces an empty rendering at runtime if the wrong arm fires (V9's `node_4r.json.id` issue, fixed via the always-runs upstream HTTP node). Catching this requires branch-aware control-flow analysis — deferred.
- **Field existence.** Does `node_4r.json.id` actually produce `json.id`? Requires static knowledge of every node handler's output shape. The CTX-MGMT.I `outputSchema` field will enable this once authors declare schemas.

**Tests (1174 passed, 1 skipped after this slice — was 1152).**
- New `tests/test_jinja_refs_lint.py` (22 tests):
  - `extract_node_refs` — Jinja shape, safe_eval shape, bare ref no attrs, multiple refs in one string, word-boundary handling (`not_a_node_X_thing` doesn't match; `(node_4r.id)` does), non-string input, fast-path empty when no `node_` substring, deep attribute path.
  - `walk_node_strings` + `collect_refs_from_config` — recursive walk, list traversal, scalar skipping, dedup.
  - `lint_jinja_dangling_reference` — no lint when ref exists, fires on missing id with correct severity/node_id/fix-hint, fires on self-ref, walks nested config fields (HTTP headers etc.), empty-graph no-op, multiple dangling refs in one node yield multiple lints, real V10 pattern (`node_router.intents[0]` square-bracket access) doesn't false-positive.
  - `run_lints` integration — confirms the new lint is wired into the SMART-04 entrypoint and a well-formed V10-shape workflow produces zero lints.

**Refs.** Anthropic — *"system prompts should be extremely clear and use simple, direct language"* — applies to authoring tools too: catch authoring-time errors at promote rather than letting them propagate as silent runtime degradation.

### C. Per-node `dependsOn` / `exposeAs` scope declaration — **Shipped 2026-05-01 (v1: aliasing + lint)**

**Gap.** A node 25 hops downstream saw every upstream node's output. Privacy concern (PII flows transitively) and cost concern (agents read outputs nobody references). Plus the readability concern — `{{ node_4r.json.id }}` is opaque; `{{ case.id }}` is self-documenting.

**What shipped (branch `ctx-mgmt-c-scope`).**

**`exposeAs` aliasing** (`app/engine/dag_runner.py` + `app/engine/config_validator.py`):
- After the engine writes `context[node_id] = output` (post-reducer, post-overflow stub), if the node's `data.config.exposeAs` is a non-empty string, the engine ALSO writes `context[<exposeAs_value>] = context[node_id]`. Backward-compatible — the canonical `node_id` slot is unchanged; the alias is additive.
- Wired on BOTH the sequential and parallel-branch paths in `_execute_single_node` / `_apply_result`.
- Validator (`_validate_expose_as_collisions`) catches three classes of authoring error: alias collides with another node's id (would silently overwrite that slot); two nodes share the same alias (only the last firing one's output is readable); alias matches the node's own id (pointless no-op).
- Use case: V10's `node_4r.json.id` becomes `{{ case.id }}` when `node_4r` is configured `exposeAs: "case"`. Self-documenting, refactor-safe.

**`dependsOn` config + extended lint** (`app/copilot/lints.py`):
- Node config accepts `dependsOn: list[str]` — explicit list of upstream node ids this node reads from. **Runtime enforcement is deferred to v2** (it requires per-node context filtering at every render site — `render_prompt`, HTTP body/url/headers, safe_eval expressions, ReAct system prompts). For v1 the field is informational.
- The `lint_jinja_dangling_reference` lint now honors `dependsOn`:
  - When `dependsOn` is set on a node and a Jinja reference targets something NOT in that list (and not an alias of something in that list), warn `jinja_ref_outside_depends_on`. Catches drift — "I forgot to update dependsOn after adding a new template reference".
  - The lint's existing existence check now also accepts `exposeAs` aliases as valid targets (so `{{ case.id }}` doesn't false-positive when `case` is an alias).

**v2 deferral.**
- **Runtime enforcement of `dependsOn`.** When implemented: `render_prompt(template, context)` would build a filtered context containing only `trigger`, the listed `dependsOn` ids (and their aliases), and always-globals (`_runtime.*`, `approval`). Same shape for HTTP/Switch/Loop expression evaluation. Bigger surgery — every render site needs the filter — and best done once authors are using `dependsOn` enough that the lint shows real drift.

**Tests (1229 passed, 1 skipped after this slice — was 1213).**
- New `tests/test_scope_c.py` (16 tests):
  - `_validate_expose_as_collisions`: clean alias no-op, alias collides with another node id, alias collides with own id, two nodes share alias (Multiple-claim warning), non-string alias rejected, empty alias skipped.
  - `lint_jinja_dangling_reference` with exposeAs: alias resolves dangling reference; alias doesn't help unrelated dangling references.
  - `lint_jinja_dangling_reference` with dependsOn: no lint when unset, no lint when ref is in list, warns when ref outside list, accepts ref via alias of a dependsOn-listed node.
  - `validate_graph_configs` integration confirms `exposeAs` collision check is wired.
  - Engine alias smoke — direct simulation of the post-reducer write block confirms both `context[node_id]` and `context[alias]` are populated and reference the same value; no alias when `exposeAs` is unset.

**Refs.** Anthropic — *"each agent operates with scoped instructions and context"*. The principle generalizes from sub-agents to nodes; v1 starts with aliasing (refactor-friendly readability) and lint-time scope drift detection. Runtime scope enforcement is the next step.

### E. Fan-in primitive + child-evidence promotion + reachability lint — **Shipped 2026-05-01**

**Gap.** Switch/Condition `_prune_subtree` blocked fan-in: a node downstream of multiple branch arms would never satisfy its full in-degree (only one arm fires per execution). V9 worked around this by always-running an extra HTTP node so `node_route` had a single satisfied predecessor — workflow-level workaround for an engine-level limitation.

**What shipped (branch `ctx-mgmt-e-coalesce`).**
- **Engine — Merge node `waitAny` semantics** (`app/engine/dag_runner.py`):
  - Implementation note: a `Merge` node type already existed in `shared/node_registry.json` (line ~356, type=merge, label=Merge) with strategy enum `waitAll | waitAny`, but the `waitAny` path was never wired into the engine — graphs that set it got `waitAll` semantics regardless. CTX-MGMT.E wires it up rather than introducing a new `Coalesce` node alongside (cleaner, no new registry entry, backward-compatible since the default `waitAll` is unchanged).
  - New `_is_waitany_merge(node)` helper checks `nodeCategory == "logic"` AND `label == "Merge"` AND `config.strategy == "waitAny"` (case-insensitive).
  - `_find_ready_nodes` branches on the helper: waitAny fires when any active source is satisfied (`active_sources & sat`); default waitAll fires when all are (`active_sources <= sat`).
  - The `nid in executed` guard in `_find_ready_nodes` ensures a waitAny merge fires once per execution, not once per upstream completion.
- **Engine — Merge handler output shape** (`app/engine/node_handlers.py::_handle_logic`):
  - `waitAny` returns `{merged: <value>, value: <value>, from: <upstream_node_id>, strategy: "waitAny"}` so downstream Jinja can read `{{ node_merge.value }}` or `{{ node_merge.from }}`.
  - `waitAll` now also stamps `strategy: "waitAll"` for symmetry.
- **Sub-workflow shared_evidence promotion** (`app/engine/node_handlers.py::_execute_sub_workflow`):
  - After the child workflow completes, read `child.context_json["_runtime"]["shared_evidence"]` (list, optional). If present, append-merge into the parent's `_runtime["shared_evidence"]` list via the CTX-MGMT.D `_get_runtime` helper.
  - Lets sub-workflows surface structured findings to the parent without dumping their full context_json — preserves Anthropic's scoped-per-agent-context pattern (the original walked-back `outputContext: "all"` proposal violated this; this design preserves the isolation while enabling the legitimate "promote evidence up" use case).
  - Downstream parent nodes can read accumulated evidence via Jinja `{{ _runtime.shared_evidence }}` or via a future `Distill` node (CTX-MGMT.J).
- **`lint_unreachable_node_after_switch` SMART-04 lint** (`app/copilot/lints.py`):
  - Static graph reachability analysis. For each non-Coalesce / non-Merge-waitAny node N with in_degree ≥ 2: walk back through each incoming edge to find the nearest Switch/Condition ancestor + the source handle used. If two of N's incoming edges trace back to the SAME branch via DIFFERENT handles, warn — that pair will never both fire.
  - Coalesce-aware: nodes with `data.label == "Coalesce"` are skipped (leaves room for a hypothetical future Coalesce node type).
  - Targets the bug shape directly: V9-style "switch picks one arm; downstream fan-in waits forever for the pruned arm".
  - Fix-hint: "Replace with a `Coalesce` node OR set Merge `strategy: waitAny`, or restructure so the fan-in happens AFTER all branch arms have converged".

**Tests (1213 passed, 1 skipped after this slice — was 1190).**
- New `tests/test_coalesce_e.py` (23 tests):
  - `lint_unreachable_node_after_switch` (no lint when no branch nodes, fires on classic V9 two-arm fan-in shape, fires on Condition true/false fan-in, direct Switch-to-fanin without intermediate, skipped for Coalesce-labeled nodes, two independent branches don't fire, empty graph no-op, single-predecessor doesn't fire).
  - `_is_waitany_merge` (default Merge isn't waitAny, explicit waitAll isn't, waitAny detected, case-insensitive, non-Merge isn't, non-logic category isn't).
  - `_find_ready_nodes` with waitAny merge (fires with one satisfied source, waitAll waits for all, waitAll fires when all three satisfied, waitAny doesn't re-fire after executed, waitAny treats all-pruned-sources as no-active-sources path).
  - `_handle_logic` Merge waitAny output shape (carries value + from + strategy; waitAll aggregates upstream).
  - `_execute_sub_workflow` shared_evidence promotion (child evidence appends to parent; child-no-evidence leaves parent unchanged).

**Refs.** Anthropic — *"each agent operates with scoped instructions and context, the system runs in parallel, sustains accuracy, and avoids overloading any single agent with too much responsibility or information"*. The walked-back `outputContext: "all"` would have violated this; the shared_evidence channel preserves isolation while enabling structured findings to flow up.

### G. ReAct iterations summary vs full split — **Shipped 2026-05-01**

**Gap.** `node_worker.iterations` exposed full LLM reasoning + tool args + tool results. Downstream Jinja could pull the whole list into another prompt; downstream LLMs saw private reasoning state.

**What shipped (branch `ctx-mgmt-g-iterations-split`).**
- New helpers in `app/engine/react_loop.py`:
  - `_summarize_iterations(iterations)` — builds the safe public summary. Each entry keeps `iteration` + `action`; depending on action: `tool_use` keeps `tool_calls: [{"name": str}]` (just names — no args, no results, no LLM reasoning content); `final_response` keeps `content_length` (integer for telemetry; the actual content is exposed at the top-level `response` field); `llm_error`/`timeout`/`max_iterations_exceeded` keep a 200-char-truncated `error`; `approved_tool_executed` keeps `tool_name` for the HITL-04 audit trail.
  - `_finalize_iterations_payload(iterations, *, expose_full)` — returns the dict to merge into the ReAct loop's output. Always emits `iterations` (summary). Optionally emits `iterations_full` (verbose form, scrubbed via the engine's `scrub_secrets`) when the node config opted into `exposeFullIterations: True`.
- `run_react_loop` reads `expose_full_iterations = bool(config.get("exposeFullIterations", False))` once at top, then merges the finalized payload at all 4 return-shape sites (timeout, llm_error, final_response, max_iterations_exceeded).
- `scrub_secrets` is applied to `iterations_full` at write-time. Existing key-based redaction (`api_key`, `token`, `password`, etc.) catches the common leak shapes; the original list is untouched (scrub is pure/functional).
- `predicates._all_tool_calls` updated to read `iterations_full` first, fall back to `iterations`. So `tool_called` / `no_tool_called` / `tool_call_count` predicates work on both the safe summary AND the verbose form (for callers that opted in). Forward-compatible with predicates that may inspect args in the future.

**What didn't change.** The top-level `response` field is unchanged — Bridge nodes and downstream user-facing paths still see the agent's final reply identically. Only the iterations trace's exposure narrowed.

**Tests (1190 passed, 1 skipped after this slice — was 1174).**
- New `tests/test_iterations_split.py` (16 tests):
  - `_summarize_iterations` — empty list, `tool_use` keeps only names (drops args + results + content), `final_response` keeps content_length only, `llm_error` truncates, `timeout` minimal shape, `max_iterations_exceeded`, `approved_tool_executed` keeps tool name (drops results), defensive non-dict skip, unknown action passes through with iteration+action.
  - `_finalize_iterations_payload` — default path emits only `iterations`; expose_full emits both keys; `iterations_full` is scrubbed for sensitive keys (`api_key` redacted, non-sensitive `q` passes through); summary unaffected by the expose flag.
  - Predicate compatibility — summary's tool name is enough for `tool_called` to match; when `iterations_full` is present, predicates prefer it; legacy-shaped contexts (full iterations under `iterations` without `_full`) still work via the fallback path.

**Refs.** Anthropic — *"each agent has a specific role and context window... avoids overloading any single agent with too much responsibility or information"*. Also LangChain — *"context engineering is the set of strategies for curating and maintaining the optimal set of tokens"*: tool args + reasoning traces are the highest-volume class of cruft in agent contexts.

---

## §5. P2 / P3 — defer until P0/P1 land

### I. `outputSchema` on every node — **Shipped 2026-05-01**

**Gap.** Trigger had implicit shape; no validation across webhook / scheduler / A2A / sub-workflow callers. Generalize: every node could declare an output schema.

**What shipped (branch `ctx-mgmt-i-output-schema`).**
- New `app/engine/output_schema.py`:
  - `validate_node_output(output, schema)` — pure validator returning `(is_valid, errors)`. Uses `jsonschema.Draft202012Validator` (already in deps). Soft-fails on malformed schema (returns `True` plus the error in the list so the caller can log).
  - `schema_paths(schema)` — returns the set of dotted attribute paths reachable through `properties`. `max_depth=6` cap prevents recursive-schema loops.
  - `schema_allows_path(schema, path)` — predicate the lint uses; respects `additionalProperties: true` / `x-permissive: true` to avoid false-positives on intentionally-permissive schemas.
  - `annotate_output_with_validation(output, ...)` — when a dict output fails validation, stamps `_schema_mismatch: True` + `_schema_errors: [...]` on a defensive copy.
  - `OutputSchemaError` for the strict-fail path.
- `dag_runner._execute_single_node` (sequential + parallel paths): after `dispatch_node` returns and BEFORE the overflow / reducer / trace pipeline, validate against the node's `data.config.outputSchema`. Soft by default — `_schema_mismatch` annotation + warn log; on `outputSchemaStrict: true`, raise `OutputSchemaError`. Empty / missing schema = one-dict-lookup no-op.
- `config_validator._validate_output_schema`: catches non-dict schemas + invalid-JSON-Schema shapes at promote time so authors see the issue before runtime.
- Lint extension — `lint_jinja_dangling_reference` adds `jinja_ref_path_not_in_schema` (warn): when the referenced node declares an `outputSchema` and the Jinja ref's path isn't reachable through it (e.g. typo `node_4r.id` when the handler actually produces `node_4r.json.id`).
- `extract_node_refs` / `collect_refs_from_config` in `app/copilot/jinja_refs.py` upgraded to accept an `aliases` set, so `{{ case.foo }}` (where `case` is an `exposeAs` alias) is captured the same way as `{{ node_4r.foo }}`. Reserved names (`trigger`, `output`, `context`, `_runtime`, `approval`) are excluded from alias scanning. **This was a latent bug fix** — pre-this-slice, the dangling-ref lint silently missed alias refs (the regex required `node_` prefix). The earlier "alias resolves dangling check" tests passed only because no refs were extracted at all.

**Tests (1262 passed, 1 skipped after this slice — was 1229).**
- New `tests/test_output_schema.py` (33 tests):
  - `validate_node_output` — empty schema valid, valid output passes, missing-required fails, wrong-type fails, enum-violation fails, non-dict schema soft-fails, malformed schema soft-fails, nested path reported in error.
  - `schema_paths` — empty / top-level / nested / array-of-objects / max_depth caps recursion.
  - `schema_allows_path` — no schema always allows, declared path allowed, undeclared blocked, additionalProperties=true allows anything, x-permissive=true allows anything, parent-path implies child-allowed.
  - `annotate_output_with_validation` — valid returns unchanged, invalid dict gets metadata (defensive copy doesn't mutate original), invalid non-dict returned unchanged, errors capped at 5.
  - `_validate_output_schema` config check — skip when unset, non-dict warns, empty schema skipped, valid no warning, malformed JSON Schema warns.
  - Lint integration — no schema = no path lint; undeclared path warns; declared path no warn; alias resolves to schema; `additionalProperties: true` silences lint.

**Refs.** LangGraph 2026 — *"Pydantic v3 is used for state definitions because it provides runtime validation"*. Anthropic — *"system prompts should be extremely clear and use simple, direct language"* — applies to schema authoring too.

### J. First-class `distillBlocks` — **Shipped 2026-05-01**

**Gap.** Manual Jinja distillation (V10 `RECENT TOOL FINDINGS` pattern in `WORKER_PROMPT_DYNAMIC`) didn't generalize across workflows. Authors copy-paste-adapt → drift.

**What shipped (branch `ctx-mgmt-j-distill`).**
- New `app/engine/distill.py` with pure helpers:
  - `walk_dotted_path(context, path)` — resolves `"node_4r.json.worknotes"` (or `"node_4r.items[0].name"`) into the actual nested value. Returns `None` on any miss; never raises.
  - `render_one_block(context, block)` — renders one distill block. Skips empty results (caller can omit the section). Hard limit of 100 items per block; per-item char cap of 280 with `…` truncation.
  - `render_distill_blocks(context, blocks)` — combines multiple blocks into one string ready to append to a system prompt. Empty / missing blocks → empty string. Malformed blocks logged + skipped (observability concern, not correctness).
  - `validate_distill_blocks(blocks)` — promote-time shape validator.
  - Three formats: `bullet` (default), `numbered`, `json`. Project field via `project: list[str]` — single field returns the bare value, multiple fields return a dict.
- Engine wiring:
  - `node_handlers._handle_agent` (LLM Agent path) appends rendered distill blocks to the system prompt after `render_prompt(raw_prompt, context)` and BEFORE `assemble_agent_messages`.
  - `react_loop.run_react_loop` does the same after the system prompt is rendered (between render_prompt and the CONCISE-01 suffix).
  - One-list-check no-op when `distillBlocks` is unset, so existing graphs are unaffected.
- Validator (`config_validator._validate_distill_blocks`) catches missing `fromPath`, non-string label/fromPath, non-int limit, non-list project, and unknown format values at promote time.

**Block shape:**

```yaml
distillBlocks:
  - label: "RECENT TOOL FINDINGS"
    fromPath: "node_4r.json.worknotes"
    limit: 4              # last N entries (recency bias)
    project: ["text"]      # fields to keep per entry
    format: "bullet"       # bullet | numbered | json
```

Renders as a labelled section appended to the system prompt::

    === RECENT TOOL FINDINGS ===
    - agent flagged license expiry
    - queue depth alarm
    - restart succeeded

**What's NOT in v1 (deferred).**
- Separate-cacheable-user-message variant. The plan called for distill blocks as a cacheable user message; v1 ships them appended to the system prompt (which is cacheable in our existing prefix-cache setup). Splitting into a separate user message would let the static system prompt stay byte-stable while distill content varies — useful when distill blocks change per-turn AND the rest of the system prompt is large. Worth revisiting once a workflow demonstrates the cost.

**Tests (1302 passed, 1 skipped after this slice — was 1262).**
- New `tests/test_distill.py` (40 tests):
  - `walk_dotted_path` — simple, nested, missing root, missing attr, walk-through-scalar, list-index, out-of-range, invalid-index, empty path, non-dict context.
  - `render_one_block` — bullet default format, last-N when limit exceeded, numbered, JSON, multi-field project returns dict, empty path returns empty string, no label omits header, long string truncated, hard-limit ceiling, limit=0 means all, scalar value renders as single item.
  - `render_distill_blocks` — empty input no-op, multiple blocks combined with blank-line separator, empty blocks skipped, one-empty-one-full keeps full only, malformed block skipped silently.
  - `validate_distill_blocks` — None valid, non-list invalid, block must be object, missing/non-string fromPath, non-string label, non-int limit, non-list project, unknown format, clean block passes.
  - V10 pattern smoke (RECENT TOOL FINDINGS + EVIDENCE blocks render correctly from a typical case-store context).
  - Validator integration (valid no warning, invalid warns with node-id + label, missing field skipped).

**Refs.** Anthropic — *"structured note-taking"* as a context-pollution mitigation. The V10 prompt-craft scan that drove the original CTX-MGMT plan called this out explicitly.

### F. Scrub-secrets at write-time — **Shipped 2026-05-01**

**Gap.** `scrub_secrets` only ran on log writes; in-memory context could carry secrets that downstream LLM nodes pulled via Jinja, leaking auth tokens / passwords / API keys into prompts.

**What shipped (branch `ctx-mgmt-f-write-scrub`).**
- Migration `0038`: `tenant_policies.context_secret_scrub_enabled BOOLEAN NOT NULL DEFAULT TRUE`. Default-on (close-leak by default); tenants needing un-scrubbed in-memory context can opt out.
- `dag_runner.execute_graph` resolves the flag once at run start and stamps `_runtime["context_secret_scrub_enabled"]` (which survives suspend/resume via CTX-MGMT.D's hoist).
- `dag_runner._execute_single_node` (sequential) and `_apply_result` (parallel) run `scrub_secrets(output)` FIRST in the post-handler pipeline — BEFORE schema validation (CTX-MGMT.I), overflow check (CTX-MGMT.A), reducer (CTX-MGMT.L), alias (CTX-MGMT.C), trace (CTX-MGMT.H), or compaction (CTX-MGMT.K). So every downstream step (including artifact storage and schema-error annotations) sees the scrubbed value.
- The scrubber itself (`app/engine/scrubber.py`) is the existing key-based redactor that's been on log writes since 0001 — same regex / sensitive-suffix logic. No new pattern set introduced; the engine and the existing log scrubber share one source of truth.
- Pure/functional — original `output` reference unchanged; the in-context value is the scrubbed copy.

**Tests (1316 passed, 1 skipped after this slice — was 1302).**
- New `tests/test_write_scrub.py` (14 tests):
  - Scrubber smoke (api_key / token / nested headers / list walk / non-mutation of input).
  - Runtime flag (default ON, explicit ON/OFF respected, missing-runtime defaults ON).
  - End-to-end post-handler shape (token gets scrubbed; opt-out preserves raw; scrubbed value round-trips through overflow stub so the persisted artifact is also scrubbed; non-dict outputs unchanged).
  - **Pipeline-order regression test** — loads `dag_runner.py` source and asserts the scrub marker comes BEFORE the schema validation marker. Catches future refactors that might silently move scrub below schema validation (which would leak secrets into schema-error annotations).

**Order rationale.** Scrub runs FIRST because:
- Schema validation post-scrub reports field-shape errors on scrubbed values (`[REDACTED]` is still a string, so most enum/type checks survive); pre-scrub would stamp raw secrets into `_schema_errors`.
- Overflow artifacts persist the scrubbed value, so `inspect_node_artifact` callers don't see secrets.
- Trace records carry scrubbed-value `size_bytes`, not raw.
- Compaction stub previews use canonical scalar keys from the scrubbed value.

**Refs.** Anthropic — *"context engineering is the set of strategies for curating and maintaining the optimal set of tokens"*. Removing leaked secrets from agent prompts is part of curation.

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
| 2026-05-01 | **P1 stack merged into core** via fast-forward of `ctx-mgmt-c-scope` (linear stack of B/G/E/C). Backend suite 1229 passed on merged core. |
| 2026-05-01 | **CTX-MGMT.F shipped** on branch `ctx-mgmt-f-write-scrub` (stacked on `ctx-mgmt-j-distill`). Migration 0038: `tenant_policies.context_secret_scrub_enabled` DEFAULT TRUE. Engine wires `scrub_secrets(output)` as the FIRST post-handler step in `_execute_single_node` (both sequential + parallel paths), before schema / overflow / reducer / alias / trace / compaction so every downstream step sees the scrubbed value (including persisted artifacts). Reuses the existing key-based scrubber from `app/engine/scrubber.py` — no new pattern set. 14 new unit tests including a source-inspection guard against future ordering regressions; 1316 backend tests pass (was 1302). |
| 2026-05-01 | **CTX-MGMT.J shipped** on branch `ctx-mgmt-j-distill` (stacked on `ctx-mgmt-i-output-schema`). New `app/engine/distill.py` with `walk_dotted_path` + `render_one_block` + `render_distill_blocks` + `validate_distill_blocks`. Three formats: bullet (default), numbered, json. Wired into LLM Agent (`_handle_agent`) and ReAct (`run_react_loop`) — appends rendered blocks to the system prompt after `render_prompt`. Validator catches missing fromPath / unknown format / non-int limit at promote time. Generalises V10's manual `RECENT TOOL FINDINGS` Jinja-block pattern into one-line config. 40 new unit tests; 1302 backend tests pass (was 1262). Separate-cacheable-user-message variant deferred (current version appends to system prompt, which is already cache-friendly under our prefix-cache setup). |
| 2026-05-01 | **CTX-MGMT.I shipped** on branch `ctx-mgmt-i-output-schema` (off merged core). New `app/engine/output_schema.py` with `validate_node_output` (uses jsonschema.Draft202012Validator) + `schema_paths` + `schema_allows_path` + `annotate_output_with_validation`. Wired into `_execute_single_node` on both paths — validates handler output against `data.config.outputSchema` post-dispatch; soft-annotates failures with `_schema_mismatch` by default, raises `OutputSchemaError` when `outputSchemaStrict: true`. Validator catches malformed JSON Schemas at promote time. Lint extension `jinja_ref_path_not_in_schema` (warn) catches Jinja refs to fields outside the declared schema's properties. Latent fix in `extract_node_refs` — now accepts `aliases` set so alias-based refs (`{{ case.foo }}`) are captured (pre-fix, the dangling-ref lint silently missed them; some prior tests were vacuously passing). 33 new unit tests; 1262 backend tests pass (was 1229). |
| 2026-05-01 | **CTX-MGMT.C v1 shipped** on branch `ctx-mgmt-c-scope` (stacked on `ctx-mgmt-e-coalesce`). All four P1 items now done. v1 ships exposeAs aliasing — the engine writes `context[<exposeAs_value>] = context[node_id]` after each write, so downstream Jinja can read `{{ case.id }}` instead of `{{ node_4r.json.id }}`. Validator `_validate_expose_as_collisions` catches three collision classes (alias matches another node id; two nodes share alias; alias matches own id). `lint_jinja_dangling_reference` extended to (a) accept exposeAs aliases as valid reference targets, (b) warn `jinja_ref_outside_depends_on` when a node has `dependsOn` set and a Jinja ref targets something outside that list. `dependsOn` is informational at runtime in v1 — runtime context-filtering (build per-node read-scope) deferred to v2 because it touches every render site (Jinja, safe_eval, ReAct system prompts, HTTP body / url / headers). 16 new unit tests; 1229 backend tests pass (was 1213). |
| 2026-05-01 | **CTX-MGMT.E shipped** on branch `ctx-mgmt-e-coalesce` (stacked on `ctx-mgmt-g-iterations-split`). Implementation note: rather than adding a new `Coalesce` node, wired the missing `waitAny` semantics into the existing `Merge` node (registry entry has been there since 0001, but the engine ignored the strategy field). New `_is_waitany_merge` helper + `_find_ready_nodes` branch in `dag_runner.py` — fires when ANY active source is satisfied (vs default waitAll). `_handle_logic` Merge handler outputs `{merged, value, from, strategy}` for waitAny so downstream Jinja can read `{{ node_merge.value }}`. `_execute_sub_workflow` propagates `child._runtime.shared_evidence` to `parent._runtime.shared_evidence` via append-merge. New SMART-04 lint `lint_unreachable_node_after_switch` catches the V9-pre-fix bug shape — fan-in node where two incoming edges trace to different arms of the same Switch/Condition ancestor; Coalesce/non-Merge-style nodes flagged with fix-hint pointing at Merge waitAny. 23 new unit tests; 1213 backend tests pass (was 1190). |
| 2026-05-01 | **CTX-MGMT.G shipped** on branch `ctx-mgmt-g-iterations-split` (stacked on `ctx-mgmt-b-jinja-lint`). New helpers `_summarize_iterations` + `_finalize_iterations_payload` in `react_loop.py` build a safe public summary (action + tool names only — no args, no results, no reasoning content) and emit `iterations_full` (scrubbed via `scrub_secrets`) only when `exposeFullIterations: True` on the node config. Updated 4 return-shape sites in the ReAct loop (timeout / llm_error / final_response / max_iterations_exceeded). `predicates._all_tool_calls` reads `iterations_full` first, falls back to `iterations` — backward-compat with legacy-shaped contexts AND forward-compat with future predicates that may inspect args. 16 new unit tests; 1190 backend tests pass (was 1174). Bridge / user-facing `response` field unchanged. |
| 2026-05-01 | **CTX-MGMT.B shipped** on branch `ctx-mgmt-b-jinja-lint` (off merged `core`). New `app/copilot/jinja_refs.py` with regex-based `extract_node_refs` covering Jinja + safe_eval in one pass. New SMART-04 lint `lint_jinja_dangling_reference` emits `jinja_dangling_node_ref` (error — ref to non-existent node id, would render to empty string silently at runtime) and `jinja_node_self_ref` (warn — node reads its own slot which is empty at handler-run time). Wired into `run_lints`. 22 new unit tests; 1174 backend tests pass (was 1152). Reachability + field-existence checks deferred to v2. |
| 2026-05-01 | **CTX-MGMT.K shipped** on branch `ctx-mgmt-k-compaction` (stacked on `ctx-mgmt-h-context-trace`). All four P0 items now done. Migration 0037: `tenant_policies.context_compaction_enabled` (DEFAULT TRUE — opposite of trace flag's default-off). New `app/engine/compaction.py` with running-size approximation tracker, oldest-first candidate selection, and `maybe_compact` end-to-end pass that reuses CTX-MGMT.A's `node_output_artifacts` table for storage. Stub shape upgrade — `materialize_overflow_stub` accepts `kind: "overflow"|"compaction"` and now spreads canonical scalar keys to top level so `{{ node_X.id }}` renders the same pre/post-stub. Wired into `dag_runner._execute_single_node` on both paths. Selection signal is write-age (oldest-first) until CTX-MGMT.H v2 adds read-recency. 32 new unit tests; 1152 backend tests pass (was 1120). Per-tenant opt-out for strict audit-trail replay; full output remains in artifacts either way. |
| 2026-05-01 | **CTX-MGMT.H shipped** (v1: writes only) on branch `ctx-mgmt-h-context-trace` (stacked on `ctx-mgmt-l-reducers`). New `instance_context_trace` table (migration 0036) + `tenant_policies.context_trace_enabled` flag. `app/engine/context_trace.py` with fast-path no-op helpers. Wired into `dag_runner._execute_single_node` on both paths so every context write records `{instance_id, node_id, op, key, size_bytes, reducer, overflowed, ts}` when tracing is on. Ephemeral (copilot-initiated) instances always trace; production opts in via the new tenant policy. New copilot runner tool `inspect_context_flow(instance_id, key?)` with exact-match + prefix (`node_*`) filtering. Per-instance cap of 500 events with batched 50-row trim. 19 new unit tests; 1120 backend tests pass (was 1101). Read-event + miss-event tracking deferred to v2 — volume is template-dependent and the missing-key story is better served by `lint_jinja_dangling_reference` (CTX-MGMT.B) at promote time. |
| 2026-05-01 | **CTX-MGMT.L shipped** on branch `ctx-mgmt-l-reducers` (stacked on `ctx-mgmt-a-overflow`). New `app/engine/reducers.py` with `KNOWN_REDUCERS` registry (6 entries: `overwrite`/`append`/`merge`/`max`/`min`/`counter`) + pure helpers `resolve_reducer` and `apply_reducer`. Wired into `dag_runner._execute_single_node` (sequential) and `_apply_result` (parallel-branch) — fires AFTER the overflow check so the overflow stub composes correctly with reducers. `app/engine/config_validator.py` gains `_validate_output_reducer` (rejects unknown names) and `_validate_output_budget` (rejects non-positive budgets) at promote time. Default `overwrite` keeps every existing graph identical; new reducers unlock parallel-branch aggregation, audit trails, counters, max/min trackers without ad-hoc handler code. 44 new unit tests; full backend suite 1101 passed (was 1057). Deferred sub-task: refactoring ForEach + Loop body aggregation to use reducers — invasive, current semantics work, current reducers already add value for new patterns. |
