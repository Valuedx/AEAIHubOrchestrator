"""End-to-end AutomationEdge integration tests (AE-08).

Exercises the full async-external suspend → poll/webhook → resume loop
against:
  * a real Postgres (testcontainer) so migrations, RLS, and JSONB
    round-trips are live
  * a respx-mocked AE REST API so no real AutomationEdge server is
    required

Scope deliberately stops short of the resume path's downstream thread:
``resume_workflow_task.delay`` is patched to a Mock. Whether the parent
workflow's ready queue actually executes is covered by the unit tests
in dag_runner / node_handlers; here we only need to prove the async-
external half of the pipeline fires correctly.

Common seeding pattern:
  1. INSERT workflow_definitions + workflow_instances (suspended) +
     async_jobs (submitted / running / diverted) via the superuser
     engine so RLS doesn't get in the way.
  2. Mock AE httpx calls with respx.
  3. Invoke the Beat task or the webhook endpoint directly.
  4. Assert async_jobs row mutations + resume dispatch.
"""

from __future__ import annotations

import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from unittest.mock import patch

import httpx
import pytest
import respx
from sqlalchemy import text


AE_BASE = "http://ae.test.example.com/aeengine/rest"


# ---------------------------------------------------------------------------
# Per-test truncate — AE tests drive the ``superuser_sessionmaker`` fixture
# directly (not ``app_session``) because Beat runs BYPASSRLS in production.
# ``app_session``'s finally-block truncate doesn't fire in that path, so rows
# seeded by earlier tests would otherwise survive into later tests — most
# visibly, a lingering 'running' async_jobs row from the Diverted test would
# get re-polled (and hit AE) during the abandoned-parent test's
# poll_async_jobs() call, breaking its call-count assertion.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _truncate_between_ae_tests(superuser_engine):
    yield
    from sqlalchemy import text as _text
    with superuser_engine.begin() as conn:
        conn.execute(_text(
            "TRUNCATE TABLE "
            "async_jobs, tenant_integrations, workflow_instances, "
            "workflow_definitions "
            "RESTART IDENTITY CASCADE"
        ))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _seed_ae_suspension(
    superuser_sessionmaker,
    *,
    tenant_id: str = "tenant-a",
    external_job_id: str = "42",
    submitted_at: datetime | None = None,
    next_poll_at: datetime | None = None,
    last_external_status: str | None = None,
    total_diverted_ms: int = 0,
    diverted_since: datetime | None = None,
    webhook_token: str | None = None,
    webhook_hmac_secret: str | None = None,
    webhook_auth: str = "token",
    completion_mode: str = "poll",
    timeout_seconds: int = 3600,
    max_diverted_seconds: int = 604800,
    instance_status: str = "suspended",
    instance_suspended_reason: str | None = "async_external",
) -> dict:
    """Insert the trio (workflow_definition, workflow_instance, async_jobs)
    representing an AE node that has submitted to AE and is now waiting.

    Returns the ids as a dict so tests can reload + assert.
    """
    session = superuser_sessionmaker()
    try:
        wf_id = uuid.uuid4()
        instance_id = uuid.uuid4()
        async_job_id = uuid.uuid4()
        now = submitted_at or _utcnow()
        poll_at = next_poll_at or (now - timedelta(seconds=1))  # already due

        session.execute(
            text(
                """
                INSERT INTO workflow_definitions
                    (id, tenant_id, name, graph_json, version, is_published,
                     created_at, updated_at)
                VALUES
                    (:id, :tenant, 'AE Test',
                     CAST(:graph AS jsonb), 1, false, now(), now())
                """
            ),
            {
                "id": wf_id,
                "tenant": tenant_id,
                "graph": json.dumps({
                    "nodes": [
                        {"id": "node_1", "type": "agenticNode",
                         "data": {"label": "AutomationEdge", "nodeCategory": "action", "config": {}}},
                    ],
                    "edges": [],
                }),
            },
        )
        session.execute(
            text(
                """
                INSERT INTO workflow_instances
                    (id, tenant_id, workflow_def_id, status, suspended_reason,
                     context_json, cancel_requested, pause_requested,
                     created_at)
                VALUES
                    (:id, :tenant, :wf_id, :status, :reason,
                     CAST('{}' AS jsonb), false, false, now())
                """
            ),
            {
                "id": instance_id,
                "tenant": tenant_id,
                "wf_id": wf_id,
                "status": instance_status,
                "reason": instance_suspended_reason,
            },
        )

        metadata = {
            "base_url": AE_BASE,
            "org_code": "AEDEMO",
            "credentials_secret_prefix": "AE",
            "auth_mode": "ae_session",
            "user_id": "orchestrator",
            "source": "AE AI Hub Orchestrator",
            "completion_mode": completion_mode,
            "poll_interval_seconds": 30,
            "timeout_seconds": timeout_seconds,
            "max_diverted_seconds": max_diverted_seconds,
        }
        if webhook_auth:
            metadata["webhook_auth"] = webhook_auth
        if webhook_token:
            metadata["webhook_token"] = webhook_token
        if webhook_hmac_secret:
            metadata["webhook_hmac_secret"] = webhook_hmac_secret

        session.execute(
            text(
                """
                INSERT INTO async_jobs
                    (id, instance_id, node_id, system, external_job_id, status,
                     metadata_json, submitted_at, next_poll_at,
                     total_diverted_ms, diverted_since, last_external_status)
                VALUES
                    (:id, :inst_id, 'node_1', 'automationedge', :ext_id, 'submitted',
                     CAST(:meta AS jsonb), :submitted, :next_poll,
                     :banked, :div_since, :last_ext)
                """
            ),
            {
                "id": async_job_id,
                "inst_id": instance_id,
                "ext_id": external_job_id,
                "meta": json.dumps(metadata),
                "submitted": now,
                "next_poll": poll_at,
                "banked": total_diverted_ms,
                "div_since": diverted_since,
                "last_ext": last_external_status,
            },
        )
        session.commit()
    finally:
        session.close()

    return {
        "workflow_def_id": wf_id,
        "instance_id": instance_id,
        "async_job_id": async_job_id,
        "tenant_id": tenant_id,
        "external_job_id": external_job_id,
    }


