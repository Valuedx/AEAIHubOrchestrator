"""Replay-driven eval harness for AE Ops Support workflows (V7 / V8 / future).

Loads scratch/ae_ops_eval_transcripts.json, replays each multi-turn case
against one or more workflow ids, captures structured artifacts (intent
classification, tool calls, case state transitions, latency, final reply),
scores against per-turn expectations, and emits a markdown side-by-side
report.

Usage
-----
    # Single workflow
    ORCHESTRATOR_BASE_URL=http://localhost:8001 \\
    python -m scratch.run_ae_ops_evals \\
      --workflow eac515ac-2529-4440-80cc-0f16fa73a5b8 \\
      --label V7 \\
      --out report.md

    # Side-by-side V7 vs V8
    python -m scratch.run_ae_ops_evals \\
      --workflow <V7-id>:V7 \\
      --workflow <V8-id>:V8 \\
      --out report.md

    # Run a single case for fast iteration
    python -m scratch.run_ae_ops_evals \\
      --workflow <id>:V8 \\
      --case missing-identifier-business-vague

The harness is INTENTIONALLY conservative: it never hits destructive AE
MCP tools because the orchestrator's HITL gate parks runs at
AWAITING_APPROVAL — destructive cases (e.g. "destructive-with-verification")
are scored on whether the run reached SUSPENDED state with the right
approval message, not on the outcome of the action.

Best-practice references this harness leans on:
  - Anthropic, *Building Effective Agents* — eval-driven iteration over
    workflow patterns; the eval set IS the spec.
  - OpenAI, *Practical Guide to Building Agents* — explicit step limits,
    structured outputs, replayable traces.
  - 12-Factor Agents — own your prompts AND your evals; treat them as
    code, not docs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

ORCH_URL = os.environ.get("ORCHESTRATOR_BASE_URL", "http://localhost:8001")
TENANT_ID = os.environ.get("TENANT_ID", "default")
HEADERS = {"X-Tenant-Id": TENANT_ID, "Content-Type": "application/json"}
TRANSCRIPTS = Path(__file__).parent / "ae_ops_eval_transcripts.json"
# Test Flask server (AEAIHUBTesterUI/agent_server.py) hosts the
# eval-mock fixture endpoints. Set EVAL_MOCK_BASE_URL to override
# (e.g. when the Flask server is on a different port).
EVAL_MOCK_BASE_URL = os.environ.get("EVAL_MOCK_BASE_URL", "http://localhost:5050").rstrip("/")

# Tightened 2026-05-02 — substring-matching on "stop" / "restart" was
# causing false positives on read-only listing tools like
# ``ae.agent.list_stopped`` and ``ae.agent.list_running`` (which are
# explicitly NOT destructive — they enumerate state). Switch to
# explicit unambiguous fragments that only appear in tools that
# actually mutate state.
DESTRUCTIVE_TOOL_FRAGMENTS = (
    "restart_service",          # ae.agent.restart_service
    "clear_stale_rdp_session",  # ae.agent.clear_stale_rdp_session
    ".rerun",                   # ae.request.rerun (leading dot avoids matching "rerunning" in summaries)
    "terminate_running",        # ae.request.terminate_running
    "cancel_new_or_retry",      # ae.request.cancel_new_or_retry
    "resubmit_from",            # ae.request.resubmit_from_failure_point
    "run_now",                  # ae.schedule.run_now
    "rotate",                   # credential rotation
    "change_schedule",          # schedule mutation
    ".disable",                 # ae.schedule.disable / ae.workflow.disable
    ".enable",                  # ae.schedule.enable / ae.workflow.enable
    "rollback_version",         # ae.workflow.rollback_version
    "kill_process",             # if ever added
)


@dataclass
class TurnArtifacts:
    """Structured slice of a single turn's run, keyed off node ids in the V7/V8 graphs."""
    turn_index: int
    user_input: str
    final_reply: str = ""
    status: str = ""  # completed | failed | suspended | timeout
    instance_id: str = ""
    intent: str | None = None
    intent_score: float | None = None
    case_state: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    verifier_text: str | None = None
    latency_seconds: float = 0.0
    error: str | None = None

    @property
    def tool_call_count(self) -> int:
        return len(self.tool_calls)

    @property
    def destructive_called(self) -> bool:
        for tc in self.tool_calls:
            name = (tc.get("toolName") or tc.get("name") or "").lower()
            if any(frag in name for frag in DESTRUCTIVE_TOOL_FRAGMENTS):
                return True
        return False


