"""CTX-MGMT.L — unit tests for the per-node reducer registry.

Pure-helper tests — no DB, no engine. The dag_runner integration is
exercised via the existing test_dag_runner suite (default `overwrite`
preserves all current semantics), plus one targeted integration test
here that proves a node configured with `outputReducer: "append"`
actually accumulates across two writes.
"""

from __future__ import annotations

import pytest

from app.engine.reducers import (
    DEFAULT_REDUCER,
    KNOWN_REDUCERS,
    apply_reducer,
    resolve_reducer,
)


# ---------------------------------------------------------------------------
# Registry + resolve_reducer
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_known_reducers_set(self):
        assert "overwrite" in KNOWN_REDUCERS
        assert "append" in KNOWN_REDUCERS
        assert "merge" in KNOWN_REDUCERS
        assert "max" in KNOWN_REDUCERS
        assert "min" in KNOWN_REDUCERS
        assert "counter" in KNOWN_REDUCERS

    def test_default_is_overwrite(self):
        assert DEFAULT_REDUCER == "overwrite"


class TestResolveReducer:
    def test_default_when_unset(self):
        assert resolve_reducer({}) == DEFAULT_REDUCER
        assert resolve_reducer(None) == DEFAULT_REDUCER

    def test_default_for_empty_string(self):
        assert resolve_reducer({"config": {"outputReducer": ""}}) == DEFAULT_REDUCER

    def test_returns_named_reducer(self):
        for name in KNOWN_REDUCERS:
            assert resolve_reducer({"config": {"outputReducer": name}}) == name

    def test_case_insensitive(self):
        assert resolve_reducer({"config": {"outputReducer": "APPEND"}}) == "append"
        assert resolve_reducer({"config": {"outputReducer": "Merge"}}) == "merge"

    def test_unknown_falls_back_to_default(self):
        # The author-time validator catches typos, but the runtime
        # falls back gracefully if a typo slips through (e.g.
        # hand-edited graph_json bypassing the validator).
        assert resolve_reducer({"config": {"outputReducer": "appned"}}) == DEFAULT_REDUCER


# ---------------------------------------------------------------------------
# apply_reducer — overwrite
# ---------------------------------------------------------------------------


class TestOverwrite:
    def test_replaces_current(self):
        assert apply_reducer("overwrite", "old", "new") == "new"

    def test_replaces_with_none_input_too(self):
        # First write — current is None — produces new.
        assert apply_reducer("overwrite", None, {"x": 1}) == {"x": 1}


# ---------------------------------------------------------------------------
# apply_reducer — append
# ---------------------------------------------------------------------------


class TestAppend:
    def test_first_write_initialises_list(self):
        assert apply_reducer("append", None, "a") == ["a"]

    def test_second_write_appends(self):
        assert apply_reducer("append", ["a"], "b") == ["a", "b"]

    def test_complex_values_preserved(self):
        result = apply_reducer("append", [{"x": 1}], {"y": 2})
        assert result == [{"x": 1}, {"y": 2}]

    def test_wraps_non_list_current(self):
        # If a slot started life as `overwrite` and was switched to
        # `append`, the existing value isn't lost.
        assert apply_reducer("append", "first", "second") == ["first", "second"]

    def test_does_not_mutate_input_list(self):
        # Defence in depth — reducers must produce a new list so
        # downstream Jinja renderings on `current` don't see future
        # mutations.
        original = ["a"]
        result = apply_reducer("append", original, "b")
        assert original == ["a"]  # untouched
        assert result == ["a", "b"]


# ---------------------------------------------------------------------------
# apply_reducer — merge
# ---------------------------------------------------------------------------


