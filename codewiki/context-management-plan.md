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
| **M** | Forgetting / decay (run-end pruning + checkpoint TTL) | P3 | **Shipped** (branch `ctx-mgmt-m-forgetting`) | — | 2026-05-01 |
| **M2** | Beat-task wiring for `prune_aged_checkpoints` (daily sweep) | P3 | **Shipped** (branch `ctx-mgmt-m2-beat-prune`) | — | 2026-05-01 |
| **J v2** | distillBlocks ride per-turn user message (cache-stable system prompt) | P2 | **Shipped** (branch `ctx-mgmt-j2-distill-cache`) | — | 2026-05-01 |
| **H v2** | Context-trace reads + misses (sampled reads, every miss recorded) | P2 | **Shipped** (branch `ctx-mgmt-h2-context-trace-reads`) | — | 2026-05-01 |
| **C v2.a** | Runtime `dependsOn` enforcement — Jinja-only filter | P1 | **Shipped** (branch `ctx-mgmt-c2a-jinja-scope`) | — | 2026-05-01 |
| **C v2.c** | Runtime `dependsOn` enforcement — structured user-message bundle | P1 | **Shipped** (branch `ctx-mgmt-c2c-user-msg-scope`) | — | 2026-05-01 |

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

### C v2.a. Runtime `dependsOn` enforcement — Jinja-only filter — **Shipped 2026-05-01**

**Gap.** v1 made `dependsOn` informational; the static lint warned at promote time but the runtime still handed every node the full `context` dict. So a stale Jinja ref to a node not in `dependsOn` rendered to its actual value at runtime instead of empty string — the declaration drifted out of sync with what the template actually consumed and nobody noticed until reading the rendered prompt.

C v2 was originally scoped as one mega-slice covering Jinja, `safe_eval`, and the structured user-message block. Splitting into three independent phases keeps the per-PR review surface manageable; **C v2.a is the Jinja-only filter** (highest leverage, lowest risk, ~250 LOC). v2.b (safe_eval) and v2.c (structured context block) are still on the backlog.

**What shipped (branch `ctx-mgmt-c2a-jinja-scope`).**
- New `app/engine/scope.py` with pure helpers:
  - `get_depends_on(node_data)` — extracts and normalises the `dependsOn` list, handles both shapes (`{config: ...}` handler input and `{data: {config: ...}}` graph definition), strips whitespace, drops non-string entries. Returns `None` when the field is unset (so the filter is opt-in); returns `[]` when explicitly empty (meaningful — author declared "no upstream node visible").
  - `collect_alias_index(nodes_map)` — walks the graph to build `{alias: source_node_id}` for every declared `exposeAs`. The filter uses this to (a) keep aliases of declared deps and (b) drop aliases of un-declared nodes (which would otherwise leak through as non-`node_*` keys).
  - `build_scoped_safe_context(context, node_data, nodes_map)` — the filter itself. Returns `context` unchanged when `dependsOn` is unset (hot path, identity-preserving); otherwise returns a new dict containing the declared `node_*` slots, their aliases, and all true infrastructure keys (`trigger`, `_runtime`, `_loop_*`, conv-memory, `approval`, etc.).
  - `set_current_node_data` / `get_current_node_data` / `clear_current_node_data` — thread-local stash so the runner can inform `render_prompt` about the dispatching node without each handler having to thread `node_data` through. **Thread-local, not context-dict** because the parallel-branch executor runs sibling dispatches concurrently against the same `context`; a shared-dict stash would race.
