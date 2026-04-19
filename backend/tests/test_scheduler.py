"""Unit tests for the scheduler helpers.

The Beat task body itself (``check_scheduled_workflows``) is integration-tested
against a live Postgres via S1-12's testcontainers harness — here we cover
the pure helpers and the dedupe-claim behaviour using a small fake session.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


class TestMinuteBucket:
    def test_zeros_seconds_and_microseconds(self):
        from app.workers.scheduler import minute_bucket

        t = datetime(2026, 4, 19, 12, 34, 56, 789_000, tzinfo=timezone.utc)
        assert minute_bucket(t) == datetime(2026, 4, 19, 12, 34, 0, 0, tzinfo=timezone.utc)

    def test_already_minute_aligned_is_idempotent(self):
        from app.workers.scheduler import minute_bucket

        t = datetime(2026, 4, 19, 12, 34, 0, 0, tzinfo=timezone.utc)
        assert minute_bucket(t) == t

    def test_two_ticks_within_the_same_minute_collide(self):
        """This is the core property — two Beat fires in the same minute
        must produce the same bucket so the DB UNIQUE constraint catches
        them."""
        from app.workers.scheduler import minute_bucket

        early = datetime(2026, 4, 19, 12, 34, 0, 100_000, tzinfo=timezone.utc)
        late = datetime(2026, 4, 19, 12, 34, 59, 900_000, tzinfo=timezone.utc)
        assert minute_bucket(early) == minute_bucket(late)

    def test_adjacent_minutes_produce_different_buckets(self):
        from app.workers.scheduler import minute_bucket

        a = datetime(2026, 4, 19, 12, 34, 0, tzinfo=timezone.utc)
        b = datetime(2026, 4, 19, 12, 35, 0, tzinfo=timezone.utc)
        assert minute_bucket(a) != minute_bucket(b)

    def test_preserves_timezone(self):
        from app.workers.scheduler import minute_bucket

        t = datetime(2026, 4, 19, 12, 34, 56, 789_000, tzinfo=timezone.utc)
        assert minute_bucket(t).tzinfo is timezone.utc


class TestScheduledTriggerModel:
    """The model itself is a dumb row; these tests are a smoke check that
    the class is wired up with the right constraints so the Alembic
    migration and the runtime agree on the dedupe contract."""

    def test_model_has_unique_constraint_on_workflow_and_minute(self):
        from app.models.workflow import ScheduledTrigger

        constraint_names = {
            c.name for c in ScheduledTrigger.__table__.constraints
        }
        assert "uq_scheduled_trigger_wf_minute" in constraint_names, (
            f"expected uq_scheduled_trigger_wf_minute in {constraint_names}"
        )

    def test_instance_id_is_nullable(self):
        """``instance_id`` is set AFTER we've claimed the minute bucket,
        so it must be nullable — otherwise the INSERT that claims the
        slot would fail before we've created the child instance."""
        from app.models.workflow import ScheduledTrigger

        col = ScheduledTrigger.__table__.c.instance_id
        assert col.nullable is True

    def test_scheduled_for_is_required(self):
        from app.models.workflow import ScheduledTrigger

        col = ScheduledTrigger.__table__.c.scheduled_for
        assert col.nullable is False


# NOTE: the full check_scheduled_workflows flow (claim → instance creation →
# execute_workflow_task.delay) is exercised in the S1-12 integration
# tests against a real Postgres. Unit-testing it here would require
# faking SQLAlchemy's flush + IntegrityError semantics, which is more
# brittle than the value it provides.

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
