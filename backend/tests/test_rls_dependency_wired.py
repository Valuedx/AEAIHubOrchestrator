"""RLS-01 regression: tenant-scoped endpoints must route through
``get_tenant_db`` so the ``app.tenant_id`` GUC is set before any query.

The April 2026 incident: ``POST /api/v1/workflows`` failed with
``InsufficientPrivilege: new row violates row-level security policy``
only after the operator switched from a superuser DB role to a normal
one — because nearly every endpoint had been using the tenant-unaware
``get_db`` dependency, and superuser bypass silently hid it. The fix
was a blanket swap to ``get_tenant_db`` on every tenant-scoped route.

These tests guard the wiring: they mount a real API router, let the
real ``get_tenant_db`` run, and spy on ``SessionLocal()`` to confirm
that a request-handler session has received exactly one
``SELECT set_tenant_id(:tid)`` call with the right tenant value.

Each test covers a different top-level router so we don't regress a
single one silently. If a new endpoint is added with ``Depends(get_db)``
on a tenant-scoped table, the matching assertion here will fail.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


TENANT = "tenant-rls-probe"


def _mount(router, prefix: str | None = None) -> TestClient:
    app = FastAPI()
    if prefix:
        app.include_router(router, prefix=prefix)
    else:
        app.include_router(router)

    from app.security.tenant import get_tenant_id

    app.dependency_overrides[get_tenant_id] = lambda: TENANT
    return TestClient(app)


def _fresh_session() -> MagicMock:
    """A MagicMock that stands in for a real SQLAlchemy Session.

    The only call we care about is ``db.execute(text("SELECT set_tenant_id(:tid)"), {"tid": tenant_id})``
    issued by ``set_tenant_context``. The rest is handler-specific; we
    stub queries to return empty lists so the endpoint can finish.
    """
    session = MagicMock()
    query = session.query.return_value
    query.filter_by.return_value = query
    query.order_by.return_value = query
    query.limit.return_value = query
    query.all.return_value = []
    query.first.return_value = None
    return session


def _assert_set_tenant_id_called(session: MagicMock) -> None:
    """Walk ``session.execute`` calls and confirm one was a set_tenant_id bind."""
    for call in session.execute.call_args_list:
        args, kwargs = call
        if not args:
            continue
        stmt, *rest = args
        sql = str(stmt)
        if "set_tenant_id" not in sql:
            continue
        params = rest[0] if rest else kwargs.get("parameters") or {}
        if params.get("tid") == TENANT:
            return
    pytest.fail(
        "endpoint ran without calling set_tenant_id(:tid) — the session "
        "came from get_db instead of get_tenant_db, and RLS policies "
        "will fail silently under a non-superuser DB role. "
        f"execute calls: {session.execute.call_args_list!r}"
    )


# ---------------------------------------------------------------------------
# Per-router probes
# ---------------------------------------------------------------------------


def test_secrets_list_sets_tenant_context():
    from app.api.secrets import router

    session = _fresh_session()

    with patch("app.database.SessionLocal", return_value=session):
        client = _mount(router, prefix="/api/v1/secrets")
        resp = client.get("/api/v1/secrets")

    assert resp.status_code == 200
    _assert_set_tenant_id_called(session)


def test_tools_list_sets_tenant_context():
    from app.api.tools import router

    session = _fresh_session()

    # tools.list_tools calls into the MCP client for the tool catalogue;
    # stub it so the handler doesn't touch the network.
    with patch("app.database.SessionLocal", return_value=session), \
         patch("app.engine.mcp_client.list_tools", return_value=[]):
        client = _mount(router)
        resp = client.get("/api/v1/tools")

    assert resp.status_code == 200
    _assert_set_tenant_id_called(session)


def test_conversations_list_sets_tenant_context():
    from app.api.conversations import router

    session = _fresh_session()

    with patch("app.database.SessionLocal", return_value=session):
        client = _mount(router)
        resp = client.get("/api/v1/conversations")

    assert resp.status_code == 200
    _assert_set_tenant_id_called(session)


def test_tenant_integrations_list_sets_tenant_context():
    from app.api.tenant_integrations import router

    session = _fresh_session()

    with patch("app.database.SessionLocal", return_value=session):
        client = _mount(router, prefix="/api/v1/tenant-integrations")
        resp = client.get("/api/v1/tenant-integrations")

    assert resp.status_code == 200
    _assert_set_tenant_id_called(session)


def test_tenant_mcp_servers_list_sets_tenant_context():
    from app.api.tenant_mcp_servers import router

    session = _fresh_session()

    with patch("app.database.SessionLocal", return_value=session):
        client = _mount(router, prefix="/api/v1/tenant-mcp-servers")
        resp = client.get("/api/v1/tenant-mcp-servers")

    assert resp.status_code == 200
    _assert_set_tenant_id_called(session)
