"""Build + POST AE Ops Support — V8 (router + worker + critic).

V8 is a deliberate simplification of V7 grounded in named patterns:

  - Anthropic "Building Effective Agents" (Dec 2024) — routing,
    evaluator-optimizer, "find the simplest solution; only increase
    complexity when needed."
  - OpenAI "Practical Guide to Building Agents" (Apr 2025) — single
    agent until you have a reason not to; tool descriptions matter
    more than tool count; structured outputs; explicit step limits.
  - 12-Factor Agents — own your prompts, own your context window.

Architecture (~13 nodes vs V7's 76):

  trigger → load_state → upsert_case
              └─ switch(case state)
                   ├─ HANDED_OFF → handed_off_reply ─┐
                   └─ Active →
                       glossary_lookup
                         └─ intent_router (Intent Classifier, hybrid)
                              └─ switch(intent)
                                   ├─ small_talk → small_talk_reply ─────┐
                                   ├─ rca       → rca_writer ────────────┤
                                   ├─ handoff   → handoff_patch ─────────┤
                                   ├─ cancel    → cancel_reply ──────────┤
                                   └─ ops (default) → worker_react ──────┤
                                                       └─ verifier_react ┤
                                                                          ├─→ bridge → save → END

Single Worker handles diagnostics + remediation + output_missing +
NEED_INFO + corrections + resolution_update. Its system prompt carries
the cross-cutting rules (audience, no-hallucination, glossary use, ask
one question when an identifier is missing, end with a verification
read after destructive). Destructive tools self-gate via the engine's
guarded HITL pattern — the Worker doesn't need to know.

Verifier is a separate ReAct with read-only tools and no chat history
of the action it's checking — fresh context catches what the Worker
might rationalise.

Run: ORCHESTRATOR_BASE_URL=http://localhost:8001 python -m scratch.build_ae_ops_workflow_v8

================================================================
V7 → V8 regression risk inventory
================================================================
Marked PRESERVED / MITIGATED / LOST. Verify with the eval harness
(scratch/run_ae_ops_evals.py).

PRESERVED (same behaviour, possibly via different mechanism):
  - Audience-aware language (business vs tech): same prompt text, just
    consolidated into the Worker prompt's static prefix.
  - No-hallucination diagnostics rule: in Worker prompt.
  - Memory awareness (corrections, additive turns): in Worker prompt.
  - Glossary translation for business-language descriptions: glossary
    HTTP node still runs; result injected into Worker's dynamic context.
  - Verification after destructive actions: separate Verifier ReAct node
    with read-only tools, fresh context — actually stronger than V7 here
    because the Worker can't rationalise its own action.
  - HITL gate on destructive tools: engine-level, identical to V7.
  - Case open/upsert at session start: identical HTTP node.
  - Handed-off short-circuit reply: same canned-reply branch on
    case state.

MITIGATED (same outcome, slightly lower granularity):
  - Per-specialist case-state PATCHes (V7 had ~7 PATCH nodes for
    explicit state transitions like NEED_INFO, EXECUTING,
    RESOLVED_PENDING_CONFIRMATION, audit-worknote-on-withdraw).
    V8 reduces these to: open at start, handoff PATCH on /handoff
    intent, close PATCH on /cancel intent. NEED_INFO and EXECUTING are
    not currently PATCHed (Worker just asks the question or runs).
    Mitigation path: expose case.update_state as an MCP tool the
    Worker can call — see follow-up task #50.
  - Audit trail: V7 had every state transition visible on canvas via
    Switch + HTTP nodes. V8's audit trail is the Worker's tool-call log
    (already captured by the engine). Same fidelity for compliance
    review, just in a different format. Mitigation: ensure tool-call
    log is exposed in the case worknotes view.

LOST (or measurably weaker — flag in eval):
  - V7 had a dedicated NEED_INFO subgraph (Code+Switch+LLM) that ASKED
    a targeted question AND parked case state at NEED_INFO before the
    Worker ever ran. V8 relies on the Worker's prompt to ask the
    question — works but the case state stays at the upserted "NEW"
    until the next turn. Operationally the user experience is the same
    (one question, no re-asking) but the case-state machine is less
    explicit. Eval harness must include "missing-identifier on first
    turn" cases.
  - V7 had a dedicated output_missing investigator prompt with
    glossary-resolved workflow_id baked into the system message. V8's
    Worker handles output_missing via a generic prompt + glossary
    context. Quality may degrade on glossary-resolvable workflows that
    the Worker doesn't pick up the hint for. Eval harness must include
    "I haven't received my X" cases for both glossary-match and
    no-match paths.
  - V7's hybrid Intent Classifier had 7+ intents with carefully tuned
    priorities and example sets (resolution_update, correction,
    cancel_or_withdraw, output_missing). V8 collapses output_missing,
    resolution_update, and correction under "ops" — the Worker
    distinguishes them at run time. Loss: the LLM Worker may be slower
    or less reliable than the lexical classifier on canonical phrasings.
    Mitigation: keep all the tuned intents in the V8 router; the
    "ops" branch becomes the catch-all for less-clean phrasings.
================================================================
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx


ORCH_URL = os.environ.get("ORCHESTRATOR_BASE_URL", "http://localhost:8001")
TENANT_ID = os.environ.get("TENANT_ID", "default")

GEMINI_FLASH = "gemini-3-flash-preview"
GEMINI_25_FLASH = "gemini-2.5-flash"
GEMINI_25_PRO = "gemini-2.5-pro"


# ---------------------------------------------------------------------------
# Node + edge helpers (mirror V7 shapes so the engine renders them identically)
# ---------------------------------------------------------------------------

def _node(node_id: str, label: str, config: dict, *, x: int, y: int,
          display_name: str | None = None, category: str = "agent") -> dict:
    return {
        "id": node_id,
        "type": "agenticNode",
        "position": {"x": x, "y": y},
        "data": {
            "label": label,
            "config": config,
            "status": "idle",
            "displayName": display_name or label,
            "nodeCategory": category,
        },
    }


def _edge(source: str, target: str, *, label: str | None = None,
          source_handle: str | None = None, color: str | None = None) -> dict:
    e: dict = {"id": f"e_{source}_{target}", "source": source, "target": target}
    if label:
        e["label"] = label
        e["animated"] = True
    if source_handle:
        e["sourceHandle"] = source_handle
    if color:
        e["style"] = {"stroke": color, "strokeWidth": 2}
    return e


# ---------------------------------------------------------------------------
# WORKER prompt — split into a STATIC prefix (byte-stable across turns →
# Vertex prompt cache hits) and a DYNAMIC context block injected at the end.
# This pattern matters: Jinja interpolation breaks prefix caching, so
# everything that varies per turn lives in one bounded suffix.
# ---------------------------------------------------------------------------

WORKER_PROMPT_STATIC = """You are the AutomationEdge Ops L1 Support Agent. You investigate workflow / agent / scheduler issues using AE MCP tools, propose corrective actions, and escalate to humans when warranted. You are autonomous within strict rules.

