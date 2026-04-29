"""Build + POST the AE Ops Support workflow.

Single workflow at webhook ``/ae/ops/inbound``. The chat UI sends
``trigger.user_role = "business" | "tech"`` based on which page the user
is on; the workflow uses that to gate approver-only behaviours later.

Architecture (~22 nodes):

    webhook → load_state → case_resolver → switch(handed_off?)
        ├─ true  → handed_off_reply → bridge → save → END
        └─ false → llm_router → switch(intent)
                       ├─ chitchat       → llm_agent  → bridge → save
                       ├─ handoff        → code_exec  → bridge → save
                       ├─ diagnostics    → react      → bridge → save
                       ├─ remediation    → react      → bridge → save (HITL via guarded tools)
                       ├─ rca            → llm_agent  → bridge → save
                       └─ default        → react      → bridge → save

Run: python -m scratch.build_ae_ops_workflow

Reads ORCHESTRATOR_BASE_URL (default http://localhost:8001) and
TENANT_ID (default "default") from env.
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


# ---------------------------------------------------------------------------
# Code-execution snippets — small, deterministic Python that runs inside
# the orchestrator's sandboxed code_execution node.
# ---------------------------------------------------------------------------

CASE_RESOLVER_CODE = '''
"""Look up or create the support case for this conversation.