def _reload_async_job(superuser_sessionmaker, async_job_id: uuid.UUID) -> dict:
    """Return the current async_jobs row as a plain dict."""
    session = superuser_sessionmaker()
    try:
        row = session.execute(
            text(
                """
                SELECT status, completed_at, last_external_status,
                       total_diverted_ms, diverted_since, last_error
                FROM async_jobs WHERE id = :id
                """
            ),
            {"id": async_job_id},
        ).mappings().first()
        assert row is not None, f"async_job {async_job_id} vanished"
        return dict(row)
    finally:
        session.close()


def _reload_instance(superuser_sessionmaker, instance_id: uuid.UUID) -> dict:
    session = superuser_sessionmaker()
    try:
        row = session.execute(
            text(
                "SELECT status, suspended_reason FROM workflow_instances WHERE id = :id"
            ),
            {"id": instance_id},
        ).mappings().first()
        assert row is not None
        return dict(row)
    finally:
        session.close()


def _mock_ae_auth_login():
    return respx.post(f"{AE_BASE}/authenticate").mock(
        return_value=httpx.Response(
            200, json={"success": True, "sessionToken": "sess-test"},
        ),
    )


def _mock_vault_creds():
    """Stub the vault so the client's _resolve_credentials returns without DB."""
    def fake(_tenant, key):
        return {"AE_USERNAME": "u", "AE_PASSWORD": "p"}.get(key)
    return patch(
        "app.engine.automationedge_client.get_tenant_secret",
        side_effect=fake,
    )


# ---------------------------------------------------------------------------
# Beat poll end-to-end — Pattern C happy path
# ---------------------------------------------------------------------------

