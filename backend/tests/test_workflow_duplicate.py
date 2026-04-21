"""DV-05 — Unit tests for POST /workflows/{id}/duplicate.

Covers:
  * Happy path: new row created with a copy-suffixed name, version=1,
    is_active=True, deep-copied graph_json (including pinnedOutput).
  * Collision handling: multiple duplicates produce (copy), (copy 2),
    (copy 3) — never clobber each other.
  * 404 when the source doesn't exist under this tenant.

Mirrors the MagicMock-Session pattern used by test_pin_endpoints.py and
test_test_node_endpoint.py. A full round-trip against a real DB is
exercised by the Sprint 2A integration suite.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.workflows import router as workflows_router


TENANT = "tenant-a"


@pytest.fixture
def client_and_session():
    app = FastAPI()
    app.include_router(workflows_router)

    from app.database import get_db, get_tenant_db
    from app.security.tenant import get_tenant_id

    session = MagicMock()

    def _fake_get_db():
        yield session

    app.dependency_overrides[get_tenant_id] = lambda: TENANT
    app.dependency_overrides[get_db] = _fake_get_db
    app.dependency_overrides[get_tenant_db] = _fake_get_db
    return TestClient(app), session


def _make_wf(*, name: str = "My Flow", wf_id=None, graph=None) -> MagicMock:
    wf = MagicMock()
    wf.id = wf_id or uuid.uuid4()
    wf.tenant_id = TENANT
    wf.name = name
    wf.description = "original desc"
    wf.version = 3  # intentionally non-1 to confirm clone resets to 1
    wf.is_active = False  # source inactive — clone should still be active
    wf.created_at = datetime(2026, 4, 21, tzinfo=timezone.utc)
    wf.updated_at = datetime(2026, 4, 21, tzinfo=timezone.utc)
    wf.graph_json = graph or {
        "nodes": [
            {
                "id": "node_1",
                "type": "agenticNode",
                "data": {
                    "label": "LLM Agent",
                    "nodeCategory": "agent",
                    "config": {"systemPrompt": "hi"},
                    "pinnedOutput": {"response": "Hi", "usage": {"tokens": 5}},
                },
            },
            {
                "id": "node_2",
                "type": "agenticNode",
                "data": {"label": "Condition", "nodeCategory": "logic", "config": {}},
            },
        ],
        "edges": [{"source": "node_1", "target": "node_2"}],
    }
    return wf


def _captured_clone(session: MagicMock):
    """Return the WorkflowDefinition argument the endpoint handed to session.add()."""
    from app.models.workflow import WorkflowDefinition

    for call in session.add.call_args_list:
        (arg,), _ = call
        if isinstance(arg, WorkflowDefinition):
            return arg
    raise AssertionError("No WorkflowDefinition was added to the session")


def _install_query_responses(session: MagicMock, src: MagicMock, existing_names: list[str]):
    """Wire up the two distinct queries the endpoint runs:

    1. ``query(WorkflowDefinition).filter_by(id=..., tenant_id=...).first()``
       → return ``src`` (or None if the ID won't match)
    2. ``query(WorkflowDefinition.name).filter_by(tenant_id=...).all()``
       → return tuples shaped like real SQLAlchemy column rows

    Also make ``session.refresh(clone)`` populate the server-side fields
    (``id``, timestamps) so the response model can serialise.
    """
    from app.models.workflow import WorkflowDefinition

    name_rows = [(name,) for name in existing_names]

    def _query(*args, **kwargs):
        q = MagicMock()
        # Route the "name list" query (which asks for a column, not the
        # full model) to the tuple-shaped response; everything else goes
        # to the source-lookup response.
        if args and args[0] is not WorkflowDefinition:
            q.filter_by.return_value.all.return_value = name_rows
        else:
            q.filter_by.return_value.first.return_value = src
            q.filter_by.return_value.all.return_value = name_rows
        return q

    session.query.side_effect = _query

    def _refresh(obj):
        # Populate the server-side defaults Postgres would fill on commit
        # so the WorkflowOut response model sees valid values.
        if isinstance(obj, WorkflowDefinition):
            if obj.id is None:
                obj.id = uuid.uuid4()
            now = datetime(2026, 4, 21, 12, 0, tzinfo=timezone.utc)
            if obj.created_at is None:
                obj.created_at = now
            if obj.updated_at is None:
                obj.updated_at = now

    session.refresh.side_effect = _refresh


class TestDuplicateHappyPath:
    def test_creates_row_with_copy_suffix(self, client_and_session):
        client, session = client_and_session
        src = _make_wf(name="Customer Intake")
        _install_query_responses(session, src, existing_names=["Customer Intake"])

        resp = client.post(f"/api/v1/workflows/{src.id}/duplicate")

        assert resp.status_code == 201
        clone = _captured_clone(session)
        assert clone.name == "Customer Intake (copy)"
        assert clone.version == 1
        assert clone.is_active is True
        # Description + graph copied
        assert clone.description == "original desc"
        assert clone.graph_json["nodes"][0]["data"]["pinnedOutput"] == {
            "response": "Hi",
            "usage": {"tokens": 5},
        }

    def test_deep_copies_graph_json(self, client_and_session):
        client, session = client_and_session
        src = _make_wf()
        _install_query_responses(session, src, existing_names=[src.name])

        client.post(f"/api/v1/workflows/{src.id}/duplicate")
        clone = _captured_clone(session)

        # Mutating the clone's graph must not touch the source.
        clone.graph_json["nodes"][0]["data"]["pinnedOutput"]["response"] = "changed"
        assert src.graph_json["nodes"][0]["data"]["pinnedOutput"]["response"] == "Hi"

    def test_inactive_source_produces_active_clone(self, client_and_session):
        client, session = client_and_session
        src = _make_wf()
        src.is_active = False
        _install_query_responses(session, src, existing_names=[src.name])

        client.post(f"/api/v1/workflows/{src.id}/duplicate")
        clone = _captured_clone(session)

        assert clone.is_active is True


class TestDuplicateNameCollision:
    def test_second_duplicate_gets_numbered_suffix(self, client_and_session):
        client, session = client_and_session
        src = _make_wf(name="Onboarding")
        _install_query_responses(
            session,
            src,
            existing_names=["Onboarding", "Onboarding (copy)"],
        )

        client.post(f"/api/v1/workflows/{src.id}/duplicate")
        clone = _captured_clone(session)

        assert clone.name == "Onboarding (copy 2)"

    def test_skips_numbered_holes_to_first_free(self, client_and_session):
        client, session = client_and_session
        src = _make_wf(name="Onboarding")
        _install_query_responses(
            session,
            src,
            existing_names=[
                "Onboarding",
                "Onboarding (copy)",
                "Onboarding (copy 2)",
                "Onboarding (copy 3)",
            ],
        )

        client.post(f"/api/v1/workflows/{src.id}/duplicate")
        clone = _captured_clone(session)

        assert clone.name == "Onboarding (copy 4)"

    def test_duplicating_a_copy_reads_naturally(self, client_and_session):
        # Duplicating "Foo (copy)" should yield "Foo (copy) (copy)" —
        # the source's existing suffix is preserved, we don't try to
        # be clever about collapsing nested copies.
        client, session = client_and_session
        src = _make_wf(name="Foo (copy)")
        _install_query_responses(session, src, existing_names=["Foo", "Foo (copy)"])

        client.post(f"/api/v1/workflows/{src.id}/duplicate")
        clone = _captured_clone(session)

        assert clone.name == "Foo (copy) (copy)"


class TestDuplicateNotFound:
    def test_returns_404_when_source_missing(self, client_and_session):
        client, session = client_and_session
        # Source lookup returns None — endpoint must 404 and NEVER call
        # session.add() with a WorkflowDefinition clone.
        _install_query_responses(session, src=None, existing_names=[])

        resp = client.post(f"/api/v1/workflows/{uuid.uuid4()}/duplicate")

        assert resp.status_code == 404
        from app.models.workflow import WorkflowDefinition
        for call in session.add.call_args_list:
            (arg,), _ = call
            assert not isinstance(arg, WorkflowDefinition), (
                "Clone must not be created when the source is missing"
            )
