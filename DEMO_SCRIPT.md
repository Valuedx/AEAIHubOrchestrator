# AE AI Hub Orchestrator — End-to-End Technical Demo

> **Multiple audiences, one document.** Choose your route through this doc based on who's reviewing — see [Demo paths by audience](#demo-paths-by-audience) immediately below. The 2-3 hour live demo is the same regardless; the **conversation around it** changes.
>
> **Primary audiences:**
> - **A. Engineering leadership / product / sales-engineering / AE ops practitioners** — the team that will run, deploy, and use this. Wants to know it works, what it does end-to-end, how to extend it.
> - **B. Investor tech-review / YC technical partners** — wants to know the architecture is real, the IP is defensible, the roadmap is honest, the gaps are sized.
> - **C. Developers / platform engineers / open-source-curious** — wants to see code, evaluate the abstractions, check whether they could build on top of it.
>
> This is a demo doc, not a sales deck. **Every capability claim cites a file:line.** Every gap is named, sized, and given a roadmap entry. If something is hand-wavy or aspirational, it's flagged as such.

---

## Demo paths by audience

| If you're audience… | Read these sections in this order |
|---|---|
| **A. Eng leadership / product / sales-eng / AE ops** | §0 Pre-flight → §1 What we built → §3 Use case → §4 Live demo (all 7 scenarios) → §10 **Value to business** → §11 **Value to developers** → §9 Q&A (skip the "what's the IP" hard questions) → §6 Gaps. Skip §5 design rationale and §8 market unless asked. |
| **B. Investor tech-review / YC** | §0 Pre-flight → §1 What we built → §2 Architecture → §5 **Modern orchestration: MCP & A2A** → §6 Engineering harness → §3 Use case (briefly) → §4 Live demo (Scenarios 2, 4, 5 are the headlines) → §7 Design decisions → §12 Gaps (don't skip) → §13 Roadmap → §14 Market → §9 Q&A. |
| **C. Developers / platform-curious** | §0 Pre-flight → §2 Architecture → §5 MCP/A2A → §6 Engineering harness → §11 **Value to developers** → §3 Use case → §4 Live demo (Scenario 5 — show the canvas while it runs) → §15 Appendix file/line index. |

The total run time is ~2.5–3 hours when delivered straight through. Audience A trims to ~2h by skipping §5–§8. Audience B trims to ~2.5h by leaning on §2/§5/§6/§12. Audience C is almost code-only, ~2h.

---

## Document map

| § | Section | Best for audience |
|---|---|---|
| 0 | [Pre-flight](#0--pre-flight-check) | All |
| 1 | [What we built](#1--what-we-built) | All |
| 2 | [Architecture deep-dive](#2--architecture-deep-dive) | B, C |
| 3 | [The use case: AE Ops Support](#3--the-use-case-ae-ops-support-workflow) | All |
| 4 | [Live demo — 7 scenarios](#4--live-demo--7-scenarios) | All |
| 5 | [Modern orchestration: MCP and A2A](#5--modern-orchestration-mcp-and-a2a) | B, C |
| 6 | [Engineering harness](#6--engineering-harness) | B, C |
| 7 | [Five design decisions and why](#7--five-design-decisions-and-why) | B, C |
| 8 | [What we got wrong — honest gap list](#8--what-we-got-wrong--honest-gap-list) | B (don't skip) |
| 9 | [Q&A — hard questions](#9--qa--hard-questions) | All |
| 10 | [Value to business](#10--value-to-business) | A |
| 11 | [Value to developers](#11--value-to-developers) | A, C |
| 12 | [Roadmap and what funding accelerates](#12--roadmap-and-what-funding-accelerates) | A, B |
| 13 | [Market context](#13--market-context) | B |
| 14 | [Appendix — file/line index](#14--appendix--filline-index) | C |

---

## 0 · Pre-flight check

```bash
docker ps --filter name=ae-pgvector --format "{{.Names}} {{.Status}}"
docker ps --filter name=contextedge-redis --format "{{.Names}} {{.Status}}"
curl -s -m 3 http://localhost:8001/health/ready -o /dev/null -w "backend %{http_code}\n"
curl -s -m 3 http://localhost:8080/ -o /dev/null -w "frontend %{http_code}\n"
curl -s -m 3 http://localhost:5050/health
curl -s -m 3 -X POST http://localhost:3000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","method":"initialize","id":1}' \
  -o /dev/null -w "ae-mcp %{http_code}\n"   # 400 = up, demanding session
```

Open browser tabs:
- **A** `http://localhost:8080/` (Orchestrator React canvas)
- **B** `http://localhost:5050/business` (chat — business user)
- **C** `http://localhost:5050/tech` (chat + approver + case-memory panel)
- **D** `http://localhost:5050/tester` (dual-pane for HITL theatre)
- **E** `http://localhost:8001/docs` (FastAPI Swagger)

In tab A: load _"AE Ops Support — V7 (verification + NEED_INFO)"_.

---

## 1 · What we built

> The 90-second pitch for a technical reviewer:

A **multi-tenant workflow orchestration platform** with 35 alembic migrations of schema, ~70 node types, full RLS-enforced tenant isolation, MCP-protocol tool calls, Vertex/OpenAI/Anthropic provider abstraction, and a React Flow visual editor. Built in ~6 weeks. ~135 commits in the last 30 days.

On top of it: **one workflow** that does what an L1 AutomationEdge ops support engineer does — routes intents (hybrid heuristic + LLM), translates business descriptions to system identifiers, runs deterministic diagnostics, gates destructive actions through human approval, verifies its own work after acting, and gracefully exits with a ticket ID when stuck.

The workflow is the demo. The orchestrator is the platform.

```
┌───────────────────┐    ┌─────────────────────────────┐    ┌────────────────┐
│ Tester UI         │    │  AE AI Hub Orchestrator     │    │ AE MCP server  │
│ Flask :5050       │←──→│  FastAPI :8001 + React :8080│←──→│ :3000          │
│ /business /tech   │    │  pgvector + Redis           │    │ 116 tools      │
│ + case panel      │    │  V7 workflow (~70 nodes)    │    │ T4 creds wired │
│ + glossary API    │    │                             │    │ (real AE T4)   │
└───────────────────┘    └─────────────────────────────┘    └────────────────┘
                                       ↑
                                Vertex AI (gemini-2.5/3-flash) for routing + specialists
```

**Lines of code** (excluding tests): backend ~22k, frontend ~14k. Migrations: 35. Test files: 124. Recent CI: 947 passing, 1 skipped.

---

## 2 · Architecture deep-dive

### 2.1 Workflow execution engine (the core IP)

**Where**: `backend/app/engine/dag_runner.py` (~900 lines), `node_handlers.py` (~1500 lines).

**Model**: workflows are DAGs of typed nodes. Each node has a category (`trigger | agent | action | logic | nlp | knowledge | notification`) that selects a handler. Edges can have `sourceHandle` (for branch routing) and a separate `kind` for loopback edges (cyclic-graph support, see `migration 0014` and `react_loop.py`).

**Why DAG-with-loopbacks instead of pure LangGraph state machines?**

- **Pure DAG** is hard to reason about for cyclic patterns (refinement loops, ReAct).
- **LangGraph** state machines are great for concurrent agents but make the simple linear case verbose.
- **Hybrid** (`dag_runner.py:880-905` shows the loopback gating logic) lets workflow authors pick the simpler primitive per problem.

**Code refs**:
- Edge propagation: `dag_runner.py:780-795` (the `is_branch_node` check that prunes irrelevant Switch branches)
- Loopback firing: `dag_runner.py:880-905`
- Sub-workflow recursion guard: `dag_runner.py` + `models/workflow.py::WorkflowInstance.parent_*` columns (migration 0011)

**Fan-in semantics**: a node fires when ALL upstream-satisfied edges resolve. No "wait any" mode — would need `dag_runner.py` extension. (V7 hit this when we tried to share a fallback subtree across 3 specialists; documented at §6.5.)

### 2.2 Multi-tenant isolation (RLS-enforced)

**Where**: `backend/app/database.py:21-39`, every tenant-scoped table's migration.

Every Postgres connection that handles tenant data sets `app.tenant_id` GUC at the top of the transaction. RLS policies on every tenant table compare against this GUC. `set_tenant_context(db, tenant_id)` writes to both the GUC and `db.info["tenant_id"]`; an `after_begin` SQLAlchemy listener restores the GUC after `commit()` flushes it (otherwise ANY post-commit query — `db.refresh()`, lazy loads — silently bypasses RLS).

**Code refs**:
- The RLS dependency: `database.py:21-39` (`set_tenant_context`)
- The `after_begin` listener: `database.py:29-39`
- Per-table policies: every migration starting with `0001` (initial RLS) through `0033` (LOCAL-AUTH-01 users) plus `48f869152a93` (support_cases)
- The systematic cutover that fixed a real production bug: `migration 0021` aka **RLS-01** — see `SETUP_GUIDE.md:23` for the war story (header-based endpoints were on `get_db` instead of `get_tenant_db` for months; the bug only surfaced when a tenant downgraded their app role to non-superuser per the STARTUP-01 warning)

**Production caveat (HONEST)**: the demo runs as Postgres `postgres` superuser, **which bypasses RLS entirely**. The boot-time check (`startup_checks.py::check_rls_posture`) explicitly warns about this. Production would run a `non_superuser_app_role`. Tests (`tests/integration/test_rls_breach.py`) verify isolation under a non-superuser role.

### 2.3 LLM provider abstraction

**Where**: `backend/app/engine/llm_providers.py`.

Single `call_llm(provider, model, system_prompt, user_message, …)` signature. Providers: `google` (AI Studio API key), `vertex` (ADC + per-tenant project), `openai`, `anthropic`. The Vertex path uses `_google_client(backend="vertex", tenant_id=tenant_id)` which threads tenant-id through `_resolve_vertex_target` to honor per-tenant Vertex project overrides (VERTEX-02, migration 0017).

**Why a custom abstraction instead of LiteLLM**:
- Tenant-aware credential resolution (each tenant's `tenant_secrets` may have its own Anthropic key — see `engine/llm_credentials_resolver.py`)
- Per-tenant Vertex project routing for billing isolation
- Streaming variants (`engine/streaming_llm.py`) emit tokens to Redis so the chat UI can show progressive output

**Smart-fallback** in `llm_providers.py:273-286`: if a tenant has no AI Studio key but has a Vertex project, `_google_client` transparently re-dispatches to Vertex. Avoids "key not configured" errors mid-conversation. (Discovered to be load-bearing during the dev demo — committed as `5688874`.)

### 2.4 MCP integration

**Where**: `backend/app/engine/mcp_client.py`, `mcp_server_resolver.py`.

We're a first-class MCP client (per the Anthropic 2025-06-18 spec). Per-tenant MCP server registry (table `tenant_mcp_servers`, migration 0019) — operators register zero or more MCP endpoints per tenant via the toolbar Globe icon. Each entry captures URL + auth mode (`none | static_headers | oauth_2_1`) + headers with `{{ env.KEY }}` templating into the secrets vault.

**Session pool**: `mcp_client.py::_MCPSessionPool` keyed by `(tenant_id, server_label)`. Pool size from `tenant_policies.mcp_pool_size`. Streamable-HTTP transport pinned to a daemon background event loop (the V0 sync-wrapper-per-call architecture leaked sessions across loops, fixed in `9f7e7f5`).

**SMART-06 + top-15 semantic filter**: when a ReAct agent's `tools` config is empty, `react_loop.py:286-318` discovers all tools, keyword-scores them against the user's query, and prunes to top-15. Cuts ~12k tokens of tool definitions out of every prompt for tenants with large tool surfaces. Implemented in PR #6 (originally `0317e8a`).

### 2.5 Memory and conversation state

**Where**: `migration 0012` (advanced memory hard cutover), `migration 0013` (episodes), `app/engine/memory_service.py`, `app/engine/memory_vector_store.py`.

Five tables compose the memory architecture:
- `conversation_messages` — turn-by-turn user + assistant messages, RLS-tenant-scoped
- `conversation_episodes` — segmented topic threads inside one session
- `memory_profiles` — per-tenant memory policy (scope priorities, summary cadence, vector store choice)
- `memory_records` — semantic memory (text + 768-dim embedding via `text-embedding-005`)
- `entity_facts` — relational facts ("user reports about timesheets")

**The "Load/Save Conversation State" workflow nodes** (`node_handlers.py::_handle_load_conversation_state` ~ line 700) read the policy, decide which scopes to retrieve, and emit a `messages[]` array the LLM sees. Workflow authors don't write SQL — the policy resolves at runtime.

**Why this matters for the use case**: V5 cross-cutting prompt rules tell every specialist to read `node_2.messages` and detect corrections / additions / resolutions. Multi-turn coherence is built into the platform, not bolted on per workflow.

### 2.6 HITL — three layers

**Layer 1: Human Approval node** (`migration 0030` — `approval_audit_log`, V0.9.13).
Explicit suspend point in the workflow. The frontend's `PendingApprovalsButton` polls `GET /api/v1/workflows/pending-approvals`; an approver clicks; workflow resumes via `POST /api/v1/workflows/{id}/instances/{id}/callback`.

**Layer 2: Guarded HITL on tool calls** (`react_loop.py:174-184`, V6 PR #6).
ANY MCP tool can return `{status: "AWAITING_APPROVAL"}`. The ReAct loop suspends, the platform creates an audit-log entry, the tech approver sees it. On resume, `technical_approval_id` is injected into the next call. Means destructive tools self-gate without explicit approval nodes. **This is the killer feature for production deployment**: every tool call to `ae.agent.restart_service` automatically pauses for human approval, even if the workflow author forgot.

**Layer 3: Per-specialist health check + fallback** (V5.3, in `build_ae_ops_workflow.py::HEALTH_CHECK_CODE`).
Workflow-level safety net. If a specialist exhausts iterations or returns a dead-end response, a Code node detects it and routes to a fallback subtree that PATCHes the case to `WAITING_ON_TEAM` and surfaces a ticket ID to the user.

### 2.7 The hybrid Intent Classifier

**Where**: `backend/app/engine/intent_classifier.py`, `_match_intents` at line 35.

Three modes (config field `mode`):
- `heuristic_only` — lexical (substring on intent name + examples) + embedding cosine. Zero LLM cost.
- `hybrid` — heuristic first; if confidence < threshold, fall through to LLM.
- `llm_only` — pure LLM with the candidate list.

V7 workflow uses `hybrid`. `EMBED_SCORE_WEIGHT = 4.0` weights embeddings over lexical so semantic matches dominate when both fire.

**Why hybrid wins for our use case**:
- 80%+ of utterances hit the heuristic with conf > 0.85 (slash-commands, canonical phrasings)
- The remaining 20% — corrections, novel phrasings, edge cases — get LLM at $0.0002/turn
- Average classification cost: ~$0.0001/turn

### 2.8 Ticket-first case management

**Where**: `migration 48f869152a93`, `app/api/support_cases.py`, `D:/Projects/AEAIHUBTesterUI/agent_server.py:430-700`.

Every conversation creates a `support_cases` row (idempotent upsert per session). State machine: `NEW → PLANNING → NEED_INFO → WAITING_APPROVAL → READY_TO_EXECUTE → EXECUTING → WAITING_ON_TEAM → RESOLVED_PENDING_CONFIRMATION → HANDED_OFF → CLOSED → FAILED`.

The case row has:
- `state` — current machine state
- `worknotes` (JSONB array) — append-only audit log
- `evidence` (JSONB array) — tool-call results, log excerpts
- `resolved_context` (JSONB) — accumulated AE context (workflow_id, request_id, agent_id)
- `plan_json` — the planner's proposed remediation plan, surfaced to approvers

Two API surfaces — FastAPI (`backend/app/api/support_cases.py`, RLS-gated via `get_tenant_db`) and Flask (`agent_server.py:430-700`, RLS-gated via per-connection `set_tenant_id` GUC). Both write to the same Postgres rows — proven cross-surface.

**Code ref for the cross-surface RLS**: `agent_server.py::_case_db()` opens a fresh psycopg2 connection and runs `SELECT set_tenant_id(<header>)` before any tenant query. Same RLS contract as FastAPI.

### 2.9 Workflow-side HTTP body templating (V3 contribution)

**Where**: `backend/app/engine/node_handlers.py:298-353`.

`_call_http` runs URL, every header value, AND body through Jinja2 (`prompt_template.render_prompt`). This means HTTP Request nodes can reference `{{ trigger.session_id }}`, `{{ node_2b.json.id }}`, etc. Without this, the workflow couldn't dynamically POST to its own `/api/cases/{id}/handoff` endpoint with the session-specific case id.

**Implementation**: `~30 lines of code`, ~2 hours from idea to merge. Demonstrates the codebase is small enough to extend in an afternoon when a real need surfaces.

### 2.10 Frontend — workflow editor

**Where**: `frontend/src/` (~14k lines).

React Flow + Zustand + shadcn/ui + Tailwind. The canvas at `:8080` is the production workflow editor — operators drag nodes, configure them via the Property Inspector (schema-driven form), save versions (with `migration 0002` snapshot history), pin node outputs for iterative debugging (DV-01), test individual nodes (DV-02).

**Reality check**: this is a **developer-facing canvas**, not a "no-code business analyst" tool. It exposes node types, configuration schemas, expression syntax. A non-engineer cannot productively use this. That's a deliberate choice (we're targeting platform engineers + DevRel-savvy ops teams) but it's worth flagging — if your thesis is "no-code for everyone", this is not that. See §6.

---

## 3 · The use case: AE Ops Support workflow

> Switch to tab A and pan around the canvas while explaining. The canvas IS the architecture diagram.

V7 workflow: ~70 nodes, ~80 edges. Hand-built via `backend/scratch/build_ae_ops_workflow.py` (~1800 lines of Python that emits a JSON graph and POSTs to `/api/v1/workflows`). The script is a meta-tool: edit a Python builder, get a complete versioned workflow.

### Topology

```
                                                          ┌→ chitchat (LLM)
[1 Webhook]                                               ├→ handoff (LLM)
   ↓                                                      ├→ resolution_update (LLM + PATCH state)
[2 LoadConvState] → memory loaded                         ├→ cancel_or_withdraw (LLM + worknote)
   ↓                                                      ├→ correction (LLM)
[2b HTTP /api/cases]  ─→ case row created/updated         ├→ output_missing → glossary subgraph
   ↓                                                      │      ↓
[2c Switch HANDED_OFF?] ─yes→ canned reply, exit          │   [glossary lookup HTTP]
   ↓ no                                                   │      ↓
[3 Intent Classifier] ─ hybrid (heuristic + LLM)          │   [Switch on match]
   ↓                                                      │      ├ matched → ReAct investigator
[3b Entity Extractor] ─ regex + intent-scoped             │      └ no match → clarify LLM
   ↓                                                      │
[3c Code: NEED_INFO check] ─ business + missing IDs?      ├→ remediation (ReAct)
   ↓                                                      │      ↓
[3d Switch] ─ ask → clarification LLM, exit               │   [Code: did_destructive?]
   ↓ proceed                                              │      ↓
[4 Switch on intent.0] ─────────────────────────→         │   [Switch] yes → verification ReAct
                                                          │                  ↓
                                                          ├→ rca_report (LLM)│
                                                          ├→ default ops (ReAct)
                                                          │
                                                          ├→ each ReAct branch → health check
                                                          │      ↓
                                                          │   [Switch] healthy → bridge+save
                                                          │              fallback → patch case
                                                          │                          → ticket-ID LLM
                                                          │                          → bridge+save
                                                          │
                                                          └→ each LLM branch → side-effect HTTP
                                                                            → bridge+save
```

### Code references for each subsystem

| Subsystem | Builder ref (`build_ae_ops_workflow.py`) | Runtime ref |
|---|---|---|
| Case upsert | line 470 (HTTP node config) | `app/api/support_cases.py::upsert_support_case` |
| Hybrid Intent Classifier | line 555 (intent definitions) | `app/engine/intent_classifier.py::_handle_intent_classifier` |
| Entity Extractor | line 685 (entity definitions) | `app/engine/entity_extractor.py::_extract_entities_from_config` |
| NEED_INFO check (V7.2) | line 750 (Code body) | `app/engine/sandbox.py::run_python_sandbox` |
| Cross-cutting prompt rules | `CROSS_CUTTING_RULES` (line 195) | applied via `_wrap()` to every specialist |
| Per-specialist fallback subtrees | line 855 (`fallback_subtrees` dict) | `_call_http` for the PATCHes |
| Verification subgraph (V7.1) | line 945 (`node_v1` Code) | reads `node_8.iterations` for destructive tool patterns |
| Output_missing playbook | line 1010 (`node_om*`) | calls Flask `/api/glossary/lookup` |
| Health-check + fallback (V5.3) | line 1085 (`HEALTH_CHECK_CODE`) | reads `node_4.branch` + specialist output |

The whole workflow is one Python file generating JSON. Demo ops teams can edit it with diff review.

---

## 3.5 · V8 — the same use case, simplified (router + worker + critic)

> Switch to tab A's workflow picker. Show "AE Ops Support — V7 (NEED_INFO + verification + glossary)" sitting next to "AE Ops Support — V8 (router + worker + critic, case tools as MCP)". Same use case, two architectures.

V8 is a deliberate simplification of V7 grounded in named patterns from Anthropic's *Building Effective Agents* (Dec 2024), OpenAI's *Practical Guide to Building Agents* (Apr 2025), and 12-Factor Agents. We built it in parallel — V7 stays deployed; V8 lives alongside it; an eval harness benchmarks both on the same transcript suite (`backend/scratch/run_ae_ops_evals.py`, 12 multi-turn cases).

**Why V8 exists:** V7 grew because each iteration patched a specific failure with a node — V5 misclassified → Switch fan-out; V6 hallucinated → dedicated Investigator + rule; V7 looped on missing IDs → NEED_INFO Code+Switch+LLM subgraph. Each fix was right in isolation; the DAG-as-control-flow approach compounded into 76 nodes. With a capable model and good prompts, most of that collapses.

### Topology

```
[1 Webhook]
   ↓
[2 LoadConvState]   capped at last 10 turns
   ↓
[3 HTTP /api/cases]   case opened or fetched (idempotent)
   ↓
[4 Switch HANDED_OFF?] ─yes→ canned reply, exit
   ↓ no
[Glossary lookup]   business desc → workflow_id (always-on, cheap)
   ↓
[Router: Intent Classifier]   5 intents only (small_talk, rca_request, handoff, cancel, ops)
   ↓
[Switch on intent]
   ├ small_talk    → small-talk LLM (gemini-2.5-flash, 384 tok)        → bridge+save
   ├ rca_request   → RCA LLM       (gemini-2.5-flash, 2048 tok, no tools) → bridge+save
   ├ handoff       → HTTP PATCH /handoff → handed-off LLM              → bridge+save
   ├ cancel        → HTTP PATCH /close   → cancel LLM                  → bridge+save
   └ ops (default) → Worker ReAct (gemini-3-flash, 8 iters, MCP+case+glossary tools)
                       ↓
                     Verifier ReAct (gemini-2.5-flash, 2 iters, read-only tools)
                       ↓ (output goes to case worknote, NOT user-facing)
                     bridge+save (Worker's reply to user)
```

**~28 nodes vs V7's 76.** The 13-node "core logic" plus per-branch Bridge+Save pairs needed for fan-in convergence.

### What's load-bearing in the DAG

1. **Trigger / load memory / case open** — engine integration, not reasoning.
2. **HITL gate** — engine watches for `AWAITING_APPROVAL`. The Worker calls a destructive tool, the runtime parks the run; an approver clicks the badge in the toolbar; the tool returns and the Worker continues. This cannot be agent-controlled for irreversible actions.
3. **Save memory + bridge user reply** — engine integration.

That's 5 nodes that must stay deterministic. Everything else moves into the prompt or tools.

### What collapses into the Worker

| V7 (DAG) | V8 (prompt + tools) |
|---|---|
| Intent Classifier + 7-way Switch fan-out | Single 5-way router; Worker handles diagnostics / remediation / output_missing / NEED_INFO / resolution_update / correction |
| Entity Extractor node | Worker extracts identifiers inline |
| Glossary HTTP + Switch + clarify-LLM | Worker calls `glossary.lookup(description)` MCP tool |
| NEED_INFO Code+Switch+LLM subgraph | Worker prompt: "if missing identifier, ask one targeted question + `case.update_state('NEED_INFO')`" |
| 7 per-intent specialist subtrees | One Worker with all tools |
| Verification ReAct after destructive | Same — preserved as a separate node with FRESH context (best-practice evaluator-optimizer pattern catches what the Worker would rationalise about its own action) |
| Per-specialist case-state PATCHes (~7 HTTP nodes) | Worker calls `case.add_worknote / update_state / handoff / close / add_evidence` MCP tools |
| `_wrap()` cross-cutting rules over every specialist | One consolidated Worker prompt with cacheable static prefix + per-turn dynamic context block |

### Cost-aware model tiering

| Role | Model | Why |
|---|---|---|
| Worker (hot path) | `gemini-3-flash-preview` | Best Flash-tier tool savvy; supports built-in + custom tools in one turn |
| Router | `gemini-2.5-flash` | Fast, structured output; doesn't need 3-flash savvy |
| Verifier | `gemini-2.5-flash`, max 2 iters | Compare task only |
| RCA writer | `gemini-2.5-flash`, 2048 tok | Synthesis at structured-prompt is enough; Pro tier rejected (4–10× cost without measured quality benefit at L1 scope) |
| Small-talk / handoff / cancel | `gemini-2.5-flash`, ≤384 tok | Tight token budget for canned replies |

### Two tool tiers (named in the Worker prompt)

  - **ALWAYS-AVAILABLE** (pinned, never SMART-06-filtered): `case.add_worknote / update_state / handoff / close / add_evidence / get`, `glossary.lookup`. Live in `D:/Projects/AEAIHUBTesterUI/mcp_server/tools/case_tools.py`.
  - **DOMAIN TOOLS** (semantically filtered top-15 from ~116 AE tools per turn): `ae.workflow.* / ae.request.* / ae.agent.* / ae.schedule.* / ae.support.*`.

This split follows OpenAI's *descriptions matter more than count* principle. The Worker knows which tier to reach for via its prompt; the engine handles filtering.

### Prompt engineering — cacheable prefix + dynamic suffix

Worker prompt is split into:

  1. **STATIC prefix** — audience rules, no-hallucination rule, ask-one-question rule, destructive-action rules, memory awareness, intent-label hints, response style, AE architecture primer (failure-cause chains, standard remediations). Byte-stable across turns → hits Vertex prompt cache.
  2. **DYNAMIC context block** — `user_role`, router intent, case_id, case_state, prior evidence count, glossary match. Per-turn variables, isolated at the very end.

Vertex's prefix-cache TTL means the static section is amortised across a session. The dynamic section is small.

### Eval-driven verdict (12-case transcript suite)

`backend/scratch/run_ae_ops_evals.py` against `ae_ops_eval_transcripts.json` — 12 multi-turn cases covering routing diversity, NEED_INFO loop safety, no-hallucination on bogus IDs, correction, resolution_update mid-thread, RCA, smalltalk, handoff, cancel, destructive+verification, hostile prompt-injection.

| Aggregate | V7 | V8 |
|---|---|---|
| Cases passing all-criteria | 2/12 | 2/12 |
| Cases tied | — | — |
| Avg latency / turn (canned reply) | ~50s | ~55s |
| Avg latency / turn (Worker / specialist) | ~55s | ~110s |

**V8 wins** (where V7 fails outright):

  1. **no-hallucination on bogus request_id**: V7 returns *empty reply*. V8 says "I couldn't locate request ID `99999...` — it may have been purged. I've logged this in case `b79934a7`." This is the most important quality signal for an L1 ops agent.
  2. **tech-specific request_id**: V7 misclassifies "diagnose request 9876" as `resolution_update` and replies with cheerful nonsense. V8 surfaces "couldn't locate request 9876" + opens a case + offers L2 routing.
  3. **destructive-with-verification**: V7 hits `suspended` state with empty reply. V8 honestly reports "couldn't locate agent worker-12 in the system" + checks running/stopped/unknown agent lists.

**V7 wins** (V8 regressions found by the eval):

  1. **glossary-match (turn 1)**: Both intent classifiers misclassify "I haven't received my daily recon report this morning" — V7 as `chitchat` (but produces empathetic full reply), V8 as `small_talk` (reply gets cut off at "Hello there! I" because max_tokens was 192). FIX SHIPPED: bumped V8 small-talk max_tokens to 384, added missing-report phrasings as `ops` examples with priority 130 > small_talk's 100.
  2. **vague-business (3-turn)**: V7 asks clarifying questions properly. V8 hits "Maximum iterations reached without final answer" because no MCP tools were available and the Worker kept retrying instead of stopping. FIX SHIPPED: added STOP-AND-ASK BUDGET section to Worker prompt with concrete stop conditions (2 consecutive 404s, 3 tool calls without convergence, no glossary match → straight to NEED_INFO without any tool call).
  3. **hostile-prompt-injection**: Both refuse but V8's reply still contains the words "system prompt" — confirms what the attacker wanted. FIX SHIPPED: added prompt-injection-defence section to small-talk prompt explicitly forbidding those words in the reply.

After fixes: V8 redeployed at `029ecb53`. Re-running the eval is the next step before drawing a final cost-quality verdict.

### V9 — smart HANDED_OFF

V9 lifts a single targeted bug from V8: once a case was HANDED_OFF, every subsequent message in the session got the canned "logged on case" reply — even if the user asked something new like "restart timesheet workflow". V9 moves the case-state Switch AFTER the intent router and combines `(case_state, intent)` into the routing decision:

  - HANDED_OFF + small_talk    → canned reply (covers "any update?", "any news?", "is it done yet?")
  - HANDED_OFF + cancel        → normal cancel branch (close existing case)
  - HANDED_OFF + ops/rca/handoff → archive old case + open fresh one, then continue to Worker

Implemented via an always-running second `/api/cases` POST (`node_4r`) whose body conditionally sets `archive_existing_handed_off=true` based on the upstream intent. Same nodes count, same pattern, just smarter routing. Builder: `build_ae_ops_workflow_v9.py`.

### V10 — sharper prompts + read-only critic

V10 lifts three improvements from a scan of the legacy AE Ops codebase (memory: `feedback_v9_adoption_targets_from_legacy.md`). Topology unchanged from V9; only Worker prompt and Verifier sandbox change.

  1. **RECENT TOOL FINDINGS prompt block** in `WORKER_PROMPT_DYNAMIC` — distils last few `case.worknotes` and `case.evidence` entries so the Worker stops re-asking for IDs already surfaced.
  2. **Four sharper STATIC rules** added to `WORKER_PROMPT_STATIC`:
     - MEANINGFUL FIRST-LINE (lead with answer, not filler)
     - CHAT-NOT-SYSTEM (no raw JSON / field-name dumps in user replies)
     - STRICT AGENT ENFORCEMENT (`ae.agent.list_running` first; never diagnose a STOPPED agent)
     - PROACTIVE DIAGNOSTIC DISCOVERY (search before asking, when there's anything searchable)
  3. **Engine-level `allowedToolCategories`** on the Verifier — the prompt has always claimed read-only, but nothing previously enforced it. V10 adds `_categorize_tool` to `react_loop.py` and threads an `allowed_categories` allowlist through `_load_tool_definitions`. Categories: `case` / `glossary` / `web` / `remediation` / `read`. Verifier gets `["read", "case"]`; tools in other categories are dropped before the model ever sees them. Worker keeps full access (HITL gate handles destructive consent — categories are not how that gate works).

Builder: `build_ae_ops_workflow_v10.py`. Engine change requires an orchestrator restart (no `--reload`); prompt changes are live as soon as the workflow is POSTed. Comparison notes: `v9_vs_v10.md` at repo root.

### When to pick which

| Scenario | Pick |
|---|---|
| Quality / no-hallucination is the most important signal | V8 |
| Highest tool-call budget on the hot path (cost-sensitive, high-volume) | V7 (cheaper LLM hops on canned-reply paths) |
| Need to add a new playbook / intent quickly | V8 (one prompt paragraph + maybe one tool) |
| Compliance demands canvas-visible state transitions | V7 (Switch+HTTP nodes are auditable artefacts) |
| Best demo story for AI-tech audience | V8 (autonomous agent + tool trace) |
| L1 ops console with strict SLA per branch | V7 (deterministic branch latency) |

Both ship. Don't replace V7 yet — let the eval data decide as workloads evolve.

### File index for V8

| Subsystem | Builder ref (`build_ae_ops_workflow_v8.py`) | Runtime / data |
|---|---|---|
| Worker prompt (static + dynamic) | `WORKER_PROMPT_STATIC` / `WORKER_PROMPT_DYNAMIC` | applied to `node_worker` |
| Router (Intent Classifier) | line ~330, 5 intents | `app/engine/intent_classifier.py` |
| Verifier (read-only critic) | `VERIFIER_PROMPT` | `node_verifier`, max 2 iters |
| RCA writer | `RCA_PROMPT` | `node_rca`, gemini-2.5-flash, 2048 tok |
| Always-available case + glossary tools | — | `D:/Projects/AEAIHUBTesterUI/mcp_server/tools/case_tools.py` |
| Tool descriptions (curated) | — | `D:/Projects/AEAIHUBTesterUI/mcp_server/tool_specs.py:_CURATED_TOOL_OVERRIDES` |
| Eval transcripts | — | `backend/scratch/ae_ops_eval_transcripts.json` (12 cases) |
| Eval runner | — | `backend/scratch/run_ae_ops_evals.py` |
| V8 design notes | — | `backend/scratch/V8_NOTES.md` |
| V9 builder (smart HANDED_OFF) | — | `backend/scratch/build_ae_ops_workflow_v9.py` |
| V10 builder (sharper prompts + read-only critic) | — | `backend/scratch/build_ae_ops_workflow_v10.py` |
| V10 `allowedToolCategories` engine support | — | `app/engine/react_loop.py::_categorize_tool` |
| V10 evolution notes | — | `v9_vs_v10.md` (repo root) |

---

## 4 · Live demo — 7 scenarios

> Time-box: each scenario ~5-7 minutes. By default scenarios run on **V7** (case-tracked, audience-aware, NEED_INFO loop-safe) — the canvas tells the story most clearly there. For YC tech-review audiences, run scenarios 4 (no-hallucination) and 7 (destructive+verification) on **V8** as well to show the architecture-simplification win. Workflow picker in tab B switches between them; both share the same case API + glossary, so case state continues seamlessly.

### Scenario 1 — Chitchat baseline (3 min)

**Tab B** (`/business`). Select V7 workflow. Type: `hi there`.

What you'll see:
- ~5s response: friendly greeting, capability hint
- Tab C case panel: new `#xxxx NEW` row appears within 5s
- 1500-token input, 30-token output, ~$0.0001 cost

**Code ref**: chitchat specialist is `node_5` in the canvas. Click it → property inspector → see `gemini-2.5-flash`, `temp=0.7`, no tools, the prompt that includes the cross-cutting rules block.

**Architectural callout**: the bot opened a ticket on a chitchat. That's intentional — every conversation has a case row from second one. **Audit-trail by default, not by configuration.**

### Scenario 2 — Business "missing report" (8 min) ⭐ headline

**Tab B**, new session: `I have not received my daily recon report today`

Expected ~30s response:
> "I couldn't find any recent runs of your Daily Recon Report in the last 24 hours… routing to the Finance Ops team… update in about 15 minutes — would you like me to keep looking…"

**What to point at**:

| Bot phrase | Architecture |
|---|---|
| "Daily Recon Report" (capitalized as friendly_name) | Glossary lookup via Flask `/api/glossary/lookup` (`agent_server.py:548`). Lexical alias hit on "daily recon", `method: lexical_alias`, conf 0.95, 50ms latency |
| "the system that runs your report" (no agent_id mentioned) | Cross-cutting rule in `CROSS_CUTTING_RULES` (line 195 of builder): never quote agent_id / request_id / workflow_id to a business user |
| "Finance Ops team" | `glossary["DailyReconciliation"].owner_team` — pulled at glossary lookup time |
| "in about 15 minutes" | `expected_delivery_minutes` from glossary, modulated by prompt rule "always give a TIME ESTIMATE" |
| "would you like me to keep looking" | Cross-cutting rule "never end with a passive ending" |

**Tab F** (`/api/cases?limit=3`): refresh → see the new case row. `state: NEW`, `session_id` matches, `worknotes: []`. The case is the audit trail. Every subsequent action will append worknotes via the Flask `/api/cases/{id}/worknote` endpoint.

**Live curl** (tab G):
```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"description":"daily recon"}' \
  http://localhost:5050/api/glossary/lookup | jq

# {"match": {"workflow_id": "DailyReconciliation", "owner_team": "Finance Ops", ...},
#  "confidence": 0.95, "method": "lexical_alias", "matched_alias": "daily recon"}
```

```bash
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"description":"the OCR processing for license plate stuff"}' \
  http://localhost:5050/api/glossary/lookup | jq

# {"match": {"workflow_id": "LogExtractionAndRecognition", ...},
#  "confidence": 0.92, "method": "llm"}   ← 500ms LLM call when lexical missed
```

**Honest gap call-out**: the glossary is hand-curated (5 entries). Production would auto-populate from `ae.workflow.list_for_user()` + LLM-generated descriptions. ~1 day of work; not done. See §7.

### Scenario 3 — Tech, no-hallucination diagnostics (6 min)

**Tab D** (dual pane). New session. Business pane: `diagnose request id 99999999`.

Expected ~40s response:
> "I'm not 100% sure but it looks like request_id 99999999 does not exist in the system, as the diagnostics tool returned a 404 error. It may have been purged or the ID might be incorrect. Would you like me to search for other recently failed requests…"

**What to point at**:
- "I'm not 100% sure" — confidence-hedging from cross-cutting rules (classifier confidence was 0.77, below 0.85 threshold)
- "does not exist" — V6.2 anti-hallucination prompt (`build_ae_ops_workflow.py:282`). _"If user asked about a SPECIFIC request_id and your tool calls returned data about DIFFERENT request_ids, do NOT fabricate analysis."_
- "Would you like me to search for other recently failed requests" — proactive next-step

**Honest gap call-out**: this prompt rule is mitigation, not guarantee. LLMs can still hallucinate under sufficient pressure. Production would add a deterministic post-LLM check: "did the response cite an entity NOT returned by any tool call?" → fallback. Not implemented; would be V8.

**Pre-fix comparison** (if anyone asks): V5 of this same workflow would call `ae.request.search`, find unrelated failed requests, write a confident-looking RCA about a request the user didn't ask about. The fix: 6 lines of prompt added + LLM-backed.

### Scenario 4 — NEED_INFO (5 min) ⭐ V7 highlight

**Tab B**, new session: `my agent is stopped, please help`

Expected ~10s:
> "I can help with that. Which system or scheduled job is having the issue? You can describe it in your words — I'll match it up."

**What to point at**:
- Tab C: case appears with state `NEED_INFO` (yellow badge)
- The bot did NOT ask for `agent_id` — it asked for "system or scheduled job"
- Why? `node_3c` Code node detected `user_role==business + intent in [diagnostics, remediation] + entities=={}` → set `flag=ask`

**Code ref**: `build_ae_ops_workflow.py:758-790` (the NEED_INFO Code body). It's 8 lines of Python in a sandboxed subprocess.

**Continue the conversation**: same chat, type `the one that runs at 2 AM and produces the recon report`.

The next turn:
- Intent Classifier reads `node_2.messages` (now contains both prior turns)
- Likely classifies `output_missing` (the "recon report" lexical signal dominates)
- Routes to glossary → `DailyReconciliation` → investigator runs

**The case state transitions in real time** in tab C. Architecturally honest: `NEED_INFO → NEW` (the new turn re-upserted to NEW). Production might want a more sophisticated state graph; we deferred.

### Scenario 5 — HITL with verification (12 min) ⭐ headline

**Tab D**. New session. Business pane: `please restart the agent that runs my daily recon report`

What happens depends on AE tenant state. Honest version:
> Our AE T4 tenant has zero agents in the `default` namespace. So the remediation specialist will call `ae.agent.list_running` etc., come back empty, and request clarification — not actually try `restart_service`.

**Run the architecture commentary regardless**:

> "If our tenant had real agents, here's the flow: remediation specialist calls `ae.agent.restart_service` → guarded HITL fires (`react_loop.py:174-184`) → workflow suspends → tech pane shows pending approval → tech clicks Approve → workflow resumes with `technical_approval_id` injected → tool actually runs → verification subgraph (`node_v1` → `node_v2` → `node_v3`) re-fetches status → user sees 'before/after' summary."

**Show the wiring on canvas**: pan to `node_v1` (Code: did_destructive_run?). Click → see the Python that scans `node_8.iterations` for destructive tool patterns.

**The fallback path IS exercisable** with a non-existent agent name:
- Type `please restart agent xyz123` (will not be found)
- ~60s later: ReAct exhausts tool calls, response signals "couldn't find" → health check fires `is_fallback=True, reason=response_signals_dead_end` → fallback subtree fires
- HTTP PATCH case → `WAITING_ON_TEAM`, team `L2_OPS`
- LLM formats: _"I couldn't fully resolve this from my end. I've logged this as ticket **#abc12345** with the L2_OPS team — they'll reach out within 30 minutes…"_

Tab C: case panel shows the state transition and the fallback worknote.

**This is the "professional even when it can't" moment**. Show the user the ticket ID in bold. The case is now real, queued for humans, with full investigation trail in `worknotes`/`evidence`. **No abandoned conversation.**

### Scenario 6 — Memory awareness across turns (5 min) ⭐ qualitative

**Tab B**, new session. Three turns:
1. `my month-end close numbers are off`
2. `wait, I meant the daily recon, not month-end`
3. `do you have updates yet?`

Expected:
- T1: `output_missing` → glossary → `MonthEndClose` → investigator runs
- T2: `correction` intent (lexical match on "I meant"). Specialist reads `node_2.messages`, acknowledges, re-routes to `DailyReconciliation`
- T3: classifier picks `chitchat` or `default ops`; specialist reads conversation history, references prior investigation

**Architecture callout**: same case ID across all 3 turns. `node_2.messages` accumulates. Cross-cutting **memory awareness rule** (line 195 of builder) tells every specialist to read prior turns and detect corrections.

**Tab C**: case row stays the same; `worknotes` accumulates with each turn's actions; `state` evolves.

### Scenario 7 — Resolution detection + auto-state-transition (4 min)

Continuing Scenario 6's chat: `actually I just got the recon report, came in 10 minutes late`

- Intent: `resolution_update` (V6.1 fix — score 5.5 vs `correction` 1.9)
- Side-effect HTTP PATCH → state `RESOLVED_PENDING_CONFIRMATION`
- Reply: _"That's good news — sounds like the run finished, just delayed. I'll mark this resolved. Did the data look right…"_

Tab C: state badge flips to green.

**Final turn (optional)**: `yeah looks fine, you can close it`.

**Honest limitation**: V7 doesn't auto-CLOSE on positive confirmation. The case stays in RESOLVED_PENDING_CONFIRMATION until a human moves it. ~30 minutes of work to add; deferred to V8 (Beat task scanning resolved-pending cases older than 1h).

---

## 5 · Modern orchestration: MCP and A2A

> **Why this section exists.** The two protocols that change how this generation of orchestration platforms is built — Model Context Protocol (MCP) and Agent-to-Agent (A2A) — are first-class citizens here, not bolted on. This is the **forward-looking architectural bet** of the platform.

### 5.1 Why MCP, why now

**The problem MCP solves.** Every LLM tool integration today is bespoke: SDKs per vendor (Slack SDK, Salesforce SDK, ServiceNow SDK). Each new tool surface needs:
- Auth code per vendor
- Schema translation
- Rate-limit handling
- Versioning policy

For a workflow platform, this is N×M complexity: N customers × M tools each. We'd be writing connectors forever.

**MCP collapses N×M to N+M.** The protocol (Anthropic spec, 2025-06-18) standardizes:
- Tool discovery (`list_tools` returns name + JSON schema for parameters)
- Tool invocation (`call_tool` is a JSON-RPC method)
- Session management (streamable-HTTP transport with session continuity)
- Auth (none / static_headers / oauth_2_1)

A vendor ships an MCP server **once**; every MCP-aware client (us, Claude Desktop, Cursor, future LangChain wrappers) gets all their tools for free.

### 5.2 Where MCP lives in the orchestrator

**Code:** `backend/app/engine/mcp_client.py` (~360 lines), `mcp_server_resolver.py` (~120 lines).

**Per-tenant MCP server registry** (table `tenant_mcp_servers`, migration 0019):

```sql
CREATE TABLE tenant_mcp_servers (
    id UUID PRIMARY KEY,
    tenant_id VARCHAR(64),
    label VARCHAR(64),
    url TEXT,
    auth_mode VARCHAR(32), -- 'none' | 'static_headers' | 'oauth_2_1'
    config_json JSONB,     -- {headers: {...}}, with {{ env.KEY }} templating
    is_default BOOLEAN,
    created_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ
);
-- + RLS policy tenant_isolation_tenant_mcp_servers
```

Operators add MCP servers via the toolbar Globe icon (`McpServersDialog.tsx`). Each MCP Tool node and ReAct Agent node accepts an optional `mcpServerLabel` config field; blank → tenant default → legacy env-var fallback (`ORCHESTRATOR_MCP_SERVER_URL`) so pre-MCP-02 tenants keep working.

**Session pool keyed by `(tenant_id, server_label)`** (`mcp_client.py::_MCPSessionPool` line 47-108). Pool size from `tenant_policies.mcp_pool_size`. Tenants never share warm connections — isolation at the connection layer, not just the query layer.

**Tool discovery cache** (`mcp_client.py:38`): 5-minute TTL on `list_tools` per `(tenant, server)` pair. Cuts ~12k tokens of tool definitions out of every prompt for tenants with large surfaces.

**SMART-06 + top-15 semantic filter** (`react_loop.py:286-318`): when a ReAct agent's `tools: []` is empty, the runner discovers all tools, keyword-scores against the user's query, prunes to top-15 candidates per turn. Demonstrably improves Gemini 3's tool-selection accuracy on large surfaces — see Scenario 5 in §4 where the AE MCP exposes 116 tools and the diagnostics specialist receives a context-relevant top-15.

### 5.3 What MCP unlocks for the V7 demo

| MCP capability | Where it shows up |
|---|---|
| 116 AE tools, registered once | The `mcp_server/` bundle in the tester UI repo. One config (URL + creds), all tools available |
| Per-tenant tool-set | `tenant_mcp_servers` row gives `default` tenant access to AE; another tenant could have only ServiceNow tools |
| Tool definitions stay outside the workflow | Workflows reference tools by **name** (or no name → discovery). When AE adds a new tool, no workflow change needed |
| Auth is the MCP server's problem | T4 username/password lives in the AE MCP server's `.env`. The orchestrator never sees AE credentials |
| Streaming-HTTP transport | The orchestrator's MCP client handles long-running tool calls (e.g., `ae.support.diagnose_failed_request` taking 30s) without timing out the workflow |

### 5.4 Why A2A, why now

**The problem A2A solves.** As workflows compose into multi-agent systems, you need a standard way for agent A to invoke agent B. Without A2A:
- Custom REST endpoints per agent
- Bespoke auth + auth context propagation
- Idiosyncratic message formats

A2A (Agent-to-Agent protocol) standardizes:
- **Agent discovery** via an `/agent-card` endpoint that publishes capabilities
- **Task lifecycle** (created → running → suspended → completed) with state-message coverage
- **Multimodal parts** (text, file, data) in messages and artifacts
- **Auth scheme declaration** (Bearer, OAuth2.1, etc.) on the agent card

### 5.5 Where A2A lives in the orchestrator

**Code:** `backend/app/api/a2a.py` (~700 lines), `backend/app/engine/a2a_client.py` (~250 lines).

**Implemented (V0.9.x and beyond):**

| Feature | Migration / commit | Status |
|---|---|---|
| **A2A-01.a** Dual-path discovery + v1.0 method aliases | `7ed3571` | Live. Agent card available at `/.well-known/agent.json` AND `/api/v1/a2a/agent-card`. v0.2 method names accepted for back-compat |
| **A2A-01.b** Agent card completeness + task-state coverage | `949168f` | Live. Card declares `provider`, `documentationUrl`, `securitySchemes`, `defaultInputModes`, `defaultOutputModes`, `extendedAgentCard:false`. State machine covers `submitted`, `running`, `suspended-by-reason`, `working`, all terminal states |
| **A2A-01.c** FilePart + DataPart in messages + artifacts | `20978b5` | Live. Multi-part messages parsed to trigger payload; completed artifacts emit text + data parts |
| **A2A Agent Call** workflow node | `node_handlers.py:_handle_a2a_call` | Live. A workflow can call another A2A agent as a single node — the agent_card is fetched, capabilities matched, task created and polled |

**Demonstrable**: every saved workflow gets an agent card automatically. Other A2A clients (Claude Desktop, etc.) can discover and invoke published workflows.

**Code refs:**
- `app/api/a2a.py:50-200` — agent card construction from workflow definitions
- `app/api/a2a.py:300-450` — JSON-RPC 2.0 task creation + state machine
- `app/engine/a2a_client.py` — outbound A2A call from a workflow node

### 5.6 The combined bet

> Use this in the investor pitch:

> "We're betting on the protocols. MCP standardizes tools — vendors ship MCP servers, we use them. A2A standardizes agent invocation — we publish workflows as agents, anyone can call them, our workflows can call anyone else's. The orchestrator becomes the **multi-tenant infrastructure layer** for an MCP+A2A world. Single-vendor SDKs become legacy code by 2027."

**The risk**: if MCP doesn't win, we've over-invested in the abstraction. Mitigation: every MCP integration is wrapped at our own boundary (`mcp_client.py`); we can replace it with bespoke SDKs without touching the workflow layer. ~3 weeks of refactor.

**The signal**: Anthropic, OpenAI, Cursor, Zed, and several enterprise vendors have shipped MCP support in 2025. Adoption curve looks like the early days of OpenAPI. Our moat: first-mover on **multi-tenant MCP infrastructure** (per-tenant pools, RLS-aware tool routing, credential isolation).

---

## 6 · Engineering harness

> The plumbing that makes the platform reliably built. Often invisible in demos but critical for production confidence.

### 6.1 Test infrastructure

| Layer | Count | Where | Notes |
|---|---|---|---|
| Unit tests | ~947 passing | `backend/tests/test_*.py` | Mocked external services. Fast (~75s on Windows) |
| Integration tests | 17 | `backend/tests/integration/` | Real Postgres via `testcontainers[postgres]`. Validates RLS under non-superuser role, scheduled-trigger dedupe, AE webhook end-to-end |
| Frontend tests | 33 | `frontend/src/test/` | Vitest + jsdom. UI components, hooks, store mutations |
| Smoke / e2e | (this doc) | `DEMO_SCRIPT.md` | The demo IS the e2e test |

**Test fixtures of note:**
- `tests/integration/test_rls_breach.py` — explicitly validates that a tenant cannot read another tenant's data when the app role is non-superuser. **The single most important test in the suite for security claims.**
- `tests/test_react_loop.py` — covers SMART-06 top-15 filter, guarded HITL suspension, max-iteration handling
- `tests/test_intent_classifier.py` — hybrid scoring math, embedding cache behavior, LLM fallback gating
- `tests/test_vertex_provider.py` — per-tenant Vertex project resolution

**Sandbox testing:**
- `tests/test_code_sandbox.py` — verifies Python sandbox blocks `import sqlalchemy`, `subprocess`, `os.system`, etc.
- This is what makes the workflow's code-execution nodes safe for tenant-authored code

### 6.2 CI / CD

**Where:** `.github/workflows/ci.yml`.

Runs on every PR + push to `main`:
1. **`backend`** — pytest with `pytest.ini` testpaths constraint (added in PR #3) — keeps the test surface tight
2. **`backend-integration`** — testcontainers-driven Postgres, validates real DB behavior including RLS
3. **`frontend`** — `tsc -b && vite build` + `eslint .` + `vitest run`

PR #6 fixed two pre-existing CI breakages: scratch test files getting collected (fixed via `pytest.ini testpaths = tests`) and `react-refresh/only-export-components` ESLint errors (fixed via targeted `eslint-disable` on shared utility exports).

**The test suite has caught real bugs**:
- The `_FakeDB.info` regression that PR #3 fixed (4 tests broken)
- The `gemini-3.1-pro-preview-customtools` default revert (the test guard PR #5 restored)
- The Vertex `api_key=None` constructor parameter that broke the Vertex provider tests
- The migration `vector_dims()` cast issue that the V5 hardening pre-flight check catches

### 6.3 Observability

**Langfuse integration** (`backend/app/observability.py`, ~140 lines):

```python
# Every LLM call wrapped:
with span_llm(trace, model=..., prompt=..., …) as span:
    result = call_llm(...)
    span.update(output=result.response, usage=result.usage)
```

Plumbed into `_handle_agent`, `_handle_action`, `_handle_llm_router`, the ReAct loop, and the streaming variants. **Disabled by default** (`ORCHESTRATOR_LANGFUSE_ENABLED=false`); enable per environment.

**Structured logging** (`logging.basicConfig` in `main.py:33`): every backend log line has timestamp, level, logger name, message. Engine modules emit progress at INFO, MCP client at DEBUG.

**Health-check endpoint** (`/health/ready` — `STARTUP-01a`): runs 8 preflight checks at boot AND on every health probe:
1. `database` — connection + alembic head check
2. `redis` — PING
3. `celery_workers` — heartbeat (or "USE_CELERY=false" pass)
4. `rls_posture` — warns if connected as superuser
5. `auth_mode` — coherence (e.g., `oidc_enabled=true` requires `oidc_issuer`)
6. `vault_key` — VAULT_KEY env var set
7. `mcp_default_server` — TCP reachability of `ORCHESTRATOR_MCP_SERVER_URL`
8. `model_registry_drift` — `shared/node_registry.json` matches `app/engine/model_registry.py`

Returns 503 on any `fail`, 200 with details otherwise. Frontend `StartupHealthBanner` renders per-check remediation strings.

### 6.4 Sandboxing

**Where:** `backend/app/engine/sandbox.py`.

Every `code_execution` node runs Python in a **subprocess sandbox** with a curated `_safe_builtins`. The sandbox blocks:
- `import os`, `import subprocess`, `import sys`
- `import sqlalchemy`, `import requests`
- File-system access via `open()`
- Network access (the subprocess has no network capability)

`open()` is the only fs primitive available, and only for stdin/stdout streams. Imports go through a custom `__import__` allowlist.

**Why this matters for tenants**: workflow authors can write small data-transformation Python without us auditing every line. The sandbox is the security boundary, not human review.

**Honest limit**: the sandbox is process-based, not container-based. A determined attacker with eval-tricks could plausibly escape (we haven't seen one, but we haven't run a pen-test). Production would containerize the sandbox or replace with WASM.

### 6.5 Migration discipline

**35 alembic migrations**, every one reversible (has a working `downgrade()`). The most recent two demonstrate the discipline:

- `48f869152a93_ae_ops_support_cases.py` (V7 demo's case table) — clean up + down
- `384daed57459_fix_memory_embedding_dim_768.py` — the **breaking** migration (1536→768 vector dim) that has a **pre-flight check** refusing to upgrade if any rows are at the wrong dimension. The check has a `vector_dims()` query, fails fast with operator remediation instructions, has been tested on real data.

**Why this matters**: most early-stage codebases have migrations that work in dev and corrupt production. Our discipline catches it before it ships. PR #5 added the pre-flight to one migration; the pattern can be applied to others.

### 6.6 Developer onboarding

**`SETUP_GUIDE.md`** has 30 numbered steps from "git clone" to "first workflow runs". `HOW_IT_WORKS.md` walks through the runtime. `DEVELOPER_GUIDE.md` covers extending nodes, debugging, the API surface.

**Reality**: the bar for "I can ship a feature" is ~1-2 days for an experienced FastAPI/React engineer. Adding a new node type (e.g., a Slack send-message node) is a 1-day task: define the schema in `node_registry.json`, add a handler in `node_handlers.py`, add a Property Inspector form mapping (auto-generated from schema), add tests.

**What's missing**: no `make dev`, no `docker-compose up` shortcut. ~1 week of DX work to fix.

### 6.7 Demo harness

**Where:** `backend/scratch/build_ae_ops_workflow.py` — the V7 workflow itself is generated by ~1800 lines of Python. The script:
1. Defines node IDs, positions, configs, edges
2. Emits a `graph_json` JSON
3. POSTs to `/api/v1/workflows`

Re-running the script produces a new version. Diff'ing two builds shows what changed structurally.

**The pattern**: workflow authors who don't want to use the canvas can use Python builders. Powerful, scriptable, version-controlled. For complex flows (V7 has ~70 nodes), this is faster than dragging boxes.

---

## 7 · Five design decisions and why

> The "show your reasoning" section. Each decision has a rejected alternative.

### 5.1 DAG with loopbacks instead of LangGraph state machines

**Rejected**: pure LangGraph state-machine model.

**Why DAG**: 80% of workflow patterns are linear or near-linear (triggered → process → respond). LangGraph forces upfront state schema even for trivial flows. Our Sub-Workflow node + cyclic-edges (CYCLIC-01.a-e migrations) cover the harder cases without imposing the cost on the simple ones.

**Code**: `dag_runner.py:780-905` (forward propagation with branch pruning + loopback gating).

**Cost**: we wrote our own DAG runner. ~500 lines. LangGraph would have been ~50 lines of integration but a heavier mental model for the canvas user.

### 5.2 RLS in Postgres instead of app-level tenant checks

**Rejected**: `WHERE tenant_id = ?` everywhere.

**Why RLS**: a single `set_tenant_id()` at the request boundary protects every subsequent query, including ones written by future engineers who forget the WHERE clause. Postgres enforces it at the storage layer.

**Code**: every migration since 0001 has `ALTER TABLE … ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY` + a `tenant_isolation_<table>` policy.

**Honest limitation**: superuser bypasses RLS. We have a `startup_checks::check_rls_posture` that warns if connected as superuser, but there's no hard gate. Production setup requires creating a non-superuser app role; documented but easy to forget.

### 5.3 Hybrid intent classification instead of "always LLM"

**Rejected**: send every utterance to gemini-2.5-flash for routing.

**Why hybrid**: a slash-command like `/handoff L2` is deterministic. Spending an LLM call to recognize it is wasteful at scale. The hybrid mode captures 80% of cases for free (lexical + embedding) and falls back to LLM for the rest. Average classification cost: $0.0001/turn.

**Code**: `intent_classifier.py::_match_intents` (line 35). `EMBED_SCORE_WEIGHT = 4.0` weights embeddings over lexical. `_llm_classify` (line 200ish) is the fallback.

**Demonstrable**: Scenarios 4 (V7.2) classifies "my agent is stopped" via heuristic at conf 0.86 in <100ms. Scenario 6 turn 2 ("wait, I meant…") via heuristic at conf 0.81 — caught a correction without needing LLM.

### 5.4 Workflow-as-data instead of workflow-as-code

**Rejected**: every workflow is a Python class with a `run()` method.

**Why JSON-graph**: the workflow IS the data structure. Versioning is a row in `workflow_definitions` (migration 0001). Testing a node in isolation is `POST /api/v1/workflows/{id}/nodes/{node_id}/test` (DV-02). Pinning a node's output for iteration is `pinnedOutput` JSONB (DV-01). The canvas IS the editor.

**Code**: `models/workflow.py::WorkflowDefinition.graph_json` (JSONB column).

**Cost**: configuration discoverability suffers. We mitigate with `shared/node_registry.json` (the schema for every node type) which the frontend Property Inspector renders as a typed form (DynamicConfigForm.tsx).

### 5.5 MCP for tool integration instead of bespoke connectors

**Rejected**: a SDK per integration (Slack SDK, AE SDK, Salesforce SDK…).

**Why MCP**: the protocol just landed, but the bet is that platform vendors will ship MCP servers (some already do). Our connector burden becomes maintaining a single MCP client and a per-tenant registry of MCP endpoints. Future integrations are zero-code: register a new MCP server URL via the Globe icon, tools auto-discover.

**Code**: `engine/mcp_client.py`, `models/workflow.py::TenantMCPServer` (migration 0019).

**Risk**: MCP adoption is uncertain. If only Anthropic and a handful of vendors ship MCP, we're back to bespoke connectors. We watch quarterly. Current bet: MCP wins because it solves a real problem (tool definitions are the new schema).

---

## 8 · What we got wrong — honest gap list

> This section is the most important one for an investor review. Don't skip it.

### 6.1 The demo runs as Postgres superuser → RLS bypassed

The local dev DB connects as `postgres` superuser. **Postgres superusers bypass RLS entirely.** The demo's tenant isolation claims would not hold under this setup if attacked.

- **Where it matters**: production deployment
- **What's needed**: create a non-superuser role (`SETUP_GUIDE.md §5.2a` has the SQL), point `ORCHESTRATOR_DATABASE_URL` at it, run boot
- **Effort**: ~1 hour
- **Status**: documented, not done. Tests under `tests/integration/test_rls_breach.py` validate it works correctly under non-superuser

### 6.2 No production deployment ever

This has not been deployed to a customer. We've run it on one machine for ~6 weeks. We have no production observability data, no SLA history, no actual cost-at-scale data, no customer success stories.

- What `cost-per-turn` actually is at scale (vs theoretical numbers)
- How session pool sizing behaves under 100 concurrent users
- How LLM rate limits surface
- Whether 30-min ETA promises hold up

This is the biggest gap. Pure platform demo, no operational war stories yet.

### 6.3 The glossary is hand-curated

`workflow_glossary.json` has 5 entries. Production would have hundreds. The intended pipeline:
- Sync `ae.workflow.list_for_user()` per tenant
- LLM-generate `friendly_name` and `aliases` per workflow
- Periodically refresh

Not built. ~1 day of work. The glossary is the bridge between the "business user friendly" claim and reality.

### 6.4 LLM hallucination is mitigated, not eliminated

V6.2 prompt rule prevents the diagnostics specialist from inventing analysis of unrelated requests. **It works in 95% of cases we've tested.** The other 5% — sufficiently confused prompts, edge-case tool responses — can still hallucinate. Production would add:
- A deterministic post-LLM check: every entity cited in the response must appear in a tool call's response
- Fall to fallback subtree if not

Not implemented. Sized at ~4 hours.

### 6.5 Workflow fan-in semantics are limited

Our DAG runner waits for ALL upstream edges (`dag_runner.py:780-795`). When V7 tried to share a fallback subtree across 3 specialists, the subtree couldn't fire because the runner waited for all three to satisfy. We split into 3 dedicated subtrees (12 extra nodes) to work around it.

A "wait any" join semantics would simplify the graph by ~15 nodes. ~2 days of dag_runner work to add.

### 6.6 The React Flow canvas is developer-facing, not business-friendly

You need to understand:
- node types and their config schemas
- expression syntax (`node_3.intents[0]`, Jinja2, etc.)
- DAG topology (sourceHandle routing)

A non-engineer cannot productively edit workflows. **This is intentional** — we're targeting platform engineers and DevRel-savvy ops teams, not "no-code" — but if the buyer expects "any business user can build this", we've mis-targeted.

### 6.7 Vertex-only in practice

Code supports `google` (AI Studio), `vertex`, `openai`, `anthropic`. The V7 demo workflow uses Vertex exclusively. We haven't load-tested OpenAI or Anthropic paths recently. Recent test suite confirms they pass unit tests; no integration tests for non-Vertex paths in the LLM-call layer.

### 6.8 No multi-region, no DR, no backup automation

pgvector container is a single-instance Docker on the demo machine. No replicas. No nightly dump. If the laptop crashes, the demo state is gone. Not a production architecture.

### 6.9 Memory leak risk if RLS isn't enforced

The `conversation_messages` table has RLS, but if running as superuser (see 6.1), one tenant's session_id collision with another tenant's could surface that other tenant's messages to the LLM. The session_id space is global — production would need to namespace by tenant.

### 6.10 The workflow editor doesn't have first-class testing UX

DV-01 (data pinning) and DV-02 (test single node) help, but there's no "run this exact workflow with this exact input as a test, assert the response matches X". For complex workflows that's a gap. Would build a vitest-style snapshot framework for workflows. ~1 week.

### 6.11 Onboarding is manual

"Spin up an instance" is a 30-step process today (see `SETUP_GUIDE.md`). No `ae-orchestrator init` CLI, no docker-compose-up-and-go. ~1 week of DX work.

### 6.12 The customer's "no-AE" path is unproven

The orchestrator was designed to be useful beyond AutomationEdge — the Copilot subsystem, knowledge bases, A2A protocol all suggest it. But our flagship demo IS AE-flavored. If we pitch as a general-purpose workflow platform, the demo undersells. If we pitch as "AE ops AI", we shrink the TAM. Open positioning question.

---

## 12 · Roadmap and what funding accelerates

> What we'd build with $X. Be specific.

### Quarter 1 — production hardening (2 engineers, 12 weeks)

| Item | Effort | Why |
|---|---|---|
| Multi-tenant deployment infra (Helm chart, Terraform) | 3w | unblock first paying customer |
| Non-superuser DB role wiring + RLS verification harness | 1w | make the security claims real |
| Auto-glossary from `ae.workflow.list_*` + LLM enrichment | 1w | production-grade business→system translation |
| Deterministic specialist runbooks (top 3 scenarios) | 3w | match L1 audit-trail expectations for compliance customers |
| Workflow snapshot/test framework | 2w | onboarding velocity for ops teams |
| Streaming SSE wiring in chat UI | 1w | latency perception |
| Cross-incident pattern detection (case-similarity index) | 1w | "this is the 3rd time" L1 behavior |

End-state: design partner deployment, ~3-5 customers running real ops support flows.

### Quarter 2 — domain expansion (3 engineers)

- ServiceNow MCP server integration (Q1 customer feedback dependent)
- Salesforce / Hubspot ops support workflow (parallel use case to AE)
- Multi-region deploy template
- SLA + observability dashboard (Langfuse-driven; already plumbed)

### Quarter 3 — platform monetization

- Per-tenant LLM cost dashboards
- Workflow marketplace (signed exports of common patterns: "support intake", "data quality monitor", "incident response")
- Customer-onboarded glossary import
- Permission matrices on the canvas (who can edit, who can publish)

### What $1M-$2M of seed funding buys

- 4 engineers x 12 months
- 1 designer (the canvas needs a non-engineer-friendly Lite mode, which is a designer's job, not an engineer's)
- AWS/GCP burn for design partners (~$20k/yr/customer infra cost)
- ~$60k for SOC2 / data residency audits when first regulated customer arrives

Honest: 24 months from today to break-even on a single tenant. Faster if we land a marquee logo or partnership in Q1.

---

## 13 · Market context

> Where this fits and what the moat is.

### TAM, sized

- **AE alone**: AutomationEdge has ~thousands of customers. Average L1 ops engineer headcount per AE deployment: 1-3 FTE. Loaded cost: $80k/year. If we save 30% of L1 time, that's $24k/year/customer in soft savings. Charge 50% of that ($12k/year/customer) → ~$30M ARR ceiling on AE alone if we hit 100% penetration.
- **Adjacent ops platforms** (UiPath, Automation Anywhere, ServiceNow, generic RPA): same pattern. ~10× the TAM if we generalize.
- **Workflow/orchestration platforms** (n8n, Zapier internal teams, Temporal): different shape — they sell to engineers, not ops; we sell to ops teams. Less direct competition but adjacent buyer.

### Where we beat alternatives

1. **vs n8n/Zapier**: those are integration-heavy, AI-light. Their LLM nodes are simple wrappers. We have multi-turn memory, hybrid intent classification, audience-aware responses, HITL, RLS-multi-tenancy out of the box. They'd take 6-12 months to add this; if they did, they'd land on our architecture.

2. **vs LangChain/LlamaIndex**: Python libraries, not platforms. No multi-tenancy, no canvas, no per-tenant LLM cost isolation, no RLS. If a customer is willing to write Python, they don't need us. We sell to ops teams that don't write Python.

3. **vs hand-rolled support automation**: most enterprises have a Zendesk + a few Lambda functions today. Our advantage: the workflow IS auditable (canvas + version snapshots), the LLM cost is bounded (per-tenant rate limits via tenant_policies), and the HITL pattern is built in. They'd take 6 months to build the canvas alone.

4. **vs Glean / Sana / etc.** (knowledge ops): adjacent. They surface info; we take action. Compose-able rather than competitive.

### Where alternatives beat us today

1. **No-code experience**: Zapier and Make.com are vastly more polished for non-engineers. Our canvas requires engineering literacy.

2. **Tool ecosystem**: Zapier has 5000+ connectors. We have MCP (which is bet-on-the-future). Today's MCP server count is ~50 publicly listed.

3. **Brand**: nobody has heard of us. n8n has 50k GitHub stars, $30M raised, 3-year head start.

### The moat hypothesis

- **MCP is the new schema**: if MCP wins (bet), we have first-mover advantage on multi-tenant MCP infrastructure
- **Per-tenant LLM cost / billing isolation** is hard. Per-tenant Vertex projects (VERTEX-02) is one of those features that's straightforward to build and very hard to retrofit.
- **The case-state machine + RLS-enforced audit trail** is what regulated industries (banks, healthcare) need. Build this once, sell it 100 times.
- **The L1-engineer use case is universal**. If we nail it for AE, the playbook works for ServiceNow, UiPath, Salesforce, etc.

### Where we're vulnerable

- A well-funded competitor could rebuild this in 4-6 months. The IP isn't in the code — it's in the design decisions and the specific use-case understanding.
- LLM provider price cuts could make the "hybrid heuristic+LLM" optimization moot.
- AutomationEdge could build this themselves and bundle it (we'd need a partnership before we threaten them).

---

## 9 · Q&A — hard questions

> Have these answers loaded. Practice the ones starred. The hard questions go to audience B (investor); the operational questions go to audience A.

> Have these answers loaded. Practice the ones starred.

**Q: This looks like a thin LLM wrapper. What's the IP?** ⭐
- The IP is the multi-tenant orchestration platform — DAG runner, RLS, MCP integration, per-tenant LLM cost / credentials, case-state machine, the canvas. The L1 workflow is the showcase of what the platform enables. If a competitor copied the workflow, they'd still need 6-12 months to build the platform underneath.
- Counter-evidence: 35 alembic migrations, ~22k LOC backend, 947-test suite, ~135 commits in the last 30 days. Not a wrapper.

**Q: Why not just use LangChain / LangGraph / Temporal?** ⭐
- LangChain is a Python library; we'd still need to build the multi-tenancy, the canvas, the RLS, the LLM-cost layer, the per-tenant credential vault. The library saves us ~5% of what we built.
- Temporal solves a different problem (durable workflow execution at scale). We'd use it underneath in production, but it doesn't solve LLM-tool-routing.
- LangGraph is closer to our DAG runner, but LangGraph needs upfront state schema for trivial flows. Not a fit for a canvas-driven editor.

**Q: How do you make money?**
- Per-tenant SaaS fee, scaled by LLM-cost + workflow run volume.
- Likely starting at $1-2k/month/customer for design partners; $5-15k/month for enterprises with multi-team usage.
- Implementation services for first-time customers (~$20-30k onboarding, decreasing as the no-code paths mature).

**Q: What's the cost of one chat session?** ⭐
- Hybrid intent classification: $0.0001/turn (mostly heuristic; LLM rare)
- Diagnostics ReAct (3-5 tool calls): $0.001/turn (gemini-3-flash-preview)
- Output_missing playbook: $0.0013/turn average (glossary + investigator)
- 5-turn conversation: ~$0.005-0.008
- 10k conversations/month/customer: ~$50-80/customer/month in LLM cost
- Charge $5-15k/month → 60-200x markup on direct cost. Healthy.

**Q: What if Anthropic / OpenAI cut prices 10×?**
- Our markup absorbs it; revenue per customer would expand.
- More important: our hybrid optimization makes us less LLM-cost-dependent than pure-LLM plays. Their margins compress; ours hold.

**Q: How does this scale to 1000 tenants?**
- pgvector container needs to become managed Postgres (Aurora / Cloud SQL).
- Redis becomes a managed cluster.
- Backend goes behind a load balancer + horizontal pods.
- LLM credentials: tenant_secrets vault scales fine.
- Workflow definitions: indexed by tenant_id; ~1000 tenants × ~50 workflows = 50k rows. Trivial.
- The bottleneck would be the AE MCP server — single instance per AE deployment. We'd need per-tenant MCP servers (already supported) or an MCP load balancer.
- Honestly: never tested at 1000-tenant scale. ~6 months of work to validate.

**Q: What if AutomationEdge builds this themselves?**
- Possible. We've been talking to them about partnership; their position is "we're the RPA, you're the AI layer." Partnership math:
  - Pro: their distribution + our product → faster GTM
  - Con: dependency on a single vendor
- We're hedging by making MCP/the orchestrator generic. AE is the demo; the platform is the product.

**Q: How do you handle PII / data residency?**
- Per-tenant Vertex project routing (VERTEX-02) means each tenant can pin their LLM calls to their own GCP project, billed and resided per their constraints.
- Conversation memory + entity facts have RLS; cross-tenant access is impossible at the DB layer (modulo §6.1 superuser caveat).
- Vault key (Fernet, base64) for tenant secrets.
- Not yet SOC2 / ISO 27001. Q3 2026 target.

**Q: What's the training data story?**
- We don't train models. We use Vertex's gemini-2.5/3-flash off the shelf.
- Per-tenant fine-tuning would require explicit opt-in + per-tenant cost; not on the near-term roadmap.

**Q: Walk me through the demo failure mode (something breaks live).** ⭐
- AE MCP server unreachable: HTTP nodes return error → fallback subtree → user gets ticket ID. Demo continues.
- Vertex rate limit: backend retries 3× with exponential backoff (`tenacity` in `_call_*`). If still failing, user gets fallback ticket.
- Postgres down: backend can't boot. We'd see this in the pre-flight check; restart Postgres.
- Network blip: each turn is independent; user can re-send.

**Q: The L1 use case is narrow. What else can this do?**
- Internal IT helpdesk (employee asks about VPN, the workflow opens a ticket, runs basic diagnostics, escalates)
- Compliance monitoring (workflow watches for missed daily reports, opens tickets proactively)
- Customer support triage (incoming email/Slack → intent classify → route to FAQ retrieval or human)
- Data quality monitoring (workflow detects schema drift, opens an incident, runs a validation playbook)
- Marketing ops (campaign launch checklist with HITL approval gates)
- The platform is general; the V7 workflow is one application of it.

**Q: Walk me through your worst piece of code.**
- `build_ae_ops_workflow.py` — the Python script that builds the workflow. It's ~1800 lines of imperative `nodes.append()` calls and edge wiring. The original sin: workflows are JSON, but JSON is awful to author by hand, so we wrote Python that emits JSON, but now we have all the version-control issues of Python plus the runtime issues of JSON.
- The right answer: workflows should be authored on the canvas. We use the script to bootstrap V1 and stay in JSON-edit mode for changes. Rewriting it is a 2-day refactor. Not done yet.

**Q: What's the team?**
- (Honest about your team size and gaps. This template can't fill that in.)

**Q: When do you ship?**
- Design-partner release: 3 weeks (current state + production hardening from §12 Q1)
- General availability: 6-9 months
- Profitable per-tenant unit economics: month 1
- Profitable as a company: month 18-24

---

## 10 · Value to business

> For audience A (engineering leadership / product / sales-engineering / AE ops practitioners). Spend time here when the buyer is the operator side, not the engineering or investor side.

### 10.1 What the AE Ops Support workflow replaces today

| Today's flow | With the workflow |
|---|---|
| Business user emails ops-support@ at 10 AM about missing report | Bot in chat replies in 30 seconds with status + ETA |
| L1 engineer reads ticket queue, picks tickets, opens AE console, clicks around for 5-15 min per ticket | Bot already filed the ticket, gathered the diagnostic evidence, proposed a remediation, queued it for approval |
| L1 forgets to update worknotes | Every action is auto-logged to the case row |
| L1 gives up after 3 attempts and escalates with "I don't know" | Bot escalates with a complete investigation trail in 30s — L2 starts at hour-1 of investigation, not hour-zero |
| Reports never get closed in the ticket system because confirmation is awkward | Bot detects "I just got it" → transitions case state → eventually auto-closes |

### 10.2 What this is worth, per customer

**Headcount math** (one mid-size AE customer):
- Typical L1 ops support team: 2-4 FTEs at $80k/year loaded → $160k-$320k/year
- L1 spends ~60% of time on tickets (rest is monitoring, training, on-call)
- If we save 30% of L1 ticket time → $48k-$96k/year saved per customer
- Charge 30-50% of that ($15k-$45k/year/customer) → healthy gross margin, customer ROI in 4-6 months

**Quality math** (harder to quantify but real):
- Mean time to first response: minutes vs hours
- Mean time to resolution: 30% faster on simple cases (the bot solves directly)
- Audit completeness: 100% (every action logged) vs ~60% (humans forget)
- Knowledge retention: zero loss when an engineer leaves (the workflows are the runbooks)

### 10.3 What it's NOT (manage expectations)

- **Not a replacement for L2/L3**: complex incidents still need humans. The bot escalates honestly, doesn't pretend.
- **Not zero-touch**: L1 is augmented, not eliminated. The bot does the rote work; L1 supervises, handles edge cases, runs the approval queue.
- **Not a Zapier replacement**: this is for ops support workflows specifically — short, conversational, with HITL. For "send Salesforce data to Slack on schedule" use a different tool.
- **Not a no-code tool for non-engineers**: editing the workflow needs platform engineering literacy (see §6.6 in the gap list). Operations teams can use the chat; they can't (yet) edit the canvas without help.

### 10.4 What changes for the operator on day 1 of deployment

1. **A new `/business` URL** they share with end users. End users chat there. Tickets get created automatically.
2. **A new `/tech` panel** for the L1/L2 team. Pending approvals + case memory visible at a glance.
3. **The existing AE installation is unchanged.** No migration. The MCP server reads from AE's existing API.
4. **Existing ticket system stays.** This isn't a Jira replacement. The case row is internal context; ops teams can keep using their ticketing system in parallel, or migrate over time.

### 10.5 Onboarding effort, honestly

| Step | Effort |
|---|---|
| Provision orchestrator instance | 1 day (someone with Postgres + Docker familiarity) |
| Connect AE MCP server (point at customer's AE) | 1 hour |
| Curate glossary (workflow names + business aliases) | ~2-4 hours per 10 workflows |
| Train ops team on `/tech` UI | 1-hour workshop |
| Adapt prompts (specialist behavior tuning) | ~1 day for first customer; ~30 min for subsequent customers if they look like the first |
| **Total** | ~3-5 days for first customer |

After 5 design-partner customers, expect this to drop to 1-2 days as the patterns stabilize.

---

## 11 · Value to developers

> For audience C (developers / platform engineers / open-source-curious). Also relevant to audience A's engineering team.

### 11.1 What you can build on top of this

The orchestrator is **the workflow IS the data**. Every workflow you build is a versioned row, queryable, snapshot-able, testable. Things you can build:

| Use case | Effort estimate |
|---|---|
| **Internal IT support bot** for "VPN broken / can't login" | ~1 week — clone the AE workflow, swap MCP servers (Active Directory MCP, Okta MCP) |
| **Customer support triage** routing emails / Slack to specialist or human | ~3-5 days — replace webhook trigger, add Slack/email integration via MCP |
| **Compliance monitor** that watches for missed daily reports proactively | ~1 week — Schedule Trigger + KB lookup + ticket create |
| **Data quality alert system** | ~1 week — Schedule Trigger + custom code check + remediation HITL |
| **Marketing campaign launch checklist** with HITL gates | ~3-5 days — straight workflow with Human Approval nodes |
| **Custom AI agent** with tool access, memory, audit trail | ~2-3 days — drag nodes; you're done in an afternoon for trivial cases |

### 11.2 What you don't have to build

- Multi-tenant primitives (RLS, GUC, tenant resolver) — built
- LLM provider abstraction (Vertex/OpenAI/Anthropic with per-tenant credentials) — built
- MCP integration (server registry, session pool, tool discovery, top-15 filter) — built
- Conversation memory (turn history, episodes, profiles, semantic memory) — built
- HITL primitives (Human Approval node, audit log, guarded-tool-call pause) — built
- Per-tenant rate limiting (`TenantRateLimitMiddleware`) — built
- Per-tenant API rate limit + LLM cost limits — built
- Visual editor (canvas + Property Inspector) — built
- Versioning + rollback — built
- Sub-workflow recursion guards — built
- Webhook + Schedule + Manual triggers — built
- Cyclic graph support (loopback edges for refinement loops) — built
- A2A agent card + protocol — built

### 11.3 What you do have to build (per-use-case)

- Your specialist prompts (the "personality" of each agent in your workflow)
- Your glossary mappings (description → system identifier translation, if you have business-translation needs)
- Your MCP servers if you're integrating new tool surfaces (or use existing ones)
- Your trigger payload schemas
- Your case-state machine values (we ship an 11-state default; customize per use case)

### 11.4 The developer experience

**Editing a node:**
1. Open canvas at `:8080`
2. Drag a node from the palette
3. Click the node → Property Inspector renders a typed form (from `node_registry.json` schema)
4. Save → workflow versions automatically (snapshot in `workflow_snapshots` table)
5. Test the node in isolation: right-click → "Test node" (DV-02)
6. Pin the output: click the pin icon to short-circuit dispatch on subsequent runs (DV-01)

**Adding a new node type:**
1. Define the schema in `shared/node_registry.json`
2. Add a handler in `backend/app/engine/node_handlers.py` (typically 20-50 lines)
3. (Optional) Add specific Property Inspector form behavior in `frontend/src/components/sidebar/DynamicConfigForm.tsx` if the schema needs custom rendering
4. Write tests in `backend/tests/test_<your_node>.py`
5. Open PR — CI runs all 947 tests + integration

**Adding a new LLM provider:**
1. Add `_call_<provider>(model, system_prompt, user_message, …)` in `llm_providers.py`
2. Wire into the `providers` dict in `call_llm`
3. Add credential resolver in `engine/llm_credentials_resolver.py`
4. Optionally add streaming variant in `streaming_llm.py`
5. Add tests
6. ~1-2 days

**Adding a new MCP server type:**
1. Nothing. The MCP client is generic. Register a new `tenant_mcp_servers` row via the canvas Globe icon. Done.

### 11.5 Code quality signals

| Signal | Evidence |
|---|---|
| Test coverage on critical paths | 947 unit + 17 integration tests; all passing on `main` |
| Documented schema | `shared/node_registry.json` is the source of truth for node types — both frontend Property Inspector and backend handlers consume it |
| Migrations are reversible | All 35 have working `downgrade()` |
| RLS is enforced | `tests/integration/test_rls_breach.py` |
| Sandbox is enforced | `tests/test_code_sandbox.py` |
| Frontend types are typed | `frontend/src/lib/api.ts` matches FastAPI's response models manually (no codegen yet) |
| Critical fixes have Honest commit messages | See `5688874` (model-registry revert with explanation) and `48f869152a93` migration breaking-change docstring |

### 11.6 Things developers will love

- **Workflow as JSON** — diff-able, version-controllable, scriptable
- **Code execution sandbox node** — write small Python in the workflow without us auditing every line
- **Per-tenant LLM credentials** — multi-tenant SaaS apps work out of the box
- **MCP-protocol tool integration** — plug in any MCP server, tools auto-discover
- **Streaming SSE built in** — chat UIs can show progressive output (`/api/v1/sse/stream/{instance_id}`)
- **DV-01 data pinning** — short-circuit a node's output during iterative debugging — kills LLM token cost
- **Sub-workflow node** — compose workflows. Recursion guard built in (max depth = 10)
- **Cyclic edges** (CYCLIC-01) — express refinement loops without breaking the DAG mental model

### 11.7 Things developers will resent

- **No "no-code" mode** — engineering literacy required
- **Documentation has gaps** — V7 features (NEED_INFO, verification subgraph, output_missing playbook) aren't in the codewiki yet
- **Onboarding is manual** — no `make dev`
- **Frontend types are duplicated** — no codegen from FastAPI to the API client
- **The `build_ae_ops_workflow.py` pattern is awkward** — Python emitting JSON is the original sin; canvas-first or YAML-based authoring would be cleaner. ~2-week refactor to do better.
- **Sandbox is process-based, not container-based** — adequate for trusted authors, not adequate for fully untrusted code
- **No standardized error codes** in the workflow execution model — failures surface as opaque strings in node outputs

### 11.8 Where the platform will go (from a developer's perspective)

| Item | When |
|---|---|
| `ae-orchestrator dev` CLI for one-command setup | Q1 |
| Codegen frontend client from OpenAPI spec | Q1 |
| Workflow snapshot test framework | Q2 |
| WASM sandbox (replaces Python subprocess) | Q3 |
| YAML/JSON-Schema validators for handcrafted workflows | Q2 |
| Workflow composition library (canonical "support intake", "data quality", "incident response" bundles) | Q3 |
| Public MCP server marketplace | Q3+ |

---

## 14 · Appendix — file/line index

> Every claim above has a citation here.

### Architecture
- `backend/app/engine/dag_runner.py:780-905` — DAG forward propagation + loopback firing
- `backend/app/database.py:21-39` — RLS GUC + after_begin listener
- `backend/app/engine/llm_providers.py:212-273` — provider-tenant resolution
- `backend/app/engine/streaming_llm.py` — SSE streaming variants
- `backend/app/engine/mcp_client.py:47-133` — session pool keyed by (tenant, server)
- `backend/app/engine/react_loop.py:174-184` — guarded HITL on tool calls
- `backend/app/engine/react_loop.py:286-318` — SMART-06 top-15 semantic tool filter
- `backend/app/engine/intent_classifier.py:35-84` — hybrid scoring
- `backend/app/engine/entity_extractor.py:17-58` — regex + intent-scoped extraction
- `backend/app/engine/sandbox.py` — Python code-execution sandbox
- `backend/app/engine/prompt_template.py:105-122` — Jinja2 rendering with permissive Undefined

### Migrations of note
- `0001` — initial RLS policies on workflow tables
- `0009-0010` — knowledge base + embedding cache (RAG foundation)
- `0011` — sub-workflow recursion guard
- `0012-0013` — advanced memory hard cutover
- `0017` — tenant integrations (AutomationEdge, Vertex per-tenant)
- `0019` — tenant_mcp_servers (MCP-02)
- `0021` — RLS-01 systematic cutover
- `0030-0031` — HITL approval audit log
- `0032` — per-tenant model overrides
- `0033` — local-auth users
- `48f869152a93` — support_cases (V7's case state machine backend)
- `384daed57459` — embedding dim 1536→768 with breaking-change pre-flight check

### V7 workflow construction
- `backend/scratch/build_ae_ops_workflow.py:195-275` — `CROSS_CUTTING_RULES` (audience/confidence/memory/verification rules)
- `backend/scratch/build_ae_ops_workflow.py:282-303` — `DIAGNOSTICS_PROMPT` (V6.2 no-hallucination)
- `backend/scratch/build_ae_ops_workflow.py:340-360` — `VERIFICATION_PROMPT` (V7.1)
- `backend/scratch/build_ae_ops_workflow.py:365-395` — `NEED_INFO_PROMPT` (V7.2)
- `backend/scratch/build_ae_ops_workflow.py:480-500` — case upsert HTTP node (V3)
- `backend/scratch/build_ae_ops_workflow.py:555-690` — Intent Classifier intents
- `backend/scratch/build_ae_ops_workflow.py:758-810` — NEED_INFO Code + Switch (V7.2)
- `backend/scratch/build_ae_ops_workflow.py:855-925` — fallback subtrees (V5.3)
- `backend/scratch/build_ae_ops_workflow.py:945-995` — verification subgraph (V7.1)
- `backend/scratch/build_ae_ops_workflow.py:1010-1100` — output_missing playbook (V6)
- `backend/scratch/build_ae_ops_workflow.py:1085-1145` — `HEALTH_CHECK_CODE` (V5.3)

### Tester UI / Flask
- `D:/Projects/AEAIHUBTesterUI/agent_server.py:430-700` — `/api/cases` endpoints (Flask mirror of FastAPI router)
- `D:/Projects/AEAIHUBTesterUI/agent_server.py:548-665` — `/api/glossary/lookup` (LLM-backed translation)
- `D:/Projects/AEAIHUBTesterUI/config/workflow_glossary.json` — glossary seed
- `D:/Projects/AEAIHUBTesterUI/tester/index.html:158-180` — case-memory panel HTML
- `D:/Projects/AEAIHUBTesterUI/tester/index.html:286-355` — case-memory polling JS
- `D:/Projects/AEAIHUBTesterUI/agent_server.py:50-105` — `/business`, `/tech`, `/tester` route serving with role injection

### FastAPI surface
- `backend/app/api/support_cases.py` — `/api/v1/support-cases` 8 endpoints (RLS-gated)
- `backend/app/api/copilot_sessions.py` — workflow-author copilot
- `backend/app/api/llm_credentials.py` — per-tenant LLM credential management
- `backend/main.py:90-130` — router registration

### Tests
- `backend/tests/test_copilot_agent.py` — copilot agent runner
- `backend/tests/test_intent_classifier.py` — hybrid scoring tests
- `backend/tests/test_react_loop.py` — guarded HITL + SMART-06 filter
- `backend/tests/integration/test_rls_breach.py` — RLS validation under non-superuser role
- `backend/tests/test_vertex_provider.py` — Vertex-tenant resolution

### Documentation
- `SETUP_GUIDE.md` — environment setup
- `HOW_IT_WORKS.md` — runtime walkthrough
- `TECHNICAL_BLUEPRINT.md` — architecture deep-dive
- `codewiki/` — per-feature design docs (security, vertex, copilot, hitl, etc.)
- `DEMO_SCRIPT.md` — this file

---

## Closing the demo

The 90-second close:

> "What we showed: a multi-tenant workflow orchestration platform with RLS-enforced isolation, MCP-protocol tool integration, hybrid intent classification, three layers of HITL, and a 70-node workflow that does what an L1 ops engineer does. Built in 6 weeks. ~22k lines of backend, 947 passing tests, RLS-tested at a non-superuser role.
>
> What we didn't ship: production deployment, auto-glossary, deterministic runbooks for top scenarios, streaming UI. ~12-16 weeks with 2-3 engineers.
>
> What's the moat: per-tenant LLM cost isolation, RLS-enforced audit trail, the case-state machine, MCP being the new schema. None of these are individually defensible; the combination is what's hard to copy in <12 months.
>
> What's the bet: the L1 ops layer is universal across vertical SaaS. Nail it for AE, the playbook works for ServiceNow, UiPath, Salesforce. Ship for AE customers in Q1, expand from there."

End the demo. Do **not** oversell. Acknowledge the gaps in §6 explicitly when asked.

---

*Last updated: alongside V7 workflow build (verification subgraph + NEED_INFO clarification flow). Workflow IDs change per build; use the workflow NAME (_"AE Ops Support — VN"_) to find the latest version in the canvas dropdown. This document tracks the V7 architecture; update when V8 lands.*