class TestBeatPollComplete:
    @respx.mock
    def test_complete_transitions_job_and_dispatches_resume(
        self, superuser_sessionmaker,
    ):
        from app.workers.scheduler import poll_async_jobs
        from app.engine import automationedge_client as ae_mod

        ae_mod.reset_session_cache()
        seeded = _seed_ae_suspension(superuser_sessionmaker)

        _mock_ae_auth_login()
        respx.get(f"{AE_BASE}/workflowinstances/42").mock(
            return_value=httpx.Response(
                200, json={
                    "id": 42, "status": "Complete",
                    "workflowResponse": '{"message":"ok","outputParameters":{"r":1}}',
                },
            ),
        )

        with _mock_vault_creds(), \
             patch("app.workers.scheduler.SessionLocal", superuser_sessionmaker), \
             patch("app.workers.tasks.resume_workflow_task.delay") as resume_mock:
            poll_async_jobs()

        job = _reload_async_job(superuser_sessionmaker, seeded["async_job_id"])
        assert job["status"] == "completed"
        assert job["completed_at"] is not None
        assert job["last_external_status"] == "Complete"

        resume_mock.assert_called_once()
        args = resume_mock.call_args[0]
        # resume_workflow_task.delay(tenant_id, instance_id, approval_payload, context_patch)
        assert args[0] == seeded["tenant_id"]
        assert args[1] == str(seeded["instance_id"])
        patch_body = args[3]
        assert set(patch_body.keys()) == {"node_1"}
        assert patch_body["node_1"]["state"] == "completed"
        assert patch_body["node_1"]["output_parameters"] == {"r": 1}

    @respx.mock
    def test_failure_surfaces_error_in_patch(self, superuser_sessionmaker):
        from app.workers.scheduler import poll_async_jobs
        from app.engine import automationedge_client as ae_mod

        ae_mod.reset_session_cache()
        seeded = _seed_ae_suspension(superuser_sessionmaker)

        _mock_ae_auth_login()
        respx.get(f"{AE_BASE}/workflowinstances/42").mock(
            return_value=httpx.Response(
                200, json={
                    "id": 42, "status": "Failure",
                    "failureReason": "SAPDown",
                    "failureReasonDescription": "SAP returned 503",
                    "workflowResponse": "",
                },
            ),
        )

        with _mock_vault_creds(), \
             patch("app.workers.scheduler.SessionLocal", superuser_sessionmaker), \
             patch("app.workers.tasks.resume_workflow_task.delay") as resume_mock:
            poll_async_jobs()

        job = _reload_async_job(superuser_sessionmaker, seeded["async_job_id"])
        assert job["status"] == "failed"

        resume_mock.assert_called_once()
        patch_body = resume_mock.call_args[0][3]
        assert patch_body["node_1"]["state"] == "failed"
        assert patch_body["node_1"]["error"] == "SAPDown"
        assert patch_body["node_1"]["failureReason"] == "SAPDown"


# ---------------------------------------------------------------------------
# Beat poll — next_poll_at skipping (per-job cadence)
# ---------------------------------------------------------------------------

class TestBeatPollSkipsNotDueJobs:
    @respx.mock
    def test_job_with_future_next_poll_is_skipped(self, superuser_sessionmaker):
        from app.workers.scheduler import poll_async_jobs
        from app.engine import automationedge_client as ae_mod

        ae_mod.reset_session_cache()
        seeded = _seed_ae_suspension(
            superuser_sessionmaker,
            next_poll_at=_utcnow() + timedelta(minutes=5),
        )

        # If the poller incorrectly picks up this job, this mock would be
        # called — tracking lets us assert the opposite.
        auth = _mock_ae_auth_login()
        ae_call = respx.get(f"{AE_BASE}/workflowinstances/42")

        with _mock_vault_creds(), \
             patch("app.workers.scheduler.SessionLocal", superuser_sessionmaker), \
             patch("app.workers.tasks.resume_workflow_task.delay") as resume_mock:
            poll_async_jobs()

        assert auth.call_count == 0
        assert ae_call.call_count == 0
        resume_mock.assert_not_called()

        # State unchanged
        job = _reload_async_job(superuser_sessionmaker, seeded["async_job_id"])
        assert job["status"] == "submitted"


# ---------------------------------------------------------------------------
# Beat poll — Diverted pause-the-clock accumulation
# ---------------------------------------------------------------------------

