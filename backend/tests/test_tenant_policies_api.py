"""ADMIN-01 — tests for the /api/v1/tenant-policy singleton router.

Covers:

* GET with no row returns env defaults + env_default sources.
* GET with a partial-override row returns mixed sources and the
  overridden values.
* PATCH with integer values upserts the row; re-GET reflects them.
* PATCH with explicit null for a field *clears* the override so that
  field falls through to env next call.
* PATCH with an omitted field leaves the prior override alone.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.tenant_policies import router as policies_router


TENANT = "tenant-a"


@pytest.fixture
def client_and_session():
    app = FastAPI()
    app.include_router(policies_router, prefix="/api/v1/tenant-policy")

    from app.database import get_db, get_tenant_db
    from app.security.tenant import get_tenant_id

    session = MagicMock()

    def _fake_get_db():
        yield session

    app.dependency_overrides[get_tenant_id] = lambda: TENANT
    app.dependency_overrides[get_db] = _fake_get_db
    app.dependency_overrides[get_tenant_db] = _fake_get_db
    return TestClient(app), session


@pytest.fixture
def patched_settings():
    from app.config import settings

    with patch.object(settings, "execution_quota_per_hour", 50), \
         patch.object(settings, "max_snapshots", 20), \
         patch.object(settings, "mcp_pool_size", 4), \
         patch.object(settings, "rate_limit_requests", 100), \
         patch.object(settings, "rate_limit_window_seconds", 60):
        yield settings


def _row(
    execution_quota_per_hour: int | None = None,
    max_snapshots: int | None = None,
    mcp_pool_size: int | None = None,
    rate_limit_requests_per_window: int | None = None,
    rate_limit_window_seconds: int | None = None,
    updated_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
):
    row = MagicMock()
    row.tenant_id = TENANT
    row.execution_quota_per_hour = execution_quota_per_hour
    row.max_snapshots = max_snapshots
    row.mcp_pool_size = mcp_pool_size
    row.rate_limit_requests_per_window = rate_limit_requests_per_window
    row.rate_limit_window_seconds = rate_limit_window_seconds
    row.updated_at = updated_at
    return row


def _wire_resolver_row(row):
    """Make the resolver's own SessionLocal call return a MagicMock
    whose query chain yields ``row``."""
    resolver_session = MagicMock()
    resolver_session.query.return_value.filter_by.return_value.first.return_value = row
    return patch("app.database.SessionLocal", return_value=resolver_session), \
           patch("app.database.set_tenant_context")


class TestGetPolicy:
    def test_no_row_returns_env_defaults(self, client_and_session, patched_settings):
        c, session = client_and_session
        # First filter_by is on the API's session (for updated_at lookup);
        # the resolver's SessionLocal is patched below.
        session.query.return_value.filter_by.return_value.first.return_value = None

        resolver_ctx = _wire_resolver_row(None)
        with resolver_ctx[0], resolver_ctx[1]:
            res = c.get("/api/v1/tenant-policy")

        assert res.status_code == 200
        body = res.json()
        assert body["tenant_id"] == TENANT
        assert body["values"] == {
            "execution_quota_per_hour": 50,
            "max_snapshots": 20,
            "mcp_pool_size": 4,
            "rate_limit_requests_per_window": 100,
            "rate_limit_window_seconds": 60,
        }
        assert body["source"] == {
            "execution_quota_per_hour": "env_default",
            "max_snapshots": "env_default",
            "mcp_pool_size": "env_default",
            "rate_limit_requests_per_window": "env_default",
            "rate_limit_window_seconds": "env_default",
        }
        assert body["updated_at"] is None

    def test_partial_override_returns_mixed_sources(self, client_and_session, patched_settings):
        c, session = client_and_session
        row = _row(execution_quota_per_hour=500)
        session.query.return_value.filter_by.return_value.first.return_value = row

        resolver_ctx = _wire_resolver_row(row)
        with resolver_ctx[0], resolver_ctx[1]:
            res = c.get("/api/v1/tenant-policy")

        assert res.status_code == 200
        body = res.json()
        assert body["values"]["execution_quota_per_hour"] == 500
        assert body["values"]["max_snapshots"] == 20  # env fallback
        assert body["source"]["execution_quota_per_hour"] == "tenant_policy"
        assert body["source"]["max_snapshots"] == "env_default"


class TestPatchPolicy:
    def test_patch_creates_row_when_absent(self, client_and_session, patched_settings):
        c, session = client_and_session
        # API session starts with no row
        session.query.return_value.filter_by.return_value.first.return_value = None

        # After the UPSERT, the resolver re-reads and finds a row with
        # the new override.
        newly_created = _row(execution_quota_per_hour=300)
        # First call (API _get existing) returns None; subsequent
        # resolver call returns the new row.
        resolver_session = MagicMock()
        resolver_session.query.return_value.filter_by.return_value.first.return_value = newly_created

        with patch("app.database.SessionLocal", return_value=resolver_session), \
             patch("app.database.set_tenant_context"):
            res = c.patch("/api/v1/tenant-policy", json={"execution_quota_per_hour": 300})

        assert res.status_code == 200
        # session.add was called with a TenantPolicy instance.
        from app.models.workflow import TenantPolicy
        added = [
            call.args[0]
            for call in session.add.call_args_list
            if isinstance(call.args[0], TenantPolicy)
        ]
        assert len(added) == 1

        body = res.json()
        assert body["values"]["execution_quota_per_hour"] == 300
        assert body["source"]["execution_quota_per_hour"] == "tenant_policy"

    def test_patch_updates_existing_row(self, client_and_session, patched_settings):
        c, session = client_and_session
        row = _row(execution_quota_per_hour=100)
        session.query.return_value.filter_by.return_value.first.return_value = row

        # Resolver reads the (already-mutated) same row.
        resolver_ctx = _wire_resolver_row(row)
        with resolver_ctx[0], resolver_ctx[1]:
            res = c.patch(
                "/api/v1/tenant-policy",
                json={"execution_quota_per_hour": 777},
            )

        assert res.status_code == 200
        # The handler mutated the existing row rather than adding a new one.
        assert row.execution_quota_per_hour == 777

    def test_patch_with_null_clears_override(self, client_and_session, patched_settings):
        c, session = client_and_session
        row = _row(execution_quota_per_hour=777, max_snapshots=5)
        session.query.return_value.filter_by.return_value.first.return_value = row

        resolver_ctx = _wire_resolver_row(row)
        with resolver_ctx[0], resolver_ctx[1]:
            res = c.patch(
                "/api/v1/tenant-policy",
                json={"execution_quota_per_hour": None},
            )

        assert res.status_code == 200
        # Override cleared — the column is now None.
        assert row.execution_quota_per_hour is None
        # max_snapshots was NOT in the body, so the prior override is
        # left alone.
        assert row.max_snapshots == 5

    def test_patch_rejects_negative_quota(self, client_and_session, patched_settings):
        c, _ = client_and_session
        res = c.patch(
            "/api/v1/tenant-policy",
            json={"execution_quota_per_hour": -1},
        )
        # Pydantic ge=1 validation surfaces as 422.
        assert res.status_code == 422

    def test_patch_rejects_negative_pool_size(self, client_and_session, patched_settings):
        c, _ = client_and_session
        res = c.patch(
            "/api/v1/tenant-policy",
            json={"mcp_pool_size": 0},
        )
        assert res.status_code == 422
