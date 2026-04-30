"""CTX-MGMT.A — unit tests for the per-node output budget + overflow
artifact helpers.

The pure helpers (`estimate_output_size`, `resolve_budget`,
`should_overflow`, `materialize_overflow_stub`) are unit-tested
without DB. The `persist_artifact` and `maybe_overflow` paths get
DB-touching tests with the SQLAlchemy session mocked.

The dag_runner integration (the actual end-to-end overflow round-trip
through `_execute_single_node`) is covered by an integration-style
test that mocks `dispatch_node` to return a giant payload, asserts
the in-context stub, and asserts the artifact row persisted.
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.engine import output_artifact
from app.engine.output_artifact import (
    DEFAULT_OUTPUT_BUDGET_BYTES,
    HARD_CEILING_BYTES,
    estimate_output_size,
    materialize_overflow_stub,
    maybe_overflow,
    resolve_budget,
    should_overflow,
)


# ---------------------------------------------------------------------------
# estimate_output_size
# ---------------------------------------------------------------------------


class TestEstimateSize:
    def test_none_is_zero(self):
        assert estimate_output_size(None) == 0

    def test_small_dict(self):
        # `{"x":1}` is 7 bytes JSON-encoded.
        assert 1 <= estimate_output_size({"x": 1}) <= 16

    def test_uses_default_str_for_uuid(self):
        # UUIDs aren't JSON-serialisable — make sure the helper doesn't raise.
        assert estimate_output_size({"id": _uuid.uuid4()}) > 0

    def test_large_string_grows_proportionally(self):
        small = estimate_output_size({"x": "a" * 100})
        big = estimate_output_size({"x": "a" * 10000})
        assert big > small * 50

    def test_unicode_uses_byte_count(self):
        # 'é' is 2 bytes in UTF-8 — make sure size measures bytes,
        # not Python char count.
        ascii_size = estimate_output_size({"x": "abc"})
        unicode_size = estimate_output_size({"x": "éèà"})
        assert unicode_size > ascii_size


# ---------------------------------------------------------------------------
# resolve_budget
# ---------------------------------------------------------------------------


class TestResolveBudget:
    def test_default_when_no_node_data(self):
        assert resolve_budget(None) == DEFAULT_OUTPUT_BUDGET_BYTES
        assert resolve_budget({}) == DEFAULT_OUTPUT_BUDGET_BYTES

    def test_per_node_override(self):
        node_data = {"config": {"contextOutputBudget": 32 * 1024}}
        assert resolve_budget(node_data) == 32 * 1024

    def test_clamps_to_hard_ceiling(self):
        # Author asks for 10 MB — clamped to 256 kB.
        node_data = {"config": {"contextOutputBudget": 10_000_000}}
        assert resolve_budget(node_data) == HARD_CEILING_BYTES

    def test_zero_or_negative_falls_back_to_default(self):
        # Don't let a misconfigured `contextOutputBudget: 0` disable
        # the overflow path — that's the silent unbounded growth this
        # whole module exists to prevent.
        for bad in (0, -1, -1000):
            assert resolve_budget({"config": {"contextOutputBudget": bad}}) == DEFAULT_OUTPUT_BUDGET_BYTES

    def test_non_int_falls_back_to_default(self):
        for bad in ("not-a-number", None, [1, 2, 3]):
            assert resolve_budget({"config": {"contextOutputBudget": bad}}) == DEFAULT_OUTPUT_BUDGET_BYTES


# ---------------------------------------------------------------------------
# should_overflow
# ---------------------------------------------------------------------------


class TestShouldOverflow:
    def test_none_never_overflows(self):
        assert should_overflow(None, DEFAULT_OUTPUT_BUDGET_BYTES) is False

    def test_small_under_budget(self):
        assert should_overflow({"id": "x"}, DEFAULT_OUTPUT_BUDGET_BYTES) is False

    def test_big_over_budget(self):
        big = {"data": "a" * 200_000}
        assert should_overflow(big, DEFAULT_OUTPUT_BUDGET_BYTES) is True

    def test_at_exact_budget_does_not_overflow(self):
        # The boundary is `>` strict, so an output exactly at budget
        # is allowed inline. (Tested with a known-size payload.)
        s = "a" * 8
        budget = estimate_output_size({"x": s})
        assert should_overflow({"x": s}, budget) is False


# ---------------------------------------------------------------------------
# materialize_overflow_stub
# ---------------------------------------------------------------------------


class TestMaterializeOverflowStub:
    def test_stub_shape(self):
        stub = materialize_overflow_stub(
            {"id": "abc", "status": "OK"},
            budget=64,
            size_bytes=128,
            artifact_id="art-1",
        )
        assert stub["_overflow"] is True
        assert stub["_artifact_id"] == "art-1"
        assert stub["size_bytes"] == 128
        assert stub["budget_bytes"] == 64
        assert "summary" in stub
        assert "preview" in stub

    def test_preserves_canonical_top_level_keys(self):
        # `id`, `status`, `error`, `branch`, `result` etc. are what
        # downstream Jinja and Switch nodes typically reach for —
        # they must survive the stub.
        big = {
            "id": "request-42",
            "status": "FAILED",
            "error": "Connection timeout",
            "branch": "true",
            "result": "see logs",
            "huge_payload": "x" * 200_000,
        }
        stub = materialize_overflow_stub(big, budget=64, size_bytes=200_000, artifact_id="a")
        preview = stub["preview"]
        assert preview["id"] == "request-42"
        assert preview["status"] == "FAILED"
        assert preview["error"] == "Connection timeout"
        assert preview["branch"] == "true"
        assert preview["result"] == "see logs"

    def test_lists_top_level_keys_for_navigation(self):
        big = {f"field_{i}": i for i in range(50)}
        stub = materialize_overflow_stub(big, budget=64, size_bytes=999, artifact_id="a")
        keys = stub["preview"]["top_level_keys"]
        # Capped at 30 so the stub itself stays small.
        assert len(keys) == 30
        assert keys == sorted(keys)  # deterministic order

    def test_truncates_long_string_values(self):
        big = {"id": "x" * 5000}  # >256 char cap
        stub = materialize_overflow_stub(big, budget=64, size_bytes=5100, artifact_id="a")
        # Truncated with ellipsis.
        assert len(stub["preview"]["id"]) <= 257
        assert stub["preview"]["id"].endswith("…")

    def test_omits_nested_dict_value_substitutes_shape(self):
        # Don't preserve a 100-key dict as `preview.id` — it's not
        # scalar; preview.id should NOT be present (we don't include
        # non-scalars even for canonical keys).
        big = {"id": {"nested": "stuff"}}
        stub = materialize_overflow_stub(big, budget=64, size_bytes=999, artifact_id="a")
        # `id` was a non-scalar — _scalar_preview returns "<dict[...]>"
        # which gets stamped as the preview value.
        assert stub["preview"].get("id", "").startswith("<dict[")

    def test_non_dict_output(self):
        # A node returning a list as its output (legal but unusual).
        stub = materialize_overflow_stub(
            ["a", "b", "c"] * 10000,
            budget=64, size_bytes=999, artifact_id="a",
        )
        assert stub["preview"]["__output_type"].startswith("list[")

    def test_extra_scalar_keys_capped(self):
        big = {f"k{i}": f"value-{i}" for i in range(20)}  # all scalars, none canonical
        stub = materialize_overflow_stub(big, budget=64, size_bytes=999, artifact_id="a")
        # `top_level_keys` plus up to 8 extra scalar fields.
        non_meta = {
            k: v for k, v in stub["preview"].items()
            if k not in ("top_level_keys", "__output_type", "__output_repr")
        }
        assert len(non_meta) <= 8


# ---------------------------------------------------------------------------
# maybe_overflow — DB-touching path with mocked session
# ---------------------------------------------------------------------------


class TestMaybeOverflow:
    def test_no_overflow_path_does_not_touch_db(self):
        db = MagicMock()
        in_ctx, meta = maybe_overflow(
            db,
            tenant_id="t-1",
            instance_id=_uuid.uuid4(),
            node_id="node_1",
            node_data={},
            output={"id": "x", "status": "OK"},
        )
        # Output passed through unchanged.
        assert in_ctx == {"id": "x", "status": "OK"}
        # No overflow metadata.
        assert meta is None
        # Hot path: DB never touched.
        assert db.add.call_count == 0
        assert db.flush.call_count == 0

    def test_overflow_path_persists_artifact_and_returns_stub(self):
        db = MagicMock()

        # Capture the artifact row that gets db.add()'d so we can
        # inspect it. Set the row's `id` so persist_artifact's
        # str(artifact.id) produces a stable value.
        captured_artifact: dict[str, Any] = {}
        FAKE_UUID = _uuid.uuid4()

        def _add_side_effect(row):
            row.id = FAKE_UUID
            captured_artifact["row"] = row

        db.add.side_effect = _add_side_effect

        big_output = {"id": "request-42", "status": "FAILED", "blob": "x" * 200_000}
        in_ctx, meta = maybe_overflow(
            db,
            tenant_id="t-1",
            instance_id=_uuid.uuid4(),
            node_id="node_worker",
            node_data={"config": {"contextOutputBudget": 1024}},
            output=big_output,
        )

        # Artifact row was added.
        assert "row" in captured_artifact
        row = captured_artifact["row"]
        assert row.tenant_id == "t-1"
        assert row.node_id == "node_worker"
        assert row.output_json == big_output
        assert row.budget_bytes == 1024
        assert row.size_bytes > 1024  # the actual overflow
        # flush() was called so the artifact id is allocated before we
        # return.
        assert db.flush.call_count == 1

        # In-context value is the stub, not the original.
        assert in_ctx["_overflow"] is True
        assert in_ctx["_artifact_id"] == str(FAKE_UUID)
        assert in_ctx["preview"]["id"] == "request-42"
        assert in_ctx["preview"]["status"] == "FAILED"

        # Overflow metadata is suitable for embedding in ExecutionLog.
        assert meta is not None
        assert meta["_overflow_artifact_id"] == str(FAKE_UUID)
        assert meta["budget_bytes"] == 1024

    def test_per_node_budget_override(self):
        """A node with `contextOutputBudget: 512` overflows on output
        that the default 64 kB budget would have allowed inline."""
        db = MagicMock()
        db.add.side_effect = lambda r: setattr(r, "id", _uuid.uuid4())

        # ~2 kB output — fine at default 64 kB, overflows at 512 B.
        medium = {"id": "x", "data": "a" * 2000}
        # Default budget — no overflow.
        in_ctx, meta = maybe_overflow(
            db,
            tenant_id="t-1",
            instance_id=_uuid.uuid4(),
            node_id="n",
            node_data={},
            output=medium,
        )
        assert meta is None
        # Strict budget — overflow.
        in_ctx, meta = maybe_overflow(
            db,
            tenant_id="t-1",
            instance_id=_uuid.uuid4(),
            node_id="n",
            node_data={"config": {"contextOutputBudget": 512}},
            output=medium,
        )
        assert meta is not None
        assert in_ctx["_overflow"] is True

    def test_hard_ceiling_kicks_in_for_misconfigured_budget(self):
        """A node with `contextOutputBudget: 10_000_000` (silly large)
        is clamped to the 256 kB hard ceiling — a 1 MB output still
        overflows."""
        db = MagicMock()
        db.add.side_effect = lambda r: setattr(r, "id", _uuid.uuid4())

        big = {"id": "x", "data": "a" * 1_000_000}
        in_ctx, meta = maybe_overflow(
            db,
            tenant_id="t-1",
            instance_id=_uuid.uuid4(),
            node_id="n",
            node_data={"config": {"contextOutputBudget": 10_000_000}},
            output=big,
        )
        assert meta is not None
        assert meta["budget_bytes"] == HARD_CEILING_BYTES


# ---------------------------------------------------------------------------
# Round-trip — what's in context after overflow is what's read by Jinja
# ---------------------------------------------------------------------------


class TestStubAsJinjaInput:
    """The whole point of preserving canonical scalar keys in the stub
    is that downstream Jinja templates referencing
    ``{{ node_X.status }}`` still resolve. Confirm the stub shape is
    compatible with the engine's Jinja rendering."""

    def test_jinja_can_read_preserved_status_field(self):
        from app.engine.prompt_template import render_prompt

        big = {"id": "req-1", "status": "FAILED", "blob": "x" * 200_000}
        stub = materialize_overflow_stub(
            big, budget=1024, size_bytes=200_000, artifact_id="art-1",
        )
        ctx = {"node_3": stub}
        # Templates that read from preview fields render correctly —
        # this mirrors how V8/V9/V10 worker prompts read upstream IDs.
        rendered = render_prompt(
            "Status: {{ node_3.preview.status }} (id={{ node_3.preview.id }})",
            ctx,
        )
        assert "Status: FAILED" in rendered
        assert "id=req-1" in rendered

    def test_jinja_can_detect_overflow_marker(self):
        from app.engine.prompt_template import render_prompt

        big = {"id": "req-1", "status": "OK", "huge": "x" * 200_000}
        stub = materialize_overflow_stub(
            big, budget=1024, size_bytes=200_000, artifact_id="art-1",
        )
        rendered = render_prompt(
            "{% if node_3._overflow %}OVERFLOWED ({{ node_3.size_bytes }} bytes){% endif %}",
            {"node_3": stub},
        )
        assert "OVERFLOWED" in rendered
        assert "200000" in rendered or "200,000" in rendered

    def test_unmodified_output_renders_normally(self):
        from app.engine.prompt_template import render_prompt

        ctx = {"node_3": {"id": "req-1", "status": "OK"}}
        rendered = render_prompt("Got {{ node_3.id }}", ctx)
        assert rendered == "Got req-1"
