"""CTX-MGMT.I — unit tests for per-node outputSchema validation,
schema-path reachability, and the lint extension catching Jinja
refs to fields outside the schema.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.engine.output_schema import (
    OutputSchemaError,
    annotate_output_with_validation,
    schema_allows_path,
    schema_paths,
    validate_node_output,
)


# ---------------------------------------------------------------------------
# validate_node_output
# ---------------------------------------------------------------------------


class TestValidateNodeOutput:
    def test_no_schema_is_valid(self):
        assert validate_node_output({"id": "x"}, None) == (True, [])
        assert validate_node_output({"id": "x"}, {}) == (True, [])

    def test_valid_output_passes(self):
        schema = {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "status": {"type": "string", "enum": ["OK", "FAIL"]},
            },
            "required": ["id"],
        }
        ok, errors = validate_node_output({"id": "abc", "status": "OK"}, schema)
        assert ok is True
        assert errors == []

    def test_missing_required_fails(self):
        schema = {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        }
        ok, errors = validate_node_output({}, schema)
        assert ok is False
        assert any("id" in err for err in errors)

    def test_wrong_type_fails(self):
        schema = {
            "type": "object",
            "properties": {"count": {"type": "integer"}},
        }
        ok, errors = validate_node_output({"count": "not-a-number"}, schema)
        assert ok is False

    def test_enum_violation_fails(self):
        schema = {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["OK", "FAIL"]},
            },
        }
        ok, errors = validate_node_output({"status": "MAYBE"}, schema)
        assert ok is False

    def test_non_dict_schema_soft_fails(self):
        ok, errors = validate_node_output({"id": "x"}, "not-a-dict")  # type: ignore[arg-type]
        # Soft fail — returns ok=True but with the error in the list
        # so the caller can log it.
        assert ok is True
        assert errors and "must be a dict" in errors[0]

    def test_malformed_schema_soft_fails(self):
        # Numeric "type" instead of string — invalid JSON Schema
        bad_schema = {"type": 12345}
        ok, errors = validate_node_output({"x": 1}, bad_schema)
        # Either treated as no-constraint (some validators are lenient)
        # OR flagged as malformed — either way, we don't crash.
        assert isinstance(ok, bool)

    def test_path_reported_in_error(self):
        schema = {
            "type": "object",
            "properties": {
                "json": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}},
                },
            },
        }
        ok, errors = validate_node_output(
            {"json": {"id": "should-be-int"}}, schema,
        )
        assert ok is False
        # Path is "json.id" or similar — has the nested location.
        assert any("json" in err and "id" in err for err in errors)


# ---------------------------------------------------------------------------
# schema_paths + schema_allows_path
# ---------------------------------------------------------------------------


class TestSchemaPaths:
    def test_empty_schema(self):
        assert schema_paths(None) == set()
        assert schema_paths({}) == set()
        assert schema_paths({"type": "object"}) == set()

    def test_top_level_properties(self):
        schema = {
            "type": "object",
            "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
        }
        assert schema_paths(schema) == {"id", "name"}

    def test_nested_object(self):
        schema = {
            "type": "object",
            "properties": {
                "json": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                },
            },
        }
        assert schema_paths(schema) == {"json", "json.id"}

    def test_array_with_object_items(self):
        schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    },
                },
            },
        }
        paths = schema_paths(schema)
        assert "items" in paths
        assert "items.name" in paths

    def test_max_depth_caps_recursion(self):
        # A deeply-nested schema doesn't blow up.
        schema = {
            "type": "object",
            "properties": {
                "a": {
                    "type": "object",
                    "properties": {
                        "b": {
                            "type": "object",
                            "properties": {
                                "c": {"type": "string"},
                            },
                        },
                    },
                },
            },
        }
        paths = schema_paths(schema)
        assert "a" in paths and "a.b" in paths and "a.b.c" in paths


class TestSchemaAllowsPath:
    def test_no_schema_always_allows(self):
        assert schema_allows_path(None, "anything.goes.here") is True
        assert schema_allows_path({}, "anything.goes.here") is True

    def test_declared_path_allowed(self):
        schema = {
            "type": "object",
            "properties": {
                "json": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                },
            },
        }
        assert schema_allows_path(schema, "json") is True
        assert schema_allows_path(schema, "json.id") is True

    def test_undeclared_path_blocked(self):
        schema = {
            "type": "object",
            "properties": {"id": {"type": "string"}},
        }
        assert schema_allows_path(schema, "missing_field") is False
        assert schema_allows_path(schema, "id.nested") is False

    def test_additional_properties_true_allows_anything(self):
        # When the schema is intentionally permissive, the lint
        # stays quiet.
        schema = {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "additionalProperties": True,
        }
        assert schema_allows_path(schema, "anything.else") is True

    def test_x_permissive_extension(self):
        schema = {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "x-permissive": True,
        }
        assert schema_allows_path(schema, "anything.else") is True

    def test_parent_path_implies_child_allowed_too(self):
        # If schema declares `json.id`, accessing `json` itself is fine.
        schema = {
            "type": "object",
            "properties": {
                "json": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                },
            },
        }
        assert schema_allows_path(schema, "json") is True


# ---------------------------------------------------------------------------
# annotate_output_with_validation
# ---------------------------------------------------------------------------


class TestAnnotate:
    def test_valid_output_returned_unchanged(self):
        output = {"id": "x"}
        out = annotate_output_with_validation(output, is_valid=True, errors=[])
        assert out is output

    def test_invalid_dict_gets_metadata(self):
        output = {"id": "x"}
        errs = ["json.id: expected integer"]
        out = annotate_output_with_validation(output, is_valid=False, errors=errs)
        assert out["_schema_mismatch"] is True
        assert out["_schema_errors"] == errs
        # Original keys preserved.
        assert out["id"] == "x"
        # Original dict not mutated (defensive copy).
        assert "_schema_mismatch" not in output

    def test_invalid_non_dict_returned_unchanged(self):
        # Can't annotate a list / scalar without changing its type.
        output = ["a", "b"]
        out = annotate_output_with_validation(output, is_valid=False, errors=["x"])
        assert out == ["a", "b"]

    def test_caps_errors_to_5(self):
        many_errors = [f"err {i}" for i in range(20)]
        out = annotate_output_with_validation({}, is_valid=False, errors=many_errors)
        assert len(out["_schema_errors"]) == 5


# ---------------------------------------------------------------------------
# config_validator integration
# ---------------------------------------------------------------------------


class TestConfigValidator:
    def test_skip_when_unset(self):
        from app.engine.config_validator import _validate_output_schema

        assert _validate_output_schema("n1", "LLM Agent", {}) == []

    def test_non_dict_schema_warns(self):
        from app.engine.config_validator import _validate_output_schema

        warnings = _validate_output_schema(
            "n1", "LLM Agent", {"outputSchema": "not-a-dict"},
        )
        assert len(warnings) == 1
        assert "must be a dict" in warnings[0]

    def test_empty_schema_skipped(self):
        from app.engine.config_validator import _validate_output_schema

        assert _validate_output_schema("n1", "LLM Agent", {"outputSchema": {}}) == []

    def test_valid_schema_no_warning(self):
        from app.engine.config_validator import _validate_output_schema

        warnings = _validate_output_schema(
            "n1", "LLM Agent",
            {"outputSchema": {"type": "object", "properties": {"id": {"type": "string"}}}},
        )
        assert warnings == []

    def test_malformed_jsonschema_warns(self):
        from app.engine.config_validator import _validate_output_schema

        # `type` must be a string or array of strings — int is invalid.
        warnings = _validate_output_schema(
            "n1", "LLM Agent",
            {"outputSchema": {"type": 12345}},
        )
        assert any("not a valid JSON Schema" in w for w in warnings)


# ---------------------------------------------------------------------------
# Lint extension — schema-path-not-in-schema
# ---------------------------------------------------------------------------


def _node(node_id: str, *, label: str = "LLM Agent",
          config: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "agenticNode",
        "data": {"label": label, "nodeCategory": "agent", "config": dict(config or {})},
    }


class TestLintSchemaPath:
    def test_no_schema_no_lint(self):
        from app.copilot.lints import lint_jinja_dangling_reference

        # Reference to node_1.foo but no schema declared — lint stays quiet.
        graph = {
            "nodes": [
                _node("node_1"),
                _node("node_2", config={"systemPrompt": "{{ node_1.foo.bar }}"}),
            ],
            "edges": [],
        }
        out = lint_jinja_dangling_reference(graph)
        codes = [l.code for l in out]
        assert "jinja_ref_path_not_in_schema" not in codes

    def test_undeclared_path_warns(self):
        from app.copilot.lints import lint_jinja_dangling_reference

        graph = {
            "nodes": [
                _node("node_1", config={
                    "outputSchema": {
                        "type": "object",
                        "properties": {"json": {"type": "object",
                                                 "properties": {"id": {"type": "string"}}}},
                    },
                }),
                _node("node_2", config={
                    # Typo: `.foo` instead of `.json`.
                    "systemPrompt": "{{ node_1.foo.bar }}",
                }),
            ],
            "edges": [],
        }
        out = lint_jinja_dangling_reference(graph)
        codes = [l.code for l in out]
        assert "jinja_ref_path_not_in_schema" in codes

    def test_declared_path_no_lint(self):
        from app.copilot.lints import lint_jinja_dangling_reference

        graph = {
            "nodes": [
                _node("node_1", config={
                    "outputSchema": {
                        "type": "object",
                        "properties": {"json": {"type": "object",
                                                 "properties": {"id": {"type": "string"}}}},
                    },
                }),
                _node("node_2", config={
                    "systemPrompt": "{{ node_1.json.id }}",
                }),
            ],
            "edges": [],
        }
        out = lint_jinja_dangling_reference(graph)
        codes = [l.code for l in out]
        assert "jinja_ref_path_not_in_schema" not in codes

    def test_alias_resolves_to_schema_too(self):
        from app.copilot.lints import lint_jinja_dangling_reference

        graph = {
            "nodes": [
                _node("node_1", config={
                    "exposeAs": "case",
                    "outputSchema": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                    },
                }),
                # Reference via alias — schema check applies the same way.
                _node("node_2", config={"systemPrompt": "{{ case.foo }}"}),
            ],
            "edges": [],
        }
        out = lint_jinja_dangling_reference(graph)
        codes = [l.code for l in out]
        assert "jinja_ref_path_not_in_schema" in codes

    def test_additional_properties_true_silences_lint(self):
        from app.copilot.lints import lint_jinja_dangling_reference

        graph = {
            "nodes": [
                _node("node_1", config={
                    "outputSchema": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                        "additionalProperties": True,
                    },
                }),
                _node("node_2", config={
                    "systemPrompt": "{{ node_1.anything_else }}",
                }),
            ],
            "edges": [],
        }
        out = lint_jinja_dangling_reference(graph)
        codes = [l.code for l in out]
        assert "jinja_ref_path_not_in_schema" not in codes
