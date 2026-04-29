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

DESTRUCTIVE_TOOL_FRAGMENTS = (
    "restart", "rerun", "terminate", "rotate", "change_schedule", "kill", "stop",
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

    # Tool calls — scan every node for an "iterations" list (ReAct), pull tool calls.
    # Defensive against str / unexpected shapes in the run context.
    for nid, node in cj.items():
        if not isinstance(node, dict):
            continue
        iters = node.get("iterations") if isinstance(node.get("iterations"), list) else None
        if not iters:
            continue
        for it in iters:
            if not isinstance(it, dict):
                continue
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

def run_case(case: dict, workflow_id: str) -> list[tuple[TurnArtifacts, TurnScore]]:
    session_id = f"eval-{uuid.uuid4().hex[:12]}"
    user_role = case.get("user_role") or "business"
    results: list[tuple[TurnArtifacts, TurnScore]] = []
    prior_reply = ""
    for i, turn in enumerate(case.get("turns") or []):
        art = execute_turn(workflow_id, session_id, user_role, turn["input"])
        art.turn_index = i
        expect = dict(turn.get("expect") or {})
        if "must_not_re_ask" in expect:
            expect["_prior_reply"] = prior_reply
        score = score_turn(art, expect)
        results.append((art, score))
        prior_reply = art.final_reply
    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def render_markdown(label: str, report: list[tuple[dict, list[tuple[TurnArtifacts, TurnScore]]]]) -> str:
    lines = [f"## {label}\n"]
    pass_n = fail_n = total = 0
    for case, turns in report:
        case_pass = all(s.passed for _, s in turns)
        pass_n += int(case_pass)
        fail_n += int(not case_pass)
        total += 1
        status = "✅" if case_pass else "❌"
        lines.append(f"### {status} {case['id']}")
        lines.append(f"_{case.get('description', '')}_  \nrole: `{case.get('user_role', 'business')}`\n")
        for art, score in turns:
            lines.append(f"**Turn {art.turn_index + 1}**: `{art.user_input}`")
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
                   help="filter to specific case id(s); default = all")
    p.add_argument("--out", default="ae_ops_eval_report.md", help="output markdown path")
    args = p.parse_args()

    transcripts = json.loads(TRANSCRIPTS.read_text())
    cases = transcripts["cases"]
    if args.case:
        wanted = set(args.case)
        cases = [c for c in cases if c["id"] in wanted]
        if not cases:
            print(f"no cases matched {args.case}", file=sys.stderr)
            return 1

    sections: list[str] = [f"# AE Ops Support eval — {len(cases)} cases\n",
                           f"orchestrator: `{ORCH_URL}`  ·  tenant: `{TENANT_ID}`\n"]
    overall: list[tuple[str, int, int]] = []

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

    if len(overall) >= 2:
        sections.insert(1, "## Side-by-side\n\n| Label | Passed | Total |\n|---|---|---|")
        for lbl, p_, t_ in overall:
            sections.insert(2, f"| {lbl} | {p_} | {t_} |")

    Path(args.out).write_text("\n".join(sections), encoding="utf-8")
    print(f"\nReport: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
