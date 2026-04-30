"""COPILOT-V2 — predicate-based assertions for test scenarios.

Pure helper module. Each predicate is a small function that takes the
run's ``actual_output`` dict (the same shape ``run_scenario`` already
produces — the workflow context's filtered output) and a small ``args``
dict, returns ``(status, why)`` where status is ``"pass" | "fail"`` and
``why`` is a one-line human-readable explanation.

The composer (``evaluate_predicates``) walks a list of
``{type, args}`` entries and aggregates results.

Why a separate module?

Predicate evaluation has zero engine / DB dependencies — it's a pure
read of the captured output. Keeping it pure keeps unit tests cheap
and makes the predicate set explicit + reviewable.

Predicate types (v1)
--------------------

``ends_with_question``
    Reply ends with a question mark (after stripping trailing
    whitespace and punctuation noise). No args.

``contains_any``
    ``args.terms = list[str]``: at least one substring is present
    (case-insensitive).

``contains_all``
    ``args.terms = list[str]``: every substring is present.

``lacks_terms``
    ``args.terms = list[str]``: no substring is present (case-
    insensitive).

``intent_in``
    ``args.allowed = list[str]``: ``node_router.intents[0]`` is in
    the allowed list. Useful for AE-Ops-style routing checks.

``regex_match``
    ``args.pattern = str``: reply matches the regex.
    ``args.flags = "i" | "m" | "s"`` (optional, single chars in any
    order — translated to ``re`` flags).

``no_max_iterations_marker``
    Reply does NOT contain the engine's "Maximum iterations reached"
    marker. No args.

``tool_called``
    ``args.name_prefix = str``: at least one tool call in the run's
    iterations matches the prefix.

``no_tool_called``
    ``args.name_prefix = str``: NO tool call matches the prefix.

``tool_call_count``
    ``args.min = int`` (optional), ``args.max = int`` (optional):
    total tool-call count is within bounds.
"""

from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_reply(output: dict[str, Any]) -> str:
    """Pick the user-facing reply out of a run's filtered output. The
    workflow's context typically stores the final reply at
    ``orchestrator_user_reply``; some bridge nodes also store it at
    their own ``text`` field. Try a few common locations."""
    if not isinstance(output, dict):
        return ""
    direct = output.get("orchestrator_user_reply")
    if isinstance(direct, str):
        return direct
    # Fallback: any nested dict whose source is messageExpression has
    # the reply at .text. Walk one level deep — the bridge node ids
    # vary per workflow.
    for v in output.values():
        if isinstance(v, dict) and v.get("source") == "messageExpression":
            t = v.get("text")
            if isinstance(t, str):
                return t
    return ""


def _intent(output: dict[str, Any]) -> str | None:
    router = output.get("node_router") if isinstance(output, dict) else None
    if isinstance(router, dict):
        intents = router.get("intents") or []
        if intents and isinstance(intents[0], str):
            return intents[0]
    return None


