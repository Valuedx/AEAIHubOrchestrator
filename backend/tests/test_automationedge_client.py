"""Unit tests for the AutomationEdge REST client.

Uses respx to mock httpx calls so no live AE server is required.
Covers terminal-status mapping, session-token caching, 401 refresh,
bearer-mode, /execute shape, and /workflowinstances response parsing.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx

from app.engine import automationedge_client as ae
from app.engine.automationedge_client import (
    AEAuthError,
    AEConnection,
    get_status,
    reset_session_cache,
    submit_workflow,
    terminal_status_for,
    try_terminate,
)


# ---------------------------------------------------------------------------
# terminal_status_for
# ---------------------------------------------------------------------------

class TestTerminalStatusFor:
    @pytest.mark.parametrize("ae_status,expected", [
        ("Complete", "completed"),
        ("Success", "completed"),
        ("Failure", "failed"),
        ("Failed", "failed"),
        ("Terminated", "cancelled"),
    ])
    def test_terminal_mapping(self, ae_status, expected):
        assert terminal_status_for(ae_status) == expected

    @pytest.mark.parametrize("ae_status", [
        None, "", "New", "Executing", "Diverted", "Pending", "unknown",
    ])
    def test_non_terminal_returns_none(self, ae_status):
        # Diverted intentionally treated as non-terminal — Beat keeps
        # polling with pause-the-clock timeout accounting.
        assert terminal_status_for(ae_status) is None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BASE = "http://ae.example.com/aeengine/rest"


@pytest.fixture(autouse=True)
def _clear_session_cache():
    reset_session_cache()
    yield
    reset_session_cache()


@pytest.fixture
def conn_session():
    return AEConnection(
        base_url=BASE,
        tenant_id="tenant-a",
        credentials_secret_prefix="AE",
        auth_mode="ae_session",
        org_code="AEDEMO",
        source="AE AI Hub Orchestrator",
        user_id="orchestrator",
    )


@pytest.fixture
def conn_bearer():
    return AEConnection(
        base_url=BASE,
        tenant_id="tenant-a",
        credentials_secret_prefix="AE",
        auth_mode="bearer",
        org_code="AEDEMO",
    )


def _stub_vault(username="u", password="p", token="bearer-tok"):
    """Patch get_tenant_secret so calls don't hit the DB."""
    def fake(tenant_id, key_name):
        return {
            "AE_USERNAME": username,
            "AE_PASSWORD": password,
            "AE_TOKEN": token,
        }.get(key_name)
    return patch("app.engine.automationedge_client.get_tenant_secret", side_effect=fake)


# ---------------------------------------------------------------------------
# ae_session auth flow
# ---------------------------------------------------------------------------

class TestSessionAuth:
    @respx.mock
    def test_logs_in_and_caches_session_token(self, conn_session):
        auth = respx.post(f"{BASE}/authenticate").mock(
            return_value=httpx.Response(200, json={
                "success": True, "message": "OK", "sessionToken": "sess-xyz",
            }),
        )
        status = respx.get(f"{BASE}/workflowinstances/42").mock(
            return_value=httpx.Response(200, json={"id": 42, "status": "Executing"}),
        )
        with _stub_vault():
            body1 = get_status(conn_session, 42)
            body2 = get_status(conn_session, 42)

        assert body1["status"] == "Executing" == body2["status"]
        # Login called only ONCE — cache hit on the second call.
        assert auth.call_count == 1
        assert status.call_count == 2
        # And every status call carried the session token header.
        for call in status.calls:
            assert call.request.headers["X-session-token"] == "sess-xyz"

    @respx.mock
    def test_refreshes_on_401_once(self, conn_session):
        # Prime the cache with a stale token so the first status call 401s.
        ae._cache_put(BASE, "u", "stale-token")

        status = respx.get(f"{BASE}/workflowinstances/42").mock(
            side_effect=[
                httpx.Response(401, json={"message": "expired"}),
                httpx.Response(200, json={"id": 42, "status": "Complete"}),
            ],
        )
        auth = respx.post(f"{BASE}/authenticate").mock(
            return_value=httpx.Response(200, json={
                "success": True, "sessionToken": "fresh-token",
            }),
        )

        with _stub_vault():
            body = get_status(conn_session, 42)

        assert body["status"] == "Complete"
        assert auth.call_count == 1    # one fresh login triggered by the 401
        assert status.call_count == 2  # stale + retry

    @respx.mock
    def test_login_failure_raises_ae_auth_error(self, conn_session):
        respx.post(f"{BASE}/authenticate").mock(
            return_value=httpx.Response(200, json={
                "success": False, "message": "bad creds", "sessionToken": None,
            }),
        )
        respx.get(f"{BASE}/workflowinstances/42").mock(
            return_value=httpx.Response(401),
        )
        with _stub_vault(), pytest.raises(AEAuthError):
            get_status(conn_session, 42)

    def test_missing_vault_secret_raises(self, conn_session):
        # No session in cache, and vault lookup returns None for both.
        with patch(
            "app.engine.automationedge_client.get_tenant_secret",
            return_value=None,
        ), pytest.raises(AEAuthError, match="Missing vault secrets"):
            get_status(conn_session, 42)


# ---------------------------------------------------------------------------
# bearer auth flow
# ---------------------------------------------------------------------------