@dataclass
class TurnScore:
    pass_count: int = 0
    fail_count: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.pass_count + self.fail_count

    @property
    def passed(self) -> bool:
        return self.fail_count == 0 and self.pass_count > 0


# ---------------------------------------------------------------------------
# Orchestrator interaction
# ---------------------------------------------------------------------------

def execute_turn(workflow_id: str, session_id: str, user_role: str, message: str,
                 timeout_seconds: int = 180) -> TurnArtifacts:
    """POST one turn through the orchestrator, poll to completion / suspend, harvest artifacts."""
    art = TurnArtifacts(turn_index=-1, user_input=message)
    t0 = time.time()
    try:
        url = f"{ORCH_URL}/api/v1/workflows/{workflow_id}/execute"
        body = {
            "trigger_payload": {
                "message": message,
                "session_id": session_id,
                "user_id": "eval_runner",
                "user_role": user_role,
            }
        }
        r = httpx.post(url, json=body, headers=HEADERS, timeout=30)
        if r.status_code >= 400:
            art.status = "failed"
            art.error = f"execute HTTP {r.status_code}: {r.text[:200]}"
            return art
        instance_id = r.json().get("id") or ""
        art.instance_id = instance_id

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            time.sleep(1.5)
            ctx_url = f"{ORCH_URL}/api/v1/workflows/{workflow_id}/instances/{instance_id}/context"
            cr = httpx.get(ctx_url, headers=HEADERS, timeout=10)
            if cr.status_code != 200:
                continue
            ctx = cr.json()
            status = ctx.get("status") or ""
            if status in ("completed", "failed", "suspended"):
                art.status = status
                _harvest(ctx, art)
                break
        else:
            art.status = "timeout"
            art.error = f"polling exceeded {timeout_seconds}s"
    except Exception as exc:
        art.status = "failed"
        art.error = f"{type(exc).__name__}: {exc}"
    finally:
        art.latency_seconds = round(time.time() - t0, 2)
    return art