class TestBeatPollDivertedClock:
    @respx.mock
    def test_entering_diverted_sets_diverted_since(
        self, superuser_sessionmaker,
    ):
        from app.workers.scheduler import poll_async_jobs
        from app.engine import automationedge_client as ae_mod

        ae_mod.reset_session_cache()
        seeded = _seed_ae_suspension(
            superuser_sessionmaker,
            last_external_status="Executing",
        )

        _mock_ae_auth_login()
        respx.get(f"{AE_BASE}/workflowinstances/42").mock(
            return_value=httpx.Response(
                200, json={"id": 42, "status": "Diverted"},
            ),
        )

        with _mock_vault_creds(), \
             patch("app.workers.scheduler.SessionLocal", superuser_sessionmaker), \
             patch("app.workers.tasks.resume_workflow_task.delay") as resume_mock:
            poll_async_jobs()

        job = _reload_async_job(superuser_sessionmaker, seeded["async_job_id"])
        assert job["last_external_status"] == "Diverted"
        assert job["diverted_since"] is not None
        assert job["total_diverted_ms"] == 0
        assert job["status"] == "running"     # Diverted is non-terminal
        resume_mock.assert_not_called()

    @respx.mock
    def test_exiting_diverted_banks_elapsed_time(
        self, superuser_sessionmaker,
    ):
        from app.workers.scheduler import poll_async_jobs
        from app.engine import automationedge_client as ae_mod

        ae_mod.reset_session_cache()
        # Seed as if the job has been Diverted for 30 seconds.
        div_since = _utcnow() - timedelta(seconds=30)
        seeded = _seed_ae_suspension(
            superuser_sessionmaker,
            last_external_status="Diverted",
            diverted_since=div_since,
            total_diverted_ms=0,
        )

        _mock_ae_auth_login()
        respx.get(f"{AE_BASE}/workflowinstances/42").mock(
            return_value=httpx.Response(
                200, json={"id": 42, "status": "Executing"},
            ),
        )

        with _mock_vault_creds(), \
             patch("app.workers.scheduler.SessionLocal", superuser_sessionmaker), \
             patch("app.workers.tasks.resume_workflow_task.delay"):
            poll_async_jobs()

        job = _reload_async_job(superuser_sessionmaker, seeded["async_job_id"])
        assert job["last_external_status"] == "Executing"
        assert job["diverted_since"] is None
        # Within a generous window — poll runtime adds a few ms.
        assert 29_000 <= job["total_diverted_ms"] <= 35_000, (
            f"expected banked ~30s, got {job['total_diverted_ms']} ms"
        )


# ---------------------------------------------------------------------------
# Beat poll — timeout resumes as failed with warning
# ---------------------------------------------------------------------------

class TestBeatPollTimeout:
    @respx.mock
    def test_submitted_past_timeout_fires_timed_out_resume(
        self, superuser_sessionmaker,
    ):
        from app.workers.scheduler import poll_async_jobs
        from app.engine import automationedge_client as ae_mod

        ae_mod.reset_session_cache()
        # Submitted 2h ago, timeout is 1h — should be timed_out.
        seeded = _seed_ae_suspension(
            superuser_sessionmaker,
            submitted_at=_utcnow() - timedelta(hours=2),
            timeout_seconds=3600,
        )

        _mock_ae_auth_login()
        # AE shouldn't be hit — pre-poll timeout check short-circuits.
        ae_call = respx.get(f"{AE_BASE}/workflowinstances/42")

        with _mock_vault_creds(), \
             patch("app.workers.scheduler.SessionLocal", superuser_sessionmaker), \
             patch("app.workers.tasks.resume_workflow_task.delay") as resume_mock:
            poll_async_jobs()

        job = _reload_async_job(superuser_sessionmaker, seeded["async_job_id"])
        assert job["status"] == "timed_out"
        assert "exceeded" in (job["last_error"] or "")
        assert ae_call.call_count == 0

        resume_mock.assert_called_once()
        patch_body = resume_mock.call_args[0][3]
        assert patch_body["node_1"]["state"] == "timed_out"
        assert patch_body["node_1"]["_warning"] is True


# ---------------------------------------------------------------------------
# Webhook endpoint — full round-trip against a real DB
# ---------------------------------------------------------------------------