=== AUTOMATIONEDGE — WHAT YOU'RE OPERATING ===
AutomationEdge (AE) is an enterprise automation platform that runs scheduled and event-driven business workflows (RPA, ETL, integrations, OCR, reporting). The runtime model:
  - **Workflow**: a named definition users + admins author. Identified by workflow_id; has a friendly name (e.g. "DailyReconciliation").
  - **Schedule**: tells the platform when a workflow should run (cron-like or trigger-driven). A schedule misfire is a common cause of "I didn't get my report."
  - **Request**: one execution of a workflow. Identified by request_id. Has a state (QUEUED → RUNNING → COMPLETED / FAILED / TERMINATED) and an output (file, data, callback). Most user-facing complaints map to a specific request.
  - **Agent**: a runner host (a process / VM / container) that picks requests off a queue and executes them. Identified by agent_id. States: RUNNING / STOPPED / DISCONNECTED. A stopped agent means workflows assigned to it pile up in the queue.
  - **Queue**: holds pending requests by priority / agent affinity. A backed-up queue means the agent isn't keeping pace.
  - **Output**: a file, payload, or notification produced by a successful request. Delivery channels include email, shared-path, API callback, dashboard refresh.

Common failure-cause chains (memorise these; use them to build hypotheses BEFORE tool-calling):
  1. "Report didn't arrive" → check Request status for the latest run of the workflow → if FAILED, get the error → if COMPLETED, check output delivery channel (email bounce, shared-path permissions, callback failure).
  2. "Workflow stuck" → check Agent status (STOPPED?) → if RUNNING, check Queue depth + recent error logs → if DISCONNECTED, check connectivity + restart agent.
  3. "Schedule didn't fire" → check Schedule definition → check next-run-time → check whether scheduler service itself is healthy.
  4. "Agent slow / errors" → analyze_logs for the agent → check connectivity_state, license validity, host CPU / memory, version.

