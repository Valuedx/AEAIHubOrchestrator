"""A2A-01.a — dual-path discovery + v1.0 method aliases.

Pins the v0.2.x → v1.0 compatibility surface:

* Agent card served at BOTH ``/.well-known/agent-card.json`` (spec
  v1.0) and ``/.well-known/agent.json`` (legacy v0.2.x) — both
  paths return identical bodies.
* JSON-RPC method aliases: ``message/send`` accepted as equivalent
  to ``tasks/send``, ``message/sendStreaming`` accepted as
  equivalent to ``tasks/sendSubscribe``.
* Param-shape compatibility: v1.0 clients that place ``skillId``
  inside ``message.metadata`` instead of at the top level still
  route correctly.

Mocks the DB session so tests stay hermetic — we're pinning the
dispatch layer, not the engine.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


TENANT = "tenant-a2a"


@pytest.fixture
def client_and_session():
    from app.api.a2a import router
    from app.database import get_db

    app = FastAPI()
    app.include_router(router)
    session = MagicMock()

    def _fake_get_db():
        yield session

    app.dependency_overrides[get_db] = _fake_get_db
    return TestClient(app), session


def _stub_workflow(skill_id: uuid.UUID | None = None, name: str = "slack-summarise"):
    wf = MagicMock()
    wf.id = skill_id or uuid.uuid4()
    wf.tenant_id = TENANT
    wf.name = name
    wf.description = "Summarise a Slack thread"
    wf.version = 1
    wf.is_published = True
    return wf


def _stub_api_key():
    """Represents a valid A2A bearer key for the tenant."""
    row = MagicMock()
    row.tenant_id = TENANT
    row.revoked_at = None
    return row


# ---------------------------------------------------------------------------
# Agent card discovery — both paths return identical bodies
# ---------------------------------------------------------------------------


def test_agent_card_served_at_v1_path(client_and_session):
    client, session = client_and_session
    wf = _stub_workflow()

    # Agent card endpoint is unauthenticated and runs a single
    # WorkflowDefinition filter-by query.
    session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = [wf]

    resp = client.get(f"/tenants/{TENANT}/.well-known/agent-card.json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"].endswith(TENANT)
    assert body["capabilities"]["streaming"] is True
    assert body["skills"][0]["id"] == str(wf.id)


def test_agent_card_served_at_legacy_path(client_and_session):
    """v0.2.x clients discover us at the legacy path — must return
    the exact same body as the v1.0 path."""
    client, session = client_and_session
    wf = _stub_workflow()
    session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = [wf]

    resp_v1 = client.get(f"/tenants/{TENANT}/.well-known/agent-card.json")
    # Reset the stub chain (one all() per call)
    session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = [wf]
    resp_legacy = client.get(f"/tenants/{TENANT}/.well-known/agent.json")

    assert resp_v1.status_code == 200
    assert resp_legacy.status_code == 200
    assert resp_v1.json() == resp_legacy.json()


# ---------------------------------------------------------------------------
# JSON-RPC method aliasing — tasks/send ≡ message/send
# ---------------------------------------------------------------------------


def _stage_send_success(session: MagicMock, wf) -> MagicMock:
    """Configure the session so _get_a2a_tenant (bearer check) + the
    tasks/send handler both succeed. Returns the stubbed WorkflowInstance
    added to the session so tests can inspect it.
    """
    api_key = _stub_api_key()

    # _get_a2a_tenant runs one .filter_by(...).first() on A2AApiKey.
    # _tasks_send runs another .filter_by(...).first() on WorkflowDefinition.
    # They're ordered by call site so a side_effect list works.
    session.query.return_value.filter_by.return_value.first.side_effect = [
        api_key,
        wf,
    ]

    # Mock refresh to stamp an instance id after flush so _instance_to_task
    # has something to serialise.
    def _refresh(obj):
        if not getattr(obj, "id", None):
            obj.id = uuid.uuid4()

    session.refresh.side_effect = _refresh
    return session


def test_tasks_send_accepts_v02_method_name(client_and_session):
    client, session = client_and_session
    wf = _stub_workflow()
    _stage_send_success(session, wf)

    with patch("app.security.rate_limiter.check_execution_quota"), \
         patch("app.workers.tasks.execute_workflow_task.delay"):
        resp = client.post(
            f"/tenants/{TENANT}/a2a",
            headers={"Authorization": "Bearer dummy"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tasks/send",
                "params": {
                    "skillId": str(wf.id),
                    "message": {"role": "user", "parts": [{"text": "hi"}]},
                },
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["jsonrpc"] == "2.0"
    assert "result" in body
    assert body["result"]["status"]["state"] in {"submitted", "working"}


def test_message_send_alias_works_identically(client_and_session):
    """v1.0 spec method name must reach the same handler."""
    client, session = client_and_session
    wf = _stub_workflow()
    _stage_send_success(session, wf)

    with patch("app.security.rate_limiter.check_execution_quota"), \
         patch("app.workers.tasks.execute_workflow_task.delay") as fake_enqueue:
        resp = client.post(
            f"/tenants/{TENANT}/a2a",
            headers={"Authorization": "Bearer dummy"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "message/send",  # v1.0 name
                "params": {
                    "skillId": str(wf.id),
                    "message": {"role": "user", "parts": [{"text": "hi"}]},
                },
            },
        )

    assert resp.status_code == 200
    assert "result" in resp.json()
    # Handler actually fired — an execute task was enqueued.
    assert fake_enqueue.called


def test_message_send_reads_skill_id_from_metadata(client_and_session):
    """v1.0 clients stuff the skill id into message.metadata rather
    than at the top level. The dispatcher must accept both shapes."""
    client, session = client_and_session
    wf = _stub_workflow()
    _stage_send_success(session, wf)

    with patch("app.security.rate_limiter.check_execution_quota"), \
         patch("app.workers.tasks.execute_workflow_task.delay"):
        resp = client.post(
            f"/tenants/{TENANT}/a2a",
            headers={"Authorization": "Bearer dummy"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "message/send",
                "params": {
                    # NO top-level skillId — v1.0 carries it here:
                    "message": {
                        "role": "user",
                        "parts": [{"text": "hi"}],
                        "metadata": {"skillId": str(wf.id)},
                    },
                },
            },
        )

    assert resp.status_code == 200
    assert "result" in resp.json()


def test_missing_skill_id_surfaces_clean_400(client_and_session):
    """Neither top-level nor metadata → clear error pointing at
    both locations."""
    client, session = client_and_session
    api_key = _stub_api_key()
    session.query.return_value.filter_by.return_value.first.return_value = api_key

    resp = client.post(
        f"/tenants/{TENANT}/a2a",
        headers={"Authorization": "Bearer dummy"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "message/send",
            "params": {"message": {"role": "user", "parts": [{"text": "hi"}]}},
        },
    )

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "skillId" in detail
    assert "message.metadata" in detail


def test_unknown_method_returns_jsonrpc_error(client_and_session):
    client, session = client_and_session
    api_key = _stub_api_key()
    session.query.return_value.filter_by.return_value.first.return_value = api_key

    resp = client.post(
        f"/tenants/{TENANT}/a2a",
        headers={"Authorization": "Bearer dummy"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tasks/unknownMethod",
            "params": {},
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == -32601


# ---------------------------------------------------------------------------
# A2A-01.b — agent card completeness
# ---------------------------------------------------------------------------


def test_agent_card_declares_bearer_security_scheme(client_and_session):
    """v1.0 clients discover auth requirements from the card's
    securitySchemes + security blocks. Without this they 401 and
    guess — we ship the bearer scheme explicitly."""
    client, session = client_and_session
    session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = []

    resp = client.get(f"/tenants/{TENANT}/.well-known/agent-card.json")
    assert resp.status_code == 200
    card = resp.json()

    assert "securitySchemes" in card
    bearer = card["securitySchemes"]["bearer"]
    assert bearer["type"] == "http"
    assert bearer["scheme"] == "bearer"
    # Top-level security must reference the declared scheme.
    assert {"bearer": []} in card["security"]


def test_agent_card_declares_default_input_output_modes(client_and_session):
    client, session = client_and_session
    session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = []

    resp = client.get(f"/tenants/{TENANT}/.well-known/agent-card.json")
    card = resp.json()
    assert card["defaultInputModes"] == ["text"]
    assert card["defaultOutputModes"] == ["text", "data"]


def test_agent_card_declares_provider_from_settings(client_and_session):
    from app.config import settings

    client, session = client_and_session
    session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = []

    with patch.object(settings, "a2a_provider_organization", "Acme Corp"), \
         patch.object(settings, "a2a_provider_url", "https://acme.example"), \
         patch.object(settings, "a2a_provider_email", "platform@acme.example"), \
         patch.object(settings, "a2a_documentation_url", "https://acme.example/agent-docs"):
        resp = client.get(f"/tenants/{TENANT}/.well-known/agent-card.json")

    card = resp.json()
    assert card["provider"]["organization"] == "Acme Corp"
    assert card["provider"]["url"] == "https://acme.example"
    assert card["provider"]["email"] == "platform@acme.example"
    assert card["documentationUrl"] == "https://acme.example/agent-docs"


def test_agent_card_omits_optional_fields_when_unset(client_and_session):
    """A minimally-configured deployment (no provider URL, no docs
    URL) must still produce a spec-valid card — we suppress the
    optional keys instead of emitting empty strings."""
    from app.config import settings

    client, session = client_and_session
    session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = []

    with patch.object(settings, "a2a_provider_organization", "AE AI Hub"), \
         patch.object(settings, "a2a_provider_url", ""), \
         patch.object(settings, "a2a_provider_email", ""), \
         patch.object(settings, "a2a_documentation_url", ""):
        resp = client.get(f"/tenants/{TENANT}/.well-known/agent-card.json")

    card = resp.json()
    assert card["provider"] == {"organization": "AE AI Hub"}
    assert "documentationUrl" not in card


def test_agent_card_declares_extended_agent_card_false(client_and_session):
    client, session = client_and_session
    session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = []

    resp = client.get(f"/tenants/{TENANT}/.well-known/agent-card.json")
    # We don't split public vs. extended cards, so clients must not
    # try to call agent/getExtendedAgentCard against us.
    assert resp.json()["capabilities"]["extendedAgentCard"] is False


def test_agent_card_skills_echo_default_output_modes(client_and_session):
    client, session = client_and_session
    wf = _stub_workflow()
    session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = [wf]

    resp = client.get(f"/tenants/{TENANT}/.well-known/agent-card.json")
    skill = resp.json()["skills"][0]
    # Every published workflow today consumes text and emits both
    # text and data (structured context) — match the card default.
    assert skill["inputModes"] == ["text"]
    assert set(skill["outputModes"]) == {"text", "data"}


# ---------------------------------------------------------------------------
# A2A-01.b — task-state mapping coverage
# ---------------------------------------------------------------------------


def _fake_instance(*, status: str, suspended_reason: str | None = None,
                    context_json: dict | None = None):
    inst = MagicMock()
    inst.id = uuid.uuid4()
    inst.status = status
    inst.suspended_reason = suspended_reason
    inst.context_json = context_json or {}
    return inst


def test_map_a2a_state_covers_active_states():
    from app.api.a2a import _map_a2a_state
    assert _map_a2a_state(_fake_instance(status="queued")) == "submitted"
    assert _map_a2a_state(_fake_instance(status="running")) == "working"


def test_map_a2a_state_covers_terminal_states():
    from app.api.a2a import _map_a2a_state
    assert _map_a2a_state(_fake_instance(status="completed")) == "completed"
    assert _map_a2a_state(_fake_instance(status="failed")) == "failed"
    assert _map_a2a_state(_fake_instance(status="cancelled")) == "canceled"


def test_map_a2a_state_routes_suspended_by_reason():
    """A suspended instance can be HITL (no reason), async-external
    (``async_external``), or auth-required (``auth_required``). Only
    the last maps to the v1.0 auth-required state; the others fit
    input-required."""
    from app.api.a2a import _map_a2a_state

    assert _map_a2a_state(_fake_instance(status="suspended")) == "input-required"
    assert _map_a2a_state(
        _fake_instance(status="suspended", suspended_reason="async_external"),
    ) == "input-required"
    assert _map_a2a_state(
        _fake_instance(status="suspended", suspended_reason="auth_required"),
    ) == "auth-required"


def test_map_a2a_state_recognises_rejected():
    """Pre-execution validation refusal is semantically different
    from a runtime failure; the ``failed + rejected`` combo routes
    to the spec's ``rejected`` terminal state."""
    from app.api.a2a import _map_a2a_state
    assert _map_a2a_state(
        _fake_instance(status="failed", suspended_reason="rejected"),
    ) == "rejected"


def test_map_a2a_state_falls_back_to_unknown_not_working():
    """A bug that invents a new status shouldn't silently masquerade
    as ``working`` — the spec requires a valid enum and ``unknown``
    tells the client "we genuinely don't know"."""
    from app.api.a2a import _map_a2a_state
    assert _map_a2a_state(_fake_instance(status="some-future-state")) == "unknown"
    # None status (e.g. freshly-constructed instance before any
    # persist) also goes to unknown instead of leaking a false
    # lifecycle claim.
    inst = _fake_instance(status="running")
    inst.status = None
    assert _map_a2a_state(inst) == "unknown"
