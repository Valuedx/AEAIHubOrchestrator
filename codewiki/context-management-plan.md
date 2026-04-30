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
| **A** | Per-node output budget + overflow artifacts | **P0** | Not started | — | — |
| **L** | Reducer-per-channel state model (`outputReducer`) | **P0** | Not started | — | — |
| **K** | Compaction pass within a single workflow run | **P0** | Not started | — | — |
| **B** | `lint_jinja_dangling_reference` static lint | P1 | Not started | — | — |
| **C** | Per-node `dependsOn` / `exposeAs` scope declaration | P1 | Not started | — | — |
| **E** | Native `Coalesce` node + child-evidence promotion channel | P1 | Not started | — | — |
| **G** | ReAct iterations summary vs full split | P1 | Not started | — | — |
| **I** | `outputSchema` on every node (not just trigger) | P2 | Not started | — | — |
| **J** | First-class `distillBlocks` for context distillation | P2 | Not started | — | — |
| **F** | Scrub-secrets at write-time, not just log-time | P2 | Not started | — | — |
| **H** | `context_trace` runtime channel + copilot inspector | P2 | Not started | — | — |
| **M** | Forgetting / decay (run-end pruning + checkpoint TTL) | P3 | Not started | — | — |

**Status vocabulary.** `Not started` → `In design` → `In progress` → `Shipped` → `Verified` (when post-merge eval / soak confirms the fix). A `Walked back` state exists for items the literature contradicts; we removed one (E.original — see §6).

---

## §3. P0 — ship first

### A. Per-node output budget + overflow artifacts

**Gap.** Every node's output stays in `context[node_id]` for the entire run AND gets persisted to DB on every node completion via `_save_checkpoint`. ReAct nodes write `iterations: [{tool_calls, tool_results, content}, ...]` — easily 10–50 kB per ReAct node; a 28-node V10-shape workflow can produce a `context_json` of several hundred kB. This is the #1 production-failure category per LangChain's report.

**Edge cases.** ForEach over 100 items writes per-downstream-node lists at peak. Sub-workflow `output_node_ids` merge into parent context. HTTP node response bodies stored verbatim. `InstanceCheckpoint` writes a full snapshot per node — 28 full copies for a 28-node graph.

**Plan.**
- Add `data.config.contextOutputBudget: int` (default 64 kB) on every node type.
- When a node's output exceeds budget, store `context[node_id] = {"_overflow": True, "summary": "<auto-generated>", "_artifact_id": "<uuid>"}`. Full output goes to a new `node_output_artifacts` table (RLS-tenant-scoped, FK to `instance_id`).
- New copilot tool `inspect_node_artifact(instance_id, node_id)` for retrieving the overflow blob.
- `_save_checkpoint` writes deltas (changed `node_*` keys only) instead of full snapshots; reconstruction walks checkpoints in order.
- Migration: new `node_output_artifacts` table.

**Tests.** Round-trip with an output that exceeds budget; verify `context[node_id]` carries the summary; verify the artifact row is fetchable; verify downstream Jinja `{{ node_X.summary }}` renders; verify `{{ node_X._artifact_id }}` is also accessible.

**Eval.** Re-run the V10 eval suite (28 nodes) with the budget enforced; compare context_json sizes pre/post. Target: <80 kB per turn (was 250–400 kB).

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

### K. Compaction pass within a single workflow run

**Gap.** We run rolling summarization on cross-session memory (`refresh_rolling_summary`) but NOT on within-run context. A 28-node workflow accumulates every node's full output for the whole run; nothing summarizes the early nodes once they're 20+ hops back.

**Edge cases.** Long-running graphs that loop through many iterations (cyclic graph support already shipped) compound the problem — each cycle adds another snapshot to checkpoints.

**Plan.**
- When `len(context_json_serialized) > COMPACTION_THRESHOLD` (default 64 kB), run an async compaction pass.
- Replace `context[node_X]` with `{_compacted: True, summary: "<auto>", _artifact_id: "<uuid>"}` for nodes that haven't been read in the last K downstream node executions (read-tracking from item H).
- Compaction summary preserves IDs, status fields, top-level keys most likely to be referenced via Jinja.
- Same artifact storage as A.
- Per-tenant policy `tenant_policies.context_compaction_enabled` (default True; off for tenants with strict audit-trail requirements who need full-fidelity replay).

**Tests.** Construct a 50-node synthetic workflow whose cumulative outputs exceed 64 kB; verify compaction fires; verify oldest-not-recently-read nodes are compacted first; verify summaries are reachable for downstream Jinja; verify the artifact row carries the full state.

**Refs.** Anthropic — *"Compaction is the practice of taking a conversation nearing the context window limit, summarizing its contents, and reinitiating a new context window with the summary. Compaction typically serves as the first lever in context engineering"*.

### L. Reducer-per-channel state model

**Gap.** Our context only does last-write-wins per node id. This breaks down for parallel-branch aggregation, append-only audit trails, and counters. Today `_cycle_iterations` is hand-coded; would be a one-line `Counter` reducer.

**Edge cases.** Multiple parallel branches each write evidence — they overwrite each other today. ForEach aggregates via post-loop merge logic; could be a uniform `append` reducer.

**Plan.**
- Add `data.config.outputReducer: "overwrite" | "append" | "merge" | "max" | "counter"` per node. Default `overwrite` (current behavior).
- Engine applies the reducer when writing `context[node_id]`. For `append`, the value is `context[node_id] + [output]` (auto-init to list). For `merge`, dict.update semantics. For `counter`, integer accumulation.
- Validator checks: a node downstream of a parallel-branch with `append` reducer must expect `list[...]`, not the latest single output. Cross-references with `outputSchema` (item I) when both are set.
- `Coalesce` node (item E) leverages this — its output is an `append`-reduced list of upstream outputs.

**Tests.** Two parallel HTTP nodes write to a downstream LLM via `append` reducer; verify both outputs appear in `context[downstream]`. ForEach replaced with append-reducer pattern; verify equivalent results.

**Refs.** LangGraph — *"In LangGraph, key is operator.add, which tells LangGraph to append new messages to the existing list instead of overwriting"*. LangChain *State of Agent Engineering 2026*.

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

### H. `context_trace` runtime channel

**Gap.** No introspection — when context is unexpectedly empty, debugging means eyeballing `context_json`.

**Plan.** Optional `instance_context_trace` table recording every `{node_id, op: write|read|miss, key, size_bytes, ts}`. Copilot tool `inspect_context_flow(instance_id, key)` answers "where was X set / where was it read / where did rendering miss?".

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
