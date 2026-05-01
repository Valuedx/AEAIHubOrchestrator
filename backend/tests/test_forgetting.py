"""CTX-MGMT.M — unit tests for forgetting / decay helpers.

Two surfaces:
  1. ``clear_non_retained_outputs`` — pure helper that pops node
     slots from context when the node's config opts out of
     retention via ``retainOutputAfterRun: false``.
  2. ``prune_aged_checkpoints`` — operator utility for retention
     sweep on `InstanceCheckpoint` rows. DB-touching; tests use
     mock sessions.
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.engine.forgetting import (
    SYSTEM_DEFAULT_RETENTION_DAYS,
    clear_non_retained_outputs,
    prune_aged_checkpoints,
    resolve_checkpoint_retention_days,
)


# ---------------------------------------------------------------------------
# clear_non_retained_outputs
# ---------------------------------------------------------------------------


def _node(node_id: str, *, retain: Any = None,
          alias: str | None = None) -> dict[str, Any]:
    config: dict[str, Any] = {}
    if retain is not None:
        config["retainOutputAfterRun"] = retain
    if alias:
        config["exposeAs"] = alias
    return {
        "id": node_id,
        "data": {"label": "LLM Agent", "config": config},
    }


class TestClearNonRetained:
    def test_default_retains_everything(self):
        # No flag set on any node — context untouched.
        nodes_map = {"node_1": _node("node_1"), "node_2": _node("node_2")}
        ctx = {"node_1": {"x": 1}, "node_2": {"y": 2}, "trigger": {}}
        cleared = clear_non_retained_outputs(ctx, nodes_map)
        assert cleared == []
        assert ctx["node_1"] == {"x": 1}
        assert ctx["node_2"] == {"y": 2}

    def test_explicit_true_retains(self):
        nodes_map = {"node_1": _node("node_1", retain=True)}
        ctx = {"node_1": {"x": 1}}
        cleared = clear_non_retained_outputs(ctx, nodes_map)
        assert cleared == []
        assert "node_1" in ctx

    def test_explicit_false_clears(self):
        nodes_map = {"node_1": _node("node_1", retain=False)}
        ctx = {"node_1": {"x": 1}, "trigger": {}}
        cleared = clear_non_retained_outputs(ctx, nodes_map)
        assert "node_1" in cleared
        assert "node_1" not in ctx
        # Trigger preserved (only node slots are touched).
        assert "trigger" in ctx

    def test_clears_alias_too(self):
        nodes_map = {"node_1": _node("node_1", retain=False, alias="case")}
        ctx = {"node_1": {"x": 1}, "case": {"x": 1}}
        cleared = clear_non_retained_outputs(ctx, nodes_map)
        assert "node_1" not in ctx
        assert "case" not in ctx
        # Both reported.
        assert "node_1" in cleared
        assert any("case" in c for c in cleared)

    def test_skip_when_slot_already_absent(self):
        # If the node's slot was already popped (e.g. by overflow
        # path or never written), clear is a no-op.
        nodes_map = {"node_1": _node("node_1", retain=False)}
        ctx = {"trigger": {}}  # no node_1
        cleared = clear_non_retained_outputs(ctx, nodes_map)
        assert cleared == []

    def test_mixed_retain_and_clear(self):
        nodes_map = {
            "node_1": _node("node_1"),  # default retain
            "node_2": _node("node_2", retain=False),
            "node_3": _node("node_3", retain=True),
            "node_4": _node("node_4", retain=False, alias="result"),
        }
        ctx = {
            "node_1": {"x": 1},
            "node_2": {"y": 2},
            "node_3": {"z": 3},
            "node_4": {"w": 4},
            "result": {"w": 4},
            "trigger": {},
        }
        cleared = clear_non_retained_outputs(ctx, nodes_map)
        # node_2 + node_4 cleared; node_1 + node_3 + trigger + result kept.
        assert "node_1" in ctx
        assert "node_2" not in ctx
        assert "node_3" in ctx
        assert "node_4" not in ctx
        assert "result" not in ctx
        assert "trigger" in ctx


# ---------------------------------------------------------------------------
# resolve_checkpoint_retention_days
# ---------------------------------------------------------------------------


class TestResolveRetention:
    def test_default_when_unset(self):
        db = MagicMock()
        fake_policy = MagicMock(spec=[])  # no checkpoint_retention_days attr
        with patch(
            "app.engine.tenant_policy_resolver.get_effective_policy",
            return_value=fake_policy,
        ):
            assert resolve_checkpoint_retention_days(db, tenant_id="t") == SYSTEM_DEFAULT_RETENTION_DAYS

    def test_explicit_override(self):
        db = MagicMock()
        fake_policy = MagicMock()
        fake_policy.checkpoint_retention_days = 7
        with patch(
            "app.engine.tenant_policy_resolver.get_effective_policy",
            return_value=fake_policy,
        ):
            assert resolve_checkpoint_retention_days(db, tenant_id="t") == 7

    def test_zero_falls_back_to_default(self):
        # Defensive — never accept 0 (would delete everything).
        db = MagicMock()
        fake_policy = MagicMock()
        fake_policy.checkpoint_retention_days = 0
        with patch(
            "app.engine.tenant_policy_resolver.get_effective_policy",
            return_value=fake_policy,
        ):
            assert resolve_checkpoint_retention_days(db, tenant_id="t") == SYSTEM_DEFAULT_RETENTION_DAYS

    def test_negative_falls_back_to_default(self):
        db = MagicMock()
        fake_policy = MagicMock()
        fake_policy.checkpoint_retention_days = -5
        with patch(
            "app.engine.tenant_policy_resolver.get_effective_policy",
            return_value=fake_policy,
        ):
            assert resolve_checkpoint_retention_days(db, tenant_id="t") == SYSTEM_DEFAULT_RETENTION_DAYS

    def test_lookup_error_falls_back_to_default(self):
        db = MagicMock()
        with patch(
            "app.engine.tenant_policy_resolver.get_effective_policy",
            side_effect=RuntimeError("policy DB hiccup"),
        ):
            assert resolve_checkpoint_retention_days(db, tenant_id="t") == SYSTEM_DEFAULT_RETENTION_DAYS


# ---------------------------------------------------------------------------
# prune_aged_checkpoints
# ---------------------------------------------------------------------------


class TestPruneAged:
    def test_zero_threshold_refuses_delete(self):
        # Defensive: refuse to wipe everything even on operator
        # override.
        db = MagicMock()
        deleted = prune_aged_checkpoints(db, older_than_days=0)
        assert deleted == 0
        # No delete query issued.
        assert db.commit.call_count == 0

    def test_negative_threshold_refuses_delete(self):
        db = MagicMock()
        deleted = prune_aged_checkpoints(db, older_than_days=-7)
        assert deleted == 0

    def test_no_rows_to_delete_no_commit(self):
        # When the query returns an empty list, no commit fires.
        db = MagicMock()
        # Setup the query chain: filter + with_entities + all → []
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.with_entities.return_value.all.return_value = []
        db.query.return_value = chain

        deleted = prune_aged_checkpoints(db, older_than_days=30)
        assert deleted == 0
        assert db.commit.call_count == 0

    def test_uses_system_default_when_no_args(self):
        # When neither tenant_id nor older_than_days is set, falls
        # through to SYSTEM_DEFAULT_RETENTION_DAYS.
        db = MagicMock()
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.with_entities.return_value.all.return_value = []
        db.query.return_value = chain

        deleted = prune_aged_checkpoints(db)
        assert deleted == 0  # nothing to delete in test
        # But we did issue a query against the threshold.
        assert chain.filter.call_count >= 1

    def test_tenant_id_uses_tenant_policy_retention(self):
        # When tenant_id is set, retention comes from the tenant
        # policy's `checkpoint_retention_days` (or system default).
        db = MagicMock()
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.join.return_value.filter.return_value = chain
        chain.with_entities.return_value.all.return_value = []
        db.query.return_value = chain

        fake_policy = MagicMock()
        fake_policy.checkpoint_retention_days = 14
        with patch(
            "app.engine.tenant_policy_resolver.get_effective_policy",
            return_value=fake_policy,
        ):
            prune_aged_checkpoints(db, tenant_id="t-1")

        # Query joined through WorkflowInstance for tenant filtering.
        assert chain.join.call_count >= 1
