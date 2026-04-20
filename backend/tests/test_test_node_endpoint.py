"""Unit tests for the DV-02 test-single-node endpoint.

Exercises ``POST /api/v1/workflows/{id}/nodes/{node_id}/test`` with
a MagicMock session (matching the secrets / tenant_integrations test
style). The actual ``dispatch_node`` is patched so tests aren't
coupled to handler internals; what we're pinning down is:

  * context shape passed to dispatch (trigger, upstream pins, synthetic
    _instance_id / _current_node_id / _workflow_def_id)
  * error-catching semantics (handler raise → 200 + error string)
  * ``NodeSuspendedAsync`` → explanatory message about the side effect
  * 404 for unknown workflow and unknown node_id
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


TENANT = "tenant-a"


@pytest.fixture
def client_and_session():
    from app.api.workflows import router as workflows_router
    from app.database import get_db
    from app.security.tenant import get_tenant_id

    app = FastAPI()
    app.include_router(workflows_router)

    session = MagicMock()

    def _fake_get_db():
        yield session

    app.dependency_overrides[get_tenant_id] = lambda: TENANT
    app.dependency_overrides[get_db] = _fake_get_db
    return TestClient(app), session


def _make_wf(graph: dict | None = None) -> MagicMock:
    wf = MagicMock()
    wf.id = uuid.uuid4()
    wf.tenant_id = TENANT
    wf.name = "Test Workflow"
    wf.description = None
    wf.version = 1
    wf.created_at = datetime(2026, 4, 21, tzinfo=timezone.utc)
    wf.updated_at = datetime(2026, 4, 21, tzinfo=timezone.utc)
    wf.graph_json = graph or {
        "nodes": [
            {"id": "trigger_1", "type": "agenticNode",
             "data": {"label": "Webhook Trigger", "nodeCategory": "trigger", "config": {}}},
            {"id": "node_1", "type": "agenticNode",
             "data": {"label": "LLM Agent", "nodeCategory": "agent", "config": {},
                      "pinnedOutput": {"response": "Hi", "usage": {"tokens": 5}}}},
            {"id": "node_2", "type": "agenticNode",
             "data": {"label": "Condition", "nodeCategory": "logic",
                      "config": {"condition": "node_1.response == 'Hi'"}}},
        ],
        "edges": [],
    }
    return wf


class TestHappyPath:
    def test_dispatches_with_upstream_pins_and_trigger_in_context(
        self, client_and_session,
    ):
        client, session = client_and_session
        wf = _make_wf()
        session.query.return_value.filter_by.return_value.first.return_value = wf

        with patch(
            "app.engine.node_handlers.dispatch_node",
            return_value={"routed": "true", "branch": "yes"},
        ) as mock_dispatch:
            res = client.post(
                f"/api/v1/workflows/{wf.id}/nodes/node_2/test",
                json={"trigger_payload": {"message": "hello"}},
            )

        assert res.status_code == 200
        body = res.json()
        assert body["error"] is None
        assert body["output"] == {"routed": "true", "branch": "yes"}
        assert isinstance(body["elapsed_ms"], int)
        assert body["elapsed_ms"] >= 0

        mock_dispatch.assert_called_once()
        # First positional arg: the target node's data dict
        call = mock_dispatch.call_args
        node_data_arg = call.args[0]
        assert node_data_arg["label"] == "Condition"

        # Second positional arg: the synthetic context
        ctx = call.args[1]
        assert ctx["trigger"] == {"message": "hello"}
        # Upstream pin flowed in
        assert ctx["node_1"] == {"response": "Hi", "usage": {"tokens": 5}}
        # Synthetic internals present
        assert ctx["_current_node_id"] == "node_2"
        assert ctx["_workflow_def_id"] == str(wf.id)
        uuid.UUID(ctx["_instance_id"])  # is a real UUID

    def test_no_trigger_payload_defaults_to_empty_dict(self, client_and_session):
        client, session = client_and_session
        wf = _make_wf()
        session.query.return_value.filter_by.return_value.first.return_value = wf

        with patch(
            "app.engine.node_handlers.dispatch_node",
            return_value={"ok": True},
        ) as mock_dispatch:
            res = client.post(
                f"/api/v1/workflows/{wf.id}/nodes/node_1/test",
                json={},
            )
        assert res.status_code == 200
        ctx = mock_dispatch.call_args.args[1]
        assert ctx["trigger"] == {}

    def test_target_node_with_its_own_pin_is_short_circuited_by_dispatch(
        self, client_and_session,
    ):
        """Pinning a node and then testing it is a sensible "verify the
        pin" flow — dispatch_node's short-circuit returns the pin
        directly. The test endpoint doesn't special-case this — it
        passes the node data through as-is and lets dispatch do its
        thing."""
        client, session = client_and_session
        wf = _make_wf()
        session.query.return_value.filter_by.return_value.first.return_value = wf

        # dispatch_node is the real one — no patch. (Not strictly a unit
        # test because it exercises the pin short-circuit path, but the
        # coverage is cheap and locks the interaction.)
        res = client.post(
            f"/api/v1/workflows/{wf.id}/nodes/node_1/test",
            json={"trigger_payload": {}},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["error"] is None
        assert body["output"]["response"] == "Hi"
        assert body["output"]["_from_pin"] is True


class TestErrorHandling:
    def test_handler_raise_is_caught_and_returned_as_error(
        self, client_and_session,
    ):
        client, session = client_and_session
        wf = _make_wf()
        session.query.return_value.filter_by.return_value.first.return_value = wf

        with patch(
            "app.engine.node_handlers.dispatch_node",
            side_effect=ValueError("bad config: x must be an int"),
        ):
            res = client.post(
                f"/api/v1/workflows/{wf.id}/nodes/node_2/test",
                json={},
            )
        # HTTP 200 even though the handler raised — we want the UI to
        # iterate without treating this as a request failure.
        assert res.status_code == 200
        body = res.json()
        assert body["output"] is None
        assert "bad config" in body["error"]

    def test_node_suspended_async_returns_explanatory_message(
        self, client_and_session,
    ):
        from app.engine.exceptions import NodeSuspendedAsync

        client, session = client_and_session
        wf = _make_wf()
        session.query.return_value.filter_by.return_value.first.return_value = wf

        with patch(
            "app.engine.node_handlers.dispatch_node",
            side_effect=NodeSuspendedAsync(
                async_job_id="11111111-1111-1111-1111-111111111111",
                system="automationedge",
                external_job_id="2968",
            ),
        ):
            res = client.post(
                f"/api/v1/workflows/{wf.id}/nodes/node_1/test",
                json={},
            )
        assert res.status_code == 200
        err = res.json()["error"]
        assert "automationedge" in err
        assert "2968" in err
        assert "async_job row was created" in err

    def test_unknown_workflow_returns_404(self, client_and_session):
        client, session = client_and_session
        session.query.return_value.filter_by.return_value.first.return_value = None

        res = client.post(
            f"/api/v1/workflows/{uuid.uuid4()}/nodes/node_1/test",
            json={},
        )
        assert res.status_code == 404

    def test_unknown_node_id_returns_404(self, client_and_session):
        client, session = client_and_session
        wf = _make_wf()
        session.query.return_value.filter_by.return_value.first.return_value = wf

        res = client.post(
            f"/api/v1/workflows/{wf.id}/nodes/node_does_not_exist/test",
            json={},
        )
        assert res.status_code == 404


class TestContextIsolation:
    def test_nodes_without_pins_are_absent_from_context(
        self, client_and_session,
    ):
        """Node without ``pinnedOutput`` must NOT appear in the context
        at all — handlers that read missing keys fail loudly, which
        correctly tells the operator to pin the predecessor."""
        client, session = client_and_session
        # Same graph but strip node_1's pin.
        graph = {
            "nodes": [
                {"id": "node_1", "type": "agenticNode",
                 "data": {"label": "LLM Agent", "nodeCategory": "agent", "config": {}}},
                {"id": "node_2", "type": "agenticNode",
                 "data": {"label": "Condition", "nodeCategory": "logic", "config": {}}},
            ],
            "edges": [],
        }
        wf = _make_wf(graph=graph)
        session.query.return_value.filter_by.return_value.first.return_value = wf

        with patch(
            "app.engine.node_handlers.dispatch_node",
            return_value={"routed": "true"},
        ) as mock_dispatch:
            res = client.post(
                f"/api/v1/workflows/{wf.id}/nodes/node_2/test",
                json={},
            )
        assert res.status_code == 200
        ctx = mock_dispatch.call_args.args[1]
        assert "node_1" not in ctx
        # Synthetic keys still populated
        assert ctx["_current_node_id"] == "node_2"

    def test_does_not_write_execution_log_or_instance(
        self, client_and_session,
    ):
        """Design-time probe — no side-effect rows on workflow_instances
        or execution_logs."""
        client, session = client_and_session
        wf = _make_wf()
        session.query.return_value.filter_by.return_value.first.return_value = wf

        with patch(
            "app.engine.node_handlers.dispatch_node",
            return_value={"ok": True},
        ):
            client.post(
                f"/api/v1/workflows/{wf.id}/nodes/node_1/test",
                json={},
            )

        # No .add() calls for instance or log rows.
        session.add.assert_not_called()
