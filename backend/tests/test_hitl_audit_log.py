"""HITL-01.a — approval audit log + identity capture.

Pins the new behaviour:

* Every resume call writes exactly one ``approval_audit_log`` row.
* Explicit ``decision="rejected"`` closes the instance with
  ``status=failed`` + ``suspended_reason="rejected"`` (no Celery
  resume fires) so it lands as A2A-01.b's ``rejected`` terminal
  state.
* Back-compat: callers on the v0 shape (no ``approver`` /
  ``decision``) still succeed, produce an audit row with
  ``approver="anonymous"`` / ``decision="approved"``, and queue
  the resume.
* ``context_before`` + ``context_after`` capture the patch diff
  without needing to crack open ``instance_checkpoints``.
* ``GET /approvals`` returns rows ordered oldest-first, tenant-
  scoped, and 404s on instances the tenant doesn't own.
* Invalid ``decision`` → 422; over-long ``approver`` → 422.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

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


def _suspended_instance(*, workflow_id: uuid.UUID, instance_id: uuid.UUID):
    inst = MagicMock()
    inst.id = instance_id
    inst.tenant_id = TENANT
    inst.workflow_def_id = workflow_id
    inst.status = "suspended"
    inst.suspended_reason = None
    inst.current_node_id = "node_4"
    inst.context_json = {"trigger": {"msg": "hi"}, "node_3": {"score": 0.9}}
    inst.completed_at = None
    # InstanceOut response model serialises these fields — set them
    # to real values (not Mock defaults) so Pydantic doesn't choke
    # when the endpoint returns the instance.
    inst.parent_instance_id = None
    inst.parent_node_id = None
    inst.trigger_payload = {}
    inst.definition_version_at_start = 1
    inst.cancel_requested = False
    inst.started_at = datetime.now(timezone.utc)
    inst.created_at = datetime.now(timezone.utc)
    return inst


def _set_instance_lookup(session: MagicMock, instance):
    """Wire the session.query(...).filter_by(...).first() chain to
    return the given instance. The refresh side-effect just returns
    the object unchanged so the endpoint's `db.refresh(instance)`
    works."""
    session.query.return_value.filter_by.return_value.first.return_value = instance
    session.refresh.side_effect = lambda obj: None


def _captured_audit_rows(session: MagicMock) -> list:
    """Every ApprovalAuditLog added via session.add."""
    from app.models.workflow import ApprovalAuditLog
    return [
        c.args[0] for c in session.add.call_args_list
        if isinstance(c.args[0], ApprovalAuditLog)
    ]


# ---------------------------------------------------------------------------
# Happy path — approve writes audit + queues resume
# ---------------------------------------------------------------------------


def test_callback_approves_writes_audit_and_queues_resume(client_and_session):
    client, session = client_and_session
    wf_id = uuid.uuid4()
    inst_id = uuid.uuid4()
    inst = _suspended_instance(workflow_id=wf_id, instance_id=inst_id)
    _set_instance_lookup(session, inst)

    with patch("app.workers.tasks.resume_workflow_task.delay") as fake_delay:
        resp = client.post(
            f"/api/v1/workflows/{wf_id}/instances/{inst_id}/callback",
            json={
                "approver": "alice@acme.example",
                "decision": "approved",
                "reason": "Looks good.",
                "approval_payload": {"ok": True},
                "context_patch": {"node_3": {"score": 0.95}},
            },
        )

    assert resp.status_code == 200
    assert fake_delay.called  # resume queued

    rows = _captured_audit_rows(session)
    assert len(rows) == 1
    row = rows[0]
    assert row.tenant_id == TENANT
    assert row.instance_id == inst_id
    assert row.node_id == "node_4"
    assert row.approver == "alice@acme.example"
    assert row.decision == "approved"
    assert row.reason == "Looks good."
    # context_before is pre-merge; context_after carries the patch.
    assert row.context_before_json["node_3"]["score"] == 0.9
    assert row.context_after_json["node_3"]["score"] == 0.95
    assert row.context_after_json["approval"] == {"ok": True}
    # Reserved-for-later columns are null on v0.a rows.
    assert row.approvers_allowlist_matched is None
    assert row.parent_instance_id is None


def test_callback_defaults_approver_to_anonymous_for_v0_callers(client_and_session):
    """Back-compat: callers that don't send approver/decision still
    succeed and produce an audit row."""
    client, session = client_and_session
    wf_id = uuid.uuid4()
    inst_id = uuid.uuid4()
    _set_instance_lookup(
        session, _suspended_instance(workflow_id=wf_id, instance_id=inst_id),
    )

    with patch("app.workers.tasks.resume_workflow_task.delay"):
        resp = client.post(
            f"/api/v1/workflows/{wf_id}/instances/{inst_id}/callback",
            json={"approval_payload": {"ok": True}},
        )

    assert resp.status_code == 200
    row = _captured_audit_rows(session)[0]
    assert row.approver == "anonymous"
    assert row.decision == "approved"


# ---------------------------------------------------------------------------
# Reject path — no Celery, terminal state, maps to A2A rejected
# ---------------------------------------------------------------------------


def test_callback_rejects_without_queueing_resume(client_and_session):
    client, session = client_and_session
    wf_id = uuid.uuid4()
    inst_id = uuid.uuid4()
    inst = _suspended_instance(workflow_id=wf_id, instance_id=inst_id)
    _set_instance_lookup(session, inst)

    with patch("app.workers.tasks.resume_workflow_task.delay") as fake_delay:
        resp = client.post(
            f"/api/v1/workflows/{wf_id}/instances/{inst_id}/callback",
            json={
                "approver": "bob@acme.example",
                "decision": "rejected",
                "reason": "Wrong recipient list.",
            },
        )

    assert resp.status_code == 200
    # No Celery task — the rejection is terminal.
    fake_delay.assert_not_called()
    # Instance is closed as failed+rejected so the A2A resolver
    # maps it to the A2A-01.b `rejected` terminal state.
    assert inst.status == "failed"
    assert inst.suspended_reason == "rejected"
    assert inst.completed_at is not None

    row = _captured_audit_rows(session)[0]
    assert row.decision == "rejected"
    assert row.reason == "Wrong recipient list."


def test_callback_invalid_decision_returns_422(client_and_session):
    client, session = client_and_session
    wf_id = uuid.uuid4()
    inst_id = uuid.uuid4()
    _set_instance_lookup(
        session, _suspended_instance(workflow_id=wf_id, instance_id=inst_id),
    )

    resp = client.post(
        f"/api/v1/workflows/{wf_id}/instances/{inst_id}/callback",
        json={"approver": "a", "decision": "maybe"},
    )
    assert resp.status_code == 422
    assert "decision" in resp.json()["detail"]
    # No audit row written on a rejected-at-validation request.
    assert _captured_audit_rows(session) == []


def test_callback_overlong_approver_returns_422(client_and_session):
    client, session = client_and_session
    wf_id = uuid.uuid4()
    inst_id = uuid.uuid4()
    _set_instance_lookup(
        session, _suspended_instance(workflow_id=wf_id, instance_id=inst_id),
    )

    # Pydantic enforces the 256-char cap at schema parse time.
    resp = client.post(
        f"/api/v1/workflows/{wf_id}/instances/{inst_id}/callback",
        json={"approver": "a" * 300, "decision": "approved"},
    )
    assert resp.status_code == 422


def test_callback_404_when_instance_not_suspended(client_and_session):
    """Non-suspended instances can't be resumed. The endpoint's
    filter (status='suspended') surfaces this as 404."""
    client, session = client_and_session
    session.query.return_value.filter_by.return_value.first.return_value = None

    resp = client.post(
        f"/api/v1/workflows/{uuid.uuid4()}/instances/{uuid.uuid4()}/callback",
        json={"approver": "x", "decision": "approved"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /approvals
# ---------------------------------------------------------------------------


def _make_audit_row(**overrides):
    from app.models.workflow import ApprovalAuditLog
    row = ApprovalAuditLog(
        id=uuid.uuid4(),
        tenant_id=TENANT,
        instance_id=overrides.get("instance_id") or uuid.uuid4(),
        node_id=overrides.get("node_id", "node_4"),
        parent_instance_id=None,
        approver=overrides.get("approver", "alice"),
        decision=overrides.get("decision", "approved"),
        reason=overrides.get("reason"),
        context_before_json=overrides.get("context_before_json"),
        context_after_json=overrides.get("context_after_json"),
        approvers_allowlist_matched=None,
        created_at=overrides.get("created_at", datetime.now(timezone.utc)),
    )
    return row


def test_list_approvals_returns_ordered_rows(client_and_session):
    client, session = client_and_session
    wf_id = uuid.uuid4()
    inst_id = uuid.uuid4()
    inst = _suspended_instance(workflow_id=wf_id, instance_id=inst_id)

    row1 = _make_audit_row(
        instance_id=inst_id, approver="alice", decision="rejected",
        reason="First pass: not quite.",
        created_at=datetime(2026, 4, 20, tzinfo=timezone.utc),
    )
    row2 = _make_audit_row(
        instance_id=inst_id, approver="alice", decision="approved",
        reason="Second pass: looks good.",
        created_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
    )

    # Two queries: instance lookup (.first()), then audit rows
    # (.order_by().all()). side_effect returns the right
    # instance for each call-site.
    filter_by = session.query.return_value.filter_by.return_value
    filter_by.first.return_value = inst
    filter_by.order_by.return_value.all.return_value = [row1, row2]

    resp = client.get(f"/api/v1/workflows/{wf_id}/instances/{inst_id}/approvals")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["approver"] == "alice"
    assert body[0]["decision"] == "rejected"
    assert body[1]["decision"] == "approved"
    # ApprovalAuditOut passes through the rich context snapshots.
    assert "context_before_json" in body[0]
    assert "context_after_json" in body[0]


def test_list_approvals_404_when_instance_missing(client_and_session):
    client, session = client_and_session
    session.query.return_value.filter_by.return_value.first.return_value = None
    resp = client.get(
        f"/api/v1/workflows/{uuid.uuid4()}/instances/{uuid.uuid4()}/approvals",
    )
    assert resp.status_code == 404