def _harvest(ctx: dict, art: TurnArtifacts) -> None:
    """Pull the structured slice we score against. Node ids match V7/V8 graphs.

    This deliberately covers BOTH V7 and V8 node naming so the same harness
    works against both. If a node isn't present, we just skip it — the
    scorer treats a None field as 'not asserted', not as a failure.
    """
    cj = ctx.get("context_json") or {}

    # Final reply: orchestrator_user_reply OR last assistant message
    reply = cj.get("orchestrator_user_reply")
    if isinstance(reply, dict):
        reply = reply.get("reply") or reply.get("content") or reply.get("text")
    if not reply:
        for v in cj.values():
            if isinstance(v, dict) and isinstance(v.get("messages"), list):
                for m in reversed(v["messages"]):
                    if isinstance(m, dict) and m.get("role") == "assistant":
                        reply = m.get("content") or ""
                        break
                if reply:
                    break
    art.final_reply = reply or ""

    # Intent (V7 → node_3 / V8 → node_router). The Intent Classifier returns
    # `intents` as a flat list of strings (e.g. ["small_talk"]) plus a single
    # `confidence` float — not a list of objects. See intent_classifier.py.
    for nid in ("node_router", "node_3"):
        node = cj.get(nid)
        if not isinstance(node, dict):
            continue
        intents = node.get("intents") if isinstance(node.get("intents"), list) else None
        if intents:
            art.intent = intents[0] if isinstance(intents[0], str) else None
            art.intent_score = node.get("confidence")
            break

    # Case state (V7 → node_2b / V8 → node_3)
    for nid in ("node_3", "node_2b"):
        node = cj.get(nid)
        if not isinstance(node, dict):
            continue
        j = node.get("json")
        if isinstance(j, dict) and j.get("state"):
            art.case_state = j.get("state")
            break

    # Tool calls — scan every node for an "iterations" list (ReAct).
    # CTX-MGMT.G (2026-05-01) split the per-iteration tool-call detail
    # off ``iterations`` into ``iterations_full``; the default
    # ``iterations`` shape now carries only ``{action, iteration}``.
    # Prefer ``iterations_full`` when the worker emits it (i.e. the
    # node has ``exposeFullIterations: true``); otherwise fall back
    # to the summary shape (which means we can't see tool calls and
    # the harness will under-count — set the worker to expose-full
    # for reliable eval scoring).
    #
    # Within either shape, the canonical structure is:
    #   iteration.tool_calls = [{name, arguments}, ...]
    # The legacy ``it.get('toolName')`` shape (per-iter single tool)
    # is preserved as a fall-through for older workflow runs.
    for nid, node in cj.items():
        if not isinstance(node, dict):
            continue
        iters = node.get("iterations_full")
        if not isinstance(iters, list) or not iters:
            iters = node.get("iterations") if isinstance(node.get("iterations"), list) else None
        if not iters:
            continue
        for it in iters:
            if not isinstance(it, dict):
                continue
            # Modern shape — list of tool calls per iteration.
            tcs = it.get("tool_calls")
            if isinstance(tcs, list):
                for tc in tcs:
                    if not isinstance(tc, dict):
                        continue
                    tname = tc.get("name") or tc.get("toolName") or tc.get("tool")
                    if tname:
                        art.tool_calls.append({
                            "node": nid,
                            "toolName": tname,
                            "arguments": tc.get("arguments") or tc.get("args") or {},
                        })
                continue
            # Legacy single-tool-per-iteration shape.
            tname = it.get("toolName") or it.get("tool") or it.get("name")
            if tname:
                art.tool_calls.append({
                    "node": nid,
                    "toolName": tname,
                    "arguments": it.get("arguments") or it.get("args") or {},
                })

    # Verifier (V7 → node_v3 / V8 → node_verifier)
    for nid in ("node_verifier", "node_v3"):
        node = cj.get(nid)
        if not isinstance(node, dict):
            continue
        text = node.get("text")
        if text:
            art.verifier_text = text
            break


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_turn(art: TurnArtifacts, expect: dict) -> TurnScore:
    score = TurnScore()
    reply_lower = art.final_reply.lower()

    if "intent_in" in expect:
        ok = art.intent in set(expect["intent_in"])
        _record(score, ok,
                f"intent={art.intent} ∈ {expect['intent_in']}",
                f"intent={art.intent} not in {expect['intent_in']}")

    if "case_state_in" in expect:
        ok = art.case_state in set(expect["case_state_in"])
        _record(score, ok,
                f"case_state={art.case_state} ∈ {expect['case_state_in']}",
                f"case_state={art.case_state} not in {expect['case_state_in']}")

    if "tool_family_any" in expect:
        fams = expect["tool_family_any"]
        ok = any(any(f in (tc.get("toolName") or "") for f in fams) for tc in art.tool_calls)
        _record(score, ok,
                f"tool family ∈ {fams}: hit",
                f"no tool from any of {fams} called (called {len(art.tool_calls)})")

    if "tool_calls_max" in expect:
        ok = art.tool_call_count <= expect["tool_calls_max"]
        _record(score, ok,
                f"tool_calls={art.tool_call_count} ≤ max {expect['tool_calls_max']}",
                f"tool_calls={art.tool_call_count} > max {expect['tool_calls_max']}")

    if "must_include_any" in expect:
        opts = [s.lower() for s in expect["must_include_any"]]
        ok = any(s in reply_lower for s in opts)
        _record(score, ok,
                f"reply contains one of {expect['must_include_any']}",
                f"reply contains NONE of {expect['must_include_any']}")

    if "must_exclude" in expect:
        violators = [s for s in expect["must_exclude"] if s.lower() in reply_lower]
        ok = not violators
        _record(score, ok,
                "reply excludes forbidden terms",
                f"reply LEAKS forbidden term(s): {violators}")

    if "must_exclude_any_of" in expect:
        violators = [s for s in expect["must_exclude_any_of"] if s.lower() in reply_lower]
        ok = not violators
        _record(score, ok,
                "reply excludes forbidden phrases",
                f"reply LEAKS phrase(s): {violators}")

    if expect.get("must_end_with_question"):
        ok = art.final_reply.rstrip().endswith("?")
        _record(score, ok,
                "reply ends with a question",
                f"reply does NOT end with question (tail='{art.final_reply.rstrip()[-40:]}')")

    if expect.get("must_not_re_ask"):
        # Heuristic: if turn ends with a question AND the prior turn already ended
        # with a question, that's a re-ask. Caller passes prior reply via expect.
        prior = expect.get("_prior_reply") or ""
        ok = not (prior.rstrip().endswith("?") and art.final_reply.rstrip().endswith("?"))
        _record(score, ok,
                "did not re-ask (good — accumulated context across turns)",
                "RE-ASKED instead of acting on prior answer (NEED_INFO loop)")

    if expect.get("destructive_tool_called"):
        ok = art.destructive_called
        _record(score, ok,
                "destructive tool was called (HITL gate triggered)",
                "no destructive tool called — expected restart/rerun/terminate")

    if expect.get("verifier_runs"):
        ok = bool(art.verifier_text)
        _record(score, ok,
                "verifier produced output",
                "verifier did not run / no output")

    if "verifier_must_include_any" in expect and art.verifier_text:
        opts = [s.lower() for s in expect["verifier_must_include_any"]]
        ok = any(s in art.verifier_text.lower() for s in opts)
        _record(score, ok,
                f"verifier text mentions one of {expect['verifier_must_include_any']}",
                f"verifier text mentions NONE of {expect['verifier_must_include_any']}")

    if expect.get("must_not_fabricate"):
        # Soft signal: fabrication usually appears as confident analysis without
        # a "not found" signal. We assert the reply DOES include a "not found"
        # marker. Caller can pair this with must_include_any to harden.
        markers = ("not found", "couldn't find", "could not find", "no record",
                   "no evidence", "may have been", "doesn't exist")
        ok = any(m in reply_lower for m in markers)
        _record(score, ok,
                "reply acknowledges 'not found' (no fabrication)",
                "reply LACKS 'not-found' marker — possible fabrication")

    if expect.get("entity_grounding_required"):
        # Hard signal — the Worker made a confident factual claim
        # but called zero tools. That's a fabrication. Catches the
        # turn-1 hallucination pattern surfaced by the V10 eval on
        # 2026-05-01 (Worker replied with specific workflow names +
        # failure counts on a "which requests have failed recently"
        # prompt without ever calling ae.request.failed_recent /
        # ae.workflow.list).
        #
        # Lookup-claim signals:
        #   (a) first-person tool-action phrasing ("I searched", "I
        #       also found", "I checked", "I looked up", etc.) —
        #       Worker claims to have done a lookup,
        #   (b) numeric count claims ("failed N times", "ran X
        #       minutes", "in the last N hours/days") — both digit
        #       and spelled-out forms,
        #   (c) workflow-id-shaped strings that the user did NOT
        #       supply this turn — CamelCase joined
        #       (LogExtractionAndRecognition), snake_case_v<N>
        #       (timesheet_report_generation_v5), OR markdown-bold
        #       multi-word entity phrases (`**timesheet report
        #       generation**` style — the prose disguise Gemini 3
        #       defaulted to when 2.5's CamelCase pattern got
        #       penalised).
        # When ANY fire AND tool_calls == 0 → fabrication.
        #
        # Allowlist: legitimate product/system names (e.g.
        # "AutomationEdge") are not fabrications even though they
        # match the CamelCase shape.
        import re as _re

        # Adverb gap between "I" and the verb — catches "I ALSO found".
        action_re = _re.compile(
            r"\bI(?:\s+\w+){0,2}\s+(searched|found|"
            r"looked\s+up|queried|checked|verified|investigated|located)\b",
            _re.IGNORECASE,
        )
        # Digit OR spelled-out count
        _NUM = (
            r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
            r"a few|several|multiple)"
        )
        count_re = _re.compile(
            r"\b(failed|succeeded|ran|completed|errored)\s+" + _NUM +
            r"\s+times?\b|"
            r"\bin the last\s+" + _NUM + r"\s+(hour|minute|day|week)s?\b|"
            r"\b" + _NUM + r"\s+failures?\b",
            _re.IGNORECASE,
        )
        # CamelCase joined ≥ 2 capital-led runs.
        camel_re = _re.compile(r"\b(?:[A-Z][a-z]+){2,}\b")
        # snake_case_v<N>.
        snake_v_re = _re.compile(r"\b[a-z][a-z_]{4,}_v\d+\b")
        # Markdown-bold multi-word phrase: **two or more words**.
        # Gemini 3 dressed up its hallucinated workflow names this
        # way when the obvious CamelCase shape was unavailable.
        bold_phrase_re = _re.compile(r"\*\*(\w+(?:\s+\w+){1,5})\*\*")

        # Allowlist of known system / product names that legitimately
        # show up in worker replies and aren't fabricated entities.
        # Extended 2026-05-02 to cover AE upstream systems that
        # appear in real HDFC ticket data (LifeAsia, CallHealth,
        # MediAssist) and the AS400 platform reference.
        SYSTEM_NAMES = {
            "automationedge", "automation edge",
            "lifeasia", "life asia",
            "callhealth", "call health",
            "mediassist", "medi assist", "medi-assist",
            "as400", "automationanywhere", "automation anywhere",
            "sharepath", "sharepoint",
        }

        user_lower = (art.user_input or "").lower()
        def _is_user_supplied(token: str) -> bool:
            return token.lower() in user_lower

        def _is_system_name(token: str) -> bool:
            return token.lower() in SYSTEM_NAMES

        action_hits = action_re.findall(art.final_reply)
        count_hits = count_re.findall(art.final_reply)
        camel_hits = [
            t for t in camel_re.findall(art.final_reply)
            if not _is_user_supplied(t) and not _is_system_name(t)
        ]
        snake_hits = [
            t for t in snake_v_re.findall(art.final_reply)
            if not _is_user_supplied(t)
        ]
        bold_hits = [
            t for t in bold_phrase_re.findall(art.final_reply)
            if not _is_user_supplied(t) and not _is_system_name(t)
        ]

        any_lookup_claim = bool(
            action_hits or count_hits or camel_hits or snake_hits or bold_hits
        )
        if any_lookup_claim and art.tool_call_count == 0:
            evidence: list[str] = []
            if action_hits:
                evidence.append(f"action_phrases={action_hits[:3]}")
            if count_hits:
                evidence.append(f"count_claims={count_hits[:3]}")
            if camel_hits:
                evidence.append(f"camel_ids={camel_hits[:3]}")
            if snake_hits:
                evidence.append(f"snake_ids={snake_hits[:3]}")
            if bold_hits:
                evidence.append(f"bold_phrases={bold_hits[:3]}")
            _record(score, False,
                    "entity grounding ok",
                    "FABRICATION: reply makes a data-lookup claim but "
                    f"tool_calls=0 ({'; '.join(evidence)})")
        else:
            _record(score, True,
                    "entity grounding ok (no unsupported claims)",
                    "")

    if "must_reach_status" in expect:
        # Direct check that the workflow instance reached a specific
        # status (typically "suspended" for HITL-gated tests). Tests
        # that the gate fired, not just that a destructive tool name
        # appears. Pairs with `destructive_tool_called`.
        target = expect["must_reach_status"]
        ok = art.status == target
        _record(score, ok,
                f"instance reached status={target}",
                f"instance status={art.status!r}, expected {target!r}")

    if "max_iterations" in expect:
        # Heuristic via tool-call count. ReAct exposes iterations directly but
        # the harvester only keeps tool calls; iterations include zero-tool
        # reasoning steps. tool_calls ≤ max_iterations is a weak upper bound.
        ok = art.tool_call_count <= expect["max_iterations"]
        _record(score, ok,
                f"tool_calls={art.tool_call_count} within max_iterations={expect['max_iterations']}",
                f"tool_calls={art.tool_call_count} exceeds max_iterations={expect['max_iterations']}")

    return score


