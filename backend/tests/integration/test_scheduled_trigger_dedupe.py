"""End-to-end test for the scheduled_triggers UNIQUE-constraint dedupe.

Proves that two concurrent inserts targeting the same (workflow_def_id,
scheduled_for) minute bucket result in exactly one success and one
IntegrityError — the Postgres-level guarantee that the unit tests in
test_scheduler.py can only assert at the ORM-model level.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.workflow import ScheduledTrigger, WorkflowDefinition


def _insert_workflow(app_session, tenant_id: str = "tenant-a") -> uuid.UUID:
    app_session.execute(text("SELECT set_tenant_id(:t)"), {"t": tenant_id})
    wf_id = uuid.uuid4()
    app_session.execute(
        text(
            """
            INSERT INTO workflow_definitions
                (id, tenant_id, name, graph_json, version, is_published, created_at, updated_at)
            VALUES
                (:id, :tenant, 'schedule-test', '{"nodes":[],"edges":[]}', 1, false,
                 now(), now())
            """
        ),
        {"id": wf_id, "tenant": tenant_id},
    )
    app_session.commit()
    return wf_id


def test_two_claims_for_same_minute_collide(app_session):
    wf_id = _insert_workflow(app_session)
    slot = datetime(2026, 4, 19, 12, 34, 0, tzinfo=timezone.utc)

    first = ScheduledTrigger(workflow_def_id=wf_id, scheduled_for=slot)
    app_session.add(first)
    app_session.commit()

    second = ScheduledTrigger(workflow_def_id=wf_id, scheduled_for=slot)
    app_session.add(second)
    with pytest.raises(IntegrityError):
        app_session.commit()
    app_session.rollback()


def test_claims_for_different_minutes_both_succeed(app_session):
    wf_id = _insert_workflow(app_session)
    slot_a = datetime(2026, 4, 19, 12, 34, 0, tzinfo=timezone.utc)
    slot_b = datetime(2026, 4, 19, 12, 35, 0, tzinfo=timezone.utc)

    app_session.add(ScheduledTrigger(workflow_def_id=wf_id, scheduled_for=slot_a))
    app_session.add(ScheduledTrigger(workflow_def_id=wf_id, scheduled_for=slot_b))
    app_session.commit()  # no IntegrityError


def test_claims_for_different_workflows_in_same_minute_both_succeed(app_session):
    wf1 = _insert_workflow(app_session, "tenant-a")
    wf2 = _insert_workflow(app_session, "tenant-a")
    slot = datetime(2026, 4, 19, 12, 34, 0, tzinfo=timezone.utc)

    app_session.add(ScheduledTrigger(workflow_def_id=wf1, scheduled_for=slot))
    app_session.add(ScheduledTrigger(workflow_def_id=wf2, scheduled_for=slot))
    app_session.commit()  # no IntegrityError