- `prompt_template.render_prompt(template, context, *, node_data=None, nodes_map=None)` gains the kwargs. Defaults: `node_data` falls through to `scope.get_current_node_data()` (thread-local), `nodes_map` falls through to `context["_engine_nodes_map"]` (set once at execute_graph entry, read-only after). When `dependsOn` is set, the helper builds a filtered safe_context BEFORE Jinja's namespace is constructed — so the existing permissive-undefined behavior turns un-scoped refs into empty strings, exactly matching the lint's runtime prediction.
- `dag_runner.execute_graph` (4 entry points: fresh start, HITL resume, pause-resume, retry-from-failure) stashes `nodes_map` on `context["_engine_nodes_map"]` once. The key starts with `_` so `_get_clean_context` strips it before any DB persistence; never crosses the suspend/resume boundary in `instance.context_json`.
- `_execute_single_node` (sequential) and the parallel-branch worker (`_apply_result`'s thread function) both call `scope.set_current_node_data(node_data)` immediately before `dispatch_node`. Sibling parallel branches each set their own value on their own thread — no cross-branch race.

**Backward compatibility.** Graphs that don't declare `dependsOn` see no behavior change — `build_scoped_safe_context` returns `context` unchanged via an `is` identity. Even graphs that DO declare `dependsOn` are protected by the existing permissive-undefined Jinja policy: an out-of-scope ref renders to empty string instead of raising. This matches what the lint already predicted, so any breakage at runtime would have been a lint warning at promote time.

**Edge cases handled.**
- Empty `dependsOn: []` — meaningful; filters to infrastructure-only.
- Aliases of un-declared nodes — known via `nodes_map` walk, dropped explicitly even though they don't start with `node_`.
- Truly unknown non-`node_*` keys (HITL `approval`, conv-memory keys we haven't seen) — treated as infrastructure (kept).
- Sub-workflow children — they have their own `dependsOn` declared independently; parent's scope doesn't propagate.
- ReAct iterations within one turn — the same `node_data` is in scope for the entire turn; the thread-local stash isn't invalidated mid-turn.

**Tests (1404 passed, 1 skipped after this slice — was 1369).**
- New `tests/test_scope.py` (35 tests):
  - `get_depends_on` — both node shapes, malformed values, empty list, whitespace stripping, non-string filtering.
  - `collect_alias_index` — empty input, both node shapes, alias whitespace stripping, skip non-string aliases.
  - `build_scoped_safe_context` — no-filter identity when unset, filter keeps declared deps + their aliases, drops aliases of un-declared nodes, empty list drops all node slots, returns new dict (not alias) when filtering, unknown non-node keys treated as infrastructure.
  - Thread-local stash — initial `None`, set+get round-trip, no leak between threads (verified via `threading.Thread`).
  - `render_prompt` integration — full context when no dependsOn, undeclared ref renders empty when dependsOn is set, declared alias resolves, undeclared alias renders empty, thread-local stash picked up when no kwargs, explicit kwargs override thread-local, infrastructure keys always visible, empty dependsOn drops all node slots.
  - `is_scope_enforced` — false when unset, true with declared list, true with empty list.

**What's NOT in v2.a (deferred to v2.b).**
- `safe_eval` scope filter. 12 call sites in `app/engine/` (Condition, Loop, Set Variables, predicate matching, Intent Classifier, Entity Extractor, While `continueExpression`). Each builds its `eval_env` slightly differently; uniform filter helper TBD. Risk medium because Condition expressions return concrete values that other branches depend on.
- ~~`build_structured_context_block` filter~~ — **shipped as C v2.c below (2026-05-01)**.
- Tenant-policy escape hatch (`tenant_policies.context_scope_enforced`). Not strictly needed because the filter is already opt-in via `dependsOn`. Useful only if a tenant's existing graph carelessly declared `dependsOn` and relied on out-of-list visibility — the lint would have warned, but a hard runtime-toggle adds insurance. Defer until evidence of need.

**Refs.** Anthropic — *"each agent operates with scoped instructions and context"*. The lint catches authoring drift; the runtime filter makes the scope declaration load-bearing instead of decorative.

### C v2.c. Runtime `dependsOn` enforcement — structured user-message bundle — **Shipped 2026-05-01**

**Gap.** v2.a filtered the **system prompt** Jinja namespace but the **per-turn user message** bundle (built by `build_structured_context_block` and emitted by `assemble_agent_messages`) still iterated `context.items()` and dumped every `node_*` slot as JSON. So a worker node that declared `dependsOn=["node_a"]` had a perfectly-scoped system prompt but its user message still carried the full node_b/node_c/etc. payloads. Half-enforcement leaks state through the user surface.

**What shipped (branch `ctx-mgmt-c2c-user-msg-scope`).**
- `prompt_template.build_structured_context_block(...)` gains `node_data: dict | None = None` and `nodes_map: dict | None = None` kwargs (matching `render_prompt`'s contract).
- Both kwargs fall through to the runner's per-thread stash (`scope.get_current_node_data()`) and `context["_engine_nodes_map"]` respectively. Same convention as v2.a — handlers don't need to thread anything through; the runner's existing `set_current_node_data` call before each `dispatch_node` already covers the needed plumbing.
- When `dependsOn` is declared, the `for key, value in context.items()` loop skips `node_*` keys not in the visible set. Visible set is `set(deps) | resolved_alias_sources` (so `dependsOn: ["case"]` written against an alias still emits the source node's slot, symmetric with `build_scoped_safe_context`).
- `trigger`, `_loop_item`, and other infrastructure inputs are unconditionally emitted regardless of scope — they're per-turn inputs to the worker, not node outputs.
- The pre-existing `exclude_node_ids` parameter remains independent — both filters apply.

**Coverage matrix after v2.a + v2.c:**

| Surface | Before v2 | After v2.a | After v2.c |
|---|---|---|---|
| System prompt Jinja | full context | scoped | scoped |
| Per-turn user message workflow context | full context | full context | scoped |
| safe_eval expressions | full context | full context | full context (v2.b deferred) |

**Tests (1416 passed, 1 skipped after this slice — was 1404).**
- New `tests/test_user_msg_scope.py` (12 tests):
  - `build_structured_context_block` — no dependsOn emits all nodes; explicit `node_data` kwarg filters; empty list drops all nodes (keeps trigger); thread-local stash picked up; explicit kwarg overrides thread-local; loop_item always emitted; `exclude_node_ids` still works alongside scope; alias in dependsOn resolves to source node; backward-compat hot path when dependsOn is unset.
  - `assemble_agent_messages` integration — memory-disabled path filters the user message; memory-enabled path filters the "Workflow context:" section of the final user message; no dependsOn emits everything.

**What's still NOT in C v2 (deferred to v2.b).**
- `safe_eval` scope filter — see v2.a's deferred list. v2.b is the last remaining piece of the C v2 family.

**Refs.** Same as v2.a. Half-enforcement (only-system-prompt) leaks scope through the user surface; v2.c closes that gap.

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
- ~~Separate-cacheable-user-message variant.~~ — **shipped as J v2 below (2026-05-01).** v1's append-to-system-prompt design busted prefix caches the moment distill content drifted; v2 moves distill onto the per-turn user message so the system prompt stays cache-stable.

**Tests (1302 passed, 1 skipped after this slice — was 1262).**
- New `tests/test_distill.py` (40 tests):
  - `walk_dotted_path` — simple, nested, missing root, missing attr, walk-through-scalar, list-index, out-of-range, invalid-index, empty path, non-dict context.
  - `render_one_block` — bullet default format, last-N when limit exceeded, numbered, JSON, multi-field project returns dict, empty path returns empty string, no label omits header, long string truncated, hard-limit ceiling, limit=0 means all, scalar value renders as single item.
  - `render_distill_blocks` — empty input no-op, multiple blocks combined with blank-line separator, empty blocks skipped, one-empty-one-full keeps full only, malformed block skipped silently.
  - `validate_distill_blocks` — None valid, non-list invalid, block must be object, missing/non-string fromPath, non-string label, non-int limit, non-list project, unknown format, clean block passes.
  - V10 pattern smoke (RECENT TOOL FINDINGS + EVIDENCE blocks render correctly from a typical case-store context).
  - Validator integration (valid no warning, invalid warns with node-id + label, missing field skipped).

**Refs.** Anthropic — *"structured note-taking"* as a context-pollution mitigation. The V10 prompt-craft scan that drove the original CTX-MGMT plan called this out explicitly.

### J v2. Cache-stable distill ride-along — **Shipped 2026-05-01**

**Gap.** J v1 appended rendered distill blocks to the system prompt right after `render_prompt`. That worked but broke the provider's prefix cache on every turn whose distill content shifted (recent worknotes, recent findings — by design these change continuously). The system prompt's first ~hundreds of tokens were stable; the trailing distill drifted. Cache hit ratios for high-volume workflows showed the cost.

**What shipped (branch `ctx-mgmt-j2-distill-cache`).**
- `assemble_agent_messages` gains a new `distill_text: str = ""` keyword argument. The default keeps every existing caller working unchanged.
- Memory-disabled path: distill rides on the synthetic user message that's emitted alongside the structured workflow context — system message stays as the static rendered prompt.
- Memory-enabled path: distill is appended to `final_sections` on the per-turn user message, alongside facts / semantic hits / latest user message / workflow context. System message contains only the policy instructions + static rendered prompt.
- `_handle_agent` (LLM agent) and `run_react_loop` (ReAct workers): both stop concatenating `_distill_text` into `system_prompt`; the rendered text now flows through to the new kwarg. ReAct iterations within a single turn re-use the same `initial_messages`, so distill stays stable across iterations of one turn — no per-iteration cache flap either.
- Source-inspection regression test in `test_distill_cache_split.py` guards the wire by asserting both handlers pass `distill_text=_distill_text` and that the pre-v2 `system_prompt + "\n\n" + _distill_text` pattern is gone — a future refactor can't silently undo the cache split.

**Cache stability proof.** A direct test (`TestCacheStability::test_same_system_prompt_across_changing_distill`) calls `assemble_agent_messages` twice with the same `rendered_system_prompt` but completely different `distill_text` payloads and asserts the system message is byte-identical. That's exactly what the prefix cache requires.

**Tests (1345 passed, 1 skipped after this slice — was 1337).**
- New `tests/test_distill_cache_split.py` (8 tests):
  - Memory-disabled path — distill on user, never on system; empty distill omits cleanly; default kwarg keeps old callers valid.
  - Memory-enabled path — distill on final user message alongside latest user message; absent distill renders no marker.
  - Cache stability — system message byte-identical across turns with shifting distill content.
  - Handler wiring guard — source inspection of `node_handlers.py` and `react_loop.py` confirms both pass `distill_text=` and don't append to system prompt.

**Refs.** Anthropic — system prompt cache breakpoint guidance: *"keep dynamic content out of the cached prefix"*. Provider docs (Anthropic / Vertex / OpenAI) all advertise prefix caching that depends on byte-stable prefixes; v1 violated that contract by stamping recent-evidence into the system message.

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

**v2 (deferred — now shipped, see H v2 below).** Read-event tracking and miss-event tracking. v2 ships these with sampling for reads (1-in-10 by default) and no sampling for misses.

**Tests (1120 passed, 1 skipped after this slice — was 1101).**
- New `tests/test_context_trace.py` (19 tests):
  - `is_trace_enabled` (six cases: missing context, missing runtime, flag unset, flag explicit-false, flag explicit-true, malformed runtime).
  - `resolve_trace_flag` (ephemeral always on without consulting policy; prod honors policy on/off; prod stays off on policy lookup error).
  - `record_write` disabled fast-path (zero DB touches).
  - `record_write` enabled path (one row added with all fields, flush called, overflowed flag propagates, never raises on DB error).
  - Trim behavior (cap-check fires every TRACE_TRIM_BATCH writes; doesn't trim when under cap).
  - `fetch_events_for_instance` (serialised event shape, ts isoformat, prefix filter applies LIKE match).

**Refs.** Anthropic — *"context engineering is the set of strategies for curating and maintaining the optimal set of tokens"*. Observability is what enables that curation in practice.

### H v2. Read + miss event tracing — **Shipped 2026-05-01**

**Gap.** v1 only knew where each slot was *written*. The questions "is this slot ever read by anything?" and "which Jinja refs are silently rendering to empty string at runtime?" had no instrumented answer — only static lints. Static lints (CTX-MGMT.B) catch authoring-time typos but miss runtime drift (e.g. a node renamed in a sub-workflow whose templates still ref the old id, when the static lint can't see across sub-workflow boundaries).

**What shipped (branch `ctx-mgmt-h2-context-trace-reads`).**
- `app/engine/context_trace.py` gains three new public helpers: `record_read`, `record_miss`, `flush_render_events`. Same fast-no-op-when-disabled contract as `record_write`. New `_record_event` internal coalesces row construction with cap-check + trim shared between op types.
- `app/engine/prompt_template.py` gains a thread-local `_render_state.pending` capture buffer. `render_prompt` initialises the buffer at entry (only when `_runtime["context_trace_enabled"]` is true), accumulates `(op, key)` tuples during render, then merges them into `_runtime["_pending_render_events"]` in a `finally` block. Thread-local state is cleared after every render — no leakage outside the render scope.
- `_DotDict.__getattr__` records `("read", name)` on success, `("miss", name)` on KeyError. Skips dunder / sentinel names (`_*`) to avoid noise from Jinja's internal probing.
- `_PermissiveUndefined.__init__(name=...)` records `("miss", name)` for top-level undefined name lookups (Jinja constructs `_PermissiveUndefined("unknown_var")` when `{{ unknown_var }}` is rendered against a namespace that doesn't have it).
- `_PermissiveUndefined.__getattr__` and `__getitem__` record the chained-access miss key.
- `dag_runner._execute_single_node` (sequential) and `_apply_result` (parallel) call `flush_render_events(...)` immediately after `record_write(...)`. The flush applies sampling (1-in-N reads, every miss) and dedupe (consecutive identical events from one render → one row).

**Sampling.** `DEFAULT_READ_SAMPLE_N = 10`. Configurable via `_runtime["context_trace_read_sample_n"]` (operator can stamp a different value; future tenant policy hook is straightforward). The sample counter persists across flushes within a run, so we don't double-count when the same key shows up in multiple renders. Explicit 0 or negative is treated as "no sampling, emit every read"; missing / non-numeric falls through to the default. Misses are NEVER sampled — they're high-signal and low-volume by construction.

**Volume.** A typical 28-node V10-shape graph does ~5 renders/node × 28 nodes = 140 attribute reads per turn. At 1/10 sampling that's ~14 read events per turn. The 500-cap stays comfortable across multi-turn conversations even with full read tracing on.

**Tests (1369 passed, 1 skipped after this slice — was 1345).**
- New `tests/test_context_trace_reads.py` (24 tests):
  - Disabled fast-path — `record_read` / `record_miss` touch zero DB when `_runtime` is missing or flag is off.
  - Enabled path — both helpers emit rows with correct `op`. Neither raises on DB error.
  - `flush_render_events` — no-op without runtime, no-op without pending events, drains buffer when disabled, every miss emitted, reads sampled 1-in-N, dedupe collapses consecutive identical events, invalid event shapes skipped, sample counter persists across flushes.
  - `render_prompt` capture — no capture when tracing disabled, no capture when runtime missing, top-level read captured, top-level miss captured, attr miss on existing dict captured, chained miss on undefined captured, multiple renders accumulate, thread-local cleared after render (verified via direct state inspection).
  - Sample-N edge cases — default constant is positive, explicit 0/negative falls through to "emit every read".

**Refs.** Anthropic — *"observability is the prerequisite for ongoing context curation"*. The runtime trace + the static lint together form a closed-loop authoring story (lint catches obvious bugs at promote time; trace surfaces drift bugs that the lint can't see).

### M. Forgetting / decay — **Shipped 2026-05-01**

**Gap.** Context grew unbounded within a run; checkpoints accumulated forever — no time-based decay or explicit deletion path.

**What shipped (branch `ctx-mgmt-m-forgetting`).**
- Migration `0039`: `tenant_policies.checkpoint_retention_days INTEGER NULL` — null falls through to `SYSTEM_DEFAULT_RETENTION_DAYS` (30). Tenants with cost or compliance constraints can override.
- New `app/engine/forgetting.py`:
  - `clear_non_retained_outputs(context, nodes_map)` — pure helper. Walks every node; for any whose config sets `retainOutputAfterRun: False`, pops the slot from `context`. Also clears the corresponding `exposeAs` alias slot if present (otherwise the alias would carry a ghost value into context_json). Returns the list of cleared keys for observability.
  - `resolve_checkpoint_retention_days(db, tenant_id)` — reads tenant policy with defensive fall-through to `SYSTEM_DEFAULT_RETENTION_DAYS` on lookup error, zero, or negative values (never delete everything by accident).
  - `prune_aged_checkpoints(db, *, tenant_id=None, older_than_days=None)` — operator utility for retention sweep. Same shape as `cleanup_ephemeral_workflows`. When tenant_id is set, joins through `WorkflowInstance` for tenant filtering and uses the per-tenant retention policy. Refuses to delete when threshold ≤ 0.
- `dag_runner.execute_graph` calls `clear_non_retained_outputs` at end-of-run, AFTER the ready-queue completes but BEFORE the trace's final output stamp. No-op for graphs that don't use the flag (default behavior unchanged).

**Per-node config:**
- `data.config.retainOutputAfterRun: bool` (default True). Set to False when a node's output is only meant for the next downstream node — the slot is cleared at end-of-run before context_json persists. Useful for transient HTTP responses that downstream LLMs summarised, after which the raw response is dead weight in the persisted context.

**What's NOT in v1 (deferred).**
- ~~Beat-task wiring for `prune_aged_checkpoints`~~ — **shipped as M2 below (2026-05-01)**.
- Per-node retention TTL (different node types decaying at different rates). Boolean retain/clear is enough for v1; finer-grained TTL on top of that is additive.

**Tests (1332 passed, 1 skipped after this slice — was 1316).**
- New `tests/test_forgetting.py` (16 tests):
  - `clear_non_retained_outputs` (default retains everything, explicit True retains, explicit False clears, alias also cleared, skip when slot absent, mixed retain-and-clear).
  - `resolve_checkpoint_retention_days` (default when unset, explicit override, zero/negative fall through to default, lookup-error falls through).
  - `prune_aged_checkpoints` (zero/negative threshold refuses to delete, no-rows no commit, system default when no args, tenant_id uses tenant policy retention via JOIN).

**Refs.** Agent-memory literature — *"time-based decay, relevance scoring, explicit deletion"* are part of the design, not afterthoughts. Anthropic — context engineering as ongoing curation, not just initial assembly.

### M2. Beat-task wiring for `prune_aged_checkpoints` — **Shipped 2026-05-01**

**Gap.** M v1 shipped `prune_aged_checkpoints` as an operator-callable utility but no Beat task invoked it on a schedule. Without scheduled invocation the retention policy decayed nothing on its own — the function had to be called by hand.

**What shipped (branch `ctx-mgmt-m2-beat-prune`).**
- `app/workers/scheduler.py`: new Beat schedule entry `"prune-aged-checkpoints"` running daily at `crontab(hour=4, minute=0)`. Slot picked to avoid colliding with the 03:00 (`prune-old-snapshots`) and 03:30 (`prune-old-scheduled-triggers`) tasks.
- New Celery task `prune_aged_checkpoints_task` (`orchestrator.prune_aged_checkpoints`):
  - Enumerates every tenant with at least one checkpoint via `db.query(distinct(WorkflowInstance.tenant_id)).join(InstanceCheckpoint, ...)`. Cross-tenant by design (Beat already runs as a BYPASSRLS role per scheduler.py module docstring).
  - Calls `forgetting.prune_aged_checkpoints(db, tenant_id=tenant_id)` per tenant so each tenant's `checkpoint_retention_days` policy is honored independently.
  - Per-tenant exception handling: a single bad tenant policy lookup or transient DB hiccup logs a warning, rolls back, and continues with the rest. The daily sweep is never aborted by one tenant.
  - Top-level enumeration failure (rare — DB unavailable) is also caught + logged; task exits cleanly without raising. Beat will retry on the next tick.
  - Aggregate log line `"Daily checkpoint sweep: deleted N checkpoint(s) across M tenant(s) (failures: F)"` only emitted when there's actually something to report.

**Tests (1337 passed, 1 skipped after this slice — was 1332).**
- New `tests/test_prune_checkpoints_beat.py` (5 tests):
  - `TestBeatSchedule::test_prune_aged_checkpoints_in_schedule` — confirms the entry is registered with the expected task name and a `crontab` (not interval) schedule.
  - `TestPruneTask::test_sweeps_each_tenant_separately` — three tenants, each gets a distinct `prune_aged_checkpoints(db, tenant_id=…)` call, session closed at the end.
  - `TestPruneTask::test_continues_on_per_tenant_failure` — middle tenant raises; first + last still get swept; `db.rollback` was called.
  - `TestPruneTask::test_no_tenants_no_op` — empty enumeration; `prune_aged_checkpoints` is never called.
  - `TestPruneTask::test_top_level_failure_handled` — `db.query` itself raises; task exits cleanly with rollback + close.

**Refs.** Mirrors the existing pattern in `prune_old_snapshots` and `prune_old_scheduled_triggers` for a consistent operator surface.

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
| 2026-05-01 | **CTX-MGMT.M shipped** on branch `ctx-mgmt-m-forgetting` (stacked on `ctx-mgmt-f-write-scrub`). Migration 0039: `tenant_policies.checkpoint_retention_days INTEGER NULL` (NULL → SYSTEM_DEFAULT_RETENTION_DAYS = 30). New `app/engine/forgetting.py` with `clear_non_retained_outputs` (pops node slots whose config sets `retainOutputAfterRun: False`, also clears alias slots) + `resolve_checkpoint_retention_days` + `prune_aged_checkpoints` operator utility. Wired into `execute_graph` at end-of-run before trace finalize. Beat-task scheduling deferred — function is available as operator-callable today. 16 new unit tests; 1332 backend tests pass (was 1316). |
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
| 2026-05-01 | **CTX-MGMT.M2 shipped** on branch `ctx-mgmt-m2-beat-prune` (off merged core). Beat schedule entry `prune-aged-checkpoints` registered in `app/workers/scheduler.py` at `crontab(hour=4, minute=0)` (avoids the 03:00/03:30 prune slots). New Celery task `orchestrator.prune_aged_checkpoints` enumerates every tenant with checkpoints via `distinct(WorkflowInstance.tenant_id) JOIN InstanceCheckpoint`, calls `forgetting.prune_aged_checkpoints(db, tenant_id=…)` per tenant so each honors its own `checkpoint_retention_days` policy. Per-tenant exception → log+rollback+continue (one bad tenant never breaks the daily sweep); top-level enumeration failure → log+rollback+exit cleanly (Beat retries next tick). 5 new unit tests; 1337 backend tests pass (was 1332). Closes the v2 follow-up flagged in the M section. |
| 2026-05-01 | **CTX-MGMT.J v2 shipped** on branch `ctx-mgmt-j2-distill-cache` (off merged core). `assemble_agent_messages` gains `distill_text: str = ""` kwarg. `_handle_agent` and `run_react_loop` stop appending rendered distill into the system prompt — they now pass it through to the assembler where it lands on the per-turn user message (memory-disabled path: alongside the structured workflow block; memory-enabled path: as a `final_sections` entry alongside facts / semantic hits / latest user message). System message becomes byte-stable across turns regardless of distill content drift, so provider prefix caches (Anthropic / Vertex / OpenAI) actually hit. Source-inspection regression test guards both handler wires against future regressions. 8 new unit tests including a direct cache-stability proof; 1345 backend tests pass (was 1337). Closes the v2 follow-up flagged in J's "what's NOT in v1" list. |
| 2026-05-01 | **CTX-MGMT.H v2 shipped** on branch `ctx-mgmt-h2-context-trace-reads` (off merged core). `context_trace.py` gains `record_read`, `record_miss`, `flush_render_events` helpers (same fast-no-op contract as v1's `record_write`). `prompt_template.py` adds a thread-local capture buffer wired into `_DotDict.__getattr__` (read/miss on dict lookup), `_PermissiveUndefined.__init__` (top-level undefined name miss), and `_PermissiveUndefined.__getattr__`/`__getitem__` (chained miss); `render_prompt` accumulates events on `_runtime["_pending_render_events"]` only when tracing is on. `dag_runner._execute_single_node` (sequential) and `_apply_result` (parallel) call `flush_render_events` after `record_write`, applying 1-in-N read sampling (DEFAULT_READ_SAMPLE_N=10, tunable via `_runtime["context_trace_read_sample_n"]`) and consecutive-event dedupe. Misses are never sampled. Sample counter persists across flushes within a run. 24 new unit tests; 1369 backend tests pass (was 1345). Closes the v2 follow-up flagged in H v1's "what's NOT in v1" note. |
| 2026-05-01 | **CTX-MGMT.C v2.a shipped** on branch `ctx-mgmt-c2a-jinja-scope` (off merged core). First slice of C v2 — Jinja-only runtime enforcement of `dependsOn`. New `app/engine/scope.py` with pure helpers (`get_depends_on`, `collect_alias_index`, `build_scoped_safe_context`) plus thread-local current-node stash (`set_current_node_data`/`get_current_node_data`/`clear_current_node_data`). `prompt_template.render_prompt` gains `node_data` and `nodes_map` kwargs that fall through to the thread-local + `context["_engine_nodes_map"]` respectively. `dag_runner.execute_graph` stashes `nodes_map` once at every entry point (fresh start, HITL resume, pause-resume, retry); `_execute_single_node` and the parallel-branch worker both call `scope.set_current_node_data` before `dispatch_node`. When `dependsOn` is set, `render_prompt` filters its safe_context to the declared deps + their aliases + infrastructure keys before Jinja sees them; un-scoped refs render to empty string via the existing permissive-undefined policy. Unset `dependsOn` returns the same context object unchanged — backward-compatible identity-preserving hot path. v2.b (safe_eval) and v2.c (structured user-message block) remain on the backlog. 35 new unit tests including thread-local-leak verification across siblings; 1404 backend tests pass (was 1369). |
| 2026-05-01 | **CTX-MGMT.C v2.c shipped** on branch `ctx-mgmt-c2c-user-msg-scope` (stacked on `ctx-mgmt-c2a-jinja-scope`). Closes the half-enforcement gap from v2.a — `build_structured_context_block` (the per-turn user-message bundle in `assemble_agent_messages`) now also honors `dependsOn`. New `node_data` and `nodes_map` kwargs with the same fall-through-to-thread-local-stash convention as `render_prompt`. The `for key, value in context.items()` loop skips out-of-scope `node_*` keys; `trigger`, `_loop_item`, and other infrastructure are unconditionally emitted. Aliases in `dependsOn` resolve back to source node ids so the filter is symmetric with `build_scoped_safe_context`. After v2.a + v2.c: system prompt Jinja AND per-turn user message workflow context are both scope-filtered; `safe_eval` (v2.b) is the last remaining surface. 12 new unit tests (helper direct + assemble_agent_messages integration in both memory paths); 1416 backend tests pass (was 1404). |
