"""Unit tests for the DV-01 pin / unpin workflow endpoints.

Covers:
  * POST /workflows/{id}/nodes/{node_id}/pin — writes pinnedOutput into
    the matching graph_json.nodes[*].data block
  * DELETE /workflows/{id}/nodes/{node_id}/pin — clears it
  * 404 on unknown node_id (graph doesn't contain that id)
  * 404 on unknown workflow / wrong tenant

Uses MagicMock-backed Session (same pattern as the secrets and
tenant_integrations tests). The full round-trip against a real DB is
exercised by the Sprint 2A end-to-end integration test.
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


def _make_wf(
    *,
    wf_id=None,
    graph=None,
) -> MagicMock:
    wf = MagicMock()
    wf.id = wf_id or uuid.uuid4()
    wf.tenant_id = TENANT
    wf.name = "Test Workflow"
    wf.description = None
    wf.version = 1
    wf.is_active = True
    wf.created_at = datetime(2026, 4, 21, tzinfo=timezone.utc)
    wf.updated_at = datetime(2026, 4, 21, tzinfo=timezone.utc)
    wf.graph_json = graph or {
        "nodes": [
            {"id": "node_1", "type": "agenticNode",
             "data": {"label": "LLM Agent", "nodeCategory": "agent", "config": {}}},
            {"id": "node_2", "type": "agenticNode",
             "data": {"label": "MCP Tool", "nodeCategory": "action", "config": {}}},
        ],
        "edges": [],
    }
    return wf


class TestPinNodeOutput:
    def test_sets_pinned_output_on_matching_node(self, client_and_session):
        client, session = client_and_session
        wf = _make_wf()
        session.query.return_value.filter_by.return_value.first.return_value = wf

        payload = {"response": "Hello", "usage": {"input_tokens": 12}}
        res = client.post(
            f"/api/v1/workflows/{wf.id}/nodes/node_1/pin",
            json={"output": payload},
        )
        assert res.status_code == 200
        session.commit.assert_called_once()

        # graph_json was mutated in place on the mock; verify the write
        pinned = next(
            n["data"].get("pinnedOutput")
            for n in wf.graph_json["nodes"] if n["id"] == "node_1"
        )
        assert pinned == payload

        # Other nodes untouched
        other = next(
            n for n in wf.graph_json["nodes"] if n["id"] == "node_2"
        )
        assert "pinnedOutput" not in other["data"]

    def test_pinning_does_not_bump_version(self, client_and_session):
        """Pins are a design-time annotation — avoid snapshot churn."""
        client, session = client_and_session
        wf = _make_wf()
        original_version = wf.version
        session.query.return_value.filter_by.return_value.first.return_value = wf

        client.post(
            f"/api/v1/workflows/{wf.id}/nodes/node_1/pin",
            json={"output": {"a": 1}},
        )
        assert wf.version == original_version

    def test_overwriting_existing_pin_replaces_it_wholesale(self, client_and_session):
        client, session = client_and_session
        wf = _make_wf(graph={
            "nodes": [
                {"id": "node_1", "type": "agenticNode",
                 "data": {"label": "x", "nodeCategory": "agent", "config": {},
                          "pinnedOutput": {"old": "value"}}},
            ],
            "edges": [],
        })
        session.query.return_value.filter_by.return_value.first.return_value = wf

        res = client.post(
            f"/api/v1/workflows/{wf.id}/nodes/node_1/pin",
            json={"output": {"new": "value"}},
        )
        assert res.status_code == 200

        pinned = wf.graph_json["nodes"][0]["data"]["pinnedOutput"]
        assert pinned == {"new": "value"}

    def test_unknown_node_id_returns_404(self, client_and_session):
        client, session = client_and_session
        wf = _make_wf()
        session.query.return_value.filter_by.return_value.first.return_value = wf

        res = client.post(
            f"/api/v1/workflows/{wf.id}/nodes/node_999/pin",
            json={"output": {}},
        )
        assert res.status_code == 404
        assert "node_999" in res.json()["detail"]
        session.commit.assert_not_called()

    def test_unknown_workflow_returns_404(self, client_and_session):
        client, session = client_and_session
        session.query.return_value.filter_by.return_value.first.return_value = None

        res = client.post(
            f"/api/v1/workflows/{uuid.uuid4()}/nodes/node_1/pin",
            json={"output": {}},
        )
        assert res.status_code == 404
        session.commit.assert_not_called()


class TestUnpinNodeOutput:
    def test_clears_pinned_output_on_matching_node(self, client_and_session):
        client, session = client_and_session
        wf = _make_wf(graph={
            "nodes": [
                {"id": "node_1", "type": "agenticNode",
                 "data": {"label": "x", "nodeCategory": "agent", "config": {},
                          "pinnedOutput": {"cached": "value"}}},
            ],
            "edges": [],
        })
        session.query.return_value.filter_by.return_value.first.return_value = wf

        res = client.delete(f"/api/v1/workflows/{wf.id}/nodes/node_1/pin")
        assert res.status_code == 200
        session.commit.assert_called_once()

        data = wf.graph_json["nodes"][0]["data"]
        assert "pinnedOutput" not in data

    def test_unpin_when_no_pin_set_is_a_noop(self, client_and_session):
        """Idempotent — operators shouldn't get 404 for clearing nothing."""
        client, session = client_and_session
        wf = _make_wf()   # no pins
        session.query.return_value.filter_by.return_value.first.return_value = wf

        res = client.delete(f"/api/v1/workflows/{wf.id}/nodes/node_1/pin")
        assert res.status_code == 200

    def test_unpin_unknown_node_returns_404(self, client_and_session):
        client, session = client_and_session
        wf = _make_wf()
        session.query.return_value.filter_by.return_value.first.return_value = wf

        res = client.delete(f"/api/v1/workflows/{wf.id}/nodes/node_999/pin")
        assert res.status_code == 404
        session.commit.assert_not_called()
