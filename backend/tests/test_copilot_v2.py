"""COPILOT-V2 — unit tests for the new debugging power tools, lints,
predicate evaluator, diff layer, and tool-definition metadata.

Each helper is unit-tested in isolation. End-to-end runner-tool tests
that exercise execute_draft / replay_node_with_overrides / evaluate_run
with real LLM calls live in the integration tier (and aren't run in
this file — they need a live orchestrator + LLM credentials).

Vertex parity is exercised throughout — `TestEvaluateRunVertexParity`
and `TestSuggestIssueFilingProviderContext` mirror the Vertex tests in
``test_copilot_suggest_fix.py`` so we never accidentally regress
Vertex/Gemini support behind an Anthropic-only path.
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.copilot import diff_layer, lints, predicates, runner_tools
from app.copilot.tool_definitions import (
    COPILOT_TOOL_DEFINITIONS,
    to_anthropic_tools,
    to_google_tools,
)


TENANT = "tenant-v2"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _node(
    node_id: str,
    *,
    label: str = "LLM Agent",
    category: str = "agent",
    display_name: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "agenticNode",
        "data": {
            "label": label,
            "nodeCategory": category,
            "displayName": display_name or label,
            "config": dict(config or {}),
        },
    }


def _edge(source: str, target: str, *, edge_id: str | None = None) -> dict[str, Any]:
    return {
        "id": edge_id or f"e_{source}_{target}",
        "source": source,
        "target": target,
    }


# ---------------------------------------------------------------------------
# Predicate evaluator
# ---------------------------------------------------------------------------


def _output_with_reply(reply: str, **extra: Any) -> dict[str, Any]:
    out = {"orchestrator_user_reply": reply}
    out.update(extra)
    return out


class TestPredicates:
    def test_ends_with_question_pass(self):
        out = _output_with_reply("Which workflow are you watching?")
        result = predicates.evaluate_predicates(out, [{"type": "ends_with_question"}])
        assert result["overall"] == "pass"

    def test_ends_with_question_fail(self):
        out = _output_with_reply("This looks broken.")
        result = predicates.evaluate_predicates(out, [{"type": "ends_with_question"}])
        assert result["overall"] == "fail"

    def test_ends_with_question_strips_trailing_punctuation(self):
        # Trailing whitespace, parens, quotes shouldn't defeat the check.
        out = _output_with_reply('Please confirm? ')
        result = predicates.evaluate_predicates(out, [{"type": "ends_with_question"}])
        assert result["overall"] == "pass"

    def test_contains_any_pass(self):
        out = _output_with_reply("Your daily recon report is delayed.")
        result = predicates.evaluate_predicates(
            out, [{"type": "contains_any", "args": {"terms": ["recon", "report"]}}],
        )
        assert result["overall"] == "pass"

    def test_contains_any_fail(self):
        out = _output_with_reply("Hello there.")
        result = predicates.evaluate_predicates(
            out, [{"type": "contains_any", "args": {"terms": ["recon", "report"]}}],
        )
        assert result["overall"] == "fail"

    def test_lacks_terms_catches_leak(self):
        # The hostile-prompt-injection regression — the reply leaked
        # the words 'system prompt' even after refusing.
        out = _output_with_reply("I cannot share my system prompt.")
        result = predicates.evaluate_predicates(
            out, [{"type": "lacks_terms", "args": {"terms": ["system prompt"]}}],
        )
        assert result["overall"] == "fail"
        assert "system prompt" in result["results"][0]["why"]

    def test_intent_in_pass(self):
        out = {"node_router": {"intents": ["ops"]}}
        result = predicates.evaluate_predicates(
            out, [{"type": "intent_in", "args": {"allowed": ["ops", "small_talk"]}}],
        )
        assert result["overall"] == "pass"

    def test_intent_in_fail_when_intent_not_listed(self):
        out = {"node_router": {"intents": ["small_talk"]}}
        result = predicates.evaluate_predicates(
            out, [{"type": "intent_in", "args": {"allowed": ["ops"]}}],
        )
        assert result["overall"] == "fail"

    def test_no_max_iterations_marker(self):
        bad = _output_with_reply("Maximum iterations reached without final answer.")
        result = predicates.evaluate_predicates(bad, [{"type": "no_max_iterations_marker"}])
        assert result["overall"] == "fail"

        good = _output_with_reply("Restart took effect; agent is back online.")
        result = predicates.evaluate_predicates(good, [{"type": "no_max_iterations_marker"}])
        assert result["overall"] == "pass"

    def test_tool_called_walks_iterations(self):
        out = {
            "orchestrator_user_reply": "...",
            "node_worker": {
                "iterations": [
                    {"action": "tool_use", "tool_calls": [
                        {"name": "ae.workflow.search"},
                    ]},
                    {"action": "final_response"},
                ],
            },
        }
        result = predicates.evaluate_predicates(
            out, [{"type": "tool_called", "args": {"name_prefix": "ae.workflow"}}],
        )
        assert result["overall"] == "pass"

    def test_no_tool_called(self):
        out = {
            "orchestrator_user_reply": "...",
            "node_worker": {
                "iterations": [
                    {"action": "tool_use", "tool_calls": [
                        {"name": "ae.workflow.search"},
                    ]},
                ],
            },
        }
        result = predicates.evaluate_predicates(
            out, [{"type": "no_tool_called", "args": {"name_prefix": "ae.agent.restart"}}],
        )
        assert result["overall"] == "pass"  # no restart called

    def test_unknown_predicate_type_fails_visibly(self):
        result = predicates.evaluate_predicates(
            _output_with_reply("hi"), [{"type": "ends_with_xclaim"}],
        )
        assert result["overall"] == "fail"
        assert "unknown predicate type" in result["results"][0]["why"]

    def test_validate_predicates_catches_typos(self):
        err = predicates.validate_predicates([{"type": "ends_with_xclaim"}])
        assert err is not None
        assert "unknown" in err

    def test_validate_predicates_accepts_known(self):
        err = predicates.validate_predicates([
            {"type": "ends_with_question"},
            {"type": "contains_any", "args": {"terms": ["x"]}},
        ])
        assert err is None

    def test_validate_predicates_accepts_none(self):
        assert predicates.validate_predicates(None) is None


# ---------------------------------------------------------------------------
# Lints — prompt_cache_breakage
# ---------------------------------------------------------------------------


class TestPromptCacheLint:
    def test_short_prompt_no_lint(self):
        # Below the 800-char threshold — even with Jinja, no warn.
        graph = {
            "nodes": [_node("n1", config={"systemPrompt": "Hi {{ trigger.x }}"})],
            "edges": [],
        }
        out = lints.lint_prompt_cache_breakage(graph)
        assert out == []

    def test_long_prompt_jinja_at_top_fires(self):
        prompt = "User: {{ trigger.x }}\n" + ("rules and rules. " * 80)
        graph = {
            "nodes": [_node("n1", config={"systemPrompt": prompt})],
            "edges": [],
        }
        out = lints.lint_prompt_cache_breakage(graph)
        assert len(out) == 1
        assert out[0].code == "prompt_cache_breakage"
        assert out[0].severity == "warn"

    def test_long_prompt_jinja_only_in_tail_no_lint(self):
        # 1000 chars of static rules, then Jinja in the dynamic block.
        # This is the V8/V9/V10 pattern; lint should stay quiet.
        prompt = "STATIC rules. " * 80 + "\n=== CURRENT TURN ===\n{{ trigger.x }}"
        graph = {
            "nodes": [_node("n1", config={"systemPrompt": prompt})],
            "edges": [],
        }
        out = lints.lint_prompt_cache_breakage(graph)
        assert out == []

    def test_no_jinja_no_lint(self):
        prompt = "Plain rules. " * 80
        graph = {
            "nodes": [_node("n1", config={"systemPrompt": prompt})],
            "edges": [],
        }
        out = lints.lint_prompt_cache_breakage(graph)
        assert out == []

    def test_fires_for_react_agent_label(self):
        prompt = "{{ x }} " + ("filler. " * 100)
        graph = {
            "nodes": [_node("n1", label="ReAct Agent", config={"systemPrompt": prompt})],
            "edges": [],
        }
        out = lints.lint_prompt_cache_breakage(graph)
        assert len(out) == 1


# ---------------------------------------------------------------------------
# Lints — react_role_inconsistency
# ---------------------------------------------------------------------------


class TestReactRoleLint:
    def test_verifier_without_categories_warns(self):
        graph = {
            "nodes": [_node(
                "n1", label="ReAct Agent",
                display_name="Verifier (read-only)",
                config={"maxIterations": 2},
            )],
            "edges": [],
        }
        out = lints.lint_react_role_inconsistency(graph)
        codes = [l.code for l in out]
        assert "react_role_no_category_restriction" in codes

    def test_verifier_with_categories_no_warn(self):
        graph = {
            "nodes": [_node(
                "n1", label="ReAct Agent",
                display_name="Verifier (read-only)",
                config={
                    "maxIterations": 2,
                    "allowedToolCategories": ["read", "case"],
                },
            )],
            "edges": [],
        }
        out = lints.lint_react_role_inconsistency(graph)
        codes = [l.code for l in out]
        assert "react_role_no_category_restriction" not in codes

    def test_worker_with_low_iterations_warns(self):
        graph = {
            "nodes": [_node(
                "n1", label="ReAct Agent",
                display_name="Ops Worker (ReAct)",
                config={"maxIterations": 1},
            )],
            "edges": [],
        }
        out = lints.lint_react_role_inconsistency(graph)
        codes = [l.code for l in out]
        assert "react_worker_iterations_too_low" in codes

    def test_worker_with_normal_iterations_no_warn(self):
        graph = {
            "nodes": [_node(
                "n1", label="ReAct Agent",
                display_name="Worker",
                config={"maxIterations": 5},
            )],
            "edges": [],
        }
        out = lints.lint_react_role_inconsistency(graph)
        codes = [l.code for l in out]
        assert "react_worker_iterations_too_low" not in codes

    def test_neutral_display_name_no_warn(self):
        # A display name that doesn't match either role pattern shouldn't
        # produce a lint at all.
        graph = {
            "nodes": [_node(
                "n1", label="ReAct Agent",
                display_name="Some Custom Name",
                config={"maxIterations": 5},
            )],
            "edges": [],
        }
        out = lints.lint_react_role_inconsistency(graph)
        assert out == []

    def test_non_react_label_no_warn(self):
        # An LLM Agent node with a verifier-ish display name shouldn't
        # trigger this lint — only ReAct Agent.
        graph = {
            "nodes": [_node(
                "n1", label="LLM Agent",
                display_name="Verifier (read-only)",
                config={},
            )],
            "edges": [],
        }
        out = lints.lint_react_role_inconsistency(graph)
        assert out == []


# ---------------------------------------------------------------------------
# Diff layer
# ---------------------------------------------------------------------------


class TestDiffLayer:
    def test_identical_graphs_no_changes(self):
        g = {"nodes": [_node("n1")], "edges": []}
        out = diff_layer.diff_graphs(g, g)
        assert out["summary"] == "no changes"
        assert out["node_changes"]["added"] == []
        assert out["node_changes"]["removed"] == []
        assert out["node_changes"]["modified"] == []

    def test_added_and_removed_nodes(self):
        left = {"nodes": [_node("n1"), _node("n2")], "edges": []}
        right = {"nodes": [_node("n1"), _node("n3")], "edges": []}
        out = diff_layer.diff_graphs(left, right)
        added_ids = [n["id"] for n in out["node_changes"]["added"]]
        removed_ids = [n["id"] for n in out["node_changes"]["removed"]]
        assert added_ids == ["n2"]
        assert removed_ids == ["n3"]

    def test_modified_node_shows_field_changes(self):
        left = {
            "nodes": [_node("n1", config={"systemPrompt": "Hello world"})],
            "edges": [],
        }
        right = {
            "nodes": [_node("n1", config={"systemPrompt": "Goodbye world"})],
            "edges": [],
        }
        out = diff_layer.diff_graphs(left, right)
        assert len(out["node_changes"]["modified"]) == 1
        mod = out["node_changes"]["modified"][0]
        keys = [c["key"] for c in mod["changed_fields"]]
        assert "config.systemPrompt" in keys

    def test_long_string_value_is_truncated(self):
        long_a = "A" * 5000
        long_b = "B" * 5000
        left = {"nodes": [_node("n1", config={"prompt": long_a})], "edges": []}
        right = {"nodes": [_node("n1", config={"prompt": long_b})], "edges": []}
        out = diff_layer.diff_graphs(left, right)
        change = out["node_changes"]["modified"][0]["changed_fields"][0]
        # Both before/after should be much shorter than the originals.
        assert len(change["before_preview"]) < 500
        assert len(change["after_preview"]) < 500
        assert "elided" in change["before_preview"]

    def test_position_change_alone_is_not_a_change(self):
        left = {"nodes": [{
            "id": "n1",
            "position": {"x": 0, "y": 0},
            "data": {"label": "LLM Agent", "config": {}},
        }], "edges": []}
        right = {"nodes": [{
            "id": "n1",
            "position": {"x": 200, "y": 200},
            "data": {"label": "LLM Agent", "config": {}},
        }], "edges": []}
        out = diff_layer.diff_graphs(left, right)
        assert out["summary"] == "no changes"

    def test_edges_added_and_removed(self):
        left = {
            "nodes": [_node("n1"), _node("n2")],
            "edges": [_edge("n1", "n2")],
        }
        right = {
            "nodes": [_node("n1"), _node("n2")],
            "edges": [],
        }
        out = diff_layer.diff_graphs(left, right)
        assert len(out["edge_changes"]["added"]) == 1
        assert out["edge_changes"]["added"][0]["source"] == "n1"


# ---------------------------------------------------------------------------
# Tool-definition metadata
# ---------------------------------------------------------------------------


class TestToolDefinitions:
    def test_every_tool_has_side_effects(self):
        missing = [t["name"] for t in COPILOT_TOOL_DEFINITIONS if "side_effects" not in t]
        assert missing == [], f"tools missing side_effects: {missing}"

    def test_side_effects_values_in_vocabulary(self):
        valid = {
            "read_only", "mutates_draft", "writes_db",
            "consumes_tokens", "spawns_run", "external_call",
        }
        for tool in COPILOT_TOOL_DEFINITIONS:
            for se in tool["side_effects"]:
                assert se in valid, f"{tool['name']!r} has invalid side_effect: {se}"

    def test_anthropic_converter_strips_side_effects(self):
        anth = to_anthropic_tools()
        leaks = [t["name"] for t in anth if "side_effects" in t]
        assert leaks == [], f"side_effects leaked into Anthropic tools: {leaks}"

    def test_v2_tools_present(self):
        names = {t["name"] for t in COPILOT_TOOL_DEFINITIONS}
        for new_tool in (
            "diff_drafts", "replay_node_with_overrides",
            "evaluate_run", "suggest_issue_filing",
        ):
            assert new_tool in names, f"V2 tool {new_tool} missing"


# ---------------------------------------------------------------------------
# Suggest-issue-filing redaction
# ---------------------------------------------------------------------------


class TestIssueFilingRedaction:
    def test_redact_text_strips_anthropic_key(self):
        from app.copilot.runner_tools import _redact_text

        text = "header sk-ant-api03-abcdef-xxxxx- body"
        out, counts = _redact_text(text)
        assert "sk-ant-api" not in out
        assert "<redacted:anthropic_api_key>" in out
        assert counts["anthropic_api_key"] == 1

    def test_redact_text_strips_jwt(self):
        from app.copilot.runner_tools import _redact_text

        text = "token eyJhbGciOi.eyJzdWIiOi.SflKxwRJSMeKKF0 trailer"
        out, counts = _redact_text(text)
        assert "<redacted:jwt>" in out
        assert counts["jwt"] == 1

    def test_redact_text_strips_email(self):
        from app.copilot.runner_tools import _redact_text

        text = "contact someone@example.com please"
        out, counts = _redact_text(text)
        assert "someone@example.com" not in out
        assert counts["email"] == 1

    def test_redact_text_no_secrets_no_redactions(self):
        from app.copilot.runner_tools import _redact_text

        text = "Plain prose with nothing sensitive."
        out, counts = _redact_text(text)
        assert out == text
        assert counts == {}


# ---------------------------------------------------------------------------
# evaluate_run JSON parser
# ---------------------------------------------------------------------------


class TestEvaluateRunParser:
    def test_strips_code_fences(self):
        from app.copilot.runner_tools import _parse_evaluate_run_response

        raw = (
            "```json\n"
            '{"verdicts": [{"criterion": "x", "status": "pass", "why": "y"}], '
            '"overall": "pass", "summary": "ok"}'
            "\n```"
        )
        parsed = _parse_evaluate_run_response(raw)
        assert parsed is not None
        assert parsed["overall"] == "pass"

    def test_returns_none_on_garbage(self):
        from app.copilot.runner_tools import _parse_evaluate_run_response

        assert _parse_evaluate_run_response("not json") is None
        assert _parse_evaluate_run_response("") is None

    def test_accepts_clean_json(self):
        from app.copilot.runner_tools import _parse_evaluate_run_response

        raw = '{"verdicts": [], "overall": "noop", "summary": "no rubric"}'
        parsed = _parse_evaluate_run_response(raw)
        assert parsed["overall"] == "noop"


# ---------------------------------------------------------------------------
# System prompt split — caching shape
# ---------------------------------------------------------------------------


class TestSystemPromptSplit:
    def test_split_static_is_byte_stable(self):
        from app.copilot.prompts import build_system_prompt_split

        a_static, _ = build_system_prompt_split(draft_snapshot={"nodes": [], "edges": []})
        b_static, _ = build_system_prompt_split(
            draft_snapshot={"nodes": [_node("n1")], "edges": []},
        )
        # Static must be identical regardless of draft contents.
        assert a_static == b_static

    def test_dynamic_changes_with_draft(self):
        from app.copilot.prompts import build_system_prompt_split

        _, a_dyn = build_system_prompt_split(draft_snapshot={"nodes": [], "edges": []})
        _, b_dyn = build_system_prompt_split(
            draft_snapshot={"nodes": [_node("n1")], "edges": []},
        )
        assert a_dyn != b_dyn

    def test_back_compat_concatenation(self):
        from app.copilot.prompts import build_system_prompt, build_system_prompt_split

        snap = {"nodes": [], "edges": []}
        full = build_system_prompt(draft_snapshot=snap)
        static, dynamic = build_system_prompt_split(draft_snapshot=snap)
        # build_system_prompt is just static + "\n\n" + dynamic.
        assert full == static + "\n\n" + dynamic


# ---------------------------------------------------------------------------
# Service-account / Vertex-shaped secret redaction
# ---------------------------------------------------------------------------


class TestVertexSecretRedaction:
    """Service-account JSON is the most common secret-leak shape in a
    Vertex tenant's logs / configs. These tests pin the redaction
    behaviour so a service-account key never lands verbatim in a
    GitHub issue body."""

    def test_redact_pem_private_key_block(self):
        from app.copilot.runner_tools import _redact_text

        text = (
            "before\n"
            "-----BEGIN PRIVATE KEY-----\n"
            "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQ\n"
            "extra-line-that-should-also-be-redacted\n"
            "-----END PRIVATE KEY-----\n"
            "after"
        )
        out, counts = _redact_text(text)
        assert "-----BEGIN PRIVATE KEY-----" not in out
        assert "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQ" not in out
        assert "extra-line-that-should-also-be-redacted" not in out
        assert "-----END PRIVATE KEY-----" not in out
        assert "<redacted:private_key>" in out
        assert counts["private_key"] == 1
        # before / after literals preserved.
        assert out.startswith("before\n")
        assert out.endswith("after")

    def test_redact_rsa_private_key_block(self):
        from app.copilot.runner_tools import _redact_text

        text = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEA\n"
            "-----END RSA PRIVATE KEY-----"
        )
        out, counts = _redact_text(text)
        assert counts.get("private_key") == 1
        assert "RSA PRIVATE KEY" not in out

    def test_redact_gcp_service_account_marker(self):
        from app.copilot.runner_tools import _redact_text

        # A sanitised SA JSON snippet — even with the private_key already
        # stripped, the surrounding JSON often carries project_id /
        # client_email. Surfacing the marker tells the user "this looks
        # like a creds JSON — check before submitting."
        text = '{"type": "service_account", "project_id": "my-proj-12345"}'
        out, counts = _redact_text(text)
        assert counts.get("gcp_service_account_marker") == 1
        assert '<redacted:gcp_service_account_marker>' in out

    def test_redact_gcs_signed_url(self):
        from app.copilot.runner_tools import _redact_text

        text = (
            "logs at https://storage.googleapis.com/my-bucket/log.json"
            "?X-Goog-Algorithm=GOOG4-RSA-SHA256&X-Goog-Signature=abcdef "
            "trailer"
        )
        out, counts = _redact_text(text)
        assert counts.get("gcs_signed_url") == 1
        assert "X-Goog-Signature=abcdef" not in out

    def test_redact_oauth_refresh_token(self):
        from app.copilot.runner_tools import _redact_text

        text = "refresh 1//abcdef" + "X" * 60 + " end"
        out, counts = _redact_text(text)
        assert counts.get("google_oauth_refresh_token") == 1


# ---------------------------------------------------------------------------
# evaluate_run — Vertex parity
# ---------------------------------------------------------------------------


class _FakeDraft:
    """Lightweight fake suitable for runner-tool calls that don't
    write the draft."""

    def __init__(self, *, draft_id: _uuid.UUID | None = None,
                 graph_json: dict | None = None, version: int = 1) -> None:
        self.id = draft_id or _uuid.uuid4()
        self.graph_json = graph_json or {"nodes": [], "edges": []}
        self.version = version
        self.base_workflow_id = None


def _mock_db_for_evaluate_run(
    *,
    instance_status: str = "completed",
    is_ephemeral: bool = True,
    user_reply: str = "Hello — your daily recon is delayed.",
    session_provider: str | None = "vertex",
    session_model: str | None = "gemini-3.1-pro-preview-customtools",
):
    """Stage the DB so evaluate_run finds an ephemeral instance + WD,
    plus a CopilotSession (for `_resolve_suggest_fix_provider`)."""
    from app.models.workflow import WorkflowDefinition, WorkflowInstance
    from app.models.copilot import CopilotSession

    db = MagicMock()

    fake_instance = MagicMock(spec=WorkflowInstance)
    fake_instance.id = _uuid.uuid4()
    fake_instance.workflow_def_id = _uuid.uuid4()
    fake_instance.status = instance_status
    fake_instance.context_json = {"orchestrator_user_reply": user_reply}

    fake_wd = MagicMock(spec=WorkflowDefinition)
    fake_wd.is_ephemeral = is_ephemeral
    fake_wd.id = fake_instance.workflow_def_id

    fake_session = None
    if session_provider:
        fake_session = MagicMock(spec=CopilotSession)
        fake_session.provider = session_provider
        fake_session.model = session_model

    def _query_side_effect(model_cls):
        name = getattr(model_cls, "__name__", "")
        result = MagicMock()
        if name == "WorkflowInstance":
            result.filter_by.return_value.first.return_value = fake_instance
        elif name == "WorkflowDefinition":
            result.filter_by.return_value.first.return_value = fake_wd
        elif name == "CopilotSession":
            chain = MagicMock()
            chain.order_by.return_value.first.return_value = fake_session
            result.filter_by.return_value = chain
        else:
            result.filter_by.return_value.first.return_value = None
        return result

    db.query.side_effect = _query_side_effect
    return db, fake_instance


class TestEvaluateRunVertexParity:
    """Mirrors the Vertex parity tests in test_copilot_suggest_fix.py
    so the LLM-as-judge subcall stays on the same engine the user is
    chatting on. Critical for billing attribution + reproducibility."""

    def test_vertex_session_routes_to_google_call(self):
        """A Vertex session on the draft → evaluate_run dispatches via
        _call_evaluate_run_google with backend='vertex', NOT through
        the Anthropic path."""
        draft = _FakeDraft()
        db, _instance = _mock_db_for_evaluate_run(session_provider="vertex")

        with patch.object(
            runner_tools, "_call_evaluate_run_google",
            return_value={
                "raw_text": '{"verdicts": [{"criterion": "x", "status": "pass", "why": "ok"}], '
                            '"overall": "pass", "summary": "fine"}',
                "usage": {"input_tokens": 50, "output_tokens": 10},
            },
        ) as fake_google, patch.object(
            runner_tools, "_call_evaluate_run_anthropic",
            side_effect=AssertionError("anthropic must not be called for vertex sessions"),
        ):
            result = runner_tools.evaluate_run(
                db, tenant_id=TENANT, draft=draft,
                args={"instance_id": str(_instance.id), "rubric": "is the reply useful"},
            )

        # Anthropic was NOT called; Google adapter WAS, with backend=vertex.
        fake_google.assert_called_once()
        kwargs = fake_google.call_args.kwargs
        assert kwargs["backend"] == "vertex"
        assert kwargs["model"] == "gemini-3.1-pro-preview-customtools"
        assert kwargs["tenant_id"] == TENANT
        assert result["provider"] == "vertex"
        assert result["model"] == "gemini-3.1-pro-preview-customtools"
        assert result["overall"] == "pass"

    def test_google_session_routes_to_google_call_with_genai_backend(self):
        """Google AI Studio session → backend='genai', not 'vertex'."""
        draft = _FakeDraft()
        db, _instance = _mock_db_for_evaluate_run(
            session_provider="google",
            session_model="gemini-3.1-pro-preview-customtools",
        )

        with patch.object(
            runner_tools, "_call_evaluate_run_google",
            return_value={
                "raw_text": '{"verdicts": [], "overall": "noop", "summary": ""}',
                "usage": {},
            },
        ) as fake_google:
            runner_tools.evaluate_run(
                db, tenant_id=TENANT, draft=draft,
                args={"instance_id": str(_instance.id), "rubric": "test"},
            )

        kwargs = fake_google.call_args.kwargs
        assert kwargs["backend"] == "genai"

    def test_anthropic_session_routes_to_anthropic_call(self):
        draft = _FakeDraft()
        db, _instance = _mock_db_for_evaluate_run(
            session_provider="anthropic", session_model="claude-sonnet-4-6",
        )

        with patch.object(
            runner_tools, "_call_evaluate_run_anthropic",
            return_value={
                "raw_text": '{"verdicts": [], "overall": "noop", "summary": ""}',
                "usage": {},
            },
        ) as fake_anth, patch.object(
            runner_tools, "_call_evaluate_run_google",
            side_effect=AssertionError("google must not be called for anthropic sessions"),
        ):
            result = runner_tools.evaluate_run(
                db, tenant_id=TENANT, draft=draft,
                args={"instance_id": str(_instance.id), "rubric": "test"},
            )

        fake_anth.assert_called_once()
        assert result["provider"] == "anthropic"
        assert result["model"] == "claude-sonnet-4-6"

    def test_no_session_falls_back_to_settings_default(self):
        """No active session → ``settings.copilot_default_provider``.
        The default model resolves through the same DEFAULT_MODEL_BY_PROVIDER
        registry as suggest_fix, so flipping the env to vertex makes
        evaluate_run Vertex-by-default for orphan calls."""
        from app.config import settings

        draft = _FakeDraft()
        db, _instance = _mock_db_for_evaluate_run(session_provider=None)

        with patch.object(settings, "copilot_default_provider", "vertex"), \
             patch.object(
                runner_tools, "_call_evaluate_run_google",
                return_value={
                    "raw_text": '{"verdicts": [], "overall": "noop", "summary": ""}',
                    "usage": {},
                },
             ) as fake_google:
            runner_tools.evaluate_run(
                db, tenant_id=TENANT, draft=draft,
                args={"instance_id": str(_instance.id), "rubric": "test"},
            )

        kwargs = fake_google.call_args.kwargs
        assert kwargs["backend"] == "vertex"
        assert kwargs["model"].startswith("gemini-3")

    def test_vertex_call_dispatches_through_shared_genai_client(self):
        """Functional: _call_evaluate_run_google with backend='vertex'
        must hit the shared _google_client(backend='vertex',
        tenant_id=...) path so VERTEX-02 per-tenant project resolution
        stays in force — same parity contract as suggest_fix."""
        from google.genai import types  # noqa: F401 — import guard

        with patch(
            "app.engine.llm_providers._google_client",
        ) as fake_client_factory:
            client = MagicMock()
            part = MagicMock()
            part.text = (
                '{"verdicts": [{"criterion": "x", "status": "pass", "why": "y"}], '
                '"overall": "pass", "summary": "ok"}'
            )
            candidate = MagicMock()
            candidate.content.parts = [part]
            resp = MagicMock()
            resp.candidates = [candidate]
            resp.usage_metadata.prompt_token_count = 100
            resp.usage_metadata.candidates_token_count = 30
            client.models.generate_content.return_value = resp
            fake_client_factory.return_value = client

            result = runner_tools._call_evaluate_run_google(
                tenant_id=TENANT,
                model="gemini-3.1-pro-preview-customtools",
                user_payload='{"user_reply": "hi", "rubric": "ok"}',
                backend="vertex",
            )

        fake_client_factory.assert_called_once_with("vertex", tenant_id=TENANT)
        _, call_kwargs = client.models.generate_content.call_args
        assert call_kwargs["model"] == "gemini-3.1-pro-preview-customtools"
        assert "verdicts" in result["raw_text"]
        assert result["usage"]["input_tokens"] == 100

    def test_invalid_instance_id_short_circuits(self):
        draft = _FakeDraft()
        db, _ = _mock_db_for_evaluate_run()
        out = runner_tools.evaluate_run(
            db, tenant_id=TENANT, draft=draft,
            args={"instance_id": "not-a-uuid", "rubric": "ok"},
        )
        assert "invalid instance_id" in out["error"]

    def test_non_ephemeral_instance_refused(self):
        """Production instance ids must NOT be readable. Same safety
        boundary as get_execution_logs — if the WD isn't ephemeral,
        evaluate_run refuses the call so the LLM judge can't be used
        to leak production conversation content."""
        draft = _FakeDraft()
        db, instance = _mock_db_for_evaluate_run(is_ephemeral=False)
        out = runner_tools.evaluate_run(
            db, tenant_id=TENANT, draft=draft,
            args={"instance_id": str(instance.id), "rubric": "ok"},
        )
        assert "ephemeral" in out["error"]

    def test_empty_rubric_rejected(self):
        draft = _FakeDraft()
        db, instance = _mock_db_for_evaluate_run()
        out = runner_tools.evaluate_run(
            db, tenant_id=TENANT, draft=draft,
            args={"instance_id": str(instance.id), "rubric": ""},
        )
        assert "rubric" in out["error"]

    def test_judge_returns_garbage_surfaces_clean_error(self):
        """If the judge returns non-JSON, the parser falls through and
        evaluate_run returns a clean error rather than raising."""
        draft = _FakeDraft()
        db, instance = _mock_db_for_evaluate_run(session_provider="vertex")

        with patch.object(
            runner_tools, "_call_evaluate_run_google",
            return_value={"raw_text": "not json at all", "usage": {}},
        ):
            out = runner_tools.evaluate_run(
                db, tenant_id=TENANT, draft=draft,
                args={"instance_id": str(instance.id), "rubric": "ok"},
            )
        assert "non-JSON" in out["error"]
        assert "raw_text" in out


# ---------------------------------------------------------------------------
# suggest_issue_filing — provider/model context
# ---------------------------------------------------------------------------


class TestSuggestIssueFilingProviderContext:
    """Issue body must surface the active session's provider + model so
    a maintainer triaging the issue knows whether the user was on
    Anthropic vs Vertex — different stacks, different bugs."""

    def _mock_db_with_session(self, *, provider: str | None, model: str | None):
        from app.models.copilot import CopilotSession, CopilotTurn

        db = MagicMock()
        fake_session = None
        if provider:
            fake_session = MagicMock(spec=CopilotSession)
            fake_session.provider = provider
            fake_session.model = model
            fake_session.id = _uuid.uuid4()
            fake_session.created_at = None

        def _query_side_effect(model_cls):
            name = getattr(model_cls, "__name__", "")
            result = MagicMock()
            if name == "CopilotSession":
                chain = MagicMock()
                chain.order_by.return_value.first.return_value = fake_session
                result.filter_by.return_value = chain
            elif name == "CopilotTurn":
                # No prior tool turns
                chain = MagicMock()
                chain.order_by.return_value.limit.return_value.all.return_value = []
                result.filter_by.return_value = chain
            return result

        db.query.side_effect = _query_side_effect
        return db

    def test_vertex_session_surfaced_in_body(self):
        from app.config import settings

        draft = _FakeDraft()
        db = self._mock_db_with_session(
            provider="vertex", model="gemini-3.1-pro-preview-customtools",
        )
        with patch.object(settings, "copilot_issue_link_enabled", True), \
             patch.object(settings, "copilot_issue_repo", "owner/repo"):
            result = runner_tools.suggest_issue_filing(
                db, tenant_id=TENANT, draft=draft,
                args={"category": "bug", "summary": "X did Y"},
            )

        assert result["enabled"] is True
        assert "provider=`vertex`" in result["body_preview"]
        assert "gemini-3.1-pro-preview-customtools" in result["body_preview"]

    def test_anthropic_session_surfaced_in_body(self):
        from app.config import settings

        draft = _FakeDraft()
        db = self._mock_db_with_session(
            provider="anthropic", model="claude-sonnet-4-6",
        )
        with patch.object(settings, "copilot_issue_link_enabled", True), \
             patch.object(settings, "copilot_issue_repo", "owner/repo"):
            result = runner_tools.suggest_issue_filing(
                db, tenant_id=TENANT, draft=draft,
                args={"category": "feature", "summary": "want X"},
            )

        assert "provider=`anthropic`" in result["body_preview"]
        assert "claude-sonnet-4-6" in result["body_preview"]

    def test_no_session_falls_back_to_unknown(self):
        from app.config import settings

        draft = _FakeDraft()
        db = self._mock_db_with_session(provider=None, model=None)
        with patch.object(settings, "copilot_issue_link_enabled", True), \
             patch.object(settings, "copilot_issue_repo", "owner/repo"):
            result = runner_tools.suggest_issue_filing(
                db, tenant_id=TENANT, draft=draft,
                args={"category": "bug", "summary": "X"},
            )

        assert "provider=`unknown`" in result["body_preview"]

    def test_disabled_when_settings_off(self):
        """Default-disabled: tenant deployment hasn't enabled issue filing."""
        from app.config import settings

        draft = _FakeDraft()
        db = self._mock_db_with_session(provider="vertex", model="gemini-3")
        with patch.object(settings, "copilot_issue_link_enabled", False):
            result = runner_tools.suggest_issue_filing(
                db, tenant_id=TENANT, draft=draft,
                args={"category": "bug", "summary": "X"},
            )

        assert result["enabled"] is False
        assert "link" not in result
        assert "not configured" in result["reason"]
