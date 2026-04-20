"""Unit tests for the tenant_integrations CRUD endpoints.

Exercises the endpoint logic (label uniqueness, default-swapping,
system allowlist, 404 on unknown id) via FastAPI TestClient with a
MagicMock-backed session. The end-to-end RLS behaviour + the partial
unique index are exercised separately in the integration tests.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.tenant_integrations import router as integrations_router


TENANT = "tenant-a"


@pytest.fixture
def app_and_session():
    app = FastAPI()
    app.include_router(
        integrations_router,
        prefix="/api/v1/tenant-integrations",
        tags=["tenant-integrations"],
    )

    # Override the two FastAPI dependencies: tenant resolver (pin to TENANT)
    # and get_db (yield a MagicMock session per request).
    from app.database import get_db
    from app.security.tenant import get_tenant_id

    session = MagicMock()

    def _fake_tenant_id():
        return TENANT

    def _fake_get_db():
        yield session

    app.dependency_overrides[get_tenant_id] = _fake_tenant_id
    app.dependency_overrides[get_db] = _fake_get_db

    return app, session


@pytest.fixture
def client(app_and_session):
    app, session = app_and_session
    return TestClient(app), session


def _mock_row(
    row_id=None,
    *,
    system="automationedge",
    label="prod-ae",
    config=None,
    is_default=False,
):
    row = MagicMock()
    row.id = row_id or uuid.uuid4()
    row.tenant_id = TENANT
    row.system = system
    row.label = label
    row.config_json = config or {"baseUrl": "http://ae/rest", "orgCode": "X"}
    row.is_default = is_default
    row.created_at = datetime(2026, 4, 21, tzinfo=timezone.utc)
    row.updated_at = datetime(2026, 4, 21, tzinfo=timezone.utc)
    return row


class TestCreate:
    def test_creates_new_integration(self, client):
        c, session = client
        # No existing row with this label
        session.query.return_value.filter_by.return_value.first.return_value = None

        res = c.post("/api/v1/tenant-integrations", json={
            "system": "automationedge",
            "label": "prod-ae",
            "config_json": {"baseUrl": "http://ae/rest", "orgCode": "PROD"},
            "is_default": False,
        })
        assert res.status_code == 201
        body = res.json()
        assert body["label"] == "prod-ae"
        assert body["system"] == "automationedge"
        assert body["is_default"] is False
        session.add.assert_called_once()
        session.commit.assert_called_once()

    def test_rejects_unknown_system(self, client):
        c, _ = client
        res = c.post("/api/v1/tenant-integrations", json={
            "system": "made-up",
            "label": "x",
            "config_json": {},
        })
        assert res.status_code == 400
        assert "Supported" in res.json()["detail"]

    def test_duplicate_label_returns_409(self, client):
        c, session = client
        existing = _mock_row()
        session.query.return_value.filter_by.return_value.first.return_value = existing

        res = c.post("/api/v1/tenant-integrations", json={
            "system": "automationedge",
            "label": "prod-ae",
            "config_json": {},
        })
        assert res.status_code == 409
        session.add.assert_not_called()

    def test_default_creation_clears_prior_default(self, client):
        c, session = client
        prior_default = _mock_row(label="old-default", is_default=True)

        # First lookup (existence check) returns None; second lookup
        # (clear_default) returns the prior default row.
        q = session.query.return_value
        q.filter_by.return_value.first.return_value = None
        q.filter_by.return_value.all.return_value = [prior_default]

        res = c.post("/api/v1/tenant-integrations", json={
            "system": "automationedge",
            "label": "new-default",
            "config_json": {"baseUrl": "http://ae/rest"},
            "is_default": True,
        })
        assert res.status_code == 201
        # Prior default was flipped off before the new one was added.
        assert prior_default.is_default is False


class TestList:
    def test_lists_rows_for_tenant(self, client):
        c, session = client
        rows = [_mock_row(label="dev-ae"), _mock_row(label="prod-ae", is_default=True)]
        query = session.query.return_value
        query.filter_by.return_value.order_by.return_value.all.return_value = rows

        res = c.get("/api/v1/tenant-integrations")
        assert res.status_code == 200
        labels = [r["label"] for r in res.json()]
        assert labels == ["dev-ae", "prod-ae"]

    def test_system_filter_narrows_query(self, client):
        c, session = client
        query = session.query.return_value
        filtered = query.filter_by.return_value
        # Two calls to filter_by expected (tenant, then system)
        filtered.filter_by.return_value.order_by.return_value.all.return_value = []

        res = c.get("/api/v1/tenant-integrations?system=automationedge")
        assert res.status_code == 200


class TestGet:
    def test_returns_row_when_found(self, client):
        c, session = client
        row = _mock_row()
        session.query.return_value.filter_by.return_value.first.return_value = row

        res = c.get(f"/api/v1/tenant-integrations/{row.id}")
        assert res.status_code == 200
        assert res.json()["label"] == "prod-ae"

    def test_returns_404_when_missing(self, client):
        c, session = client
        session.query.return_value.filter_by.return_value.first.return_value = None

        res = c.get(f"/api/v1/tenant-integrations/{uuid.uuid4()}")
        assert res.status_code == 404

    def test_returns_422_on_non_uuid(self, client):
        c, _ = client
        res = c.get("/api/v1/tenant-integrations/not-a-uuid")
        assert res.status_code == 422


class TestUpdate:
    def test_partial_update_of_config_json(self, client):
        c, session = client
        row = _mock_row(config={"baseUrl": "http://old/rest"})
        session.query.return_value.filter_by.return_value.first.return_value = row

        res = c.patch(f"/api/v1/tenant-integrations/{row.id}", json={
            "config_json": {"baseUrl": "http://new/rest", "orgCode": "PROD"},
        })
        assert res.status_code == 200
        assert row.config_json == {"baseUrl": "http://new/rest", "orgCode": "PROD"}
        assert row.label == "prod-ae"   # untouched

    def test_flipping_to_default_clears_prior_default(self, client):
        c, session = client
        target = _mock_row(label="new-default", is_default=False)
        prior_default = _mock_row(label="old-default", is_default=True)

        q = session.query.return_value
        # _get_or_404 — find target; _clear_default — find prior default(s).
        q.filter_by.return_value.first.return_value = target
        q.filter_by.return_value.filter.return_value.all.return_value = [prior_default]

        res = c.patch(f"/api/v1/tenant-integrations/{target.id}", json={
            "is_default": True,
        })
        assert res.status_code == 200
        assert target.is_default is True
        assert prior_default.is_default is False

    def test_rename_collision_returns_409(self, client):
        c, session = client
        row = _mock_row(label="old-label")
        clash = _mock_row(label="taken")

        # _get_or_404 returns row; label-collision lookup returns clash.
        q = session.query.return_value
        q.filter_by.return_value.first.side_effect = [row, clash]

        res = c.patch(f"/api/v1/tenant-integrations/{row.id}", json={"label": "taken"})
        assert res.status_code == 409
        assert row.label == "old-label"  # unchanged


class TestDelete:
    def test_deletes_when_found(self, client):
        c, session = client
        row = _mock_row()
        session.query.return_value.filter_by.return_value.first.return_value = row

        res = c.delete(f"/api/v1/tenant-integrations/{row.id}")
        assert res.status_code == 204
        session.delete.assert_called_once_with(row)
        session.commit.assert_called_once()

    def test_missing_returns_404(self, client):
        c, session = client
        session.query.return_value.filter_by.return_value.first.return_value = None

        res = c.delete(f"/api/v1/tenant-integrations/{uuid.uuid4()}")
        assert res.status_code == 404
        session.delete.assert_not_called()