def _all_tool_calls(output: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect tool-call records across every ReAct node in the run.

    Each ReAct node stores its iteration trace in either:
      * ``iterations_full`` — verbose form, only present when the
        node opted in via ``exposeFullIterations: True`` (CTX-MGMT.G).
      * ``iterations`` — safe summary, ALWAYS present. Each entry's
        ``tool_calls`` field carries just the tool name (no args).

    The predicate evaluator only needs tool names to answer
    ``tool_called`` / ``no_tool_called`` / ``tool_call_count``
    questions, so the summary suffices. Read ``iterations_full``
    preferentially so callers that opted into the verbose form get
    the full args (in case predicates evolve to inspect args), and
    fall back to ``iterations`` otherwise.
    """
    out: list[dict[str, Any]] = []
    if not isinstance(output, dict):
        return out
    for v in output.values():
        if not isinstance(v, dict):
            continue
        # CTX-MGMT.G — prefer iterations_full when present (the
        # verbose form has identical-or-richer tool_calls shape);
        # fall back to iterations (the canonical summary).
        iters = v.get("iterations_full")
        if not isinstance(iters, list):
            iters = v.get("iterations")
        if not isinstance(iters, list):
            continue
        for it in iters:
            if not isinstance(it, dict):
                continue
            tcs = it.get("tool_calls")
            if isinstance(tcs, list):
                for tc in tcs:
                    if isinstance(tc, dict):
                        out.append(tc)
    return out


def _flags(spec: str) -> int:
    """Translate a flag string ('ims') to re flag bits."""
    if not isinstance(spec, str):
        return 0
    bits = 0
    if "i" in spec:
        bits |= re.IGNORECASE
    if "m" in spec:
        bits |= re.MULTILINE
    if "s" in spec:
        bits |= re.DOTALL
    return bits


# ---------------------------------------------------------------------------
# Predicate implementations
# ---------------------------------------------------------------------------


def _pred_ends_with_question(output: dict[str, Any], args: dict[str, Any]) -> tuple[str, str]:
    reply = _user_reply(output).rstrip()
    # Strip trailing punctuation/whitespace combos like ' ?', ').'
    while reply and reply[-1] in ' \t\n)]"\'`*':
        reply = reply[:-1]
    if reply.endswith("?"):
        return "pass", "reply ends with '?'"
    tail = reply[-40:] if reply else "<empty>"
    return "fail", f"reply does not end with '?' (tail: {tail!r})"


def _pred_contains_any(output: dict[str, Any], args: dict[str, Any]) -> tuple[str, str]:
    terms = args.get("terms") or []
    if not isinstance(terms, list) or not terms:
        return "fail", "contains_any requires non-empty 'terms' list"
    reply = _user_reply(output).lower()
    matched = [t for t in terms if isinstance(t, str) and t.lower() in reply]
    if matched:
        return "pass", f"matched: {matched[:3]}"
    return "fail", f"reply contains none of: {terms[:5]}"


def _pred_contains_all(output: dict[str, Any], args: dict[str, Any]) -> tuple[str, str]:
    terms = args.get("terms") or []
    if not isinstance(terms, list) or not terms:
        return "fail", "contains_all requires non-empty 'terms' list"
    reply = _user_reply(output).lower()
    missing = [t for t in terms if isinstance(t, str) and t.lower() not in reply]
    if not missing:
        return "pass", f"all {len(terms)} terms present"
    return "fail", f"missing: {missing[:5]}"


def _pred_lacks_terms(output: dict[str, Any], args: dict[str, Any]) -> tuple[str, str]:
    terms = args.get("terms") or []
    if not isinstance(terms, list) or not terms:
        return "fail", "lacks_terms requires non-empty 'terms' list"
    reply = _user_reply(output).lower()
    leaks = [t for t in terms if isinstance(t, str) and t.lower() in reply]
    if not leaks:
        return "pass", "no forbidden terms found"
    return "fail", f"reply leaks forbidden term(s): {leaks[:5]}"


def _pred_intent_in(output: dict[str, Any], args: dict[str, Any]) -> tuple[str, str]:
    allowed = args.get("allowed") or []
    if not isinstance(allowed, list) or not allowed:
        return "fail", "intent_in requires non-empty 'allowed' list"
    intent = _intent(output)
    if intent is None:
        return "fail", "no router intent found in run context"
    if intent in allowed:
        return "pass", f"intent={intent!r} ∈ {allowed}"
    return "fail", f"intent={intent!r} not in {allowed}"


def _pred_regex_match(output: dict[str, Any], args: dict[str, Any]) -> tuple[str, str]:
    pattern = args.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return "fail", "regex_match requires 'pattern' string"
    flag_bits = _flags(args.get("flags") or "")
    try:
        compiled = re.compile(pattern, flag_bits)
    except re.error as exc:
        return "fail", f"regex_match: bad pattern — {exc}"
    reply = _user_reply(output)
    if compiled.search(reply):
        return "pass", "regex matched"
    return "fail", f"regex did not match (reply head: {reply[:80]!r})"


def _pred_no_max_iterations_marker(output: dict[str, Any], args: dict[str, Any]) -> tuple[str, str]:
    reply = _user_reply(output).lower()
    bad_phrases = ("maximum iterations reached", "iterations reached without final answer")
    leaks = [p for p in bad_phrases if p in reply]
    if not leaks:
        return "pass", "no max-iterations marker"
    return "fail", f"reply contains: {leaks[0]!r}"


def _pred_tool_called(output: dict[str, Any], args: dict[str, Any]) -> tuple[str, str]:
    prefix = args.get("name_prefix")
    if not isinstance(prefix, str) or not prefix:
        return "fail", "tool_called requires 'name_prefix' string"
    calls = _all_tool_calls(output)
    matched = [
        tc.get("name", "") for tc in calls
        if str(tc.get("name", "")).startswith(prefix)
    ]
    if matched:
        return "pass", f"tool prefix {prefix!r} called {len(matched)}× (e.g. {matched[0]!r})"
    return "fail", f"no tool call matched prefix {prefix!r}"


def _pred_no_tool_called(output: dict[str, Any], args: dict[str, Any]) -> tuple[str, str]:
    prefix = args.get("name_prefix")
    if not isinstance(prefix, str) or not prefix:
        return "fail", "no_tool_called requires 'name_prefix' string"
    calls = _all_tool_calls(output)
    matched = [tc.get("name", "") for tc in calls if str(tc.get("name", "")).startswith(prefix)]
    if not matched:
        return "pass", f"no tool with prefix {prefix!r} was called"
    return "fail", f"tool prefix {prefix!r} called: {matched[:3]}"


def _pred_tool_call_count(output: dict[str, Any], args: dict[str, Any]) -> tuple[str, str]:
    n = len(_all_tool_calls(output))
    lo = args.get("min")
    hi = args.get("max")
    if isinstance(lo, int) and n < lo:
        return "fail", f"tool_call_count={n} < min={lo}"
    if isinstance(hi, int) and n > hi:
        return "fail", f"tool_call_count={n} > max={hi}"
    return "pass", f"tool_call_count={n} within bounds (min={lo}, max={hi})"


_PREDICATE_REGISTRY: dict[str, Any] = {
    "ends_with_question": _pred_ends_with_question,
    "contains_any": _pred_contains_any,
    "contains_all": _pred_contains_all,
    "lacks_terms": _pred_lacks_terms,
    "intent_in": _pred_intent_in,
    "regex_match": _pred_regex_match,
    "no_max_iterations_marker": _pred_no_max_iterations_marker,
    "tool_called": _pred_tool_called,
    "no_tool_called": _pred_no_tool_called,
    "tool_call_count": _pred_tool_call_count,
}


def supported_predicate_types() -> list[str]:
    """Public list — used by save_test_scenario for early validation
    and by the docs / system prompt."""
    return sorted(_PREDICATE_REGISTRY.keys())


def evaluate_predicates(
    output: dict[str, Any],
    predicates: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Evaluate every predicate in the list against the run output.

    Returns:
        ``{results: [{type, args, status, why}], pass_count, fail_count,
        overall: 'pass' | 'fail' | 'noop'}``

    A predicate with an unknown ``type`` is recorded as ``fail`` so the
    user sees the typo rather than a silent skip.
    """
    if not predicates:
        return {
            "results": [],
            "pass_count": 0,
            "fail_count": 0,
            "overall": "noop",
        }

    results: list[dict[str, Any]] = []
    passes = 0
    fails = 0
    for entry in predicates:
        if not isinstance(entry, dict):
            results.append({
                "type": "<malformed>", "args": entry,
                "status": "fail", "why": "entry must be an object {type, args?}",
            })
            fails += 1
            continue
        ptype = str(entry.get("type") or "")
        pargs = entry.get("args") or {}
        if not isinstance(pargs, dict):
            results.append({
                "type": ptype, "args": pargs,
                "status": "fail", "why": "args must be an object",
            })
            fails += 1
            continue
        fn = _PREDICATE_REGISTRY.get(ptype)
        if fn is None:
            results.append({
                "type": ptype, "args": pargs,
                "status": "fail",
                "why": f"unknown predicate type {ptype!r}; known: {supported_predicate_types()}",
            })
            fails += 1
            continue
        try:
            status, why = fn(output, pargs)
        except Exception as exc:
            status, why = "fail", f"predicate raised: {exc}"
        results.append({"type": ptype, "args": pargs, "status": status, "why": why})
        if status == "pass":
            passes += 1
        else:
            fails += 1

    overall = "pass" if fails == 0 and passes > 0 else "fail"
    return {
        "results": results,
        "pass_count": passes,
        "fail_count": fails,
        "overall": overall,
    }


def validate_predicates(predicates: Any) -> str | None:
    """Cheap shape validation for ``save_test_scenario``. Returns an
    error message string or ``None`` if the list is acceptable. Does
    NOT run the predicates — just shape-checks them.
    """
    if predicates is None:
        return None
    if not isinstance(predicates, list):
        return "expected_output_predicates must be a list of {type, args} objects"
    known = set(_PREDICATE_REGISTRY)
    for i, entry in enumerate(predicates):
        if not isinstance(entry, dict):
            return f"expected_output_predicates[{i}] must be an object"
        ptype = entry.get("type")
        if not isinstance(ptype, str) or not ptype:
            return f"expected_output_predicates[{i}].type must be a non-empty string"
        if ptype not in known:
            return (
                f"expected_output_predicates[{i}].type={ptype!r} is unknown; "
                f"valid types: {sorted(known)}"
            )
        pargs = entry.get("args")
        if pargs is not None and not isinstance(pargs, dict):
            return f"expected_output_predicates[{i}].args must be an object"
    return None