Standard remediations (all destructive, all HITL-gated):
  - restart_service(agent_id) — fixes most STOPPED / hung agents.
  - rerun(request_id) — re-runs a failed request from scratch.
  - terminate(request_id) — kills a stuck request so the queue can drain.
  - rotate_credentials(agent_id) — refreshes connector credentials.
  - change_schedule(workflow_id) — changes when a workflow runs.

=== USER LANGUAGE (audience-aware) ===
For "business" users:
  - Forbidden words in your reply: request_id, agent_id, workflow_id, queue, scheduler, instance, node, MCP, ReAct, JSON, http, RLS, alembic, DAG.
  - Translate: "agent" → "the system that runs your X"; "request" → "yesterday's run"; "workflow" → use the friendly name from glossary or prior turns.
  - Always give a TIME ESTIMATE if proposing an action ("~5 min", "in a moment").
  - Summarise OUTCOMES — never dump raw tool-call results.

For "tech" users:
  - Cite IDs, tool names, log excerpts, exact errors freely.
  - State risk tier of any proposed remediation (low / medium / high).

=== NO-HALLUCINATION (CRITICAL) ===
If the user gave a SPECIFIC identifier and your tool calls returned data about DIFFERENT identifiers, do NOT fabricate analysis on the substitutes. Reply: "I couldn't locate <identifier> in the system — it may have been purged. I've opened a ticket and routed to L2." Empty / not-found / 404 = data, not bug. Surface it.

=== ASK ONE QUESTION WHEN AN IDENTIFIER IS MISSING ===
You typically need one of: request_id, agent_id, workflow_id, or a workflow friendly name (resolvable via glossary). If none is present and glossary did not match, ASK ONE TARGETED QUESTION. Do NOT call tools. End your reply with the question — the case is parked at NEED_INFO and the next user turn carries the answer.

Good business questions: "Which report or system? (e.g. daily recon, OCR batch)", "Roughly when did you notice it — today, yesterday, or longer ago?"
Good tech questions: "Do you have a request_id or agent name handy?"

=== TOOL TIERS (ALWAYS-AVAILABLE vs FILTERED) ===
Two tool tiers are exposed to you:

ALWAYS-AVAILABLE (pinned — never filtered out by SMART-06):
  - case.add_worknote(text)            — log a note on the active case (audit trail).
  - case.update_state(state)           — set NEW / NEED_INFO / EXECUTING / RESOLVED_PENDING_CONFIRMATION.
  - case.handoff(team, reason)         — escalate to a human team; case state → HANDED_OFF.
  - case.close(reason)                 — close the case (e.g. on user-confirmed resolution).
  - case.add_evidence(kind, content)   — attach diagnostic evidence (log excerpt, screenshot, tool output snapshot).
  - glossary.lookup(description)       — translate business phrasing to a workflow_id.
  - google_search(query)               — Vertex-grounded web search; use when an error code, SaaS name, or product term is unfamiliar.

DOMAIN TOOLS (semantically filtered — top-15 picked per turn from the AE MCP catalog of ~116):
  - ae.workflow.* / ae.request.* / ae.agent.* / ae.schedule.* / ae.support.* — the operational fabric.

Reach for ALWAYS-AVAILABLE tools to manage the case + ask Google for unknowns. Reach for DOMAIN TOOLS for AE-specific investigation.

=== TOKEN / TOOL-CALL CONSERVATION ===
Each tool call costs latency and tokens. Before calling: check if prior turns (memory) or accumulated worknotes already have the answer. Cite from memory if so. ONE diagnostic tool call before reasoning is usually enough — don't fan out. Prefer one targeted call (`ae.support.diagnose_failed_request(request_id)`) over three broad ones (`get_logs` + `get_status` + `get_summary`).

=== DESTRUCTIVE-ACTION RULES ===
Destructive tools (restart_service / rerun / terminate / rotate_credentials / change_schedule) self-gate via the platform's HITL flow — just call them; the runtime parks for an approver. After a destructive call returns:
  1. Re-fetch state with a read-only tool.
  2. Include before / after state in your reply.
  3. If it did NOT take effect, say so — never claim success.
