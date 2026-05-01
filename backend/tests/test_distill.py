"""CTX-MGMT.J — unit tests for first-class distillBlocks rendering.

Generalises the V10 ``RECENT TOOL FINDINGS`` Jinja-block pattern.
Tests cover the pure helpers (path walking, format rendering,
shape validation) and confirm the engine wires them into the LLM /
ReAct system prompt.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.engine.distill import (
    HARD_LIMIT_CEILING,
    PER_ITEM_CHAR_CAP,
    render_distill_blocks,
    render_one_block,
    validate_distill_blocks,
    walk_dotted_path,
)


# ---------------------------------------------------------------------------
# walk_dotted_path
# ---------------------------------------------------------------------------


class TestWalkDottedPath:
    def test_simple_top_level(self):
        ctx = {"node_1": {"x": 1}}
        assert walk_dotted_path(ctx, "node_1") == {"x": 1}

    def test_nested_dict(self):
        ctx = {"node_1": {"json": {"id": "abc"}}}
        assert walk_dotted_path(ctx, "node_1.json.id") == "abc"

    def test_returns_none_on_missing_root(self):
        assert walk_dotted_path({}, "node_4r.foo") is None

    def test_returns_none_on_missing_attr(self):
        ctx = {"node_1": {"json": {}}}
        assert walk_dotted_path(ctx, "node_1.json.missing") is None

    def test_returns_none_when_walking_through_scalar(self):
        ctx = {"node_1": "not-a-dict"}
        assert walk_dotted_path(ctx, "node_1.foo") is None

    def test_list_index_access(self):
        ctx = {"node_1": {"items": ["a", "b", "c"]}}
        assert walk_dotted_path(ctx, "node_1.items[1]") == "b"

    def test_list_index_out_of_range_returns_none(self):
        ctx = {"node_1": {"items": ["a"]}}
        assert walk_dotted_path(ctx, "node_1.items[5]") is None

    def test_invalid_index_returns_none(self):
        ctx = {"node_1": {"items": ["a"]}}
        assert walk_dotted_path(ctx, "node_1.items[xyz]") is None

    def test_empty_path(self):
        assert walk_dotted_path({"x": 1}, "") is None

    def test_non_dict_context(self):
        assert walk_dotted_path("not-a-dict", "x") is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# render_one_block
# ---------------------------------------------------------------------------


class TestRenderOneBlock:
    def _ctx_with_worknotes(self, n: int) -> dict[str, Any]:
        return {
            "node_4r": {
                "json": {
                    "worknotes": [
                        {"text": f"finding {i}", "ts": f"2026-05-01T0{i}:00"}
                        for i in range(n)
                    ],
                },
            },
        }

    def test_bullet_format_default(self):
        block = {
            "label": "RECENT TOOL FINDINGS",
            "fromPath": "node_4r.json.worknotes",
            "limit": 4,
            "project": ["text"],
        }
        out = render_one_block(self._ctx_with_worknotes(3), block)
        assert "=== RECENT TOOL FINDINGS ===" in out
        assert "- finding 0" in out
        assert "- finding 1" in out
        assert "- finding 2" in out

    def test_takes_last_N_when_limit_exceeded(self):
        block = {
            "label": "X",
            "fromPath": "node_4r.json.worknotes",
            "limit": 2,
            "project": ["text"],
        }
        out = render_one_block(self._ctx_with_worknotes(5), block)
        # Last 2 entries.
        assert "- finding 3" in out
        assert "- finding 4" in out
        # Earlier entries dropped.
        assert "- finding 0" not in out
        assert "- finding 1" not in out

    def test_numbered_format(self):
        block = {
            "label": "STEPS",
            "fromPath": "node_4r.json.worknotes",
            "project": ["text"],
            "format": "numbered",
        }
        out = render_one_block(self._ctx_with_worknotes(3), block)
        assert "1. finding 0" in out
        assert "2. finding 1" in out
        assert "3. finding 2" in out

    def test_json_format(self):
        block = {
            "label": "RAW",
            "fromPath": "node_4r.json.worknotes",
            "limit": 2,
            "project": ["text"],
            "format": "json",
        }
        out = render_one_block(self._ctx_with_worknotes(2), block)
        assert "=== RAW ===" in out
        # JSON pretty-printed; "finding 0" appears as a quoted string.
        assert '"finding 0"' in out

    def test_project_multi_field_returns_dict(self):
        block = {
            "label": "X",
            "fromPath": "node_4r.json.worknotes",
            "limit": 1,
            "project": ["text", "ts"],
        }
        out = render_one_block(self._ctx_with_worknotes(1), block)
        # JSON of {"text": "finding 0", "ts": "..."}
        assert '"text"' in out
        assert '"ts"' in out

    def test_empty_path_returns_empty_string(self):
        block = {"label": "X", "fromPath": "node_4r.does_not_exist"}
        out = render_one_block({"node_4r": {}}, block)
        assert out == ""

    def test_no_label_omits_section_header(self):
        block = {"fromPath": "node_4r.json.worknotes", "project": ["text"]}
        out = render_one_block(self._ctx_with_worknotes(1), block)
        # No "=== ... ===" line — just the bullet content.
        assert "===" not in out
        assert "- finding 0" in out

    def test_long_string_truncated(self):
        ctx = {"node_4r": {"text": "x" * 1000}}
        block = {"label": "X", "fromPath": "node_4r.text"}
        out = render_one_block(ctx, block)
        # Truncated to PER_ITEM_CHAR_CAP — '…' suffix.
        assert "…" in out

    def test_hard_limit_ceiling(self):
        items = [{"text": f"item {i}"} for i in range(200)]
        ctx = {"node_4r": {"json": {"worknotes": items}}}
        block = {
            "label": "X",
            "fromPath": "node_4r.json.worknotes",
            "limit": 500,  # author asked silly-large
            "project": ["text"],
        }
        out = render_one_block(ctx, block)
        # Only HARD_LIMIT_CEILING items rendered.
        line_count = out.count("\n- ") + (1 if out.startswith("- ") else 0)
        # The label adds a `===` line, so count bullets specifically.
        bullet_count = sum(1 for line in out.split("\n") if line.startswith("- "))
        assert bullet_count <= HARD_LIMIT_CEILING

    def test_limit_zero_means_all(self):
        block = {
            "label": "X",
            "fromPath": "node_4r.json.worknotes",
            "limit": 0,
            "project": ["text"],
        }
        out = render_one_block(self._ctx_with_worknotes(3), block)
        assert "- finding 0" in out
        assert "- finding 2" in out

    def test_scalar_value_at_path_renders_as_single_item(self):
        # `fromPath` resolves to a string (not a list).
        ctx = {"node_4r": {"text": "hello"}}
        block = {"label": "X", "fromPath": "node_4r.text"}
        out = render_one_block(ctx, block)
        assert "- hello" in out


# ---------------------------------------------------------------------------
# render_distill_blocks — combiner
# ---------------------------------------------------------------------------


class TestRenderDistillBlocks:
    def test_empty_input_returns_empty_string(self):
        assert render_distill_blocks({}, None) == ""
        assert render_distill_blocks({"x": 1}, []) == ""

    def test_combines_multiple_blocks(self):
        ctx = {
            "node_4r": {"json": {
                "worknotes": [{"text": "wn1"}],
                "evidence": [{"kind": "log", "content": "ev1"}],
            }},
        }
        blocks = [
            {"label": "WORKNOTES", "fromPath": "node_4r.json.worknotes", "project": ["text"]},
            {"label": "EVIDENCE", "fromPath": "node_4r.json.evidence"},
        ]
        out = render_distill_blocks(ctx, blocks)
        assert "=== WORKNOTES ===" in out
        assert "=== EVIDENCE ===" in out
        assert "- wn1" in out
        # Blocks separated by blank lines.
        assert "\n\n" in out

    def test_skips_empty_blocks(self):
        ctx = {"node_4r": {"json": {"worknotes": []}}}
        blocks = [
            {"label": "EMPTY", "fromPath": "node_4r.json.worknotes"},
            {"label": "ALSO_EMPTY", "fromPath": "node_4r.json.does_not_exist"},
        ]
        out = render_distill_blocks(ctx, blocks)
        assert out == ""

    def test_one_empty_one_full_keeps_only_full(self):
        ctx = {"node_4r": {"json": {"worknotes": [{"text": "x"}]}}}
        blocks = [
            {"label": "FULL", "fromPath": "node_4r.json.worknotes", "project": ["text"]},
            {"label": "EMPTY", "fromPath": "node_4r.json.missing"},
        ]
        out = render_distill_blocks(ctx, blocks)
        assert "=== FULL ===" in out
        assert "=== EMPTY ===" not in out

    def test_malformed_block_skipped_with_log(self):
        # Non-dict block in the list — skipped silently.
        ctx = {"node_4r": {"json": {"worknotes": [{"text": "x"}]}}}
        blocks = [
            "not-a-dict",  # type: ignore[list-item]
            {"label": "OK", "fromPath": "node_4r.json.worknotes", "project": ["text"]},
        ]
        out = render_distill_blocks(ctx, blocks)
        assert "=== OK ===" in out


# ---------------------------------------------------------------------------
# validate_distill_blocks — promote-time shape check
# ---------------------------------------------------------------------------


class TestValidate:
    def test_none_is_valid(self):
        assert validate_distill_blocks(None) == []

    def test_non_list_invalid(self):
        errors = validate_distill_blocks({"label": "x"})
        assert len(errors) == 1
        assert "must be a list" in errors[0]

    def test_block_must_be_object(self):
        errors = validate_distill_blocks(["not-a-dict"])
        assert any("must be an object" in e for e in errors)

    def test_missing_fromPath_invalid(self):
        errors = validate_distill_blocks([{"label": "x"}])
        assert any("fromPath" in e for e in errors)

    def test_non_string_fromPath_invalid(self):
        errors = validate_distill_blocks([{"fromPath": 123}])
        assert any("fromPath must be a string" in e for e in errors)

    def test_non_string_label_invalid(self):
        errors = validate_distill_blocks([{"fromPath": "x", "label": 123}])
        assert any("label must be a string" in e for e in errors)

    def test_non_int_limit_invalid(self):
        errors = validate_distill_blocks([{"fromPath": "x", "limit": "five"}])
        assert any("limit must be an integer" in e for e in errors)

    def test_non_list_project_invalid(self):
        errors = validate_distill_blocks([{"fromPath": "x", "project": "text"}])
        assert any("project must be a list" in e for e in errors)

    def test_unknown_format_invalid(self):
        errors = validate_distill_blocks([{"fromPath": "x", "format": "yaml"}])
        assert any("format must be one of" in e for e in errors)

    def test_clean_block_valid(self):
        errors = validate_distill_blocks([{
            "label": "X",
            "fromPath": "node_1.foo",
            "limit": 4,
            "project": ["text"],
            "format": "bullet",
        }])
        assert errors == []


# ---------------------------------------------------------------------------
# Engine integration — confirm distillBlocks lands in system prompt
# ---------------------------------------------------------------------------


class TestEngineIntegration:
    """Smoke at the helper level — the actual handler integration is
    a small surgical call site (`render_distill_blocks(context, ...)
    appended to system_prompt`). This test confirms the helper
    produces the right text shape for that integration."""

    def test_v10_pattern_smoke(self):
        # Mimic V10's RECENT TOOL FINDINGS block via declarative config.
        ctx = {
            "node_4r": {
                "json": {
                    "worknotes": [
                        {"text": "agent flagged license expiry", "ts": "10:00"},
                        {"text": "queue depth alarm", "ts": "10:05"},
                        {"text": "restart succeeded", "ts": "10:10"},
                    ],
                    "evidence": [
                        {"kind": "log", "content": "request 9876 failed"},
                    ],
                },
            },
        }
        blocks = [
            {
                "label": "RECENT TOOL FINDINGS",
                "fromPath": "node_4r.json.worknotes",
                "limit": 4,
                "project": ["text"],
            },
            {
                "label": "EVIDENCE",
                "fromPath": "node_4r.json.evidence",
                "project": ["content"],
            },
        ]
        out = render_distill_blocks(ctx, blocks)
        # Both blocks present.
        assert "=== RECENT TOOL FINDINGS ===" in out
        assert "=== EVIDENCE ===" in out
        # Latest 3 worknotes (limit=4 but only 3 exist).
        assert "- agent flagged license expiry" in out
        assert "- restart succeeded" in out
        # Evidence content rendered.
        assert "- request 9876 failed" in out


# ---------------------------------------------------------------------------
# Validator integration
# ---------------------------------------------------------------------------


class TestConfigValidatorIntegration:
    def test_valid_distill_blocks_no_warning(self):
        from app.engine.config_validator import _validate_distill_blocks

        config = {
            "distillBlocks": [
                {"label": "X", "fromPath": "node_1.foo"},
            ],
        }
        assert _validate_distill_blocks("n1", "ReAct Agent", config) == []

    def test_invalid_distill_blocks_warns(self):
        from app.engine.config_validator import _validate_distill_blocks

        config = {
            "distillBlocks": [
                {"label": "X"},  # missing fromPath
            ],
        }
        warnings = _validate_distill_blocks("n1", "ReAct Agent", config)
        assert any("fromPath" in w for w in warnings)
        # Warning includes the node id + label.
        assert any("n1" in w and "ReAct Agent" in w for w in warnings)

    def test_missing_field_skipped(self):
        from app.engine.config_validator import _validate_distill_blocks

        # No distillBlocks set — no validation needed.
        assert _validate_distill_blocks("n1", "LLM Agent", {}) == []
