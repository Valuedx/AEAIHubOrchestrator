"""CTX-MGMT.H v2 — read + miss event tracing.

v1 tracked writes only. v2 adds read and miss events, with
sampling on reads (template renders can hit dozens of attribute
accesses per node) and no sampling on misses (rare + high-signal).

These tests cover three layers:
    - The pure ``record_read`` / ``record_miss`` helpers
    - The ``flush_render_events`` sampling + dedupe pass
    - The ``prompt_template.render_prompt`` capture hook
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.engine import context_trace
from app.engine.context_trace import (
    DEFAULT_READ_SAMPLE_N,
    flush_render_events,
    record_miss,
    record_read,
)
from app.engine.prompt_template import render_prompt


# ---------------------------------------------------------------------------
# record_read / record_miss — disabled fast-path
# ---------------------------------------------------------------------------


class TestDisabledFastPath:
    """When tracing is off the helpers must touch zero DB."""

    def test_record_read_no_db_when_runtime_missing(self):
        db = MagicMock()
        record_read(
            db, {},
            tenant_id="t", instance_id=_uuid.uuid4(),
            node_id="node_1", key="node_history",
        )
        assert db.add.call_count == 0
        assert db.flush.call_count == 0

    def test_record_read_no_db_when_flag_off(self):
        db = MagicMock()
        record_read(
            db, {"_runtime": {"context_trace_enabled": False}},
            tenant_id="t", instance_id=_uuid.uuid4(),
            node_id="node_1", key="node_history",
        )
        assert db.add.call_count == 0

    def test_record_miss_no_db_when_flag_off(self):
        db = MagicMock()
        record_miss(
            db, {"_runtime": {"context_trace_enabled": False}},
            tenant_id="t", instance_id=_uuid.uuid4(),
            node_id="node_1", key="typo_field",
        )
        assert db.add.call_count == 0


# ---------------------------------------------------------------------------
# record_read / record_miss — enabled path inserts row with correct op
# ---------------------------------------------------------------------------


class TestEnabledPath:
    def test_record_read_emits_op_read(self):
        db = MagicMock()
        ctx = {"_runtime": {"context_trace_enabled": True}}
        instance_id = _uuid.uuid4()

        record_read(
            db, ctx,
            tenant_id="t-1", instance_id=instance_id,
            node_id="node_X", key="node_history",
        )

        assert db.add.call_count == 1
        added = db.add.call_args.args[0]
        assert added.op == "read"
        assert added.key == "node_history"
        assert added.node_id == "node_X"
        assert added.tenant_id == "t-1"

    def test_record_miss_emits_op_miss(self):
        db = MagicMock()
        ctx = {"_runtime": {"context_trace_enabled": True}}

        record_miss(
            db, ctx,
            tenant_id="t-1", instance_id=_uuid.uuid4(),
            node_id="node_X", key="typo_field",
        )

        added = db.add.call_args.args[0]
        assert added.op == "miss"
        assert added.key == "typo_field"

    def test_helpers_never_raise_on_db_error(self):
        # Same observability guarantee as record_write.
        db = MagicMock()
        db.add.side_effect = RuntimeError("connection dropped")
        ctx = {"_runtime": {"context_trace_enabled": True}}
        # Neither call should raise.
        record_read(
            db, ctx,
            tenant_id="t", instance_id=_uuid.uuid4(),
            node_id="node_1", key="x",
        )
        record_miss(
            db, ctx,
            tenant_id="t", instance_id=_uuid.uuid4(),
            node_id="node_1", key="y",
        )


# ---------------------------------------------------------------------------
# flush_render_events — sampling + dedupe + always-emit-misses
# ---------------------------------------------------------------------------


class TestFlushSamplingAndDedupe:
    def test_no_op_when_runtime_missing(self):
        db = MagicMock()
        flush_render_events(
            db, {},
            tenant_id="t", instance_id=_uuid.uuid4(), node_id="node_1",
        )
        assert db.add.call_count == 0

    def test_no_op_when_no_pending_events(self):
        db = MagicMock()
        flush_render_events(
            db, {"_runtime": {"context_trace_enabled": True}},
            tenant_id="t", instance_id=_uuid.uuid4(), node_id="node_1",
        )
        assert db.add.call_count == 0

    def test_disabled_drains_buffer_without_emitting(self):
        # If tracing flips off mid-run, the pending list should be
        # drained on the next flush so it doesn't grow unbounded.
        db = MagicMock()
        ctx = {
            "_runtime": {
                "context_trace_enabled": False,
                "_pending_render_events": [
                    ("read", "node_1"),
                    ("miss", "typo"),
                ],
            },
        }
        flush_render_events(
            db, ctx,
            tenant_id="t", instance_id=_uuid.uuid4(), node_id="cur",
        )
        assert db.add.call_count == 0
        assert "_pending_render_events" not in ctx["_runtime"]

    def test_misses_always_emitted(self):
        db = MagicMock()
        ctx = {
            "_runtime": {
                "context_trace_enabled": True,
                "_pending_render_events": [
                    ("miss", "typo_a"),
                    ("miss", "typo_b"),
                    ("miss", "typo_c"),
                ],
            },
        }
        flush_render_events(
            db, ctx,
            tenant_id="t", instance_id=_uuid.uuid4(), node_id="cur",
        )
        # Every miss recorded — never sampled.
        assert db.add.call_count == 3
        ops = [call.args[0].op for call in db.add.call_args_list]
        keys = [call.args[0].key for call in db.add.call_args_list]
        assert ops == ["miss", "miss", "miss"]
        assert keys == ["typo_a", "typo_b", "typo_c"]

    def test_reads_sampled_one_in_n(self):
        db = MagicMock()
        # 10 reads with sample_n=5 → 2 emitted (positions 5 and 10).
        events = [("read", f"node_{i}") for i in range(10)]
        ctx = {
            "_runtime": {
                "context_trace_enabled": True,
                "context_trace_read_sample_n": 5,
                "_pending_render_events": events,
            },
        }
        flush_render_events(
            db, ctx,
            tenant_id="t", instance_id=_uuid.uuid4(), node_id="cur",
        )
        # 2 of 10 reads emitted with sample_n=5.
        assert db.add.call_count == 2

    def test_dedupe_removes_consecutive_duplicates(self):
        db = MagicMock()
        # Same miss appearing 5× consecutively (e.g. Jinja iterating
        # a loop and hitting the same dangling ref each iteration)
        # collapses to one.
        events = [("miss", "typo_field")] * 5 + [("miss", "another")]
        ctx = {
            "_runtime": {
                "context_trace_enabled": True,
                "_pending_render_events": events,
            },
        }
        flush_render_events(
            db, ctx,
            tenant_id="t", instance_id=_uuid.uuid4(), node_id="cur",
        )
        # Two distinct misses recorded.
        assert db.add.call_count == 2
        keys = [call.args[0].key for call in db.add.call_args_list]
        assert keys == ["typo_field", "another"]

    def test_invalid_event_shape_skipped(self):
        db = MagicMock()
        ctx = {
            "_runtime": {
                "context_trace_enabled": True,
                "_pending_render_events": [
                    "not-a-tuple",
                    ("miss", ""),  # empty key — skipped
                    ("unknown_op", "x"),  # unknown op — skipped
                    ("miss", "valid"),  # valid
                ],
            },
        }
        flush_render_events(
            db, ctx,
            tenant_id="t", instance_id=_uuid.uuid4(), node_id="cur",
        )
        assert db.add.call_count == 1
        assert db.add.call_args.args[0].key == "valid"

    def test_sample_counter_persists_across_flushes(self):
        # First flush has 9 reads (none emitted at sample_n=10).
        # Second flush has 1 read — that's the 10th read overall →
        # emitted.
        db = MagicMock()
        ctx = {
            "_runtime": {
                "context_trace_enabled": True,
                "context_trace_read_sample_n": 10,
                "_pending_render_events": [("read", f"slot_{i}") for i in range(9)],
            },
        }
        flush_render_events(
            db, ctx,
            tenant_id="t", instance_id=_uuid.uuid4(), node_id="cur",
        )
        assert db.add.call_count == 0
        # Second flush — counter should be at 9 already.
        ctx["_runtime"]["_pending_render_events"] = [("read", "slot_9")]
        flush_render_events(
            db, ctx,
            tenant_id="t", instance_id=_uuid.uuid4(), node_id="cur",
        )
        assert db.add.call_count == 1


# ---------------------------------------------------------------------------
# prompt_template.render_prompt capture hook
# ---------------------------------------------------------------------------


class TestRenderPromptCapture:
    """Confirm render_prompt populates _runtime['_pending_render_events']
    when tracing is enabled. The events are then flushed by the runner
    after each node completes."""

    def test_no_capture_when_tracing_disabled(self):
        # Hot path: zero overhead when off. _pending_render_events
        # must not appear on _runtime.
        ctx = {
            "_runtime": {"context_trace_enabled": False},
            "node_1": {"foo": "bar"},
        }
        render_prompt("Hello {{ node_1.foo }}", ctx)
        assert "_pending_render_events" not in ctx["_runtime"]

    def test_no_capture_when_runtime_missing(self):
        # Defensive — context with no _runtime should still render
        # cleanly, just no capture.
        ctx = {"node_1": {"foo": "bar"}}
        out = render_prompt("Hello {{ node_1.foo }}", ctx)
        assert "Hello bar" in out

    def test_capture_records_top_level_read(self):
        ctx = {
            "_runtime": {"context_trace_enabled": True},
            "node_1": {"foo": "bar"},
        }
        render_prompt("Hello {{ node_1.foo }}", ctx)
        events = ctx["_runtime"].get("_pending_render_events") or []
        # node_1 was read; foo was the read leaf — both are captured.
        keys_read = {key for op, key in events if op == "read"}
        assert "foo" in keys_read

    def test_capture_records_top_level_miss(self):
        # {{ unknown_root }} — Jinja can't find unknown_root in the
        # namespace. Our _PermissiveUndefined captures the miss with
        # its name on construction.
        ctx = {
            "_runtime": {"context_trace_enabled": True},
        }
        render_prompt("Hello {{ unknown_root }}", ctx)
        events = ctx["_runtime"].get("_pending_render_events") or []
        miss_keys = {key for op, key in events if op == "miss"}
        assert "unknown_root" in miss_keys

    def test_capture_records_attr_miss_on_existing_dict(self):
        # node_1 exists, .typo doesn't — _DotDict catches the miss.
        ctx = {
            "_runtime": {"context_trace_enabled": True},
            "node_1": {"foo": "bar"},
        }
        render_prompt("{{ node_1.typo }}", ctx)
        events = ctx["_runtime"].get("_pending_render_events") or []
        miss_keys = {key for op, key in events if op == "miss"}
        assert "typo" in miss_keys

    def test_capture_records_chained_miss_on_undefined(self):
        # {{ unknown_root.deep_field }} — first the top-level miss,
        # then the chained miss on the resulting _PermissiveUndefined.
        ctx = {
            "_runtime": {"context_trace_enabled": True},
        }
        render_prompt("{{ unknown_root.deep_field }}", ctx)
        events = ctx["_runtime"].get("_pending_render_events") or []
        miss_keys = {key for op, key in events if op == "miss"}
        assert "unknown_root" in miss_keys
        assert "deep_field" in miss_keys

    def test_existing_pending_events_preserved(self):
        # If render_prompt is called twice in the same node (e.g. one
        # for system prompt, one for distill), both renders' events
        # accumulate.
        ctx = {
            "_runtime": {"context_trace_enabled": True},
            "node_1": {"foo": "bar"},
            "node_2": {"baz": "qux"},
        }
        render_prompt("{{ node_1.foo }}", ctx)
        render_prompt("{{ node_2.baz }}", ctx)
        events = ctx["_runtime"].get("_pending_render_events") or []
        keys_read = [key for op, key in events if op == "read"]
        # Both renders' reads accumulate.
        assert "foo" in keys_read
        assert "baz" in keys_read

    def test_thread_local_does_not_leak_to_unrelated_dicts(self):
        # _DotDict access OUTSIDE a render must NOT capture (no
        # thread-local pending list). Verifies the cleanup in the
        # finally branch.
        from app.engine.prompt_template import _DotDict, _render_state

        # Run a render to set up + tear down state.
        ctx = {
            "_runtime": {"context_trace_enabled": True},
            "node_1": {"foo": "bar"},
        }
        render_prompt("{{ node_1.foo }}", ctx)

        # After render, the thread-local should be cleared.
        assert getattr(_render_state, "pending", None) is None

        # And further accesses (e.g. via build_structured_context_block
        # or test access) must not capture.
        d = _DotDict({"a": 1})
        _ = d.a  # no error, no capture (pending is None)
        assert getattr(_render_state, "pending", None) is None


# ---------------------------------------------------------------------------
# DEFAULT_READ_SAMPLE_N — sanity bounds
# ---------------------------------------------------------------------------


class TestSampleConstants:
    def test_default_sample_n_is_positive(self):
        assert DEFAULT_READ_SAMPLE_N >= 1

    def test_invalid_sample_n_falls_back_to_one(self):
        # sample_n=0 would divmod by zero in naive code; the fallback
        # forces it to 1 (every read emitted).
        db = MagicMock()
        ctx = {
            "_runtime": {
                "context_trace_enabled": True,
                "context_trace_read_sample_n": 0,
                "_pending_render_events": [("read", "node_a"), ("read", "node_b")],
            },
        }
        flush_render_events(
            db, ctx,
            tenant_id="t", instance_id=_uuid.uuid4(), node_id="cur",
        )
        # Both reads emitted (effectively sample_n=1).
        assert db.add.call_count == 2
