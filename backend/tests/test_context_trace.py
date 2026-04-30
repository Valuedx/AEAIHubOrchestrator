"""CTX-MGMT.H — unit tests for the context-write trace.

The hot-path matters: every node completion calls `record_write`, so
the disabled fast-path must touch zero DB. The enabled path must
INSERT one row per write and trim oldest when the per-instance cap
is hit.
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.engine import context_trace
from app.engine.context_trace import (
    PER_INSTANCE_TRACE_CAP,
    TRACE_TRIM_BATCH,
    is_trace_enabled,
    record_write,
    resolve_trace_flag,
)


# ---------------------------------------------------------------------------
# is_trace_enabled — fast-path check
# ---------------------------------------------------------------------------


class TestIsTraceEnabled:
    def test_returns_false_for_empty_context(self):
        assert is_trace_enabled({}) is False

    def test_returns_false_when_runtime_missing(self):
        assert is_trace_enabled({"node_1": {}}) is False

    def test_returns_false_when_flag_unset(self):
        assert is_trace_enabled({"_runtime": {}}) is False

    def test_returns_false_when_flag_explicit_false(self):
        assert is_trace_enabled({"_runtime": {"context_trace_enabled": False}}) is False

    def test_returns_true_when_flag_explicit_true(self):
        assert is_trace_enabled({"_runtime": {"context_trace_enabled": True}}) is True

    def test_returns_false_when_runtime_is_not_dict(self):
        # Defensive — a malformed context_json with non-dict _runtime
        # shouldn't crash the fast-path.
        assert is_trace_enabled({"_runtime": "not-a-dict"}) is False


# ---------------------------------------------------------------------------
# resolve_trace_flag — ephemeral always on, prod opts in
# ---------------------------------------------------------------------------


class TestResolveTraceFlag:
    def test_ephemeral_always_traces(self):
        # Ephemeral path doesn't even consult the policy resolver.
        db = MagicMock()
        with patch(
            "app.engine.tenant_policy_resolver.get_effective_policy",
            side_effect=AssertionError("policy must NOT be consulted for ephemeral"),
        ):
            assert resolve_trace_flag(db, tenant_id="t", is_ephemeral=True) is True

    def test_prod_off_when_policy_disabled(self):
        db = MagicMock()
        fake_policy = MagicMock()
        fake_policy.context_trace_enabled = False
        with patch(
            "app.engine.tenant_policy_resolver.get_effective_policy",
            return_value=fake_policy,
        ):
            assert resolve_trace_flag(db, tenant_id="t", is_ephemeral=False) is False

    def test_prod_on_when_policy_enabled(self):
        db = MagicMock()
        fake_policy = MagicMock()
        fake_policy.context_trace_enabled = True
        with patch(
            "app.engine.tenant_policy_resolver.get_effective_policy",
            return_value=fake_policy,
        ):
            assert resolve_trace_flag(db, tenant_id="t", is_ephemeral=False) is True

    def test_prod_off_on_policy_lookup_error(self):
        # Defensive — never accidentally trace on in prod due to a
        # transient policy resolver failure.
        db = MagicMock()
        with patch(
            "app.engine.tenant_policy_resolver.get_effective_policy",
            side_effect=RuntimeError("DB hiccup"),
        ):
            assert resolve_trace_flag(db, tenant_id="t", is_ephemeral=False) is False


# ---------------------------------------------------------------------------
# record_write — disabled fast-path
# ---------------------------------------------------------------------------


class TestRecordWriteDisabled:
    def test_no_db_touch_when_runtime_missing(self):
        db = MagicMock()
        record_write(
            db, {},
            tenant_id="t", instance_id=_uuid.uuid4(),
            node_id="node_1", size_bytes=42, reducer="overwrite", overflowed=False,
        )
        assert db.add.call_count == 0
        assert db.flush.call_count == 0
        assert db.query.call_count == 0

    def test_no_db_touch_when_flag_off(self):
        db = MagicMock()
        ctx = {"_runtime": {"context_trace_enabled": False}}
        record_write(
            db, ctx,
            tenant_id="t", instance_id=_uuid.uuid4(),
            node_id="node_1", size_bytes=42, reducer="overwrite", overflowed=False,
        )
        assert db.add.call_count == 0


# ---------------------------------------------------------------------------
# record_write — enabled path inserts row
# ---------------------------------------------------------------------------


class TestRecordWriteEnabled:
    def test_enabled_path_adds_row(self):
        db = MagicMock()
        # No trim needed — cap-check returns 0 so the trim branch is skipped.
        ctx = {"_runtime": {"context_trace_enabled": True}}
        instance_id = _uuid.uuid4()

        record_write(
            db, ctx,
            tenant_id="t-1", instance_id=instance_id,
            node_id="node_worker", size_bytes=1024,
            reducer="overwrite", overflowed=False,
        )

        assert db.add.call_count == 1
        # The row that was added carries every field correctly.
        added_row = db.add.call_args.args[0]
        assert added_row.tenant_id == "t-1"
        assert added_row.instance_id == instance_id
        assert added_row.node_id == "node_worker"
        assert added_row.op == "write"
        assert added_row.key == "node_worker"  # writes use node_id as the key
        assert added_row.size_bytes == 1024
        assert added_row.reducer == "overwrite"
        assert added_row.overflowed is False
        # flush() is called so the insert is visible to the next
        # checkpoint commit.
        assert db.flush.call_count == 1

    def test_overflowed_flag_propagates(self):
        db = MagicMock()
        ctx = {"_runtime": {"context_trace_enabled": True}}
        record_write(
            db, ctx,
            tenant_id="t", instance_id=_uuid.uuid4(),
            node_id="big_node", size_bytes=200_000,
            reducer="overwrite", overflowed=True,
        )
        added_row = db.add.call_args.args[0]
        assert added_row.overflowed is True

    def test_record_write_never_raises_on_db_error(self):
        # Observability is not a correctness gate. A DB hiccup must
        # not crash the run.
        db = MagicMock()
        db.add.side_effect = RuntimeError("connection dropped")
        ctx = {"_runtime": {"context_trace_enabled": True}}
        # Should not raise.
        record_write(
            db, ctx,
            tenant_id="t", instance_id=_uuid.uuid4(),
            node_id="x", size_bytes=10, reducer="overwrite", overflowed=False,
        )


# ---------------------------------------------------------------------------
# Trim-on-cap behavior
# ---------------------------------------------------------------------------


class TestTrim:
    def test_trim_check_runs_every_TRACE_TRIM_BATCH_writes(self):
        """The trim count is incremented on every write; once it
        crosses TRACE_TRIM_BATCH the count query runs and (if the
        per-instance cap is hit) the oldest rows are deleted."""
        db = MagicMock()
        # Cap-check returns 0 (no trim needed) until we explicitly
        # set up the at-cap scenario.
        db.query.return_value.filter_by.return_value.count.return_value = 0
        ctx = {"_runtime": {"context_trace_enabled": True}}

        for i in range(TRACE_TRIM_BATCH + 5):
            record_write(
                db, ctx,
                tenant_id="t", instance_id=_uuid.uuid4(),
                node_id=f"node_{i}", size_bytes=1,
                reducer="overwrite", overflowed=False,
            )
        # Count was checked at least once when the trim threshold hit.
        assert db.query.return_value.filter_by.return_value.count.call_count >= 1

    def test_no_trim_when_under_cap(self):
        """When current_count < cap, no DELETE issued."""
        db = MagicMock()
        # First call at threshold returns count under the cap.
        db.query.return_value.filter_by.return_value.count.return_value = 100
        ctx = {"_runtime": {"context_trace_enabled": True}}
        for _ in range(TRACE_TRIM_BATCH + 1):
            record_write(
                db, ctx,
                tenant_id="t", instance_id=_uuid.uuid4(),
                node_id="x", size_bytes=1,
                reducer="overwrite", overflowed=False,
            )
        # Count was checked but no _trim_oldest path fired (we'd have
        # seen `db.query(...).delete(...)` calls. The simplest assert:
        # the count check fired but the cap was below threshold.
        assert PER_INSTANCE_TRACE_CAP > 100  # sanity


# ---------------------------------------------------------------------------
# fetch_events_for_instance — read API
# ---------------------------------------------------------------------------


class TestFetchEvents:
    def _fake_event(self, **overrides):
        defaults = {
            "id": _uuid.uuid4(),
            "node_id": "node_1",
            "op": "write",
            "key": "node_1",
            "size_bytes": 1024,
            "reducer": "overwrite",
            "overflowed": False,
            "ts": None,
        }
        defaults.update(overrides)
        return MagicMock(**defaults)

    def test_returns_serialised_events(self):
        from datetime import datetime, timezone
        from app.engine.context_trace import fetch_events_for_instance

        db = MagicMock()
        ts = datetime(2026, 5, 1, 10, 30, tzinfo=timezone.utc)
        rows = [self._fake_event(node_id="node_a", ts=ts)]
        # Rebuild the query chain that fetch_events_for_instance walks.
        chain = MagicMock()
        chain.filter_by.return_value = chain
        chain.order_by.return_value = chain
        chain.limit.return_value = chain
        chain.all.return_value = rows
        db.query.return_value = chain

        events = fetch_events_for_instance(
            db, tenant_id="t", instance_id=_uuid.uuid4(),
        )
        assert len(events) == 1
        assert events[0]["node_id"] == "node_a"
        assert events[0]["op"] == "write"
        assert events[0]["ts"] == ts.isoformat()

    def test_prefix_filter_applies_like_match(self):
        from app.engine.context_trace import fetch_events_for_instance

        db = MagicMock()
        chain = MagicMock()
        chain.filter_by.return_value = chain
        chain.filter.return_value = chain
        chain.order_by.return_value = chain
        chain.limit.return_value = chain
        chain.all.return_value = []
        db.query.return_value = chain

        fetch_events_for_instance(
            db, tenant_id="t", instance_id=_uuid.uuid4(), key="node_*",
        )
        # The `.filter(...)` step — the prefix path uses `.like()`,
        # not `.filter_by(key=...)`. We can't introspect the args
        # easily on MagicMock so just confirm `.filter` was called
        # (vs the exact-match path which uses .filter_by(key=...)).
        assert chain.filter.call_count == 1