A separate Verifier specialist also re-checks downstream; inconsistencies surface.

ONE destructive action per turn. Never chain restarts/reruns.

=== MEMORY AWARENESS ===
Prior turns are in node_2.messages. Before responding:
  - If this turn CORRECTS earlier text ("actually", "I meant") → treat as authoritative.
  - If it ADDS context ("oh and…", "also") → extend, do NOT restart.
  - Reference case evidence / worknotes instead of re-investigating.

=== INTENT-LABEL HINTS ===
The router pre-classified this turn — use it as a hint, not a hard rule:
  - "ops" / default     → diagnose or remediate as appropriate.
  - "output_missing"    → use the glossary match below, locate latest run, share output if done / surface failure if not.
  - "resolution_update" → user said it resolved — confirm warmly, log a worknote, ask if anything else. Do NOT re-investigate.
  - "correction"        → integrate the correction and continue.

=== RESPONSE STYLE ===
Replies are 2–4 sentences unless the user asks for detail. End with a question (keep momentum), an ETA, or "let me know if anything else comes up" — never a passive ending. If you opened or escalated a case, include the 8-char case ID for reference.
"""

WORKER_PROMPT_DYNAMIC = """
=== CURRENT TURN CONTEXT ===
user_role:           {{ trigger.user_role | default('business') }}
router_intent:       {{ node_router.intents[0].name | default('ops') }}
router_confidence:   {{ node_router.intents[0].score | default(0.0) }}
case_id:             {{ node_3.json.id[:8] | default('') }}
case_state:          {{ node_3.json.state | default('NEW') }}
prior_evidence:      {{ node_3.json.evidence | length | default(0) }}
prior_worknotes:     {{ node_3.json.worknotes | length | default(0) }}
glossary_match:      {{ node_glossary.json.match.workflow_id | default('null') }}
glossary_friendly:   {{ node_glossary.json.match.friendly_name | default('') }}
glossary_clarify_q:  {{ node_glossary.json.clarifying_question | default('') }}
"""

WORKER_PROMPT = WORKER_PROMPT_STATIC + WORKER_PROMPT_DYNAMIC


VERIFIER_PROMPT = """You are the AE Ops Verification Specialist. The Worker just finished its turn — your single job is to verify whether any destructive action it took actually worked. You have ONLY read-only tools. You do NOT see the Worker's chat history beyond the trigger and case state.

Procedure:
1. Look at the case worknotes from {{ node_3.json.worknotes | tojson }} and the Worker's last reply ({{ node_worker.text | default('') }}). Identify whether a destructive tool call (restart / rerun / terminate / rotate / change_schedule) was taken in this turn. If NOT, reply EXACTLY "VERIFIED_NOOP" and stop — do not call any tools.

2. If yes: identify the entity (agent_id / request_id / workflow_id) the action targeted and re-fetch its state with one read-only tool (ae.agent.get_status / ae.request.get_summary / ae.agent.get_connectivity_state).

3. Reply with a 3-line "before / after / verdict" block. Adapt to user_role:
  - business: "I've restarted the system that runs your X. It just came back online." (no enum values)
  - tech: include the exact state strings.

Verdict values:
  - "VERIFIED_OK"   — action took effect; before vs after differ in the expected direction.
  - "VERIFIED_FAIL" — action did NOT take effect (state unchanged).
  - "VERIFIED_INCONCLUSIVE" — read tool errored; manual check needed.

Keep it under 4 sentences. Do not editorialise.
"""


SMALL_TALK_PROMPT = """You are a friendly assistant for AutomationEdge Ops support chat. The router classified this as small-talk / general question.

If the question is genuinely unrelated to AE ops (jokes, weather, "how are you?"), answer briefly and warmly.
If it's a "what can you help with?" question, respond with this 4-bullet list:
  - Missing or late reports / outputs
  - Failed or stuck workflows
  - Agent issues (stopped, slow, errors)
  - Root-cause incident reports

Do NOT call any tools. Keep it 1–3 sentences plus the bullet list when applicable. End with an inviting question if appropriate.

Audience: trigger.user_role is "{{ trigger.user_role | default('business') }}". Match their register — keep tech jargon out for business users.
"""


RCA_PROMPT = """You are the AE Ops Root-Cause Analysis Report Specialist. You produce structured incident write-ups. You do NOT call tools — you reason over conversation history (node_2.messages) and prior case worknotes ({{ node_3.json.worknotes | tojson }}).

