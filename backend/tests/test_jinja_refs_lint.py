"""CTX-MGMT.B — unit tests for jinja_refs extraction + the
``lint_jinja_dangling_reference`` SMART-04 lint.

Pure helper tests for the regex; integration tests for the lint
against synthetic graphs.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.copilot.jinja_refs import (
    collect_refs_from_config,
    extract_node_refs,
    walk_node_strings,
)
from app.copilot.lints import lint_jinja_dangling_reference, run_lints


# ---------------------------------------------------------------------------
# extract_node_refs — regex coverage
# ---------------------------------------------------------------------------


class TestExtractNodeRefs:
    def test_jinja_simple(self):
        assert extract_node_refs("{{ node_4r.json.id }}") == [("node_4r", "json.id")]

    def test_safe_eval_expression(self):
        assert extract_node_refs(
            "if node_3.branch == 'true' else node_4r.json"
        ) == [("node_3", "branch"), ("node_4r", "json")]

    def test_bare_node_ref_no_attrs(self):
        assert extract_node_refs("{{ node_X }}") == [("node_X", "")]

    def test_multiple_refs_in_one_string(self):
        text = "Hello {{ trigger.x }} from {{ node_2.messages }} on {{ node_4r.json.id }}"
        refs = extract_node_refs(text)
        assert refs == [("node_2", "messages"), ("node_4r", "json.id")]

    def test_word_boundary_does_not_match_inside_other_identifier(self):
        # `not_a_node_X_thing` should NOT match — `node_X` is mid-word.
        assert extract_node_refs("not_a_node_X_thing") == []

    def test_word_boundary_does_match_after_punctuation(self):
        assert extract_node_refs("(node_4r.id)") == [("node_4r", "id")]
        assert extract_node_refs(",node_4r") == [("node_4r", "")]

    def test_returns_empty_for_non_string(self):
        assert extract_node_refs(123) == []  # type: ignore[arg-type]
        assert extract_node_refs(None) == []  # type: ignore[arg-type]

    def test_returns_empty_when_no_node_substring(self):
        # Fast-path: short-circuit when "node_" never appears.
        assert extract_node_refs("Hello world {{ trigger.x }}") == []

    def test_deep_attribute_path_preserved(self):
        assert extract_node_refs(
            "{{ node_X.a.b.c.d.e.f }}"
        ) == [("node_X", "a.b.c.d.e.f")]


# ---------------------------------------------------------------------------
# walk_node_strings + collect_refs_from_config
# ---------------------------------------------------------------------------


class TestWalkAndCollect:
    def test_walks_dict_recursively(self):
        config = {
            "url": "http://x",
            "headers": {"X-Auth": "Bearer {{ node_1.token }}"},
            "body": '{"id": "{{ node_2.json.id }}"}',
        }
        strings = list(walk_node_strings(config))
        assert "http://x" in strings
        assert any("node_1.token" in s for s in strings)
        assert any("node_2.json.id" in s for s in strings)

    def test_walks_lists(self):
        config = {
            "examples": [
                "{{ node_3.x }}",
                "literal",
                {"nested": "{{ node_4.y }}"},
            ],
        }
        strings = list(walk_node_strings(config))
        assert any("node_3.x" in s for s in strings)
        assert any("node_4.y" in s for s in strings)

    def test_skips_non_strings(self):
        config = {"max_iter": 5, "enabled": True, "ratio": 0.5, "tags": None}
        # No string values → nothing yielded.
        assert list(walk_node_strings(config)) == []

    def test_collect_dedupes_refs(self):
        config = {
            "a": "{{ node_1.x }}",
            "b": "{{ node_1.x }} again",
            "c": "{{ node_1.y }}",
        }
        refs = collect_refs_from_config(config)
        assert refs == [("node_1", "x"), ("node_1", "y")]


# ---------------------------------------------------------------------------
# lint_jinja_dangling_reference
# ---------------------------------------------------------------------------


def _node(node_id: str, *, label: str = "LLM Agent", config: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "agenticNode",
        "data": {
            "label": label,
            "nodeCategory": "agent",
            "config": dict(config or {}),
        },
    }


class TestLintDanglingRef:
    def test_no_lint_when_ref_exists(self):
        graph = {
            "nodes": [
                _node("node_1"),
                _node("node_2", config={"systemPrompt": "Read {{ node_1.foo }}"}),
            ],
            "edges": [],
        }
        out = lint_jinja_dangling_reference(graph)
        assert out == []

    def test_fires_on_missing_node_id(self):
        # Typo: `node_4r` → `node_4`. The ref is to `node_4r` but
        # only `node_4` exists.
        graph = {
            "nodes": [
                _node("node_4"),
                _node("node_5", config={"systemPrompt": "{{ node_4r.json.id }}"}),
            ],
            "edges": [],
        }
        out = lint_jinja_dangling_reference(graph)
        codes = [l.code for l in out]
        assert "jinja_dangling_node_ref" in codes
        # Targets the node that contains the bad reference.
        bad = next(l for l in out if l.code == "jinja_dangling_node_ref")
        assert bad.node_id == "node_5"
        assert bad.severity == "error"
        assert "node_4r" in bad.message

    def test_fires_on_self_reference(self):
        # Node reading its own slot — empty at handler-run time.
        graph = {
            "nodes": [
                _node("node_2", config={"body": "echo: {{ node_2.x }}"}),
            ],
            "edges": [],
        }
        out = lint_jinja_dangling_reference(graph)
        codes = [l.code for l in out]
        assert "jinja_node_self_ref" in codes
        warn = next(l for l in out if l.code == "jinja_node_self_ref")
        assert warn.severity == "warn"

    def test_walks_nested_config_fields(self):
        graph = {
            "nodes": [
                _node("node_1"),
                _node("node_2", config={
                    "url": "http://x",
                    "headers": {"X-Foo": "{{ node_99.bad }}"},
                }),
            ],
            "edges": [],
        }
        out = lint_jinja_dangling_reference(graph)
        # node_99 doesn't exist → dangling.
        codes = [l.code for l in out]
        assert "jinja_dangling_node_ref" in codes

    def test_skips_when_graph_is_empty(self):
        # No nodes → no refs to check.
        assert lint_jinja_dangling_reference({"nodes": [], "edges": []}) == []
        assert lint_jinja_dangling_reference({}) == []

    def test_multiple_dangling_in_one_node_yields_multiple_lints(self):
        graph = {
            "nodes": [
                _node("node_1"),
                _node("node_2", config={
                    "systemPrompt": (
                        "{{ node_X.a }} and {{ node_Y.b }} and {{ node_Z.c }}"
                    ),
                }),
            ],
            "edges": [],
        }
        out = lint_jinja_dangling_reference(graph)
        # 3 dangling refs → 3 lints.
        dangling = [l for l in out if l.code == "jinja_dangling_node_ref"]
        assert len(dangling) == 3

    def test_real_v10_pattern_no_lint(self):
        # The V10 pattern: switch on intent, fan-out to specialists.
        # Worker reads node_4r.json.id; node_4r exists.
        graph = {
            "nodes": [
                _node("node_4r"),
                _node("node_router"),
                _node("node_worker", config={
                    "systemPrompt": (
                        "case_id: {{ node_4r.json.id }} "
                        "intent: {{ node_router.intents[0] }}"
                    ),
                }),
            ],
            "edges": [],
        }
        out = lint_jinja_dangling_reference(graph)
        # `node_router.intents[0]` — square-bracket access. The regex
        # captures `node_router.intents` and stops at `[`. Both refs
        # are to existing nodes — no lint.
        assert out == []


class TestRunLintsIntegration:
    def test_run_lints_includes_dangling_check(self):
        """Confirm the new lint is wired into the SMART-04 entrypoint."""
        graph = {
            "nodes": [
                _node("node_1", config={
                    "systemPrompt": "{{ node_does_not_exist.x }}",
                }),
            ],
            "edges": [],
        }
        # run_lints requires tenant_id + db; pass a stub for
        # missing-credential lint to skip.
        out = run_lints(graph, tenant_id="t", db=None)
        codes = [l.code for l in out]
        assert "jinja_dangling_node_ref" in codes

    def test_no_double_fire_on_well_formed_v10_workflow(self):
        # Realistic node config like V10's case + worker setup. Should
        # produce zero lints from CTX-MGMT.B.
        graph = {
            "nodes": [
                # A trigger so no_trigger doesn't fire.
                {
                    "id": "node_1",
                    "type": "agenticNode",
                    "data": {"label": "Webhook Trigger", "nodeCategory": "trigger", "config": {}},
                },
                _node("node_2", config={
                    "url": "http://localhost:5050/api/cases",
                    "body": '{"session_id": "{{ trigger.session_id }}"}',
                }),
                _node("node_3", config={
                    "systemPrompt": "Use case {{ node_2.json.id }}",
                }),
            ],
            "edges": [
                {"id": "e1", "source": "node_1", "target": "node_2"},
                {"id": "e2", "source": "node_2", "target": "node_3"},
            ],
        }
        out = lint_jinja_dangling_reference(graph)
        assert out == []
