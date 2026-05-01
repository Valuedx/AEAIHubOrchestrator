"""CTX-MGMT.M2 — Beat-task wiring tests for prune_aged_checkpoints.

The pure helper is exercised by test_forgetting.py. These tests
cover the Celery Beat task body — multi-tenant enumeration,
per-tenant retention policy, and resilience to per-tenant failure.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Beat schedule registration
# ---------------------------------------------------------------------------


class TestBeatSchedule:
    def test_prune_aged_checkpoints_in_schedule(self):
        # Confirm the new task is registered on the Beat schedule.
        # Importing scheduler runs the module-level
        # `celery_app.conf.beat_schedule = {...}` assignment.
        import app.workers.scheduler  # noqa: F401
        from app.workers.celery_app import celery_app

        schedule = celery_app.conf.beat_schedule
        assert "prune-aged-checkpoints" in schedule
        entry = schedule["prune-aged-checkpoints"]
        assert entry["task"] == "orchestrator.prune_aged_checkpoints"
        # crontab schedule object — just confirm it's not the
        # interval-style float pattern (which wouldn't be daily).
        from celery.schedules import crontab
        assert isinstance(entry["schedule"], crontab)


# ---------------------------------------------------------------------------
# Task body — multi-tenant sweep
# ---------------------------------------------------------------------------


class TestPruneTask:
    def _setup_db_with_tenants(self, tenant_ids: list[str]):
        """Mock SessionLocal so the task sees a synthetic set of
        tenants with checkpoints."""
        db = MagicMock()
        # tenant_rows is db.query(distinct(...)).join(...).all()
        chain = MagicMock()
        chain.join.return_value = chain
        # Each row is a tuple (tenant_id,) per SQLAlchemy distinct().
        chain.all.return_value = [(tid,) for tid in tenant_ids]
        db.query.return_value = chain
        return db

    def test_sweeps_each_tenant_separately(self):
        from app.workers.scheduler import prune_aged_checkpoints_task

        db = self._setup_db_with_tenants(["t-1", "t-2", "t-3"])

        per_tenant_calls: list[str] = []

        def _fake_prune(_db, *, tenant_id, older_than_days=None):
            per_tenant_calls.append(tenant_id)
            return 5  # Pretend each tenant had 5 expired checkpoints.

        with patch("app.workers.scheduler.SessionLocal", return_value=db), \
             patch(
                "app.engine.forgetting.prune_aged_checkpoints",
                side_effect=_fake_prune,
             ):
            prune_aged_checkpoints_task()

        # Each tenant got a sweep call.
        assert sorted(per_tenant_calls) == ["t-1", "t-2", "t-3"]
        # SessionLocal was closed.
        assert db.close.call_count == 1

    def test_continues_on_per_tenant_failure(self):
        # If one tenant's policy lookup or delete fails, the Beat
        # task continues with the rest — never breaks the daily
        # sweep for everyone else.
        from app.workers.scheduler import prune_aged_checkpoints_task

        db = self._setup_db_with_tenants(["t-good-1", "t-bad", "t-good-2"])

        def _fake_prune(_db, *, tenant_id, older_than_days=None):
            if tenant_id == "t-bad":
                raise RuntimeError("policy DB hiccup")
            return 3

        with patch("app.workers.scheduler.SessionLocal", return_value=db), \
             patch(
                "app.engine.forgetting.prune_aged_checkpoints",
                side_effect=_fake_prune,
             ):
            # Should NOT raise — the failure is logged + rolled back.
            prune_aged_checkpoints_task()

        # Rolled back at least once (after t-bad).
        assert db.rollback.call_count >= 1

    def test_no_tenants_no_op(self):
        from app.workers.scheduler import prune_aged_checkpoints_task

        db = self._setup_db_with_tenants([])
        with patch("app.workers.scheduler.SessionLocal", return_value=db), \
             patch(
                "app.engine.forgetting.prune_aged_checkpoints",
                side_effect=AssertionError("must not be called for empty tenant set"),
             ):
            prune_aged_checkpoints_task()

    def test_top_level_failure_handled(self):
        # If the tenant enumeration itself fails (rare — DB outage),
        # the task logs and exits without raising.
        from app.workers.scheduler import prune_aged_checkpoints_task

        db = MagicMock()
        db.query.side_effect = RuntimeError("DB unavailable")

        with patch("app.workers.scheduler.SessionLocal", return_value=db):
            # Must not raise.
            prune_aged_checkpoints_task()

        # Rollback called as part of the except path.
        assert db.rollback.call_count >= 1
        assert db.close.call_count == 1