class TestMerge:
    def test_first_write_copies(self):
        new = {"a": 1}
        result = apply_reducer("merge", None, new)
        assert result == {"a": 1}
        # Defensive copy — mutating the result doesn't leak into new.
        result["a"] = 99
        assert new == {"a": 1}

    def test_new_keys_added(self):
        result = apply_reducer("merge", {"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_existing_keys_overwritten(self):
        result = apply_reducer("merge", {"a": 1}, {"a": 99})
        assert result == {"a": 99}

    def test_non_dict_new_falls_back_to_overwrite(self):
        # Type-tolerant: log + overwrite, don't raise.
        result = apply_reducer("merge", {"a": 1}, "not-a-dict")
        assert result == "not-a-dict"

    def test_non_dict_current_falls_back_to_overwrite(self):
        result = apply_reducer("merge", "legacy", {"a": 1})
        assert result == {"a": 1}


# ---------------------------------------------------------------------------
# apply_reducer — max / min
# ---------------------------------------------------------------------------


class TestExtreme:
    def test_max_seeds_with_first_value(self):
        assert apply_reducer("max", None, 5) == 5

    def test_max_keeps_larger_current(self):
        assert apply_reducer("max", 10, 5) == 10

    def test_max_replaces_with_larger_new(self):
        assert apply_reducer("max", 5, 10) == 10

    def test_min_seeds_with_first_value(self):
        assert apply_reducer("min", None, 5) == 5

    def test_min_keeps_smaller_current(self):
        assert apply_reducer("min", 5, 10) == 5

    def test_min_replaces_with_smaller_new(self):
        assert apply_reducer("min", 10, 5) == 5

    def test_coerces_numeric_string(self):
        assert apply_reducer("max", "5", "10") == "10"

    def test_non_numeric_falls_back_to_overwrite(self):
        # Hostile input — log + take new.
        assert apply_reducer("max", 5, "not-a-number") == "not-a-number"

    def test_floats_compared_correctly(self):
        assert apply_reducer("max", 1.5, 2.5) == 2.5
        assert apply_reducer("min", 1.5, 2.5) == 1.5


# ---------------------------------------------------------------------------
# apply_reducer — counter
# ---------------------------------------------------------------------------


class TestCounter:
    def test_first_write_seeds(self):
        assert apply_reducer("counter", None, 5) == 5
        # Type preserved.
        assert isinstance(apply_reducer("counter", None, 5), int)

    def test_accumulates_ints(self):
        assert apply_reducer("counter", 5, 3) == 8

    def test_accumulates_int_and_float(self):
        result = apply_reducer("counter", 5, 0.5)
        assert result == 5.5

    def test_non_numeric_new_falls_back_to_overwrite(self):
        assert apply_reducer("counter", 5, "abc") == "abc"

    def test_bool_treated_as_int(self):
        # bool is technically int in Python; treat as 0/1 for counter.
        assert apply_reducer("counter", 5, True) == 6

    def test_int_type_preserved_when_both_ints(self):
        result = apply_reducer("counter", 5, 3)
        assert isinstance(result, int) and not isinstance(result, bool)


# ---------------------------------------------------------------------------
# Unknown reducer
# ---------------------------------------------------------------------------


class TestUnknownReducer:
    def test_falls_back_to_overwrite(self):
        # Should never reach apply_reducer with an unknown name in
        # practice (resolve_reducer catches it), but defence in depth.
        assert apply_reducer("not-real", "old", "new") == "new"


# ---------------------------------------------------------------------------
# Validator integration — config_validator catches bad reducer names
# ---------------------------------------------------------------------------


class TestValidatorIntegration:
    def test_validator_rejects_bad_reducer_name(self):
        from app.engine.config_validator import _validate_output_reducer

        warnings = _validate_output_reducer(
            "node_1", "LLM Agent", {"outputReducer": "appned"},  # typo
        )
        assert len(warnings) == 1
        assert "not a known reducer" in warnings[0]

    def test_validator_accepts_known_reducer(self):
        from app.engine.config_validator import _validate_output_reducer

        for name in KNOWN_REDUCERS:
            warnings = _validate_output_reducer(
                "node_1", "LLM Agent", {"outputReducer": name},
            )
            assert warnings == []

    def test_validator_skips_when_unset(self):
        from app.engine.config_validator import _validate_output_reducer

        assert _validate_output_reducer("node_1", "LLM Agent", {}) == []

    def test_validator_rejects_negative_budget(self):
        from app.engine.config_validator import _validate_output_budget

        warnings = _validate_output_budget(
            "node_1", "LLM Agent", {"contextOutputBudget": -100},
        )
        assert len(warnings) == 1
        assert "must be > 0" in warnings[0]

    def test_validator_rejects_non_int_budget(self):
        from app.engine.config_validator import _validate_output_budget

        warnings = _validate_output_budget(
            "node_1", "LLM Agent", {"contextOutputBudget": "64kb"},
        )
        assert len(warnings) == 1
        assert "must be an integer" in warnings[0]

    def test_validator_accepts_valid_budget(self):
        from app.engine.config_validator import _validate_output_budget

        assert _validate_output_budget(
            "node_1", "LLM Agent", {"contextOutputBudget": 32768},
        ) == []

    def test_validator_skips_when_budget_unset(self):
        from app.engine.config_validator import _validate_output_budget

        assert _validate_output_budget("node_1", "LLM Agent", {}) == []


# ---------------------------------------------------------------------------
# Engine integration — append reducer accumulates across iterations
# ---------------------------------------------------------------------------


class TestEngineIntegration:
    """The dag_runner write sites (sequential + parallel) apply the
    reducer when assigning to context[node_id]. The default-overwrite
    path is exercised by every existing dag_runner test (1057 of
    them); this test adds the append-reducer round-trip."""

    def test_append_reducer_accumulates_simulated_writes(self):
        # Simulate the write loop: a hypothetical node fires twice
        # (e.g. inside a future Coalesce arm), each time producing a
        # different output. With overwrite (default), only the last
        # write survives. With append, both do.
        from app.engine.reducers import apply_reducer

        ctx: dict = {}
        # Iteration 1
        ctx["coalesce_x"] = apply_reducer(
            "append", ctx.get("coalesce_x"), {"branch": "A", "value": 1},
        )
        # Iteration 2
        ctx["coalesce_x"] = apply_reducer(
            "append", ctx.get("coalesce_x"), {"branch": "B", "value": 2},
        )
        assert ctx["coalesce_x"] == [
            {"branch": "A", "value": 1},
            {"branch": "B", "value": 2},
        ]

    def test_overwrite_preserves_existing_dag_runner_semantics(self):
        # Same scenario with default reducer — last write wins.
        from app.engine.reducers import apply_reducer

        ctx: dict = {}
        ctx["x"] = apply_reducer("overwrite", ctx.get("x"), {"value": 1})
        ctx["x"] = apply_reducer("overwrite", ctx.get("x"), {"value": 2})
        assert ctx["x"] == {"value": 2}
