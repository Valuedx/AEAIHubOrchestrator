"""Unit tests for GET /api/v1/workflows/{wf}/instances/{iid}/async-jobs.

The endpoint is a thin tenant-scoped read over the ``async_jobs``
table. Uses FastAPI dependency overrides + a MagicMock session so no
DB is required — end-to-end RLS coverage lives in AE-08's
testcontainers suite.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.workflows import router as workflows_router


TENANT = "tenant-a"


@pytest.fixture
def client_and_session():
    app = FastAPI()
    app.include_router(workflows_router)

    from app.database import get_db, get_tenant_db
    from app.security.tenant import get_tenant_id

    session = MagicMock()

    def _fake_get_db():
        yield session

    app.dependency_overrides[get_tenant_id] = lambda: TENANT
    app.dependency_overrides[get_db] = _fake_get_db
    app.dependency_overrides[get_tenant_db] = _fake_get_db

    return TestClient(app), session


def _mock_job(**overrides):
    row = MagicMock()
    row.id = overrides.get("id", uuid.uuid4())
    row.instance_id = overrides.get("instance_id", uuid.uuid4())
    row.node_id = overrides.get("node_id", "node_3")
    row.system = overrides.get("system", "automationedge")
    row.external_job_id = overrides.get("external_job_id", "42")
    row.status = overrides.get("status", "running")
    row.submitted_at = overrides.get(
        "submitted_at", datetime(2026, 4, 21, 10, tzinfo=timezone.utc),
    )
    row.last_polled_at = overrides.get("last_polled_at", None)
    row.next_poll_at = overrides.get(
        "next_poll_at", datetime(2026, 4, 21, 10, 1, tzinfo=timezone.utc),
    )
    row.completed_at = overrides.get("completed_at", None)
    row.last_error = overrides.get("last_error", None)
    row.last_external_status = overrides.get("last_external_status", "Executing")
    row.total_diverted_ms = overrides.get("total_diverted_ms", 0)
    row.diverted_since = overrides.get("diverted_since", None)
    # Secret-bearing field — never surfaced by AsyncJobOut; still needs to
    # be present on the model because the schema walks it.
    row.metadata_json = overrides.get("metadata_json", {})
    return row


class TestListInstanceAsyncJobs:
    def test_returns_jobs_for_found_instance(self, client_and_session):
        client, session = client_and_session
        wf = uuid.uuid4()
        inst_id = uuid.uuid4()

        instance = MagicMock()
        instance.id = inst_id
        instance.workflow_def_id = wf
        instance.tenant_id = TENANT

        job1 = _mock_job(instance_id=inst_id, node_id="node_3", status="running")
        job2 = _mock_job(instance_id=inst_id, node_id="node_5", status="completed")

        # First query: instance lookup. Second query: async_jobs rows.
        q = session.query.return_value
        q.filter_by.return_value.first.return_value = instance
        q.filter.return_value.order_by.return_value.all.return_value = [job1, job2]

        res = client.get(f"/api/v1/workflows/{wf}/instances/{inst_id}/async-jobs")
        assert res.status_code == 200
        body = res.json()
        assert len(body) == 2
        assert body[0]["node_id"] == "node_3"
        assert body[0]["system"] == "automationedge"
        assert body[0]["status"] == "running"
        assert body[0]["last_external_status"] == "Executing"
        assert "metadata_json" not in body[0]   # secret-bearing, never surfaced

    def test_404_when_instance_not_in_tenant(self, client_and_session):
        client, session = client_and_session
        session.query.return_value.filter_by.return_value.first.return_value = None

        res = client.get(
            f"/api/v1/workflows/{uuid.uuid4()}/instances/{uuid.uuid4()}/async-jobs",
        )
        assert res.status_code == 404

    def test_empty_list_when_no_jobs_recorded(self, client_and_session):
        client, session = client_and_session
        wf = uuid.uuid4()
        inst_id = uuid.uuid4()
        instance = MagicMock()
        instance.id = inst_id
        instance.workflow_def_id = wf
        instance.tenant_id = TENANT
        q = session.query.return_value
        q.filter_by.return_value.first.return_value = instance
        q.filter.return_value.order_by.return_value.all.return_value = []

        res = client.get(f"/api/v1/workflows/{wf}/instances/{inst_id}/async-jobs")
        assert res.status_code == 200
        assert res.json() == []
