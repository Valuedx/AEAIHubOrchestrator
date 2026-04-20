"""DV-07 — Unit tests for the is_active flag on workflow PATCH.

Covers:
  * PATCH with ``{is_active: false}`` toggles the flag without bumping
    ``version`` or writing a snapshot (the whole point: toggling should
    not churn the snapshot history).
  * PATCH with both ``is_active`` and ``graph_json`` in the same body
    still snapshot + version-bumps for the graph change.
  * Default is True on newly created rows (covered by the column
    definition; we verify WorkflowOut serialises ``is_active`` through).
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

    from app.database import get_db
    from app.security.tenant import get_tenant_id

    session = MagicMock()

    def _fake_get_db():
        yield session

    app.dependency_overrides[get_tenant_id] = lambda: TENANT
    app.dependency_overrides[get_db] = _fake_get_db
    return TestClient(app), session


def _make_wf(*, is_active: bool = True, version: int = 2) -> MagicMock:
    wf = MagicMock()
    wf.id = uuid.uuid4()
    wf.tenant_id = TENANT
    wf.name = "Flow"
    wf.description = None
    wf.version = version
    wf.is_active = is_active
    wf.created_at = datetime(2026, 4, 21, tzinfo=timezone.utc)
    wf.updated_at = datetime(2026, 4, 21, tzinfo=timezone.utc)
    wf.graph_json = {"nodes": [], "edges": []}
    return wf


class TestIsActiveToggle:
    def test_patch_toggles_off_without_version_bump(self, client_and_session):
        client, session = client_and_session
        wf = _make_wf(is_active=True, version=2)
        session.query.return_value.filter_by.return_value.first.return_value = wf

        resp = client.patch(
            f"/api/v1/workflows/{wf.id}",
            json={"is_active": False},
        )

        assert resp.status_code == 200
        assert wf.is_active is False
        # Version must NOT change when only is_active was touched.
        assert wf.version == 2
        # No snapshot written for is_active-only updates.
        from app.models.workflow import WorkflowSnapshot
        for call in session.add.call_args_list:
            (arg,), _ = call
            assert not isinstance(arg, WorkflowSnapshot), (
                "Snapshot must not be written on is_active-only PATCH"
            )

    def test_patch_toggles_on(self, client_and_session):
        client, session = client_and_session
        wf = _make_wf(is_active=False, version=5)
        session.query.return_value.filter_by.return_value.first.return_value = wf

        resp = client.patch(
            f"/api/v1/workflows/{wf.id}",
            json={"is_active": True},
        )

        assert resp.status_code == 200
        assert wf.is_active is True
        assert wf.version == 5

    def test_patch_is_active_plus_graph_snapshots_and_bumps(self, client_and_session):
        client, session = client_and_session
        wf = _make_wf(is_active=True, version=3)
        session.query.return_value.filter_by.return_value.first.return_value = wf
        # PATCH with graph_json triggers precompute_node_embeddings and
        # config validation. Patch those off so the test stays focused
        # on the snapshot/version behaviour.
        import app.engine.config_validator as cv
        import app.engine.embedding_cache_helper as ech
        cv.validate_graph_configs = lambda _: []
        ech.precompute_node_embeddings = lambda *_a, **_k: []

        new_graph = {"nodes": [{"id": "n1", "type": "agenticNode", "data": {}}], "edges": []}
        resp = client.patch(
            f"/api/v1/workflows/{wf.id}",
            json={"is_active": False, "graph_json": new_graph},
        )

        assert resp.status_code == 200
        assert wf.is_active is False
        assert wf.version == 4
        # Snapshot was written for the graph change.
        from app.models.workflow import WorkflowSnapshot
        snapshot_adds = [
            c for c in session.add.call_args_list
            if isinstance(c.args[0], WorkflowSnapshot)
        ]
        assert len(snapshot_adds) == 1

    def test_patch_without_is_active_leaves_flag_untouched(self, client_and_session):
        client, session = client_and_session
        wf = _make_wf(is_active=False, version=1)
        session.query.return_value.filter_by.return_value.first.return_value = wf

        client.patch(
            f"/api/v1/workflows/{wf.id}",
            json={"name": "renamed"},
        )

        # is_active was not in the body → unchanged.
        assert wf.is_active is False
        assert wf.name == "renamed"


class TestIsActiveSerialisation:
    def test_workflow_out_includes_is_active(self, client_and_session):
        client, session = client_and_session
        wf = _make_wf(is_active=False, version=1)
        session.query.return_value.filter_by.return_value.first.return_value = wf

        resp = client.get(f"/api/v1/workflows/{wf.id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["is_active"] is False
