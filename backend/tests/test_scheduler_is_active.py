"""DV-07 — Schedule Trigger respects the is_active flag.

``check_scheduled_workflows`` must never fire a Schedule Trigger on an
inactive workflow. The full Beat flow is integration-tested against a
real Postgres; here we verify the DB filter is present by inspecting
the query the task runs via a MagicMock session.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestIsActiveFilter:
    def test_scheduler_filters_out_inactive_workflows(self):
        """The task calls ``db.query(WorkflowDefinition).filter(...).all()``.
        We record the filter argument and confirm it references the
        ``is_active`` column."""
        from app.workers import scheduler

        # MagicMock session whose query chain records what was passed to
        # .filter() and returns an empty list of workflows (so the task
        # short-circuits immediately).
        session = MagicMock()
        filter_args: list = []

        def _filter(*args, **kwargs):
            filter_args.extend(args)
            q = MagicMock()
            q.all.return_value = []
            return q

        session.query.return_value.filter.side_effect = _filter

        with patch.object(scheduler, "SessionLocal", return_value=session):
            scheduler.check_scheduled_workflows()

        assert filter_args, "scheduler did not apply a DB-level filter on workflows"
        # At least one of the filter args must reference is_active.
        rendered = [str(arg) for arg in filter_args]
        assert any("is_active" in r for r in rendered), (
            f"expected is_active in filter clauses, got: {rendered}"
        )


class TestModelColumn:
    def test_is_active_column_exists_with_default_true(self):
        from app.models.workflow import WorkflowDefinition

        col = WorkflowDefinition.__table__.c.is_active
        assert col is not None
        assert col.nullable is False
        # server_default keeps existing rows active after the 0018 upgrade.
        assert col.server_default is not None
        assert "TRUE" in str(col.server_default.arg).upper()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