Output a Markdown report with these sections:
1. **Summary** (2–3 lines)
2. **Timeline** (chronological order of events)
3. **Root Cause** (the why, traced to the smallest verified fact)
4. **Impact** (who/what was affected, quantified if known)
5. **Prevention** (concrete recommendations: env / config / process)

Tailor wording to trigger.user_role: "{{ trigger.user_role | default('business') }}" → outcome-focused; "tech" → mechanism-focused.

If conversation history is too thin for a complete RCA, say so and list what you'd need to write one.
"""


HANDED_OFF_PROMPT = """The user's case has been assigned to a human team and is in hands-off mode. Reply in 1–3 sentences:
  1. Acknowledge their message warmly.
  2. Confirm it's been logged on case **{{ node_3.json.id[:8] }}** for team **{{ node_3.json.assigned_team | default('the support team') }}**.
  3. Remind them the team will follow up.
Do NOT call any tools. Do NOT attempt to diagnose.
"""


CANCEL_PROMPT = """The user is withdrawing or cancelling their previous request. Acknowledge briefly (1–2 sentences) and confirm nothing further will be done. The case will be marked closed. End with "Let me know if anything else comes up."
Do NOT call any tools.
"""


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_graph() -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []

    # 1. Trigger
    nodes.append(_node(
        "node_1", "Webhook Trigger",
        {"icon": "webhook", "path": "/ae/ops/inbound", "method": "POST"},
        x=0, y=400, display_name="AE Ops intake (chat webhook)", category="trigger",
    ))

    # 2. Load conversation history — capped at the last 10 turns to keep
    # the prompt context bounded and prevent runaway token cost in long
    # sessions. The agent can request older context via case worknotes.
    nodes.append(_node(
        "node_2", "Load Conversation State",
        {
            "icon": "history",
            "sessionIdExpression": "trigger.session_id",
            "maxMessages": 10,
        },
        x=220, y=400, display_name="Load chat history", category="action",
    ))
    edges.append(_edge("node_1", "node_2"))

    # 3. Upsert support case (idempotent per (tenant, session))
    nodes.append(_node(
        "node_3", "HTTP Request",
        {
            "icon": "globe",
            "url": "http://localhost:5050/api/cases",
            "method": "POST",
            "headers": {
                "Content-Type": "application/json",
                "X-Tenant-Id": "default",
            },
            "body": '{"session_id": "{{ trigger.session_id }}", "requester_id": "{{ trigger.user_role }}"}',
        },
        x=440, y=400, display_name="Upsert support case", category="action",
    ))
    edges.append(_edge("node_2", "node_3"))

    # 4. Switch on case state — short-circuit if HANDED_OFF
    nodes.append(_node(
        "node_4", "Switch",
        {
            "icon": "git-fork",
            "expression": "node_3.json.state",
            "cases": [
                {"value": "HANDED_OFF", "label": "Handed off"},
            ],
            "defaultLabel": "Active",
            "matchMode": "equals",
        },
        x=660, y=400, display_name="Active vs handed-off", category="logic",
    ))
    edges.append(_edge("node_3", "node_4"))

    # 4d. Handed-off canned reply — cheapest model, tight token budget.
    nodes.append(_node(
        "node_4d", "LLM Agent",
        {
            "icon": "user-x",
            "model": GEMINI_25_FLASH,
            "provider": "vertex",
            "temperature": 0.2,
            "maxTokens": 192,
            "systemPrompt": HANDED_OFF_PROMPT,
        },
        x=900, y=560, display_name="Handed-off canned reply", category="agent",
    ))
    edges.append(_edge("node_4", "node_4d", label="Handed off",
                       source_handle="HANDED_OFF", color="#f97316"))

    # 5. Glossary lookup — always-on, cheap. Worker prompt decides whether to use the match.
    nodes.append(_node(
        "node_glossary", "HTTP Request",
        {
            "icon": "book-open",
            "url": "http://localhost:5050/api/glossary/lookup",
            "method": "POST",
            "headers": {
                "Content-Type": "application/json",
                "X-Tenant-Id": "default",
            },
            "body": '{"description": "{{ trigger.message }}", "user_role": "{{ trigger.user_role | default(\'business\') }}"}',
        },
        x=900, y=300, display_name="Glossary lookup (description → workflow)", category="action",
    ))
    edges.append(_edge("node_4", "node_glossary", label="Active",
                       source_handle="default", color="#22c55e"))

    # 6. Intent Router — Intent Classifier in hybrid mode (lexical fast-path,
    # LLM fallback below 0.6). Five intents only — the Worker handles every-
    # thing under "ops". This is the "routing" pattern from Anthropic's
    # Building Effective Agents.
    nodes.append(_node(
        "node_router", "Intent Classifier",
        {
            "icon": "target",
            "mode": "hybrid",
            "utteranceExpression": "trigger.message",
            "allowMultiIntent": False,
            "confidenceThreshold": 0.6,
            "provider": "vertex",
            "model": GEMINI_25_FLASH,
            "embeddingProvider": "vertex",
            "embeddingModel": "text-embedding-005",
            "cacheEmbeddings": True,
            "historyNodeId": "node_2",
            "intents": [
                {
                    "name": "small_talk",
                    "description": "Greetings, thanks, jokes, 'what can you help with', off-topic.",
                    "examples": [
                        "hi", "hello", "thanks!", "thank you", "what can you help with",
                        "what do you do", "how are you", "good morning",
                    ],
                    "priority": 100,
                },
                {
                    "name": "rca_request",
                    "description": "User wants a root-cause-analysis / postmortem write-up of an incident already discussed.",
                    "examples": [
                        "write the RCA", "draft a postmortem", "give me an RCA",
                        "RCA report", "write up what happened", "incident report",
                        "write a postmortem", "draft an incident write-up",
                    ],
                    "priority": 180,
                },
                {
                    "name": "handoff",
                    "description": "User wants to escalate the case to a human team — slash-command or natural language.",
                    "examples": [
                        "/handoff", "/handoff L2", "/escalate", "escalate to L2",
                        "give this to a human", "I need a real person", "assign to L2",
                    ],
                    "priority": 200,
                },
                {
                    "name": "cancel",
                    "description": "User retracts their previous request entirely.",
                    "examples": [
                        "never mind", "ignore that", "cancel that", "scrap that",
                        "ignore my last message", "forget what I said",
                    ],
                    "priority": 175,
                },
                {
                    "name": "ops",
                    "description": "Anything operational — diagnostics, remediation, missing output, status check, resolution update, correction. The catch-all for the Worker agent.",
                    "examples": [
                        "my report didn't arrive", "request id 12345 failed",
                        "the agent is stuck", "restart the worker",
                        "where is my daily recon", "actually I just got it",
                        "no I meant the OCR one", "the workflow is broken",
                        "diagnose request 9876", "rerun yesterday's batch",
                    ],
                    "priority": 50,
                },
            ],
        },
        x=1140, y=300, display_name="Intent router", category="agent",
    ))
    edges.append(_edge("node_glossary", "node_router"))

    # 7. Switch on intent
    nodes.append(_node(
        "node_route", "Switch",
        {
            "icon": "git-fork",
            "expression": "node_router.intents[0].name",
            "cases": [
                {"value": "small_talk", "label": "Small talk"},
                {"value": "rca_request", "label": "RCA"},
                {"value": "handoff", "label": "Handoff"},
                {"value": "cancel", "label": "Cancel"},
            ],
            "defaultLabel": "Ops (Worker)",
            "matchMode": "equals",
        },
        x=1380, y=300, display_name="Route to specialist", category="logic",
    ))
    edges.append(_edge("node_router", "node_route"))

    # 7a. Small-talk reply — cheapest, tight tokens.
    nodes.append(_node(
        "node_smalltalk", "LLM Agent",
        {
            "icon": "smile",
            "model": GEMINI_25_FLASH,
            "provider": "vertex",
            "temperature": 0.4,
            "maxTokens": 192,
            "systemPrompt": SMALL_TALK_PROMPT,
        },
        x=1620, y=80, display_name="Small-talk reply", category="agent",
    ))
    edges.append(_edge("node_route", "node_smalltalk", label="Small talk",
                       source_handle="small_talk", color="#a855f7"))

    # 7b. RCA writer — gemini-2.5-flash with structured prompt is enough for
    # an L1 RCA. Pro tier was overkill at ~10× the cost; this template
    # produces the same five-section report at <$0.001/run.
    nodes.append(_node(
        "node_rca", "LLM Agent",
        {
            "icon": "file-text",
            "model": GEMINI_25_FLASH,
            "provider": "vertex",
            "temperature": 0.3,
            "maxTokens": 2048,
            "systemPrompt": RCA_PROMPT,
        },
        x=1620, y=180, display_name="RCA writer", category="agent",
    ))
    edges.append(_edge("node_route", "node_rca", label="RCA",
                       source_handle="rca_request", color="#0ea5e9"))

    # 7c. Handoff PATCH — set case state HANDED_OFF, return canned reply
    nodes.append(_node(
        "node_handoff", "HTTP Request",
        {
            "icon": "user-check",
            "url": "http://localhost:5050/api/cases/{{ node_3.json.id }}/handoff",
            "method": "POST",
            "headers": {
                "Content-Type": "application/json",
                "X-Tenant-Id": "default",
            },
            "body": '{"team": "L2 support", "reason": "user requested escalation"}',
        },
        x=1620, y=420, display_name="Mark case HANDED_OFF", category="action",
    ))
    edges.append(_edge("node_route", "node_handoff", label="Handoff",
                       source_handle="handoff", color="#f97316"))

    nodes.append(_node(
        "node_handoff_reply", "LLM Agent",
        {
            "icon": "user-x",
            "model": GEMINI_25_FLASH,
            "provider": "vertex",
            "temperature": 0.2,
            "maxTokens": 256,
            "systemPrompt": HANDED_OFF_PROMPT,
        },
        x=1860, y=420, display_name="Confirm handoff", category="agent",
    ))
    edges.append(_edge("node_handoff", "node_handoff_reply"))

    # 7d. Cancel — close case + canned reply
    nodes.append(_node(
        "node_cancel", "HTTP Request",
        {
            "icon": "x-circle",
            "url": "http://localhost:5050/api/cases/{{ node_3.json.id }}/close",
            "method": "POST",
            "headers": {
                "Content-Type": "application/json",
                "X-Tenant-Id": "default",
            },
            "body": '{"reason": "user cancelled"}',
        },
        x=1620, y=520, display_name="Close case (cancelled)", category="action",
    ))
    edges.append(_edge("node_route", "node_cancel", label="Cancel",
                       source_handle="cancel", color="#6b7280"))

    nodes.append(_node(
        "node_cancel_reply", "LLM Agent",
        {
            "icon": "x",
            "model": GEMINI_25_FLASH,
            "provider": "vertex",
            "temperature": 0.2,
            "maxTokens": 192,
            "systemPrompt": CANCEL_PROMPT,
        },
        x=1860, y=520, display_name="Acknowledge cancel", category="agent",
    ))
    edges.append(_edge("node_cancel", "node_cancel_reply"))

    # 7e (default). The Worker — single ReAct that handles diagnostics,
    # remediation, output_missing, NEED_INFO, resolution_update, correction.
    # Top-15 semantic tool filter is applied automatically when tools=[]
    # (SMART-06). Step limit is 8 — best practice from agent guides.
    nodes.append(_node(
        "node_worker", "ReAct Agent",
        {
            "icon": "repeat",
            "model": GEMINI_FLASH,
            "provider": "vertex",
            "tools": [],  # SMART-06 + top-15 semantic filter
            "maxIterations": 8,
            "systemPrompt": WORKER_PROMPT,
        },
        x=1620, y=300, display_name="Ops Worker (ReAct)", category="agent",
    ))
    edges.append(_edge("node_route", "node_worker", label="Ops",
                       source_handle="default", color="#3b82f6"))

    # 8. Verifier — separate ReAct, fresh context, READ-ONLY tools.
    # Anthropic's evaluator-optimizer pattern: independent critic catches
    # what the Worker might rationalise. Downgraded to 2.5-flash (compare
    # task, no tool savvy needed) and capped at 2 iterations (1 read +
    # summary). Verifier short-circuits with "VERIFIED_NOOP" on non-
    # destructive turns so the wasted spend is bounded.
    nodes.append(_node(
        "node_verifier", "ReAct Agent",
        {
            "icon": "shield-check",
            "model": GEMINI_25_FLASH,
            "provider": "vertex",
            "tools": [],
            "maxIterations": 2,
            "systemPrompt": VERIFIER_PROMPT,
        },
        x=1860, y=300, display_name="Verifier (read-only)", category="agent",
    ))
    edges.append(_edge("node_worker", "node_verifier"))

    # 9. Bridge — single shared exit. Multiple branches converge here; the
    # engine fires whichever upstream branch produced output. responseNodeId
    # is set per-branch via Switch fan-out, so we use the most upstream
    # specialist as the canonical responder. Bridge auto-resolves the
    # active branch's text via messageExpression="" (= use producer's text).
    #
    # Convergence pattern: each specialist branch ends with a Bridge+Save
    # pair so the runtime doesn't deadlock waiting for non-fired branches.
    # We mint per-branch Bridge+Save nodes pointing to the same convergence
    # marker (case_patch + reply produced from the active branch).
    bridges = [
        ("node_smalltalk",       "node_b_st",  "node_s_st",  1860, 80,  "Reply (small-talk)"),
        ("node_rca",             "node_b_rca", "node_s_rca", 1860, 180, "Reply (RCA)"),
        ("node_handoff_reply",   "node_b_ho",  "node_s_ho",  2100, 420, "Reply (handoff)"),
        ("node_cancel_reply",    "node_b_cn",  "node_s_cn",  2100, 520, "Reply (cancel)"),
        ("node_verifier",        "node_b_op",  "node_s_op",  2100, 300, "Reply (ops)"),
        ("node_4d",              "node_b_hf",  "node_s_hf",  1140, 560, "Reply (handed-off)"),
    ]
    for src, bridge_id, save_id, x, y, label in bridges:
        nodes.append(_node(
            bridge_id, "Bridge User Reply",
            {"icon": "message-square", "responseNodeId": src, "messageExpression": ""},
            x=x, y=y, display_name=label, category="action",
        ))
        edges.append(_edge(src, bridge_id))
        nodes.append(_node(
            save_id, "Save Conversation State",
            {
                "icon": "save",
                "responseNodeId": src,
                "sessionIdExpression": "trigger.session_id",
                "userMessageExpression": "trigger.message",
            },
            x=x + 240, y=y, display_name=f"Save ({label.split('(')[1].rstrip(')')})",
            category="action",
        ))
        edges.append(_edge(bridge_id, save_id))

    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# POST to orchestrator
# ---------------------------------------------------------------------------

def main() -> int:
    graph = build_graph()
    out_path = Path(__file__).parent / "ae_ops_support_workflow_v8.json"
    out_path.write_text(json.dumps(graph, indent=2))
    print(f"saved graph to {out_path}  ({len(graph['nodes'])} nodes, {len(graph['edges'])} edges)")

    payload = {
        "name": "AE Ops Support — V8 (router + worker + critic)",
        "description": (
            "V8 = a deliberate simplification of V7 grounded in named patterns "
            "(Anthropic Building Effective Agents, OpenAI Practical Guide, "
            "12-Factor Agents). One Worker ReAct handles diagnostics, "
            "remediation, output_missing, NEED_INFO, resolution_update and "
            "correction — its consolidated system prompt encodes audience "
            "rules, no-hallucination, glossary-first, and ask-one-question. "
            "An independent Verifier ReAct re-checks destructive actions "
            "with read-only tools and fresh context (evaluator-optimizer). "
            "Routing uses the Intent Classifier (~5 intents) so the Worker "
            "is reserved for genuinely operational turns. ~13 nodes vs V7's "
            "76. HITL on destructive tools is engine-handled, identical to "
            "V7. Eval harness in scratch/run_ae_ops_evals.py benchmarks V7 "
            "vs V8 on the same transcript set."
        ),
        "graph_json": graph,
    }
    headers = {"X-Tenant-Id": TENANT_ID, "Content-Type": "application/json"}
    print(f"\nPOST {ORCH_URL}/api/v1/workflows  (tenant={TENANT_ID})")
    r = httpx.post(f"{ORCH_URL}/api/v1/workflows", json=payload, headers=headers, timeout=30)
    if r.status_code >= 400:
        print(f"  HTTP {r.status_code}: {r.text}")
        return 1
    body = r.json()
    print(f"  HTTP {r.status_code}  workflow_id={body.get('id')}  version={body.get('version')}")
    print(f"\nWebhook live at: POST {ORCH_URL}/api/v1/workflows/{body.get('id')}/webhook/ae/ops/inbound")
    return 0


if __name__ == "__main__":
    sys.exit(main())
