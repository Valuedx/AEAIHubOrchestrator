# V8 — implementation notes

Companion to `build_ae_ops_workflow_v8.py`. Captures design decisions, known limits, and follow-up work.

## What V8 is

A simplified AE Ops Support workflow. ~28 nodes (vs V7's 76). One Worker ReAct + Verifier ReAct + small-talk / RCA / handoff / cancel canned-reply branches. Routes via Intent Classifier (5 intents). Case state managed via MCP tools (case.add_worknote / update_state / handoff / close / add_evidence / get) which are pinned ALWAYS-AVAILABLE.

## Model tiering (cost-aware)

| Role | Model | Rationale |
|---|---|---|
| Worker (hot path) | gemini-3-flash-preview | Best Flash-tier tool savvy; ~3-8 tool calls/turn |
| Router (Intent Classifier) | gemini-2.5-flash | Fast structured output, predictable |
| Verifier (read-only critic) | gemini-2.5-flash | Compare task; doesn't need 3-flash savvy. maxIterations=2 |
| RCA writer (no tools) | gemini-2.5-flash | Synthesis is enough at this tier with structured prompt |
| Small-talk / handoff / cancel | gemini-2.5-flash | Cheapest; tight max_tokens (192) |

Pro tier was deliberately rejected for RCA — 4-10× cost premium without measured quality benefit at L1 ops scope. Re-evaluate if eval harness shows quality drop.

## Prompt engineering

Worker prompt is split into:
  - **STATIC prefix** (everything that doesn't change between turns) — Vertex prefix-cacheable
  - **DYNAMIC context block** at the very end — per-turn variables only (user_role, intent, case_id, glossary match, etc.)

The static section now includes an **AutomationEdge architecture primer** so the agent reasons better about failure-cause chains (request → agent → workflow → schedule → output) and standard remediations.

## Tool tiers

The Worker prompt names two tiers explicitly:
  - **ALWAYS-AVAILABLE** (pinned, never SMART-06-filtered): case.*, glossary.lookup, google_search.
  - **SMART-06-FILTERED**: top-15 from the AE MCP catalog (~116 tools).

Best-practice call-out from OpenAI's *Practical Guide to Building Agents*: tool **descriptions** matter more than tool count. The case + glossary tool descriptions in `mcp_server/tool_specs.py:_CURATED_TOOL_OVERRIDES` follow the use-when / avoid-when / parameter-docs / examples pattern.

## Pending wiring — google_search

The Worker prompt advertises `google_search` as ALWAYS-AVAILABLE, but the tool isn't wired yet. Two implementation options:

**Option A — Vertex built-in grounding** (preferred long-term):
  - Gemini 3 series supports combining built-in tools (`google_search`, `code_execution`) with custom function calls in one turn (`tool_context_circulation` flag).
  - Requires refactor of `backend/app/engine/llm_providers.py:_call_google_backend` to pass `types.Tool(google_search=types.GoogleSearch())` alongside custom function declarations, AND for ReAct's tool-routing logic to recognise built-in tool turns.
  - Bigger orchestrator-core change. Benefits every tenant.

**Option B — MCP-side wrapper** (faster to ship):
  - Add `mcp_server/tools/web_tools.py` exposing `google_search(query, num_results=5)` that calls Google Programmable Search Engine (CSE) API. Requires `GOOGLE_CSE_API_KEY` + `GOOGLE_CSE_ENGINE_ID` env vars.
  - Register as `web.search` (or `google_search` for prompt parity) in `tool_specs.py`, marked ALWAYS-AVAILABLE, safety=`safe_read`.
  - Add a clear "use when an unknown error code / SaaS name / product term comes up" description.
  - Deferred this session due to scope.

Pick A if you're already planning a llm_providers.py refactor; B if you want the capability live in <1 hour.

## V7 → V8 regression risks (also in builder docstring)

PRESERVED: audience-aware language, no-hallucination rule, memory awareness, glossary translation, verification-after-destructive, HITL gate, case open at session start, handed-off short-circuit reply.

MITIGATED: per-specialist case-state PATCHes (V7 had ~7 explicit transition nodes; V8 has 3 PATCH paths + Worker-driven state via case.update_state once the MCP server is restarted). Audit trail moves from canvas Switch+HTTP nodes to tool-call log.

LOST: V7's dedicated NEED_INFO subgraph (Code+Switch+LLM that ASKED before Worker ran) — V8 relies on Worker prompt to ask. Functionally same UX, but case-state explicitness drops from "always parks at NEED_INFO" to "Worker asks, may forget to call case.update_state(NEED_INFO)". Eval harness exercises this in the `missing-identifier-business-vague` case.

## Eval harness

`scratch/run_ae_ops_evals.py` + `scratch/ae_ops_eval_transcripts.json` (12 cases). Run side-by-side V7 vs V8:

```
ORCHESTRATOR_BASE_URL=http://localhost:8001 \
python backend/scratch/run_ae_ops_evals.py \
  --workflow <V7-id>:V7 \
  --workflow <V8-id>:V8 \
  --out v7_vs_v8.md
```

## Operating notes

  - V8 needs a fresh AE MCP server (port 3000) start to pick up case/glossary tool registrations from `mcp_server/tools/case_tools.py` AND new `.env` credentials.
  - Tester UI Flask (port 5050) must be running for `case.*` and `glossary.lookup` tool calls to land — the MCP tools are HTTP-proxied to its `/api/cases` and `/api/glossary/lookup`.
  - Orchestrator (port 8001) connects to the MCP server via `tenant_mcp_servers.url` (default tenant) — not env-var fallback.
