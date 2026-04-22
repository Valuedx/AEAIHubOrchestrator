"""COPILOT-01b.i — integration tests for session CRUD + turn streaming.

Mocks both the SQLAlchemy session and the AgentRunner so the tests
exercise the API contract (HTTP status codes, SSE shape, 404 / 409
branches) without needing a live Postgres or Anthropic API key.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.copilot_sessions import router as sessions_router


TENANT = "tenant-a"


@pytest.fixture
def client_and_session():
    app = FastAPI()
    app.include_router(sessions_router)

    from app.database import get_db, get_tenant_db
    from app.security.tenant import get_tenant_id

    session = MagicMock()

    def _fake_get_db():
        yield session

    app.dependency_overrides[get_tenant_id] = lambda: TENANT
    app.dependency_overrides[get_db] = _fake_get_db
    app.dependency_overrides[get_tenant_db] = _fake_get_db
    return TestClient(app), session


def _mock_draft(draft_id: uuid.UUID | None = None) -> MagicMock:
    draft = MagicMock()
    draft.id = draft_id or uuid.uuid4()
    draft.tenant_id = TENANT
    draft.graph_json = {"nodes": [], "edges": []}
    draft.version = 1
    return draft


def _mock_session(
    *,
    session_id: uuid.UUID | None = None,
    draft_id: uuid.UUID | None = None,
    provider: str = "anthropic",
    model: str = "claude-sonnet-4-6",
    status: str = "active",
) -> MagicMock:
    s = MagicMock()
    s.id = session_id or uuid.uuid4()
    s.tenant_id = TENANT
    s.draft_id = draft_id or uuid.uuid4()
    s.provider = provider
    s.model = model
    s.status = status
    s.created_at = datetime.now(timezone.utc)
    s.updated_at = datetime.now(timezone.utc)
    return s


def _set_first(session: MagicMock, *returns):
    first = session.query.return_value.filter_by.return_value.first
    if len(returns) == 1:
        first.return_value = returns[0]
    else:
        first.side_effect = list(returns)


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


def test_providers_exposes_tool_surface(client_and_session):
    client, _ = client_and_session
    resp = client.get("/api/v1/copilot/sessions/providers")
    assert resp.status_code == 200
    body = resp.json()
    assert "anthropic" in body["providers"]
    assert body["default_model"]["anthropic"].startswith("claude-")
    # Eight tools surfaced — add_node, validate_graph, etc.
    assert len(body["tools"]) >= 8
    names = {t["name"] for t in body["tools"]}
    assert "add_node" in names
    assert "validate_graph" in names


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------


def test_create_session_binds_to_draft(client_and_session):
    client, session = client_and_session
    draft = _mock_draft()
    _set_first(session, draft)

    resp = client.post(
        "/api/v1/copilot/sessions",
        json={"draft_id": str(draft.id), "provider": "anthropic"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["provider"] == "anthropic"
    assert body["model"].startswith("claude-")
    assert body["status"] == "active"
    assert body["draft_id"] == str(draft.id)


def test_create_session_missing_draft_404(client_and_session):
    client, session = client_and_session
    _set_first(session, None)
    resp = client.post(
        "/api/v1/copilot/sessions",
        json={"draft_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


def test_create_session_invalid_draft_id_422(client_and_session):
    client, _ = client_and_session
    resp = client.post(
        "/api/v1/copilot/sessions",
        json={"draft_id": "not-a-uuid"},
    )
    assert resp.status_code == 422


def test_create_session_unsupported_provider_400(client_and_session):
    client, session = client_and_session
    draft = _mock_draft()
    _set_first(session, draft)
    resp = client.post(
        "/api/v1/copilot/sessions",
        json={"draft_id": str(draft.id), "provider": "ollama"},
    )
    assert resp.status_code == 400


def test_delete_session_marks_abandoned_keeps_history(client_and_session):
    client, session = client_and_session
    s = _mock_session()
    _set_first(session, s)
    resp = client.delete(f"/api/v1/copilot/sessions/{s.id}")
    assert resp.status_code == 204
    assert s.status == "abandoned"
    # Not deleted — history stays.
    session.delete.assert_not_called()


def test_get_session_not_found(client_and_session):
    client, session = client_and_session
    _set_first(session, None)
    resp = client.get(f"/api/v1/copilot/sessions/{uuid.uuid4()}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Turn streaming
# ---------------------------------------------------------------------------


def test_send_turn_streams_sse_events(client_and_session):
    client, session = client_and_session
    draft = _mock_draft()
    s = _mock_session(draft_id=draft.id)
    # Two first() calls: session lookup, then draft lookup.
    _set_first(session, s, draft)

    fake_events = [
        {"type": "assistant_text", "text": "Hi."},
        {"type": "done", "turns_added": ["..."], "final_text": "Hi."},
    ]
    with patch("app.api.copilot_sessions.AgentRunner") as MockRunner:
        MockRunner.return_value.send_turn.return_value = iter(fake_events)
        resp = client.post(
            f"/api/v1/copilot/sessions/{s.id}/turns",
            json={"text": "hello"},
        )
        assert resp.status_code == 200
        body = resp.text

    # SSE: one "data: {...}" line per event.
    data_lines = [l for l in body.split("\n") if l.startswith("data: ")]
    assert len(data_lines) == len(fake_events)
    decoded = [json.loads(l.removeprefix("data: ")) for l in data_lines]
    assert decoded[0]["type"] == "assistant_text"
    assert decoded[-1]["type"] == "done"


def test_send_turn_against_abandoned_session_409(client_and_session):
    client, session = client_and_session
    s = _mock_session(status="abandoned")
    _set_first(session, s)
    resp = client.post(
        f"/api/v1/copilot/sessions/{s.id}/turns",
        json={"text": "hello"},
    )
    assert resp.status_code == 409


def test_send_turn_session_missing_404(client_and_session):
    client, session = client_and_session
    _set_first(session, None)
    resp = client.post(
        f"/api/v1/copilot/sessions/{uuid.uuid4()}/turns",
        json={"text": "hello"},
    )
    assert resp.status_code == 404


def test_send_turn_draft_gone_404(client_and_session):
    """Session exists but the bound draft was deleted in between —
    we surface a helpful 404 rather than crash the runner."""
    client, session = client_and_session
    s = _mock_session()
    _set_first(session, s, None)
    resp = client.post(
        f"/api/v1/copilot/sessions/{s.id}/turns",
        json={"text": "hello"},
    )
    assert resp.status_code == 404


def test_send_turn_empty_text_422(client_and_session):
    client, _ = client_and_session
    resp = client.post(
        f"/api/v1/copilot/sessions/{uuid.uuid4()}/turns",
        json={"text": ""},
    )
    assert resp.status_code == 422
