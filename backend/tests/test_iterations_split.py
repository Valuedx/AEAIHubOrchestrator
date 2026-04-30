"""CTX-MGMT.G — unit tests for the ReAct iterations summary/full split.

The summary helper drops LLM reasoning content, tool arg values, and
tool result payloads — keeping only what predicates need (tool
names) for safe downstream exposure. The verbose form lands under
``iterations_full`` only when the node config opts in via
``exposeFullIterations: True``, scrubbed of secret-shaped keys via
the engine's ``scrub_secrets``.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.engine.react_loop import (
    _finalize_iterations_payload,
    _summarize_iterations,
)


# ---------------------------------------------------------------------------
# _summarize_iterations
# ---------------------------------------------------------------------------


class TestSummarize:
    def test_empty_list(self):
        assert _summarize_iterations([]) == []

    def test_tool_use_keeps_only_names(self):
        iters = [{
            "iteration": 1,
            "action": "tool_use",
            "tool_calls": [
                {"name": "ae.workflow.search", "arguments": {"q": "sensitive"}},
            ],
            "tool_results": [{"large_payload": "x" * 10000}],
            "content": "I should search for...",  # private LLM reasoning
        }]
        out = _summarize_iterations(iters)
        assert len(out) == 1
        entry = out[0]
        assert entry["action"] == "tool_use"
        assert entry["tool_calls"] == [{"name": "ae.workflow.search"}]
        # Reasoning content + tool args + tool results all dropped.
        assert "content" not in entry
        assert "tool_results" not in entry
        assert "arguments" not in entry["tool_calls"][0]

    def test_final_response_keeps_length_only(self):
        iters = [{
            "iteration": 5,
            "action": "final_response",
            "content": "The agent's full reasoning + final reply." * 10,
        }]
        out = _summarize_iterations(iters)
        assert out[0]["content_length"] == 410
        assert "content" not in out[0]

    def test_llm_error_truncates(self):
        long_err = "x" * 1000
        iters = [{
            "iteration": 2,
            "action": "llm_error",
            "error": long_err,
        }]
        out = _summarize_iterations(iters)
        assert len(out[0]["error"]) <= 200

    def test_timeout_action(self):
        iters = [{"iteration": 8, "action": "timeout"}]
        out = _summarize_iterations(iters)
        assert out[0] == {"iteration": 8, "action": "timeout"}

    def test_max_iterations_exceeded(self):
        iters = [{"iteration": 5, "action": "max_iterations_exceeded"}]
        out = _summarize_iterations(iters)
        assert out[0]["action"] == "max_iterations_exceeded"

    def test_approved_tool_executed_keeps_tool_name(self):
        # HITL-04 re-fire — audit trail needs the tool name.
        iters = [{
            "iteration": 0,
            "action": "approved_tool_executed",
            "tool_calls": [{"name": "ae.agent.restart_service"}],
            "tool_results": [{"agent_id": "abc", "huge": "x" * 10000}],
        }]
        out = _summarize_iterations(iters)
        assert out[0]["tool_name"] == "ae.agent.restart_service"
        # tool_results dropped from summary.
        assert "tool_results" not in out[0]

    def test_skips_non_dict_entries(self):
        # Defensive — malformed entries shouldn't crash.
        iters = [None, "string", {"iteration": 1, "action": "tool_use", "tool_calls": []}]
        out = _summarize_iterations(iters)
        assert len(out) == 1
        assert out[0]["action"] == "tool_use"

    def test_unknown_action_passes_through(self):
        iters = [{"iteration": 1, "action": "novel_action", "extra_field": "preserved?"}]
        out = _summarize_iterations(iters)
        # We keep iteration + action; everything else is dropped by default.
        assert out[0] == {"iteration": 1, "action": "novel_action"}


# ---------------------------------------------------------------------------
# _finalize_iterations_payload
# ---------------------------------------------------------------------------


class TestFinalizePayload:
    def _sample_iters(self) -> list[dict[str, Any]]:
        return [
            {
                "iteration": 1,
                "action": "tool_use",
                "tool_calls": [
                    {"name": "ae.workflow.search", "arguments": {"q": "x"}},
                ],
                "content": "private reasoning",
            },
            {
                "iteration": 2,
                "action": "final_response",
                "content": "Done.",
            },
        ]

    def test_default_only_emits_summary(self):
        payload = _finalize_iterations_payload(self._sample_iters(), expose_full=False)
        assert "iterations" in payload
        assert "iterations_full" not in payload
        # Summary entries contain just iteration + action + name (no args).
        first = payload["iterations"][0]
        assert "arguments" not in first["tool_calls"][0]
        assert "content" not in first

    def test_expose_full_emits_both(self):
        payload = _finalize_iterations_payload(self._sample_iters(), expose_full=True)
        assert "iterations" in payload
        assert "iterations_full" in payload
        # Full keeps args + content.
        full = payload["iterations_full"]
        assert full[0]["tool_calls"][0]["arguments"] == {"q": "x"}
        assert full[0]["content"] == "private reasoning"

    def test_full_is_scrubbed_for_sensitive_keys(self):
        iters = [{
            "iteration": 1,
            "action": "tool_use",
            "tool_calls": [
                {"name": "x", "arguments": {"api_key": "sk-ant-abc", "q": "ok"}},
            ],
        }]
        payload = _finalize_iterations_payload(iters, expose_full=True)
        full = payload["iterations_full"]
        # api_key key is sensitive → scrub_secrets redacts the value.
        assert full[0]["tool_calls"][0]["arguments"]["api_key"] == "[REDACTED]"
        # Non-sensitive key passes through.
        assert full[0]["tool_calls"][0]["arguments"]["q"] == "ok"

    def test_summary_unaffected_by_expose_flag(self):
        # Same input → same summary regardless of expose_full.
        s1 = _finalize_iterations_payload(self._sample_iters(), expose_full=False)["iterations"]
        s2 = _finalize_iterations_payload(self._sample_iters(), expose_full=True)["iterations"]
        assert s1 == s2


# ---------------------------------------------------------------------------
# Predicate compatibility — _all_tool_calls reads new + legacy shapes
# ---------------------------------------------------------------------------


class TestPredicateCompat:
    def test_summary_iterations_let_predicates_find_tool(self):
        # The summary's tool_calls entries have just `name` — that's
        # enough for the `tool_called` predicate to match.
        from app.copilot import predicates

        out = {
            "node_worker": {
                "iterations": [
                    {"action": "tool_use", "tool_calls": [{"name": "ae.workflow.search"}]},
                ],
            },
        }
        result = predicates.evaluate_predicates(
            out, [{"type": "tool_called", "args": {"name_prefix": "ae.workflow"}}],
        )
        assert result["overall"] == "pass"

    def test_full_iterations_preferred_when_present(self):
        # When iterations_full is present, predicates read from it
        # (forward-compatible with predicates that may inspect args).
        from app.copilot import predicates

        out = {
            "node_worker": {
                "iterations": [
                    # Summary says NO ae.agent calls...
                    {"action": "tool_use", "tool_calls": [{"name": "ae.workflow.search"}]},
                ],
                "iterations_full": [
                    # ...but the full form (only present when opted-in) shows otherwise.
                    {
                        "action": "tool_use",
                        "tool_calls": [
                            {"name": "ae.agent.restart_service", "arguments": {"id": "x"}},
                        ],
                    },
                ],
            },
        }
        # Predicate reads iterations_full first.
        result = predicates.evaluate_predicates(
            out, [{"type": "tool_called", "args": {"name_prefix": "ae.agent"}}],
        )
        assert result["overall"] == "pass"

    def test_legacy_full_iterations_via_summary_fallback(self):
        # A pre-CTX-MGMT.G context that still has the verbose
        # iterations under `iterations` (no _full key) — predicates
        # still work via the legacy fallback path.
        from app.copilot import predicates

        out = {
            "node_worker": {
                "iterations": [
                    {
                        "action": "tool_use",
                        "tool_calls": [
                            {"name": "ae.workflow.search", "arguments": {"q": "legacy"}},
                        ],
                    },
                ],
            },
        }
        result = predicates.evaluate_predicates(
            out, [{"type": "tool_called", "args": {"name_prefix": "ae.workflow"}}],
        )
        assert result["overall"] == "pass"