class TestBearerAuth:
    @respx.mock
    def test_sends_authorization_bearer_header(self, conn_bearer):
        status = respx.get(f"{BASE}/workflowinstances/42").mock(
            return_value=httpx.Response(200, json={"id": 42, "status": "Complete"}),
        )
        with _stub_vault():
            body = get_status(conn_bearer, 42)
        assert body["status"] == "Complete"
        assert status.calls[0].request.headers["Authorization"] == "Bearer bearer-tok"

    @respx.mock
    def test_no_authenticate_call_in_bearer_mode(self, conn_bearer):
        auth = respx.post(f"{BASE}/authenticate")
        respx.get(f"{BASE}/workflowinstances/42").mock(
            return_value=httpx.Response(200, json={"status": "Executing"}),
        )
        with _stub_vault():
            get_status(conn_bearer, 42)
        assert auth.call_count == 0

    def test_missing_bearer_token_raises(self, conn_bearer):
        with patch(
            "app.engine.automationedge_client.get_tenant_secret",
            return_value=None,
        ), pytest.raises(AEAuthError, match="Missing vault secret AE_TOKEN"):
            get_status(conn_bearer, 42)


# ---------------------------------------------------------------------------
# submit_workflow
# ---------------------------------------------------------------------------

class TestSubmitWorkflow:
    @respx.mock
    def test_posts_execute_body_and_returns_automation_request_id(self, conn_session):
        respx.post(f"{BASE}/authenticate").mock(
            return_value=httpx.Response(200, json={"success": True, "sessionToken": "t"}),
        )
        execute = respx.post(f"{BASE}/execute").mock(
            return_value=httpx.Response(200, json={
                "success": True, "automationRequestId": 2968,
                "responseCode": "RequestCreated",
            }),
        )
        with _stub_vault():
            job_id = submit_workflow(
                conn_session,
                workflow_name="Google Search 1",
                params=[{"name": "search_term", "value": "Amitabh", "type": "String"}],
                source_id="src-1",
            )
        assert job_id == 2968
        body = execute.calls[0].request.content
        import json as _json
        sent = _json.loads(body)
        assert sent["workflowName"] == "Google Search 1"
        assert sent["orgCode"] == "AEDEMO"
        assert sent["userId"] == "orchestrator"
        assert sent["source"] == "AE AI Hub Orchestrator"
        assert sent["sourceId"] == "src-1"
        assert sent["params"] == [{"name": "search_term", "value": "Amitabh", "type": "String"}]
        # 6 inputAttribute slots always present (null if caller didn't supply).
        for i in range(1, 7):
            assert f"inputAttribute{i}" in sent

    @respx.mock
    def test_execute_reports_success_false_raises(self, conn_session):
        respx.post(f"{BASE}/authenticate").mock(
            return_value=httpx.Response(200, json={"success": True, "sessionToken": "t"}),
        )
        respx.post(f"{BASE}/execute").mock(
            return_value=httpx.Response(200, json={
                "success": False, "errorDetails": "workflow not found",
            }),
        )
        with _stub_vault(), pytest.raises(RuntimeError, match="workflow not found"):
            submit_workflow(conn_session, workflow_name="bad", params=[])


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------

class TestGetStatus:
    @respx.mock
    def test_parses_embedded_workflow_response_json_string(self, conn_session):
        respx.post(f"{BASE}/authenticate").mock(
            return_value=httpx.Response(200, json={"success": True, "sessionToken": "t"}),
        )
        respx.get(f"{BASE}/workflowinstances/42").mock(
            return_value=httpx.Response(200, json={
                "id": 42,
                "status": "Complete",
                # AE encodes this field as a JSON-in-a-string
                "workflowResponse": '{"message":"Execution Successful","outputParameters":{"result":7}}',
            }),
        )
        with _stub_vault():
            body = get_status(conn_session, 42)
        assert body["status"] == "Complete"
        assert body["workflow_response_parsed"]["outputParameters"] == {"result": 7}

    @respx.mock
    def test_leaves_raw_workflow_response_alone_if_not_json(self, conn_session):
        respx.post(f"{BASE}/authenticate").mock(
            return_value=httpx.Response(200, json={"success": True, "sessionToken": "t"}),
        )
        respx.get(f"{BASE}/workflowinstances/42").mock(
            return_value=httpx.Response(200, json={
                "id": 42, "status": "Failure",
                "workflowResponse": "not-json",
            }),
        )
        with _stub_vault():
            body = get_status(conn_session, 42)
        assert "workflow_response_parsed" not in body
        assert body["workflowResponse"] == "not-json"

    @respx.mock
    def test_404_raises_descriptive_error(self, conn_session):
        respx.post(f"{BASE}/authenticate").mock(
            return_value=httpx.Response(200, json={"success": True, "sessionToken": "t"}),
        )
        respx.get(f"{BASE}/workflowinstances/42").mock(
            return_value=httpx.Response(404, text="not found"),
        )
        with _stub_vault(), pytest.raises(RuntimeError, match="not found"):
            get_status(conn_session, 42)


# ---------------------------------------------------------------------------
# try_terminate — best-effort; never raises
# ---------------------------------------------------------------------------

class TestTryTerminate:
    @respx.mock
    def test_returns_true_on_2xx(self, conn_session):
        respx.post(f"{BASE}/authenticate").mock(
            return_value=httpx.Response(200, json={"success": True, "sessionToken": "t"}),
        )
        respx.put(f"{BASE}/workflowinstances/42/terminate").mock(
            return_value=httpx.Response(200, json={"success": True}),
        )
        with _stub_vault():
            assert try_terminate(conn_session, 42) is True

    @respx.mock
    def test_returns_false_on_404_no_raise(self, conn_session):
        respx.post(f"{BASE}/authenticate").mock(
            return_value=httpx.Response(200, json={"success": True, "sessionToken": "t"}),
        )
        respx.put(f"{BASE}/workflowinstances/42/terminate").mock(
            return_value=httpx.Response(404),
        )
        with _stub_vault():
            assert try_terminate(conn_session, 42) is False
