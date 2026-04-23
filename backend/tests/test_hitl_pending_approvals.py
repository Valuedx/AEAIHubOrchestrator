"""HITL-01.b — pending-approvals dashboard endpoint.

Pins:

* Returns only HITL suspensions (``suspended_reason`` NULL) —
  async-external rows don't belong on the operator's dashboard.
* Sorted oldest-first by ``suspended_at`` so the row at the top is
  the one that's been waiting longest (most likely to be close to
  timeout).
* Falls back to ``started_at`` for v0 rows that suspended before
  migration 0031 stamped ``suspended_at``.
* ``approval_message`` is extracted from the suspended node's
  config the same way the single-instance context endpoint does,
  so cosmetic tweaks stay consistent.
* ``age_seconds`` is a non-negative integer even on clock skew.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


TENANT = "tenant-hitl"


@pytest.fixture
def client_and_session():
    from app.api.workflows import router
    from app.database import get_db, get_tenant_db
    from app.security.tenant import get_tenant_id

    app = FastAPI()
    app.include_router(router)
    session = MagicMock()

    def _fake_get_db():
        yield session

    app.dependency_overrides[get_tenant_id] = lambda: TENANT
    app.dependency_overrides[get_db] = _fake_get_db
    app.dependency_overrides[get_tenant_db] = _fake_get_db
    return TestClient(app), session


def _pending(
    *,
    workflow_name: str,
    approval_message: str,
    suspended_at: datetime,
    node_id: str = "node_4",
):
    """Stub an (instance, workflow_def) pair as the endpoint's
    joined query returns. The graph_json carries the node config
    so the endpoint's ``approvalMessage`` extraction path runs."""
    instance = MagicMock()
    instance.id = uuid.uuid4()
    instance.tenant_id = TENANT
    instance.status = "suspended"
    instance.suspended_reason = None
    instance.current_node_id = node_id
    instance.suspended_at = suspended_at
    instance.started_at = suspended_at - timedelta(seconds=10)
    instance.definition_version_at_start = 1

    wf = MagicMock()
    wf.id = uuid.uuid4()
    wf.tenant_id = TENANT
    wf.name = workflow_name
    wf.version = 1
    wf.graph_json = {
        "nodes": [{
            "id": node_id,
            "type": "agenticNode",
            "data": {
                "label": "Human Approval",
                "config": {"approvalMessage": approval_message},
            },
        }],
        "edges": [],
    }
    # Tie them so the endpoint's _resolve_graph_json_for_version
    # hits the current-version path (no snapshot lookup).
    instance.workflow_def_id = wf.id
    instance.definition = wf
    return instance, wf


def _wire_query(session: MagicMock, rows: list[tuple]):
    """Stage the joined query so .filter(...).order_by(...).all()
    returns the given (instance, wf) rows."""
    query = session.query.return_value
    joined = query.join.return_value
    filtered = joined.filter.return_value
    ordered = filtered.order_by.return_value
    ordered.all.return_value = rows


def test_list_pending_approvals_returns_rows_oldest_first(client_and_session):
    client, session = client_and_session
    now = datetime.now(timezone.utc)
    older = _pending(
        workflow_name="Slack summariser",
        approval_message="Confirm recipients",
        suspended_at=now - timedelta(hours=4),
    )
    newer = _pending(
        workflow_name="Incident triager",
        approval_message="Ready to page?",
        suspended_at=now - timedelta(minutes=3),
    )
    # Endpoint's query sorts by suspended_at asc — stage in that order.
    _wire_query(session, [older, newer])

    resp = client.get("/api/v1/workflows/pending-approvals")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    # Oldest first — matches the endpoint's sort.
    assert body[0]["workflow_name"] == "Slack summariser"
    assert body[0]["approval_message"] == "Confirm recipients"
    # Age is ~4 hours = 14400s (allow 30s slack for test timing).
    assert body[0]["age_seconds"] >= 14400 - 30
    assert body[0]["age_seconds"] <= 14400 + 60
    assert body[1]["workflow_name"] == "Incident triager"


def test_list_pending_approvals_empty_when_none_pending(client_and_session):
    client, session = client_and_session
    _wire_query(session, [])

    resp = client.get("/api/v1/workflows/pending-approvals")
    assert resp.status_code == 200
    assert resp.json() == []


def test_pending_approval_falls_back_to_started_at_when_suspended_at_missing(
    client_and_session,
):
    """v0 rows that suspended before migration 0031 ran still
    appear on the dashboard — we just age them against started_at
    instead of suspended_at."""
    client, session = client_and_session
    now = datetime.now(timezone.utc)
    instance, wf = _pending(
        workflow_name="Legacy flow",
        approval_message="Confirm",
        suspended_at=now,  # will be overridden to None below
    )
    instance.suspended_at = None
    instance.started_at = now - timedelta(minutes=30)
    _wire_query(session, [(instance, wf)])

    resp = client.get("/api/v1/workflows/pending-approvals")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    # Age derived from started_at ≈ 30min.
    assert 1700 <= body[0]["age_seconds"] <= 1900


def test_pending_approval_missing_both_timestamps_skipped(client_and_session):
    """Truly malformed rows (no suspended_at AND no started_at)
    are skipped rather than reported with a negative or phantom
    age — defensive against corrupt-state bug reports."""
    client, session = client_and_session
    now = datetime.now(timezone.utc)
    instance, wf = _pending(
        workflow_name="Flow",
        approval_message="Confirm",
        suspended_at=now,
    )
    instance.suspended_at = None
    instance.started_at = None
    _wire_query(session, [(instance, wf)])

    resp = client.get("/api/v1/workflows/pending-approvals")
    assert resp.status_code == 200
    # Malformed row silently dropped.
    assert resp.json() == []


def test_pending_approval_handles_missing_node_in_graph(client_and_session):
    """If the suspended node was deleted from the workflow graph
    (shouldn't normally happen but we don't want a 500), the row
    still appears with ``approval_message=null``."""
    client, session = client_and_session
    now = datetime.now(timezone.utc)
    instance, wf = _pending(
        workflow_name="Flow",
        approval_message="Confirm",
        suspended_at=now - timedelta(minutes=1),
        node_id="node_4",
    )
    # Graph no longer contains node_4.
    wf.graph_json = {"nodes": [], "edges": []}
    _wire_query(session, [(instance, wf)])

    resp = client.get("/api/v1/workflows/pending-approvals")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["approval_message"] is None
    assert body[0]["node_id"] == "node_4"