def _record(score: TurnScore, passed: bool, ok_msg: str, fail_msg: str) -> None:
    if passed:
        score.pass_count += 1
        score.notes.append(f"  ✓ {ok_msg}")
    else:
        score.fail_count += 1
        score.notes.append(f"  ✗ {fail_msg}")


# ---------------------------------------------------------------------------
# Replay loop
# ---------------------------------------------------------------------------

def _install_mock_fixture(case: dict) -> bool:
    """If the case declares ``mock_fixtures``, POST them to the test
    Flask server so the MCP eval_mock layer picks them up. Returns
    True on success, False if the mock infra is unreachable (case
    should be skipped). No-op + True for cases without fixtures."""
    fixtures = case.get("mock_fixtures")
    if not fixtures:
        return True
    try:
        r = httpx.post(
            f"{EVAL_MOCK_BASE_URL}/api/eval/fixture",
            json={"case_id": case["id"], "fixtures": fixtures},
            timeout=5,
        )
        if r.status_code >= 400:
            print(
                f"      [mock-infra] HTTP {r.status_code} from "
                f"{EVAL_MOCK_BASE_URL}/api/eval/fixture: {r.text[:120]}",
                file=sys.stderr,
            )
            return False
        return True
    except Exception as exc:
        print(
            f"      [mock-infra] could not reach {EVAL_MOCK_BASE_URL}: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return False


def _clear_mock_fixture() -> None:
    """Best-effort fixture teardown after a case finishes. Failures
    are non-fatal — the next case overwrites the fixture file anyway."""
    try:
        httpx.delete(f"{EVAL_MOCK_BASE_URL}/api/eval/fixture", timeout=5)
    except Exception:
        pass


def run_case(case: dict, workflow_id: str) -> list[tuple[TurnArtifacts, TurnScore]]:
    session_id = f"eval-{uuid.uuid4().hex[:12]}"
    user_role = case.get("user_role") or "business"

    # Install per-case mock fixture (if declared). When the test
    # Flask server is unreachable, mark the case as skipped so the
    # report reflects that the run was inconclusive (not a behavior
    # failure).
    if not _install_mock_fixture(case):
        art = TurnArtifacts(turn_index=0, user_input=(case.get("turns") or [{}])[0].get("input", ""))
        art.status = "skipped"
        art.error = (
            f"mock infra unavailable at {EVAL_MOCK_BASE_URL} — case requires "
            "mock_fixtures but the Flask test server isn't reachable. Start "
            "agent_server.py and ensure /api/eval/fixture responds."
        )
        skip_score = TurnScore()
        skip_score.notes.append("  ⊘ skipped (mock infra unavailable)")
        return [(art, skip_score)]

    results: list[tuple[TurnArtifacts, TurnScore]] = []
    prior_reply = ""
    try:
        for i, turn in enumerate(case.get("turns") or []):
            art = execute_turn(workflow_id, session_id, user_role, turn["input"])
            art.turn_index = i
            expect = dict(turn.get("expect") or {})
            if "must_not_re_ask" in expect:
                expect["_prior_reply"] = prior_reply
            score = score_turn(art, expect)
            results.append((art, score))
            prior_reply = art.final_reply
    finally:
        if case.get("mock_fixtures"):
            _clear_mock_fixture()

    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def render_markdown(label: str, report: list[tuple[dict, list[tuple[TurnArtifacts, TurnScore]]]]) -> str:
    """Compact summary report — one block per case, truncated reply preview.

    Pair with render_full_transcript() for verification; this one is the
    glanceable scoreboard."""
    lines = [f"## {label}\n"]
    pass_n = fail_n = total = 0
    for case, turns in report:
        case_pass = all(s.passed for _, s in turns)
        pass_n += int(case_pass)
        fail_n += int(not case_pass)
        total += 1
        status = "✅" if case_pass else "❌"
        lines.append(f"### {status} {case['id']}")
        meta = [f"role: `{case.get('user_role', 'business')}`"]
        if case.get("tier"):
            meta.append(f"tier: `{case['tier']}`")
        if case.get("themes"):
            meta.append(f"themes: `{', '.join(case['themes'])}`")
        lines.append(f"_{case.get('description', '')}_  \n{' · '.join(meta)}\n")
        for art, score in turns:
            lines.append(f"**Turn {art.turn_index + 1}**: `{art.user_input[:200]}`")
            lines.append(f"- status: `{art.status}` · latency: `{art.latency_seconds}s` · "
                         f"intent: `{art.intent}` · tool_calls: `{art.tool_call_count}` · "
                         f"case_state: `{art.case_state}`")
            if art.error:
                lines.append(f"- error: `{art.error}`")
            for note in score.notes:
                lines.append(note)
            reply_preview = art.final_reply.replace("\n", " ")[:240]
            lines.append(f"- reply: _{reply_preview}_\n")
        lines.append("")
    summary = f"**{label} summary**: {pass_n}/{total} cases passed, {fail_n} failed.\n"
    return summary + "\n" + "\n".join(lines)


def render_full_transcript(label: str, report: list[tuple[dict, list[tuple[TurnArtifacts, TurnScore]]]]) -> str:
    """Verbose report — full conversation with NO reply truncation, every
    tool call argument, every tool result preview, full verifier output.
    For human verification of agent behavior end-to-end.

    Output structure per case:
      - User turn (full input)
      - Per-turn metadata (status, latency, intent, case_state)
      - Every tool call with full args
      - Every tool result with full preview
      - Full agent reply (no truncation)
      - Verifier output if any
      - Pass/fail breakdown
    """
    lines = [f"# Full conversation transcript — {label}\n"]
    lines.append("This report shows the complete back-and-forth for every case "
                 "in the run. Use it to verify agent behavior end-to-end. The "
                 "summary report (`<out>.md`) has the scoreboard view.\n")

    for case, turns in report:
        case_pass = all(s.passed for _, s in turns)
        status = "✅ PASS" if case_pass else "❌ FAIL"
        lines.append(f"\n---\n")
        lines.append(f"## {status} — `{case['id']}`")
        meta_bits = [
            f"role: **{case.get('user_role', 'business')}**",
        ]
        if case.get("tier"):
            meta_bits.append(f"tier: **{case['tier']}**")
        if case.get("themes"):
            meta_bits.append(f"themes: **{', '.join(case['themes'])}**")
        lines.append(" · ".join(meta_bits))
        lines.append(f"\n> {case.get('description', '')}\n")

        for art, score in turns:
            lines.append(f"\n### Turn {art.turn_index + 1}")
            lines.append(f"\n**👤 User:**\n")
            # Preserve the user's input verbatim, including newlines
            # (some cases include forwarded email threads).
            lines.append("```")
            lines.append(art.user_input)
            lines.append("```")
            lines.append(f"\n**Run metadata:**")
            lines.append(f"- status: `{art.status}` · latency: `{art.latency_seconds}s`")
            lines.append(f"- intent: `{art.intent}`" +
                         (f" (score `{art.intent_score:.3f}`)" if art.intent_score is not None else ""))
            lines.append(f"- tool_calls: `{art.tool_call_count}` · case_state: `{art.case_state}` · "
                         f"instance: `{art.instance_id[:12] + '…' if art.instance_id else 'n/a'}`")
            if art.error:
                lines.append(f"- error: `{art.error}`")

            if art.tool_calls:
                lines.append(f"\n**🔧 Tool calls** ({len(art.tool_calls)}):")
                for i, tc in enumerate(art.tool_calls, 1):
                    name = tc.get("toolName") or tc.get("name") or "<unknown>"
                    args = tc.get("arguments") or {}
                    args_str = json.dumps(args, default=str, ensure_ascii=False)
                    if len(args_str) > 400:
                        args_str = args_str[:397] + "…"
                    lines.append(f"  {i}. `{name}({args_str})`")

            lines.append(f"\n**🤖 Agent reply:**\n")
            if art.final_reply:
                lines.append("> " + art.final_reply.replace("\n", "\n> "))
            else:
                lines.append("_(empty)_")

            if art.verifier_text:
                lines.append(f"\n**🔍 Verifier output:**\n")
                lines.append("> " + art.verifier_text.replace("\n", "\n> "))

            lines.append(f"\n**Scoring** ({score.pass_count}/{score.total} checks passed):")
            for note in score.notes:
                lines.append(note)

        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_workflow_arg(arg: str) -> tuple[str, str]:
    if ":" in arg:
        wid, label = arg.split(":", 1)
        return wid.strip(), label.strip()
    return arg.strip(), arg.strip()[:8]


def main() -> int:
    p = argparse.ArgumentParser(description="AE Ops Support eval harness")
    p.add_argument("--workflow", action="append", required=True,
                   help="workflow id [optionally :LABEL]; pass multiple to compare")
    p.add_argument("--case", action="append",
                   help="filter to specific case id(s); default = all (subject to --tier)")
    p.add_argument("--tier", choices=["core", "extended", "all"], default="core",
                   help="run only cases tagged with this tier (default: core)")
    p.add_argument("--out", default="ae_ops_eval_report.md",
                   help="output markdown path for the SUMMARY scoreboard. A "
                        "companion `<out>.transcript.md` is always written "
                        "alongside with the full conversation flow for human "
                        "verification.")
    args = p.parse_args()

    transcripts = json.loads(TRANSCRIPTS.read_text(encoding="utf-8"))
    cases = transcripts["cases"]

    # Tier filter applies first — narrows the pool. --case selects a
    # specific id (overrides tier; useful for fast iteration on one
    # case regardless of which tier it belongs to).
    if args.case:
        wanted = set(args.case)
        cases = [c for c in cases if c["id"] in wanted]
        if not cases:
            print(f"no cases matched {args.case}", file=sys.stderr)
            return 1
    elif args.tier != "all":
        cases = [c for c in cases if c.get("tier") == args.tier]
        if not cases:
            print(f"no cases tagged tier={args.tier!r}", file=sys.stderr)
            return 1

    print(f"running {len(cases)} case(s) (tier={args.tier})", flush=True)

    sections: list[str] = [f"# AE Ops Support eval — {len(cases)} cases\n",
                           f"orchestrator: `{ORCH_URL}`  ·  tenant: `{TENANT_ID}` · tier: `{args.tier}`\n"]
    overall: list[tuple[str, int, int]] = []

    # Collect per-workflow reports for both summary and full-transcript output.
    workflow_reports: list[tuple[str, list[tuple[dict, list[tuple[TurnArtifacts, TurnScore]]]]]] = []

    for wf_arg in args.workflow:
        wid, label = parse_workflow_arg(wf_arg)
        print(f"\n=== running {label} ({wid}) ===", flush=True)
        report: list[tuple[dict, list[tuple[TurnArtifacts, TurnScore]]]] = []
        for case in cases:
            print(f"  · {case['id']} …", flush=True)
            turns = run_case(case, wid)
            report.append((case, turns))
            for art, score in turns:
                tag = "PASS" if score.passed else "FAIL"
                print(f"      turn {art.turn_index + 1} [{tag}] {score.pass_count}/{score.total}", flush=True)
        sections.append(render_markdown(label, report))
        passed = sum(1 for _, t in report if all(s.passed for _, s in t))
        overall.append((label, passed, len(report)))
        workflow_reports.append((label, report))

    if len(overall) >= 2:
        sections.insert(1, "## Side-by-side\n\n| Label | Passed | Total |\n|---|---|---|")
        for lbl, p_, t_ in overall:
            sections.insert(2, f"| {lbl} | {p_} | {t_} |")

    out_path = Path(args.out)
    out_path.write_text("\n".join(sections), encoding="utf-8")
    print(f"\nReport: {out_path}")

    # Companion full-transcript file for human verification — always
    # generated alongside the summary so reviewers can see the actual
    # conversation flow without reading raw JSON.
    transcript_path = out_path.with_suffix(out_path.suffix + ".transcript.md") \
        if not out_path.suffix.endswith(".transcript.md") \
        else out_path.with_name(out_path.stem + "_transcript.md")
    # Simpler: always replace .md with .transcript.md
    if out_path.suffix == ".md":
        transcript_path = out_path.with_suffix(".transcript.md")
    transcript_lines = [
        f"# AE Ops Support eval — full conversation transcript\n",
        f"orchestrator: `{ORCH_URL}` · tenant: `{TENANT_ID}` · tier: `{args.tier}` · cases: `{len(cases)}`\n",
    ]
    for label, report in workflow_reports:
        transcript_lines.append(render_full_transcript(label, report))
    transcript_path.write_text("\n".join(transcript_lines), encoding="utf-8")
    print(f"Full transcript: {transcript_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
