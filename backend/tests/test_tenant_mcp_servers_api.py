"""MCP-02 — Unit tests for the tenant_mcp_servers CRUD endpoints.

Covers label uniqueness, default-swapping (partial unique index guard),
auth_mode validation, 404 / 422 on lookup, and the PATCH label-rename
collision path. MagicMock-backed session so no DB required.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.tenant_mcp_servers import router as mcp_servers_router


TENANT = "tenant-a"


@pytest.fixture
def client_and_session():
    app = FastAPI()
    app.include_router(
        mcp_servers_router,
        prefix="/api/v1/tenant-mcp-servers",
        tags=["tenant-mcp-servers"],
    )

    from app.database import get_db
    from app.security.tenant import get_tenant_id

    session = MagicMock()

    def _fake_get_db():
        yield session

    app.dependency_overrides[get_tenant_id] = lambda: TENANT
    app.dependency_overrides[get_db] = _fake_get_db
    return TestClient(app), session


def _mock_row(
    row_id=None,
    *,
    label: str = "default-mcp",
    url: str = "https://mcp.example.com/mcp",
    auth_mode: str = "none",
    config: dict | None = None,
    is_default: bool = False,
) -> MagicMock:
    row = MagicMock()
    row.id = row_id or uuid.uuid4()
    row.tenant_id = TENANT
    row.label = label
    row.url = url
    row.auth_mode = auth_mode
    row.config_json = config or {}
    row.is_default = is_default
    row.created_at = datetime(2026, 4, 21, tzinfo=timezone.utc)
    row.updated_at = datetime(2026, 4, 21, tzinfo=timezone.utc)
    return row


class TestCreate:
    def test_creates_new_server(self, client_and_session):
        c, session = client_and_session
        session.query.return_value.filter_by.return_value.first.return_value = None

        res = c.post("/api/v1/tenant-mcp-servers", json={
            "label": "github",
            "url": "https://mcp.github.com/mcp",
            "auth_mode": "static_headers",
            "config_json": {"headers": {"Authorization": "Bearer {{ env.GH_TOKEN }}"}},
            "is_default": False,
        })

        assert res.status_code == 201
        body = res.json()
        assert body["label"] == "github"
        assert body["auth_mode"] == "static_headers"
        session.add.assert_called_once()
        session.commit.assert_called_once()

    def test_duplicate_label_returns_409(self, client_and_session):
        c, session = client_and_session
        existing = _mock_row(label="github")
        session.query.return_value.filter_by.return_value.first.return_value = existing

        res = c.post("/api/v1/tenant-mcp-servers", json={
            "label": "github",
            "url": "https://mcp.github.com/mcp",
        })

        assert res.status_code == 409
        session.add.assert_not_called()

    def test_default_creation_clears_prior_default(self, client_and_session):
        c, session = client_and_session
        prior_default = _mock_row(label="old-default", is_default=True)

        q = session.query.return_value
        q.filter_by.return_value.first.return_value = None
        q.filter_by.return_value.all.return_value = [prior_default]

        res = c.post("/api/v1/tenant-mcp-servers", json={
            "label": "new-default",
            "url": "https://mcp.example.com/mcp",
            "is_default": True,
        })

        assert res.status_code == 201
        assert prior_default.is_default is False

    def test_rejects_unknown_auth_mode(self, client_and_session):
        c, _ = client_and_session
        res = c.post("/api/v1/tenant-mcp-servers", json={
            "label": "x",
            "url": "https://x/mcp",
            "auth_mode": "made-up",
        })
        # Pydantic Literal validation surfaces as 422, not 400.
        assert res.status_code == 422

    def test_oauth_2_1_is_accepted_but_not_yet_runnable(self, client_and_session):
        # The API accepts the value (so the registry row can be created
        # ahead of MCP-03); only the runtime resolver rejects it.
        c, session = client_and_session
        session.query.return_value.filter_by.return_value.first.return_value = None

        res = c.post("/api/v1/tenant-mcp-servers", json={
            "label": "future",
            "url": "https://future/mcp",
            "auth_mode": "oauth_2_1",
        })

        assert res.status_code == 201


class TestList:
    def test_lists_rows_for_tenant(self, client_and_session):
        c, session = client_and_session
        rows = [_mock_row(label="a"), _mock_row(label="b", is_default=True)]
        query = session.query.return_value
        query.filter_by.return_value.order_by.return_value.all.return_value = rows

        res = c.get("/api/v1/tenant-mcp-servers")

        assert res.status_code == 200
        assert [r["label"] for r in res.json()] == ["a", "b"]


class TestGet:
    def test_returns_row_when_found(self, client_and_session):
        c, session = client_and_session
        row = _mock_row(label="github")
        session.query.return_value.filter_by.return_value.first.return_value = row

        res = c.get(f"/api/v1/tenant-mcp-servers/{row.id}")

        assert res.status_code == 200
        assert res.json()["label"] == "github"

    def test_returns_404_when_missing(self, client_and_session):
        c, session = client_and_session
        session.query.return_value.filter_by.return_value.first.return_value = None

        res = c.get(f"/api/v1/tenant-mcp-servers/{uuid.uuid4()}")
        assert res.status_code == 404

    def test_returns_422_on_non_uuid(self, client_and_session):
        c, _ = client_and_session
        res = c.get("/api/v1/tenant-mcp-servers/not-a-uuid")
        assert res.status_code == 422


class TestUpdate:
    def test_partial_update_of_url(self, client_and_session):
        c, session = client_and_session
        row = _mock_row(url="https://old/mcp")
        session.query.return_value.filter_by.return_value.first.return_value = row

        res = c.patch(f"/api/v1/tenant-mcp-servers/{row.id}", json={
            "url": "https://new/mcp",
        })

        assert res.status_code == 200
        assert row.url == "https://new/mcp"
        # Unchanged fields remain intact
        assert row.label == "default-mcp"

    def test_flipping_to_default_clears_prior_default(self, client_and_session):
        c, session = client_and_session
        target = _mock_row(label="new-default", is_default=False)
        prior_default = _mock_row(label="old-default", is_default=True)

        q = session.query.return_value
        q.filter_by.return_value.first.return_value = target
        q.filter_by.return_value.filter.return_value.all.return_value = [prior_default]

        res = c.patch(f"/api/v1/tenant-mcp-servers/{target.id}", json={
            "is_default": True,
        })

        assert res.status_code == 200
        assert target.is_default is True
        assert prior_default.is_default is False

    def test_rename_collision_returns_409(self, client_and_session):
        c, session = client_and_session
        row = _mock_row(label="old-label")
        clash = _mock_row(label="taken")

        q = session.query.return_value
        q.filter_by.return_value.first.side_effect = [row, clash]

        res = c.patch(f"/api/v1/tenant-mcp-servers/{row.id}", json={"label": "taken"})

        assert res.status_code == 409
        assert row.label == "old-label"


class TestDelete:
    def test_deletes_when_found(self, client_and_session):
        c, session = client_and_session
        row = _mock_row()
        session.query.return_value.filter_by.return_value.first.return_value = row

        res = c.delete(f"/api/v1/tenant-mcp-servers/{row.id}")

        assert res.status_code == 204
        session.delete.assert_called_once_with(row)

    def test_missing_returns_404(self, client_and_session):
        c, session = client_and_session
        session.query.return_value.filter_by.return_value.first.return_value = None

        res = c.delete(f"/api/v1/tenant-mcp-servers/{uuid.uuid4()}")
        assert res.status_code == 404
        session.delete.assert_not_called()