class TestWebhookEndpointEndToEnd:
    def _build_app(self, superuser_sessionmaker):
        from fastapi import FastAPI
        from app.api.async_jobs import router

        app = FastAPI()
        app.include_router(router)
        return app

    def test_token_auth_terminal_resumes_parent(self, superuser_sessionmaker):
        from fastapi.testclient import TestClient

        seeded = _seed_ae_suspension(
            superuser_sessionmaker,
            completion_mode="webhook",
            webhook_auth="token",
            webhook_token="sekret-abc",
        )

        with patch("app.api.async_jobs.SessionLocal", superuser_sessionmaker), \
             patch("app.workers.tasks.resume_workflow_task.delay") as resume_mock:
            client = TestClient(self._build_app(superuser_sessionmaker))
            res = client.post(
                f"/api/v1/async-jobs/{seeded['async_job_id']}/complete?token=sekret-abc",
                json={
                    "status": "Complete",
                    "workflowResponse": '{"outputParameters":{"ok":true}}',
                },
            )

        assert res.status_code == 200
        assert res.json()["status"] == "completed"

        job = _reload_async_job(superuser_sessionmaker, seeded["async_job_id"])
        assert job["status"] == "completed"

        resume_mock.assert_called_once()
        patch_body = resume_mock.call_args[0][3]
        assert patch_body["node_1"]["state"] == "completed"
        assert patch_body["node_1"]["output_parameters"] == {"ok": True}

    def test_hmac_auth_terminal_resumes_parent(self, superuser_sessionmaker):
        from fastapi.testclient import TestClient

        seeded = _seed_ae_suspension(
            superuser_sessionmaker,
            completion_mode="webhook",
            webhook_auth="hmac",
            webhook_hmac_secret="signing-secret",
        )

        body = b'{"status":"Complete"}'
        sig = "sha256=" + hmac.new(
            b"signing-secret", body, sha256,
        ).hexdigest()

        with patch("app.api.async_jobs.SessionLocal", superuser_sessionmaker), \
             patch("app.workers.tasks.resume_workflow_task.delay") as resume_mock:
            client = TestClient(self._build_app(superuser_sessionmaker))
            res = client.post(
                f"/api/v1/async-jobs/{seeded['async_job_id']}/complete",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-AE-Signature": sig,
                },
            )

        assert res.status_code == 200
        job = _reload_async_job(superuser_sessionmaker, seeded["async_job_id"])
        assert job["status"] == "completed"
        resume_mock.assert_called_once()

    def test_invalid_token_leaves_job_alone_with_401(self, superuser_sessionmaker):
        from fastapi.testclient import TestClient

        seeded = _seed_ae_suspension(
            superuser_sessionmaker,
            completion_mode="webhook",
            webhook_auth="token",
            webhook_token="right-token",
        )

        with patch("app.api.async_jobs.SessionLocal", superuser_sessionmaker), \
             patch("app.workers.tasks.resume_workflow_task.delay") as resume_mock:
            client = TestClient(self._build_app(superuser_sessionmaker))
            res = client.post(
                f"/api/v1/async-jobs/{seeded['async_job_id']}/complete?token=wrong",
                json={"status": "Complete"},
            )

        assert res.status_code == 401

        job = _reload_async_job(superuser_sessionmaker, seeded["async_job_id"])
        assert job["status"] == "submitted"   # untouched
        resume_mock.assert_not_called()

    def test_unknown_job_id_returns_401_not_404(self, superuser_sessionmaker):
        """No information leak about which job_ids exist."""
        from fastapi.testclient import TestClient

        # No seed — random UUID won't exist.
        unknown = uuid.uuid4()

        with patch("app.api.async_jobs.SessionLocal", superuser_sessionmaker), \
             patch("app.workers.tasks.resume_workflow_task.delay") as resume_mock:
            client = TestClient(self._build_app(superuser_sessionmaker))
            res = client.post(
                f"/api/v1/async-jobs/{unknown}/complete?token=anything",
                json={"status": "Complete"},
            )

        assert res.status_code == 401
        resume_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Cross-path invariants
# ---------------------------------------------------------------------------

class TestBeatPollInstanceStateAtRest:
    """The Beat poll shouldn't touch instances whose parent is no longer
    suspended — covers the abandon case where a parent was cancelled
    out-of-band between submit and poll."""

    @respx.mock
    def test_parent_already_cancelled_marks_job_abandoned(
        self, superuser_sessionmaker,
    ):
        from app.workers.scheduler import poll_async_jobs
        from app.engine import automationedge_client as ae_mod

        ae_mod.reset_session_cache()
        # Seed the instance already cancelled — covers the case where
        # parent cancel happened between submit and this poll tick. No
        # flip-mid-test, no transaction-visibility ambiguity.
        seeded = _seed_ae_suspension(
            superuser_sessionmaker,
            instance_status="cancelled",
            instance_suspended_reason=None,
        )

        # Explicit 200 response so an unintended AE call produces a
        # clean assertion failure rather than a respx ConnectError.
        _mock_ae_auth_login()
        ae_call = respx.get(f"{AE_BASE}/workflowinstances/42").mock(
            return_value=httpx.Response(200, json={"id": 42, "status": "Complete"}),
        )

        with _mock_vault_creds(), \
             patch("app.workers.scheduler.SessionLocal", superuser_sessionmaker), \
             patch("app.workers.tasks.resume_workflow_task.delay") as resume_mock:
            poll_async_jobs()

        job = _reload_async_job(superuser_sessionmaker, seeded["async_job_id"])
        assert job["status"] == "abandoned"
        assert ae_call.call_count == 0, (
            "scheduler should short-circuit on non-suspended parent BEFORE hitting AE"
        )
        resume_mock.assert_not_called()