Reads ``trigger.session_id``, ``trigger.user_role`` (business|tech),
``trigger.message``. Writes/updates a row in ``support_cases`` (RLS
enforces tenant isolation via the running session\'s app.tenant_id GUC).

Emits:
    output["case_id"]            — UUID string of the (now-existing) case
    output["state"]              — current state machine value
    output["short_circuit"]      — "handed_off" if case is locked to a team, else ""
    output["assigned_team"]      — set when short_circuit is "handed_off"
    output["resolved_context"]   — accumulated AE context (workflow_id, request_id, …)
"""
from sqlalchemy import text
from app.database import SessionLocal, set_tenant_context

session_id = trigger.get("session_id") or "unknown"
user_role = trigger.get("user_role") or "business"
message = trigger.get("message") or ""

db = SessionLocal()
try:
    set_tenant_context(db, tenant_id)

    row = db.execute(
        text(
            """
            SELECT id, state, assigned_team, resolved_context
            FROM support_cases
            WHERE session_id = :sid
            ORDER BY created_at DESC
            LIMIT 1
            """
        ),
        {"sid": session_id},
    ).fetchone()

    if row is None:
        # Fresh case — create it. We defer setting category/title until the
        # router classifies intent, so this row is just a state-machine seat.
        new_id = db.execute(
            text(
                """
                INSERT INTO support_cases (tenant_id, session_id, requester_id, state)
                VALUES (:tid, :sid, :rid, 'NEW')
                RETURNING id
                """
            ),
            {"tid": tenant_id, "sid": session_id, "rid": user_role},
        ).scalar_one()
        db.commit()
        output["case_id"] = str(new_id)
        output["state"] = "NEW"
        output["short_circuit"] = ""
        output["assigned_team"] = None
        output["resolved_context"] = {}
    else:
        case_id, state, assigned_team, ctx = row
        output["case_id"] = str(case_id)
        output["state"] = state
        output["assigned_team"] = assigned_team
        output["resolved_context"] = ctx or {}
        # Lock the case if a human team has been assigned. The bot stops
        # actively troubleshooting and only records the user\'s message
        # as a worknote.
        if state == "HANDED_OFF":
            output["short_circuit"] = "handed_off"
            db.execute(
                text(
                    """
                    UPDATE support_cases
                    SET worknotes = worknotes || :note::jsonb,
                        updated_at = now()
                    WHERE id = :cid
                    """
                ),
                {
                    "cid": case_id,
                    "note": __import__("json").dumps([
                        {"by": user_role, "text": message[:512], "ts": __import__("datetime").datetime.utcnow().isoformat()}
                    ]),
                },
            )
            db.commit()
        else:
            output["short_circuit"] = ""
finally:
    db.close()
'''.strip()


HANDOFF_CODE = '''
"""Parse ``/handoff <team>`` and lock the case to that team.

The user sends e.g. ``/handoff L2`` or ``/handoff support_lead``. Anything
after the first whitespace is treated as the team name; if missing,
defaults to ``"unassigned"`` and the case still locks.
"""
from sqlalchemy import text
from app.database import SessionLocal, set_tenant_context

case_id = node_3.get("case_id")
message = trigger.get("message") or ""

# Extract team name after /handoff
parts = message.strip().split(maxsplit=1)
team = parts[1].strip() if len(parts) > 1 else "unassigned"

db = SessionLocal()
try:
    set_tenant_context(db, tenant_id)
    db.execute(
        text(
            """
            UPDATE support_cases
            SET state = 'HANDED_OFF',
                assigned_team = :team,
                updated_at = now()
            WHERE id = :cid
            """
        ),
        {"cid": case_id, "team": team},
    )
    db.commit()
    output["response"] = (
        f"Case **{case_id[:8]}** has been assigned to **{team}**. "
        f"The bot will not auto-troubleshoot further — every message you "
        f"send from here on is logged as a worknote on the case for the "
        f"team to review."
    )
    output["assigned_team"] = team
finally:
    db.close()
'''.strip()


# ---------------------------------------------------------------------------
# Cross-cutting prompt rules — prepended to every specialist's system prompt.
# Centralises audience-aware language, confidence hedging, verification, and
# memory awareness so any rule change is one edit.
# ---------------------------------------------------------------------------

CROSS_CUTTING_RULES = """=== USER LANGUAGE RULES ===
trigger.user_role is "{{ trigger.user_role | default('business') }}".

If "business":
  - NEVER use these words in your reply: request_id, agent_id, workflow_id,
    queue, scheduler, instance, node, MCP, ReAct, JSON, http, RLS, alembic,
    DAG, runner.
  - Translate to plain language:
      "agent" / "service" → "the system that runs your X"
      "request" / "instance" → "your 9 AM run" / "yesterday's run"
      "workflow" → use the friendly name from prior turns or the glossary
  - Always give a TIME ESTIMATE if proposing an action ("~5 min", "in a moment").
  - Acknowledge urgency / frustration when the user signals it
    ("I know this is blocking you", "sorry for the wait").
  - DO NOT dump tool-call results — summarise the OUTCOME.

If "tech":
  - You CAN cite IDs and tool names freely.
  - Include log excerpts and exact error strings when relevant.
  - State the risk tier of any proposed remediation (low/medium/high).
  - Reference case worknotes / evidence for full handoff context.

=== CONFIDENCE RULES ===
The router scored this turn with confidence {{ node_3.confidence | default('unknown') }}.
If < 0.6: prefix your reply with a hedge ("I'm not 100% sure but it looks like X.
If that's not what you meant, tell me what you're seeing.")
If >= 0.85: state directly without hedging.

=== MEMORY AWARENESS RULES ===
node_2.messages contains the prior turns of this conversation. Before responding:
1. Check if this turn CORRECTS a prior statement ("actually", "I meant", "wait").
   If so, treat the latest message as authoritative and integrate the correction.
2. Check if this turn ADDS context to a prior turn ("oh and...", "also").
   Don't restart the investigation — extend it with the new context.
3. Reference prior turns explicitly when relevant: "Earlier you mentioned X, so..."
4. If this is turn 3+ and the case has accumulated evidence (look at
   node_2b.json.evidence and node_2b.json.worknotes), reference that history
   instead of reinvestigating from scratch.

=== VERIFICATION RULES (remediation only) ===
After ANY destructive tool call (restart_service / rerun / terminate /
rotate_credentials / change_schedule), you MUST:
  1. Re-fetch the relevant status with a get_status / get_summary tool.
  2. Include before/after state in your reply.
  3. If the action did NOT take effect, surface that — don't claim success.

=== RESPONSE STYLE ===
Keep replies short (2-4 sentences) unless the user asks for detail. End each
reply with either a question (to keep momentum), an ETA, or "let me know if
something else comes up" — never a passive ending.

================================================================
"""


# ---------------------------------------------------------------------------
# System prompts (each gets CROSS_CUTTING_RULES prepended at build time)
# ---------------------------------------------------------------------------

CHITCHAT_PROMPT = """You are a friendly assistant for the AutomationEdge Ops support chat. The user is making small-talk or asking a general question. Respond briefly and warmly. If their question is unrelated to AutomationEdge ops (jokes, weather, etc.) you may answer it directly. If they seem to want help with an AE workflow / agent / scheduled job, gently invite them to describe the issue so you can route to a specialist. Do NOT call any tools.

If the user asks "what can you help with?", give a brief 4-bullet list:
- Missing or late reports / outputs
- Failed or stuck workflows
- Agent issues (stopped, slow, errors)
- Root-cause incident reports"""

OPS_ORCHESTRATOR_PROMPT = """You are the AutomationEdge Ops Orchestrator — the default specialist when the user's intent isn't clearly diagnostics / remediation / RCA. You can call AE MCP tools to investigate workflows, requests, schedules, agents, queues, and outputs.

Common tasks:
- "I have not received my <X> report" — find the workflow that generates X, locate the latest execution, share output or surface the failure.
- General workflow status questions.
- Output / artifact retrieval and sharing.

Workflow:
1. If the user says they didn't receive an output, locate the relevant workflow via tools and share the latest run's status. If completed, share the output via the appropriate AE share tool. If failed, hand off to the diagnostics specialist by surfacing your findings clearly.
2. Always summarize what you did at the end so the next turn (or specialist) has context.

Stay concise. Don't make up workflow names — use tools to discover them."""

DIAGNOSTICS_PROMPT = """You are the AutomationEdge Diagnostics Specialist. Your single goal: find the ROOT CAUSE of a failure. You are read-only — never call destructive tools (start/stop/restart/terminate/rerun).

Workflow:
1. If a request_id is mentioned, start with ae.support.diagnose_failed_request or equivalent FOR THAT EXACT id.
2. Use ae.agent.analyze_logs / ae.request.get_logs / ae.support.get_system_health to gather evidence.
3. Summarize findings: what failed, why, and what corrective action would help. Don't take the corrective action — surface it for the user (or the remediation specialist) to decide.

CRITICAL — DO NOT FABRICATE:
- If the user asked about a SPECIFIC request_id and your tool calls returned data about DIFFERENT request_ids, do NOT fabricate analysis based on the substitutes. Reply: "I couldn't locate request_id NNNN in the system — it may have been purged or might not exist. Routing to L2 with what I have."
- If you can't find the agent/workflow/request the user named, say so explicitly. Don't analyse adjacent records and present them as if they were what the user asked about.
- If a tool returns empty or 'not found', that's data — surface it honestly.

Be concise. Cite evidence (request IDs, log line snippets) so the next specialist can act on your output. If you have no evidence for the EXACT entity the user named, say "no evidence found for X" — short responses are fine."""

REMEDIATION_PROMPT = """You are the AutomationEdge Remediation Specialist. You execute corrective actions: restart agent services, rerun workflows, terminate stuck requests, etc.

CRITICAL: destructive tools (ae.agent.restart_service, ae.request.rerun, ae.request.terminate, etc.) are HITL-gated by the platform. When you call them, the runtime automatically pauses the workflow and asks a tech approver to approve or reject. You do not need to ask for approval explicitly — the platform does it.

Workflow:
1. Verify state with read-only tools (ae.agent.get_status, ae.request.get_summary) before any write.
2. Take ONE corrective action at a time. After it lands, verify success again.
3. Summarize at the end: what was broken, what you did, and what the user should expect to see.

Don't fan out destructive actions. One agent, one workflow, one action per turn."""

RCA_PROMPT = """You are the AutomationEdge RCA (Root-Cause Analysis) Report Specialist. You write structured incident reports. You do NOT call tools — you reason over the conversation history and prior specialist outputs.

Output a Markdown report with these sections:
1. **Summary** (2-3 lines)
2. **Timeline** (when events happened, in chronological order)
3. **Root Cause** (the why, traced to the smallest verified fact)
4. **Impact** (who/what was affected, quantified if known)
5. **Prevention** (concrete recommendations: env / config / process)
6. **Audience** — tailor wording to ``trigger.user_role`` (business → outcome-focused; tech → mechanism-focused)

If the data in conversation history is too thin, say so explicitly and list what you'd need to write a complete RCA."""

HANDED_OFF_PROMPT = """You are the AE Ops support assistant. The case has been assigned to a human team and is in hands-off mode — DO NOT attempt to diagnose or remediate. Reply briefly to acknowledge the user's message, confirm it's been logged as a worknote on the case for the team named in node_2b.json.assigned_team, and remind them the human team will follow up. Reference the case ID (node_2b.json.id, first 8 chars) so they have a ticket reference. Do not call any tools."""


# ---------------------------------------------------------------------------
# V7 NEW prompts — verification (after destructive) + NEED_INFO clarification
# ---------------------------------------------------------------------------

VERIFICATION_PROMPT = """You are the AE Ops Verification Specialist. The remediation specialist just took (or attempted to take) a destructive action, and your single job is to verify whether it actually worked.

Pre-context from the upstream remediation node (node_8.iterations):
  - Look at the iterations list — find the destructive tool call (anything matching restart_service / rerun / terminate / rotate_credentials / change_schedule).
  - Note the entity it targeted (agent_id or request_id from the call's arguments).

Your job:
1. Re-fetch the status of that entity using a READ-ONLY tool (ae.agent.get_status / ae.request.get_summary / ae.agent.get_connectivity_state).
2. Compare to the state that was reported BEFORE the destructive call.
3. Reply with a 2-3 sentence "before / after" summary:
   - State BEFORE: <what the pre-action tool call showed>
   - State AFTER:  <what your re-fetch shows now>
   - Verdict:      "Restart took effect" | "No change yet — agent still STOPPED" | "Could not verify — read tool errored"

CRITICAL — adapt to user_role:
  - business: "I've restarted the system that runs your X. It just came back online and is processing tasks again." Don't quote raw status enum values.
  - tech: include exact status enum, timestamp, agent_id. "Agent restart verified — get_status returned RUNNING at 21:47:03 UTC. Pre-state was STOPPED."

If the verification fails (tool error, status unchanged, agent still down), explicitly surface that — don't claim success. The fallback subtree will then route the case to L2 if needed.

Tools: read-only set (auto-discovered via SMART-06)."""


NEED_INFO_PROMPT = """The user asked about a system issue but didn't include the specific identifier the diagnostics / remediation tools need (no request_id, no agent_id, no workflow_id). Instead of failing or guessing, you ASK for the right detail in BUSINESS-FRIENDLY language.

Pre-context:
  user message:   {{ trigger.message }}
  user role:      {{ trigger.user_role | default('business') }}
  detected intent:{{ node_3.intents[0] | default('unknown') }}
  entities found: {{ node_3b.entities | default({}) }}
  entities missing: {{ node_3b.missing_required | default([]) }}

Your job:
1. Acknowledge the user's concern in 1 line ("I can help with that…").
2. Ask ONE targeted question to get the missing detail. Adapt to role:
   - business: never ask for IDs. Ask for: the report name / approximate time it normally arrives / what they were doing when it broke / who else might be affected.
   - tech: you may directly ask for request_id / agent_id / workflow_id.
3. End with a clear question mark — the next turn must resolve into actionable.

If the missing detail is `request_id` AND user is business:
  Ask "Which report or workflow is affected? Or roughly when did you notice the problem?"

If missing `agent_id` AND business:
  Ask "Which system or scheduled job is having the issue? You can describe it in your words — I'll match it up."

If missing `workflow_id` AND business:
  Ask "Could you tell me what the report or output normally produces? Or what dashboard isn't loading?"

Keep it to 2-3 sentences total. Don't apologize excessively. Don't dump a list of "here's what I might need" — pick the SINGLE most critical missing piece and ask only for that."""


# ---------------------------------------------------------------------------
# V6 NEW prompts — output_missing playbook + clarifying-question prompt
# ---------------------------------------------------------------------------

OUTPUT_MISSING_INVESTIGATOR_PROMPT = """You are the AE Ops Output-Missing Specialist. The user is a BUSINESS user reporting a missing or late output (e.g. "I haven't received my daily recon report"). The glossary translator has already resolved their description to a specific workflow.

Pre-resolved workflow context (from upstream glossary node):
  workflow_id:       {{ node_om1.json.match.workflow_id | default('UNKNOWN') }}
  friendly_name:     {{ node_om1.json.match.friendly_name | default('this workflow') }}
  expected_run_time: {{ node_om1.json.match.expected_run_time_utc | default('unknown') }}
  expected_delivery: {{ node_om1.json.match.expected_delivery_minutes | default('?') }} min
  output_destination:{{ node_om1.json.match.output_destination | default('unknown') }}
  owner_team:        {{ node_om1.json.match.owner_team | default('unknown') }}

Your job — runbook (use the AE MCP tools available):
1. Use ae.request.* tools to find the most recent run(s) of this workflow_id (or by friendly_name) in the last 24-48 hours. DO NOT guess — call the tool.
2. Branch by the run's state:
   - **Completed**: tell the user it ran, when it finished, and where the output went. If the destination differs from what they expected, say so.
   - **Failed**: identify root cause briefly. Surface ONE proposed fix without acting on it. Mention the user can ask you to "fix it" or "/handoff" to the owner team.
   - **Stuck/Queued**: say it's stuck, mention the workflow's expected_delivery_minutes, propose terminate+rerun.
   - **Never ran today**: check the schedule — say "your scheduled 02:00 UTC run didn't fire today, here's why".
3. ALWAYS reply in BUSINESS LANGUAGE. The user role is "business" — translate every IT term:
   - "agent" → "the system that runs your X"
   - "request" → "your <expected_run_time> run"
   - NEVER quote request_id / agent_id / workflow_id.
4. Always give a TIME ESTIMATE. If you propose a fix, say "should be back to you in ~X minutes after approval".
5. If you can't find ANY recent run for this workflow_id (tool returned 0 results), don't fabricate. Say honestly: "I couldn't find any recent runs of <friendly_name> in the last 24 hours — that's unusual for a daily flow. Routing to <owner_team> with what I have."

Tools: use the SMART-06 auto-discovered set. Cap iterations — once you have one run's state, stop and respond."""


OUTPUT_MISSING_CLARIFY_PROMPT = """The glossary couldn't confidently match the user's description to a specific workflow. They said something like "my data thing didn't come through" — too vague.

Available context:
  user description:       {{ trigger.message }}
  glossary clarifying_q:  {{ node_om1.json.clarifying_question | default('') }}
  alternatives the LLM considered: {{ node_om1.json.alternatives | default([]) }}

Your job:
1. If the glossary suggested a clarifying question, use it as the basis (rephrase warmly).
2. If alternatives are listed, present them as a SHORT picklist: "Did you mean: A, B, or C?"
3. Otherwise ask in plain BUSINESS terms — what kind of report or output are they expecting? When do they normally receive it? Don't ask for IDs.

Keep it to 2 sentences max. End with a clear question so the next turn classifies correctly."""


# ---------------------------------------------------------------------------
# V5 NEW prompts — for the new intents and the fallback subtree
# ---------------------------------------------------------------------------

RESOLUTION_UPDATE_PROMPT = """The user is signalling that the issue has resolved itself — their report came in, the system is working, false alarm.

Your job:
1. Confirm warmly that you heard the resolution ("Glad to hear!", "That's good news").
2. Acknowledge any pending case work — read node_2b.json (worknotes, evidence) and reference what we'd been investigating.
3. Offer to keep the case open for monitoring OR close it now.
4. End with a clear yes/no question so the next turn can act on it.

Don't call tools. Don't propose more remediation. The user said it's working — believe them and respect their time.

If trigger.user_role is "business": warm, simple, 2-3 sentences.
If trigger.user_role is "tech": acknowledge + suggest keeping case open for next 30 min in case of recurrence."""


CANCEL_WITHDRAW_PROMPT = """The user is withdrawing their previous request — never mind, scrap it, ignore it.

Your job:
1. Confirm you've cancelled / paused active work (the case has been marked CANCELLED by the workflow already).
2. DON'T be passive-aggressive. Don't ask "are you sure?" — just respect the decision.
3. Reference the case ID (node_2b.json.id, first 8 chars) so they can re-open later if needed.
4. Offer continued availability ("just let me know if you need anything else").

Keep it to 2 sentences. Tools NOT called."""


CORRECTION_PROMPT = """The user is correcting something they said earlier. The latest message in trigger.message is authoritative.

Your job:
1. Read node_2.messages to understand what they're correcting.
2. Update your understanding internally — don't restart from scratch.
3. Confirm the correction back to them ("Got it — so you meant X, not Y").
4. Re-route to the appropriate specialist if the corrected intent has changed (e.g., they said "actually I want to restart it, not just diagnose"). In that case, summarise where we were and propose the new direction.
5. If the correction is minor (just a typo / restatement), continue from where we were with the corrected detail.

Keep it short (2-3 sentences) and use the user's exact words from the correction so they know you heard them."""


FALLBACK_REPLY_PROMPT = """You are wrapping up a conversation you couldn't fully resolve from your tools.

Context:
  - Case ID: {{ node_2b.json.id }}
  - Failure reason: {{ node_health_check.reason | default('investigation incomplete') }}
  - Specialist that handled this: {{ node_health_check.specialist_name | default('unknown') }}
  - User role: {{ trigger.user_role | default('business') }}
  - Assigned team: L2_OPS

Write a 2-4 sentence reply that:
  1. Acknowledges honestly that you couldn't fully resolve from here. NEVER pretend everything's fine.
  2. States the case/ticket ID prominently as "**#{{ node_2b.json.id[:8] }}**" (first 8 chars, bolded).
  3. Names the team (L2_OPS) and an ETA ("within 30 minutes during business hours").
  4. Tells them what happens next: "they'll reach out with an update".

For business users:
  - Plain language. NEVER cite tool names, internal IDs, or technical reasons.
  - Acknowledge frustration if the user signalled it earlier ("I know this is blocking you").
  - Don't end with a generic "anything else?" if the user is clearly frustrated.

For tech users:
  - You may include the technical failure reason and a 1-line tool-call summary.
  - Reference the case worknotes / evidence array as a complete handoff trail.

NEVER promise a fix. NEVER fabricate an ETA outside the standard 30-min SLA.
NEVER blame the user."""


HEALTH_CHECK_CODE = '''
"""Decide whether the specialist's response is shippable, or if we should
fall back to a human team and surface a ticket ID.

The sandbox exposes upstream node outputs as ``inputs[node_id]``. We read
``inputs["node_4"]["match"]`` to know which specialist branch ran, then
inspect that specialist's output for failure signals: explicit errors,
max-iteration force-stop, empty/short response, or response language that
signals a dead-end.
"""
import re

specialist_map = {
    "chitchat":          "node_5",
    "handoff":           "node_6",
    "diagnostics":       "node_7",
    "remediation":       "node_8",
    "rca_report":        "node_9",
    "default":           "node_10",
    "output_missing":    "node_10",
    "resolution_update": "node_30",
    "cancel_or_withdraw":"node_31",
    "correction":        "node_32",
}

n4 = inputs.get("node_4") or {}
# NODES-01.a Switch returns {match: "case"|"default", branch: <case_value>}.
# We need the matched case value (intent name), so read `branch`. Fall back
# to `evaluated` for the same value, then "default" for a cold default.
branch = n4.get("branch") or n4.get("evaluated") or "default"
spec_id = specialist_map.get(branch, "node_10")
spec = inputs.get(spec_id) or {}

response = (spec.get("response") or "").strip()
err = spec.get("error")
iterations = spec.get("iterations") or []

is_fallback = False
reason = ""

if err:
    is_fallback = True
    reason = "specialist_error: " + str(err)[:200]
elif len(response) < 30:
    is_fallback = True
    reason = "response_too_short"
elif iterations and iterations[-1].get("action") != "final_response":
    is_fallback = True
    reason = "react_max_iterations(" + str(len(iterations)) + ")"
else:
    dead_end_pat = (
        r"(?i)(couldn'?t find|could not find|no agents? .*found|"
        r"no .*workflows? .*found|unable to (?:find|locate)|"
        r"i don'?t have (?:access|the ability)|please provide.*id\b|"
        r"i need.*specific id|cannot determine)"
    )
    if re.search(dead_end_pat, response):
        is_fallback = True
        reason = "response_signals_dead_end"

# Chitchat / cancel / correction / resolution are never a fallback even if
# short — short replies are expected for these.
if branch in ("chitchat", "cancel_or_withdraw", "correction", "resolution_update"):
    is_fallback = False
    reason = ""

output = {
    "is_fallback": is_fallback,
    "reason": reason,
    "specialist_name": branch,
    "specialist_id": spec_id,
    "specialist_response": response[:500],
}
'''.strip()


# ---------------------------------------------------------------------------
# Build the graph_json
# ---------------------------------------------------------------------------

def _node(node_id: str, label: str, config: dict, *, x: int, y: int, display_name: str | None = None, category: str = "agent") -> dict:
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


def _edge(source: str, target: str, *, label: str | None = None, source_handle: str | None = None, color: str | None = None) -> dict:
    e: dict = {"id": f"e_{source}_{target}", "source": source, "target": target}
    if label:
        e["label"] = label
        e["animated"] = True
    if source_handle:
        e["sourceHandle"] = source_handle
    if color:
        e["style"] = {"stroke": color, "strokeWidth": 2}
    return e


def _wrap(prompt: str) -> str:
    """Prepend the cross-cutting prompt rules to any specialist's system prompt."""
    return CROSS_CUTTING_RULES + prompt


def build_graph() -> dict:
    """V3 — adds support_cases integration via HTTP Request nodes against
    the Flask API at :5050. The HTTP Request handler now expands Jinja2
    expressions in url/headers/body, so we can pass the dynamic
    ``trigger.session_id`` through.

    Flow additions vs V1:

      1. After load_state, upsert a case row (POST /api/cases).
      2. Switch on case state — if HANDED_OFF, route to a handed-off
         canned reply (skips router + specialists).
      3. Otherwise continue to the LLM Router (intent classification).
      4. After /handoff intent, PATCH the case state to HANDED_OFF via
         a second HTTP Request node so the next turn short-circuits.
    """
    nodes: list[dict] = []
    edges: list[dict] = []

    # 1. Trigger
    nodes.append(_node(
        "node_1", "Webhook Trigger",
        {"icon": "webhook", "path": "/ae/ops/inbound", "method": "POST"},
        x=0, y=400, display_name="AE Ops intake (chat webhook)", category="trigger",
    ))

    # 2. Load chat history
    nodes.append(_node(
        "node_2", "Load Conversation State",
        {"icon": "history", "sessionIdExpression": "trigger.session_id"},
        x=220, y=400, display_name="Load chat history", category="action",
    ))
    edges.append(_edge("node_1", "node_2"))

    # 2b. Upsert support case (Flask /api/cases) — idempotent per (tenant, session)
    nodes.append(_node(
        "node_2b", "HTTP Request",
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
        x=220, y=560, display_name="Upsert support case", category="action",
    ))
    edges.append(_edge("node_2", "node_2b"))

    # 2c. Switch on case state — short-circuit if HANDED_OFF
    nodes.append(_node(
        "node_2c", "Switch",
        {
            "icon": "git-fork",
            "expression": "node_2b.json.state",
            "cases": [
                {"value": "HANDED_OFF", "label": "Handed off"},
            ],
            "defaultLabel": "Active",
            "matchMode": "equals",
        },
        x=460, y=560, display_name="Active vs handed-off", category="logic",
    ))
    edges.append(_edge("node_2b", "node_2c"))

    # Handed-off reply node
    nodes.append(_node(
        "node_2d", "LLM Agent",
        {
            "icon": "user-x",
            "model": GEMINI_25_FLASH,
            "provider": "vertex",
            "temperature": 0.2,
            "maxTokens": 384,
            "systemPrompt": (
                "The user's case has been handed off to a human team and is in hands-off mode. "
                "Respond briefly: acknowledge their message, confirm it has been logged as a worknote on the case, "
                "and remind them the human team will follow up. Do NOT call any tools and do NOT attempt to diagnose. "
                "Use trigger.message as the user's latest message."
            ),
        },
        x=720, y=560, display_name="Handed-off canned reply", category="agent",
    ))
    edges.append(_edge("node_2c", "node_2d", label="Handed off", source_handle="HANDED_OFF", color="#f97316"))

    # 3. Intent Classifier — HYBRID mode.
    # Lexical+embedding catches the obvious cases for free (slash-commands,
    # canonical phrasings). When confidence is below 0.6, the classifier
    # falls back to a fast LLM (gemini-2.5-flash) to handle nuance —
    # corrections ("actually I meant X"), partial resolutions ("hold on,
    # I just got it"), and language we didn't enumerate as examples.
    # This is the V5 win over V4's pure-heuristic mode.
    nodes.append(_node(
        "node_3", "Intent Classifier",
        {
            "icon": "target",
            "mode": "hybrid",
            "utteranceExpression": "trigger.message",
            "allowMultiIntent": False,
            "confidenceThreshold": 0.6,
            "provider": "vertex",
            "model": GEMINI_25_FLASH,  # LLM fallback when heuristic confidence < 0.6
            "embeddingProvider": "vertex",
            "embeddingModel": "text-embedding-005",
            "cacheEmbeddings": True,
            "historyNodeId": "node_2",  # LLM fallback sees prior turns for context
            "intents": [
                {
                    "name": "handoff",
                    "description": "User wants to hand off the case to a human team — slash-command or natural language.",
                    "examples": [
                        "/handoff", "/handoff L2", "/handoff support_lead",
                        "/escalate", "escalate to L2", "assign to L2 team",
                        "give this to a human", "I need a real person",
                    ],
                    "priority": 200,
                },
                {
                    "name": "resolution_update",
                    "description": "User signals the issue resolved itself — got the report, it came in, false alarm.",
                    "examples": [
                        # V6.1 — examples must contain a clear "received/arrived"
                        # signal so they win over generic "actually" matches.
                        "I just got it", "it came in", "received it now",
                        "I just got the report", "actually I just got the report",
                        "the report just arrived", "the report finally came",
                        "got the report", "received the email",
                        "false alarm", "all good now",
                        "actually I just received it", "it just showed up",
                        "actually it's working now",
                    ],
                    "priority": 190,  # bumped above correction so 'actually I just got X' wins
                },
                {
                    "name": "cancel_or_withdraw",
                    "description": "User retracts their previous request entirely — never mind, scrap that.",
                    "examples": [
                        "never mind", "ignore that", "cancel that",
                        "scrap that request", "ignore my last message",
                        "forget what I said", "actually don't bother",
                    ],
                    "priority": 175,
                },
                {
                    "name": "correction",
                    "description": "User corrects something they said earlier (not a full withdrawal, not a resolution).",
                    "examples": [
                        # V6.1 — drop bare "actually"; require a CORRECTION
                        # signal ("I meant", "not X but Y", etc) so resolution_update
                        # wins on 'actually I just got X'.
                        "I meant", "wait, I meant", "scratch that, I meant",
                        "let me rephrase", "I should have said",
                        "correction:", "I should have said",
                        "wait, I want", "no, I want",
                    ],
                    "priority": 165,
                },
                {
                    "name": "output_missing",
                    "description": "Business-user complaint about a missing/late/empty report or output.",
                    "examples": [
                        "I haven't received my report", "missing report",
                        "the daily report didn't come", "where is my report",
                        "my report is missing", "report didn't arrive",
                        "I have not received", "the email didn't come through",
                        "the dashboard isn't refreshing", "data is missing",
                        "my output is empty", "no daily output today",
                    ],
                    "priority": 160,
                },
                {
                    "name": "remediation",
                    "description": "User wants a corrective action: restart, rerun, terminate, fix.",
                    "examples": [
                        "restart the agent", "restart agent service", "rerun this workflow",
                        "kill the stuck request", "terminate the bot",
                        "fix this", "remediate", "please restart", "reboot the agent",
                        "run again", "trigger a retry", "rotate credentials",
                    ],
                    "priority": 150,
                },
                {
                    "name": "rca_report",
                    "description": "User wants a structured RCA / post-mortem report.",
                    "examples": [
                        "rca", "give me an rca", "write the rca", "root cause analysis",
                        "incident report", "post-mortem", "post mortem",
                        "summary of what went wrong",
                    ],
                    "priority": 140,
                },
                {
                    "name": "diagnostics",
                    "description": "User wants to know WHY something failed — read-only investigation, usually with IDs.",
                    "examples": [
                        "why did it fail", "why is this failing", "diagnose this",
                        "check the logs", "look at error",
                        "investigate request id", "request id 123",
                        "agent stopped", "workflow stuck", "bot not responding",
                    ],
                    "priority": 130,
                },
                {
                    "name": "chitchat",
                    "description": "Greeting, thanks, small-talk, off-topic.",
                    "examples": [
                        "hi", "hello", "hey", "good morning", "thanks", "thank you",
                        "who are you", "what can you do", "help",
                    ],
                    "priority": 50,
                },
                # Anything that doesn't match any of the above falls through
                # to the Switch's default branch → ops_orchestrator specialist.
            ],
        },
        x=720, y=400, display_name="Classify intent (hybrid: heuristic + LLM fallback)", category="nlp",
    ))
    edges.append(_edge("node_2c", "node_3", label="Active", source_handle="default"))

    # 3b. Entity Extractor — pulls team / request_id / agent_id from the message
    # using regex. Scoped per-intent via intentEntityMapping so we don't try to
    # extract a team out of a chitchat message.
    nodes.append(_node(
        "node_3b", "Entity Extractor",
        {
            "icon": "list-filter",
            "sourceExpression": "trigger.message",
            "scopeFromNode": "node_3",
            "intentEntityMapping": {
                "handoff": ["team"],
                "remediation": ["request_id", "agent_id"],
                "diagnostics": ["request_id", "agent_id", "workflow_id"],
            },
            "entities": [
                {
                    "name": "team",
                    "type": "regex",
                    # Match /handoff TEAM, escalate to TEAM, escalate TEAM, assign to TEAM,
                    # route to TEAM. The non-capturing 'to ' inside ?: lets us skip
                    # an optional "to" without it being captured as the team name.
                    "pattern": r"(?:/?(?:handoff|escalate)|(?:assign|route))\s+(?:to\s+)?([A-Za-z][A-Za-z0-9_-]+)",
                    "description": "Team to hand off to (e.g. L2, L3, support_lead).",
                },
                {
                    "name": "request_id",
                    "type": "regex",
                    "pattern": r"(?:request[\s_-]*id\s*[:=]?\s*|req\s*[:=]?\s*)(\d{4,})",
                    "description": "AE request / instance ID — usually 6-8 digits.",
                },
                {
                    "name": "agent_id",
                    "type": "regex",
                    "pattern": r"agent\s*(?:id\s*[:=]?\s*)?([A-Za-z0-9_@.-]+@[A-Za-z0-9_.-]+|[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+)",
                    "description": "AE agent ID (typically user@host or @group form).",
                },
                {
                    "name": "workflow_id",
                    "type": "regex",
                    "pattern": r"workflow\s*(?:[:=]?\s*)?([A-Za-z][A-Za-z0-9_-]+)",
                    "description": "Workflow name or id.",
                },
            ],
        },
        x=940, y=400, display_name="Extract entities (regex, intent-scoped)", category="nlp",
    ))
    edges.append(_edge("node_3", "node_3b"))

    # ── V7.2 NEED_INFO interceptor ─────────────────────────────────────────
    # Between Entity Extractor and the main Switch, we check whether the
    # detected intent NEEDS an entity that wasn't provided. For diagnostics
    # and remediation, both need at least one of {request_id, agent_id,
    # workflow_id}. If none was extracted AND the user is a business user
    # (who can't be expected to provide IDs), we route to a clarification
    # LLM that asks for it in business-friendly language.
    nodes.append(_node(
        "node_3c", "Code",
        {
            "icon": "code",
            "language": "python",
            "code": (
                "n3 = inputs.get('node_3') or {}\n"
                "n3b = inputs.get('node_3b') or {}\n"
                "trig = inputs.get('trigger') or {}\n"
                "intent = (n3.get('intents') or ['unknown'])[0]\n"
                "ents = n3b.get('entities') or {}\n"
                "user_role = trig.get('user_role', 'business')\n"
                "needs_id_intents = ('diagnostics', 'remediation')\n"
                "has_an_id = any(ents.get(k) for k in ('request_id', 'agent_id', 'workflow_id'))\n"
                "# Only fire NEED_INFO for business users on intents that need an id.\n"
                "# Tech users shouldn't be paused — they can use the diagnostics fallback path.\n"
                "needs_info = (intent in needs_id_intents) and (not has_an_id) and (user_role == 'business')\n"
                "output = {'needs_info': needs_info, 'flag': 'ask' if needs_info else 'proceed', 'detected_intent': intent}\n"
            ),
            "timeout": 5,
        },
        x=940, y=300, display_name="NEED_INFO check", category="action",
    ))
    edges.append(_edge("node_3b", "node_3c"))

    nodes.append(_node(
        "node_3d", "Switch",
        {
            "icon": "git-fork",
            "expression": "node_3c.flag",
            "cases": [{"value": "ask", "label": "Ask for clarification"}],
            "defaultLabel": "Proceed",
            "matchMode": "equals",
        },
        x=1010, y=300, display_name="Need info?", category="logic",
    ))
    edges.append(_edge("node_3c", "node_3d"))

    # NEED_INFO LLM that asks the targeted question
    nodes.append(_node(
        "node_ni1", "LLM Agent",
        {
            "icon": "help-circle",
            "model": GEMINI_25_FLASH,
            "provider": "vertex",
            "temperature": 0.4,
            "maxTokens": 384,
            "systemPrompt": _wrap(NEED_INFO_PROMPT),
        },
        x=1100, y=200, display_name="Ask clarifying question", category="agent",
    ))
    edges.append(_edge("node_3d", "node_ni1", label="Ask", source_handle="ask", color="#facc15"))

    # NEED_INFO bridge + save + case state PATCH (set NEED_INFO state)
    nodes.append(_node(
        "node_ni2", "HTTP Request",
        {
            "icon": "globe",
            "url": "http://localhost:5050/api/cases/{{ node_2b.json.id }}",
            "method": "PATCH",
            "headers": {"Content-Type": "application/json", "X-Tenant-Id": "default"},
            "body": '{"state": "NEED_INFO"}',
        },
        x=1200, y=200, display_name="Mark case NEED_INFO", category="action",
    ))
    edges.append(_edge("node_ni1", "node_ni2"))

    nodes.append(_node(
        "node_ni3", "Bridge User Reply",
        {"icon": "message-square", "responseNodeId": "node_ni1", "messageExpression": ""},
        x=1300, y=200, display_name="Reply (NEED_INFO)", category="action",
    ))
    nodes.append(_node(
        "node_ni4", "Save Conversation State",
        {
            "icon": "save", "responseNodeId": "node_ni1",
            "sessionIdExpression": "trigger.session_id",
            "userMessageExpression": "trigger.message",
        },
        x=1400, y=200, display_name="Save (NEED_INFO)", category="action",
    ))
    edges.append(_edge("node_ni2", "node_ni3"))
    edges.append(_edge("node_ni3", "node_ni4"))

    # 4. Switch on intent. Intent Classifier returns intents[] (top-N), so we
    # read intents[0] which is always the highest-scoring intent. Fallbacks
    # to the default branch if the array is empty.
    nodes.append(_node(
        "node_4", "Switch",
        {
            "icon": "git-fork",
            "expression": "node_3.intents[0]",
            "cases": [
                {"value": "chitchat", "label": "Chitchat"},
                {"value": "handoff", "label": "Handoff"},
                {"value": "diagnostics", "label": "Diagnostics"},
                {"value": "remediation", "label": "Remediation"},
                {"value": "rca_report", "label": "RCA report"},
                {"value": "output_missing", "label": "Output missing"},
                {"value": "resolution_update", "label": "Resolved"},
                {"value": "cancel_or_withdraw", "label": "Cancel"},
                {"value": "correction", "label": "Correction"},
            ],
            "defaultLabel": "Default ops",
            "matchMode": "equals_ci",
        },
        x=1160, y=400, display_name="Route to specialist", category="logic",
    ))
    # Wire from the NEED_INFO Switch's "Proceed" path (default) to the main
    # intent Switch. We REMOVED the direct edge from node_3b → node_4 since
    # the NEED_INFO check now sits between them.
    edges.append(_edge("node_3d", "node_4", label="Proceed", source_handle="default"))

    # 5-10: Specialist nodes (one per intent + default).
    # Each prompt gets CROSS_CUTTING_RULES prepended so audience / confidence /
    # memory / verification rules apply uniformly. ``_wrap`` is defined at
    # module level (top of build_ae_ops_workflow.py) so V7 nodes that
    # reference it before this loop also work.
    specialists = [
        # (id, label, config, y, source_handle, edge_color)
        (
            "node_5", "LLM Agent",
            {
                "icon": "brain",
                "model": GEMINI_25_FLASH,
                "provider": "vertex",
                "temperature": 0.7,
                "maxTokens": 512,
                "systemPrompt": _wrap(CHITCHAT_PROMPT),
            },
            80, "chitchat", "#a3a3a3",
        ),
        (
            "node_6", "LLM Agent",
            {
                "icon": "user-x",
                "model": GEMINI_25_FLASH,
                "provider": "vertex",
                "temperature": 0.3,
                "maxTokens": 384,
                "systemPrompt": _wrap(
                    "The user asked to hand off this case to a human team (e.g. they typed `/handoff <team>`). "
                    "Acknowledge briefly: confirm you've routed the conversation to the named team "
                    "(check node_3b.entities.team — fallback to 'unassigned' if not set), and tell them "
                    "subsequent messages will be logged for the team. Reference the case ID "
                    "node_2b.json.id (first 8 chars) so they have a ticket reference."
                ),
            },
            240, "handoff", "#f97316",
        ),
        (
            "node_7", "ReAct Agent",
            {
                "icon": "repeat",
                "model": GEMINI_FLASH,
                "provider": "vertex",
                "tools": [],  # SMART-06 auto-discover + top-15 semantic filter
                "maxIterations": 10,
                "systemPrompt": _wrap(DIAGNOSTICS_PROMPT),
            },
            400, "diagnostics", "#3b82f6",
        ),
        (
            "node_8", "ReAct Agent",
            {
                "icon": "repeat",
                "model": GEMINI_FLASH,
                "provider": "vertex",
                "tools": [],  # destructive tools self-gate via guarded HITL (0317e8a)
                "maxIterations": 10,
                "systemPrompt": _wrap(REMEDIATION_PROMPT),
            },
            560, "remediation", "#ef4444",
        ),
        (
            "node_9", "LLM Agent",
            {
                "icon": "brain",
                "model": GEMINI_FLASH,
                "provider": "vertex",
                "temperature": 0.35,
                "maxTokens": 4096,
                "systemPrompt": _wrap(RCA_PROMPT),
            },
            720, "rca_report", "#a855f7",
        ),
        (
            "node_10", "ReAct Agent",
            {
                "icon": "repeat",
                "model": GEMINI_FLASH,
                "provider": "vertex",
                "tools": [],
                "maxIterations": 10,
                "systemPrompt": _wrap(OPS_ORCHESTRATOR_PROMPT),
            },
            880, "default", "#14b8a6",
        ),
        # V5 NEW — additive intents (resolution / cancel / correction).
        # All three are LLM Agents (no tools needed), gemini-2.5-flash for speed.
        (
            "node_30", "LLM Agent",
            {
                "icon": "check-circle",
                "model": GEMINI_25_FLASH,
                "provider": "vertex",
                "temperature": 0.4,
                "maxTokens": 384,
                "systemPrompt": _wrap(RESOLUTION_UPDATE_PROMPT),
            },
            1040, "resolution_update", "#10b981",
        ),
        (
            "node_31", "LLM Agent",
            {
                "icon": "x-circle",
                "model": GEMINI_25_FLASH,
                "provider": "vertex",
                "temperature": 0.3,
                "maxTokens": 256,
                "systemPrompt": _wrap(CANCEL_WITHDRAW_PROMPT),
            },
            1200, "cancel_or_withdraw", "#6b7280",
        ),
        (
            "node_32", "LLM Agent",
            {
                "icon": "edit",
                "model": GEMINI_25_FLASH,
                "provider": "vertex",
                "temperature": 0.4,
                "maxTokens": 384,
                "systemPrompt": _wrap(CORRECTION_PROMPT),
            },
            1360, "correction", "#facc15",
        ),
        # output_missing has its own subgraph (V6.4) — handled below the
        # specialists loop, NOT routed to a single specialist node here.
    ]

    for spec_id, label, config, y, handle, color in specialists:
        # Choose category — the new V5 NEW intents are LLM Agent / nlp-ish,
        # specialist nodes are agent-category like before.
        cat = "agent"
        nodes.append(_node(
            spec_id, label, config,
            x=1200, y=y, display_name=f"{label} — {handle}",
            category=cat,
        ))
        edges.append(_edge("node_4", spec_id, label=handle, source_handle=handle, color=color))

    # ── V7.1 Verification subgraph (after remediation) ─────────────────────
    # The remediation specialist (node_8) is followed by a small subgraph
    # that detects whether a destructive tool was called, and if so, runs
    # a read-only ReAct verification specialist that re-fetches status.
    # Layout:
    #   node_8 (remediation) → node_v1 (Code: did_destructive_run?)
    #                       → node_v2 (Switch on flag)
    #                          ├ "yes" → node_v3 (Verification ReAct, read-only)
    #                          │             → bridges with the original specialist's reply
    #                          └ "no"  → existing remediation bridge (no verification needed)
    nodes.append(_node(
        "node_v1", "Code",
        {
            "icon": "code",
            "language": "python",
            "code": (
                "rem = inputs.get('node_8') or {}\n"
                "iters = rem.get('iterations') or []\n"
                "destructive = ('restart_service','rerun','terminate','rotate_credentials','change_schedule','assign_workflow','start_service','stop_service')\n"
                "did_destructive = False\n"
                "destructive_target = None\n"
                "for it in iters:\n"
                "    for tc in (it.get('tool_calls') or []):\n"
                "        nm = (tc.get('name') or '').lower()\n"
                "        if any(d in nm for d in destructive):\n"
                "            did_destructive = True\n"
                "            args = tc.get('arguments') or {}\n"
                "            destructive_target = args.get('agent_id') or args.get('request_id') or args.get('workflow_id')\n"
                "            break\n"
                "    if did_destructive: break\n"
                "output = {'did_destructive': did_destructive, 'flag': 'yes' if did_destructive else 'no', 'destructive_target': destructive_target}\n"
            ),
            "timeout": 5,
        },
        x=1340, y=720, display_name="Did remediation call a destructive tool?", category="action",
    ))
    nodes.append(_node(
        "node_v2", "Switch",
        {
            "icon": "git-fork",
            "expression": "node_v1.flag",
            "cases": [{"value": "yes", "label": "Verify"}],
            "defaultLabel": "Skip verification",
            "matchMode": "equals",
        },
        x=1410, y=720, display_name="Verify or skip?", category="logic",
    ))
    nodes.append(_node(
        "node_v3", "ReAct Agent",
        {
            "icon": "shield-check",
            "model": GEMINI_FLASH,
            "provider": "vertex",
            "tools": [],  # SMART-06; read-only verification tools
            "maxIterations": 5,  # short loop — just status re-fetch
            "systemPrompt": _wrap(VERIFICATION_PROMPT),
        },
        x=1500, y=720, display_name="Verify outcome (read-only)", category="agent",
    ))

    # ── V6.4 output_missing subgraph ───────────────────────────────────────
    # Branched off the intent Switch via source_handle="output_missing".
    # Layout:
    #   node_om1 (HTTP POST /api/glossary/lookup with trigger.message)
    #   node_om2 (Switch on node_om1.json.match — None vs <found>)
    #   node_om3 (ReAct investigator, workflow_id from glossary in prompt) — match found
    #   node_om4 (LLM Agent — clarifying question) — no match
    #   then bridge + save (shared per branch)
    nodes.append(_node(
        "node_om1", "HTTP Request",
        {
            "icon": "globe",
            "url": "http://localhost:5050/api/glossary/lookup",
            "method": "POST",
            "headers": {
                "Content-Type": "application/json",
                "X-Tenant-Id": "default",
            },
            "body": '{"description": "{{ trigger.message }}", "user_role": "{{ trigger.user_role | default(\'business\') }}"}',
        },
        x=1200, y=1280, display_name="Glossary lookup (description → workflow)", category="action",
    ))
    edges.append(_edge("node_4", "node_om1", label="output_missing", source_handle="output_missing", color="#06b6d4"))

    nodes.append(_node(
        "node_om2", "Switch",
        {
            "icon": "git-fork",
            "expression": "node_om1.json.match.workflow_id",
            "cases": [],  # any non-empty match takes the first non-default branch via source_handle
            "defaultLabel": "No match (clarify)",
            "matchMode": "equals",
        },
        x=1340, y=1280, display_name="Did glossary find a workflow?", category="logic",
    ))
    edges.append(_edge("node_om1", "node_om2"))

    # Build dynamic case list — one case per known workflow_id so we route
    # to the investigator. We use a single shared investigator either way,
    # so we can keep the Switch simple: if workflow_id is non-empty/non-null,
    # follow source_handle="<id>"; else default to clarify.
    # Simpler: use a tiny Code node to translate "has match?" into a string.
    # Actually the cleanest is: invert — Switch on node_om1.json.match's truthiness.
    # The Switch we have only does string equals. So we'll use a Code node
    # that emits "matched" or "unmatched", then a Switch on that.
    nodes.append(_node(
        "node_om2b", "Code",
        {
            "icon": "code",
            "language": "python",
            "code": (
                "om1 = inputs.get('node_om1') or {}\n"
                "j = om1.get('json') or {}\n"
                "wf = (j.get('match') or {}).get('workflow_id')\n"
                "output = {'has_match': bool(wf), 'flag': 'matched' if wf else 'unmatched'}\n"
            ),
            "timeout": 5,
        },
        x=1410, y=1280, display_name="has_match flag", category="action",
    ))
    edges.append(_edge("node_om1", "node_om2b"))

    # Replace node_om2 wiring — now Switch on node_om2b.flag.
    nodes[-2]["data"]["config"]["expression"] = "node_om2b.flag"
    nodes[-2]["data"]["config"]["cases"] = [
        {"value": "matched", "label": "Investigate"},
    ]
    edges.append(_edge("node_om2b", "node_om2"))

    nodes.append(_node(
        "node_om3", "ReAct Agent",
        {
            "icon": "repeat",
            "model": GEMINI_FLASH,
            "provider": "vertex",
            "tools": [],  # SMART-06 auto-discover; investigator picks what it needs
            "maxIterations": 8,
            "systemPrompt": _wrap(OUTPUT_MISSING_INVESTIGATOR_PROMPT),
        },
        x=1480, y=1240, display_name="Output-missing investigator (workflow_id pre-resolved)", category="agent",
    ))
    edges.append(_edge("node_om2", "node_om3", label="Investigate", source_handle="matched", color="#06b6d4"))

    nodes.append(_node(
        "node_om4", "LLM Agent",
        {
            "icon": "help-circle",
            "model": GEMINI_25_FLASH,
            "provider": "vertex",
            "temperature": 0.4,
            "maxTokens": 384,
            "systemPrompt": _wrap(OUTPUT_MISSING_CLARIFY_PROMPT),
        },
        x=1480, y=1320, display_name="Clarify which output", category="agent",
    ))
    edges.append(_edge("node_om2", "node_om4", label="Clarify", source_handle="default", color="#f59e0b"))

    # Bridge + Save for the output_missing branch (one each, both come AFTER
    # whichever of the two specialists ran — but they're mutually exclusive
    # via the Switch so no fan-in problem).
    nodes.append(_node(
        "node_om5_a", "Bridge User Reply",
        {"icon": "message-square", "responseNodeId": "node_om3", "messageExpression": ""},
        x=1700, y=1240, display_name="Reply (output_missing investigate)", category="action",
    ))
    nodes.append(_node(
        "node_om6_a", "Save Conversation State",
        {
            "icon": "save", "responseNodeId": "node_om3",
            "sessionIdExpression": "trigger.session_id",
            "userMessageExpression": "trigger.message",
        },
        x=1900, y=1240, display_name="Save (output_missing investigate)", category="action",
    ))
    edges.append(_edge("node_om3", "node_om5_a"))
    edges.append(_edge("node_om5_a", "node_om6_a"))

    nodes.append(_node(
        "node_om5_b", "Bridge User Reply",
        {"icon": "message-square", "responseNodeId": "node_om4", "messageExpression": ""},
        x=1700, y=1320, display_name="Reply (output_missing clarify)", category="action",
    ))
    nodes.append(_node(
        "node_om6_b", "Save Conversation State",
        {
            "icon": "save", "responseNodeId": "node_om4",
            "sessionIdExpression": "trigger.session_id",
            "userMessageExpression": "trigger.message",
        },
        x=1900, y=1320, display_name="Save (output_missing clarify)", category="action",
    ))
    edges.append(_edge("node_om4", "node_om5_b"))
    edges.append(_edge("node_om5_b", "node_om6_b"))

    # ── V5.3 Per-specialist fallback subtrees ─────────────────────────────
    # When a ReAct specialist hits a dead-end (max iterations, all tools
    # erroring, "couldn't find" responses), we route to a fallback subtree
    # that PATCHes the case to WAITING_ON_TEAM and uses an LLM agent to
    # format a role-aware response containing the ticket ID.
    #
    # Each ReAct specialist gets its OWN fallback subtree (4 nodes) instead
    # of sharing one — sharing causes a fan-in deadlock since the dag_runner
    # waits for all upstream edges before firing a node.
    #
    # IDs:
    #   diagnostics  → node_50/51/52/53
    #   remediation  → node_60/61/62/63
    #   default ops  → node_70/71/72/73
    fallback_subtrees: dict[str, dict[str, str]] = {}
    fallback_specs = [
        ("diagnostics", 50, 400),
        ("remediation", 60, 560),
        ("default",     70, 880),
    ]
    for name, base, y_anchor in fallback_specs:
        patch_id = f"node_{base}"
        reply_id = f"node_{base + 1}"
        bridge_id = f"node_{base + 2}"
        save_id = f"node_{base + 3}"
        fallback_subtrees[name] = {
            "patch": patch_id, "reply": reply_id, "bridge": bridge_id, "save": save_id,
        }
        y_off = y_anchor + 200  # below each specialist
        nodes.append(_node(
            patch_id, "HTTP Request",
            {
                "icon": "globe",
                "url": "http://localhost:5050/api/cases/{{ node_2b.json.id }}",
                "method": "PATCH",
                "headers": {
                    "Content-Type": "application/json",
                    "X-Tenant-Id": "default",
                },
                "body": '{"state": "WAITING_ON_TEAM", "assigned_team": "L2_OPS"}',
            },
            x=1480, y=y_off, display_name=f"Fallback {name}: → WAITING_ON_TEAM", category="action",
        ))
        nodes.append(_node(
            reply_id, "LLM Agent",
            {
                "icon": "alert-triangle",
                "model": GEMINI_25_FLASH,
                "provider": "vertex",
                "temperature": 0.5,
                "maxTokens": 384,
                "systemPrompt": _wrap(FALLBACK_REPLY_PROMPT),
            },
            x=1700, y=y_off, display_name=f"Fallback {name}: ticket-ID reply", category="agent",
        ))
        nodes.append(_node(
            bridge_id, "Bridge User Reply",
            {"icon": "message-square", "responseNodeId": reply_id, "messageExpression": ""},
            x=1900, y=y_off, display_name=f"Reply (fallback {name})", category="action",
        ))
        nodes.append(_node(
            save_id, "Save Conversation State",
            {
                "icon": "save",
                "responseNodeId": reply_id,
                "sessionIdExpression": "trigger.session_id",
                "userMessageExpression": "trigger.message",
            },
            x=2100, y=y_off, display_name=f"Save (fallback {name})", category="action",
        ))
        edges.append(_edge(patch_id, reply_id))
        edges.append(_edge(reply_id, bridge_id))
        edges.append(_edge(bridge_id, save_id))

    # 11-22: Bridge + Save pair per branch
    next_id = 11
    for spec_id, _, _, y, handle, _ in specialists:
        bridge_id = f"node_{next_id}"
        save_id = f"node_{next_id + 1}"
        nodes.append(_node(
            bridge_id, "Bridge User Reply",
            {"icon": "message-square", "responseNodeId": spec_id, "messageExpression": ""},
            x=1480, y=y, display_name=f"Reply ({spec_id})", category="action",
        ))
        nodes.append(_node(
            save_id, "Save Conversation State",
            {
                "icon": "save",
                "responseNodeId": spec_id,
                "sessionIdExpression": "trigger.session_id",
                "userMessageExpression": "trigger.message",
            },
            x=1700, y=y, display_name=f"Save ({spec_id})", category="action",
        ))

        # Per-handle case-state side-effects. These run AFTER the specialist
        # so the LLM has already formed its reply; the HTTP PATCH is purely
        # for the case row's audit trail and for the next-turn short-circuit
        # in node_2c.
        side_effect_id = None
        if handle == "handoff":
            side_effect_id = f"node_{next_id + 100}"
            nodes.append(_node(
                side_effect_id, "HTTP Request",
                {
                    "icon": "globe",
                    "url": "http://localhost:5050/api/cases/{{ node_2b.json.id }}/handoff",
                    "method": "POST",
                    "headers": {
                        "Content-Type": "application/json",
                        "X-Tenant-Id": "default",
                    },
                    "body": '{"team": "{{ node_3b.entities.team | default(\'unassigned\') }}", '
                            '"reason": "user requested via /handoff or natural-language escalation"}',
                },
                x=1340, y=y, display_name="Mark case HANDED_OFF", category="action",
            ))
        elif handle == "resolution_update":
            # Resolution: case → RESOLVED_PENDING_CONFIRMATION (state stays
            # this way until the next turn confirms it's truly resolved).
            side_effect_id = f"node_{next_id + 100}"
            nodes.append(_node(
                side_effect_id, "HTTP Request",
                {
                    "icon": "globe",
                    "url": "http://localhost:5050/api/cases/{{ node_2b.json.id }}",
                    "method": "PATCH",
                    "headers": {
                        "Content-Type": "application/json",
                        "X-Tenant-Id": "default",
                    },
                    "body": '{"state": "RESOLVED_PENDING_CONFIRMATION"}',
                },
                x=1340, y=y, display_name="Mark RESOLVED_PENDING_CONFIRMATION", category="action",
            ))
        elif handle == "cancel_or_withdraw":
            # Cancel: case state stays as-is (FAILED would be wrong — user
            # withdrew, not the system). Add a worknote so the case audit
            # trail records the cancellation.
            side_effect_id = f"node_{next_id + 100}"
            nodes.append(_node(
                side_effect_id, "HTTP Request",
                {
                    "icon": "globe",
                    "url": "http://localhost:5050/api/cases/{{ node_2b.json.id }}/worknote",
                    "method": "POST",
                    "headers": {
                        "Content-Type": "application/json",
                        "X-Tenant-Id": "default",
                    },
                    "body": '{"by": "system", "text": "User withdrew the request — investigation halted."}',
                },
                x=1340, y=y, display_name="Append worknote (withdrawn)", category="action",
            ))

        # V7.1 — for the remediation branch ONLY, insert the verification
        # subgraph between the specialist and the health check. The
        # remediation specialist's response goes into the verification
        # ReAct (which makes its OWN tool calls and emits its OWN
        # response), and the bridge then renders the verification's
        # response (so the user sees before/after, not the remediation's
        # original "I'll restart now" message).
        verify_after_remediation = (handle == "remediation")

        # V5.3 — Health check + fallback ONLY for the 3 ReAct specialists.
        # Each gets its own fallback subtree (no fan-in).
        if handle in ("diagnostics", "remediation", "default"):
            hc_id = f"node_{next_id + 200}"
            sw_id = f"node_{next_id + 201}"
            subtree = fallback_subtrees[handle]
            nodes.append(_node(
                hc_id, "Code",
                {"icon": "code", "language": "python", "code": HEALTH_CHECK_CODE, "timeout": 5},
                x=1340, y=y + 80, display_name="Health check", category="action",
            ))
            nodes.append(_node(
                sw_id, "Switch",
                {
                    "icon": "git-fork",
                    "expression": f"node_{next_id + 200}.is_fallback",
                    "cases": [{"value": "True", "label": "Fallback"}],
                    "defaultLabel": "Healthy",
                    "matchMode": "equals",
                },
                x=1410, y=y + 80, display_name="Healthy or fallback?", category="logic",
            ))
            upstream_for_hc = side_effect_id if side_effect_id else spec_id

            # V7.1 — for remediation, insert verification between
            # specialist and health check. After verification:
            #   - bridge renders the VERIFICATION agent's response
            #     (before/after summary), not the remediation's
            #     original "I'll restart now" message.
            if verify_after_remediation:
                edges.append(_edge(upstream_for_hc, "node_v1"))
                edges.append(_edge("node_v1", "node_v2"))
                edges.append(_edge("node_v2", "node_v3", label="Verify", source_handle="yes", color="#10b981"))
                edges.append(_edge("node_v2", hc_id, label="Skip", source_handle="default"))
                # Verify path → health-check → switch → bridge or fallback
                edges.append(_edge("node_v3", hc_id))
                # When verification ran, swap the bridge's responseNodeId
                # to node_v3 so the user sees the verification's reply.
                # (Bridge nodes were already created with the specialist's
                # spec_id; we patch in place via index.)
                for n in nodes:
                    if n["id"] == bridge_id and n["data"]["label"] == "Bridge User Reply":
                        n["data"]["config"]["responseNodeId"] = "node_v3"
                        break
                for n in nodes:
                    if n["id"] == save_id and n["data"]["label"] == "Save Conversation State":
                        n["data"]["config"]["responseNodeId"] = "node_v3"
                        break
            else:
                edges.append(_edge(upstream_for_hc, hc_id))

            edges.append(_edge(hc_id, sw_id))
            edges.append(_edge(sw_id, subtree["patch"], label="Fallback", source_handle="True", color="#dc2626"))
            edges.append(_edge(sw_id, bridge_id, label="Healthy", source_handle="default"))
        else:
            # Existing path for non-ReAct specialists.
            if side_effect_id is not None:
                edges.append(_edge(spec_id, side_effect_id))
                edges.append(_edge(side_effect_id, bridge_id))
            else:
                edges.append(_edge(spec_id, bridge_id))
        edges.append(_edge(bridge_id, save_id))
        next_id += 2

    # Handed-off branch — feed node_2d into a dedicated bridge+save pair
    nodes.append(_node(
        "node_2e", "Bridge User Reply",
        {"icon": "message-square", "responseNodeId": "node_2d", "messageExpression": ""},
        x=940, y=560, display_name="Reply (handed-off)", category="action",
    ))
    nodes.append(_node(
        "node_2f", "Save Conversation State",
        {
            "icon": "save",
            "responseNodeId": "node_2d",
            "sessionIdExpression": "trigger.session_id",
            "userMessageExpression": "trigger.message",
        },
        x=1160, y=560, display_name="Save (handed-off)", category="action",
    ))
    edges.append(_edge("node_2d", "node_2e"))
    edges.append(_edge("node_2e", "node_2f"))

    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# POST to orchestrator
# ---------------------------------------------------------------------------

def main() -> int:
    graph = build_graph()

    # Save a local copy for inspection.
    out_path = Path(__file__).parent / "ae_ops_support_workflow.json"
    out_path.write_text(json.dumps(graph, indent=2))
    print(f"saved graph to {out_path}  ({len(graph['nodes'])} nodes, {len(graph['edges'])} edges)")

    # POST to orchestrator
    payload = {
        "name": "AE Ops Support — V5 (hybrid + audience-aware + additive)",
        "description": "V5 = V4 + (1) Intent Classifier in HYBRID mode (heuristic fast-path, "
                       "fast-LLM fallback below 0.6 confidence — handles nuance & corrections). "
                       "(2) Cross-cutting prompt rules: audience-aware language (business vs "
                       "tech), confidence hedging, memory awareness (reads node_2.messages), "
                       "verification after destructive actions. (3) New intents: output_missing, "
                       "resolution_update, cancel_or_withdraw, correction — with case-state "
                       "side-effects (RESOLVED_PENDING_CONFIRMATION, audit-worknote-on-withdraw). "
                       "(4) Per-specialist case-state HTTP PATCHes for full audit trail. "
                       "Pending V5.3 (fallback ticket-ID exit) + V5.6 (output_missing playbook).",
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
    print(f"\nWebhook will be live at:  POST {ORCH_URL}/api/v1/workflows/{body.get('id')}/webhook/ae/ops/inbound")
    print("(Or whatever the registered webhook path is — check `GET /api/v1/workflows/{id}` for the resolved URL.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
