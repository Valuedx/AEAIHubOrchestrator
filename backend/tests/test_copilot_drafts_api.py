"""COPILOT-01 — integration tests for the draft CRUD + tool-dispatch
+ promote endpoints.

MagicMock-backed SQLAlchemy session following the same pattern as the
other API tests in this repo — no real DB required. Focuses on the
bits the API layer owns (version check, promote-path routing, HTTP
status codes, tool-name guard) and delegates graph-level assertions
to ``test_copilot_tool_layer.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.copilot_drafts import router as drafts_router


TENANT = "tenant-a"


@pytest.fixture
def client_and_session():
    app = FastAPI()
    app.include_router(drafts_router)

    from app.database import get_db, get_tenant_db
    from app.security.tenant import get_tenant_id

    session = MagicMock()

    def _fake_get_db():
        yield session

    app.dependency_overrides[get_tenant_id] = lambda: TENANT
    app.dependency_overrides[get_db] = _fake_get_db
    app.dependency_overrides[get_tenant_db] = _fake_get_db
    return TestClient(app), session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_draft(
    *,
    draft_id: uuid.UUID | None = None,
    base_workflow_id: uuid.UUID | None = None,
    base_version_at_fork: int | None = None,
    title: str = "My Draft",
    graph_json: dict | None = None,
    version: int = 1,
) -> MagicMock:
    draft = MagicMock()
    draft.id = draft_id or uuid.uuid4()
    draft.tenant_id = TENANT
    draft.base_workflow_id = base_workflow_id
    draft.base_version_at_fork = base_version_at_fork
    draft.title = title
    draft.graph_json = graph_json or {"nodes": [], "edges": []}
    draft.version = version
    draft.created_by = None
    draft.created_at = datetime.now(timezone.utc)
    draft.updated_at = datetime.now(timezone.utc)
    return draft


def _mock_workflow(
    *,
    workflow_id: uuid.UUID | None = None,
    name: str = "existing-wf",
    version: int = 5,
    graph_json: dict | None = None,
) -> MagicMock:
    wf = MagicMock()
    wf.id = workflow_id or uuid.uuid4()
    wf.tenant_id = TENANT
    wf.name = name
    wf.version = version
    wf.graph_json = graph_json or {"nodes": [], "edges": []}
    wf.description = None
    return wf


def _set_first(session: MagicMock, *returns):
    """Configure session.query(...).filter_by(...).first() to return
    each value in order across successive calls."""
    first = session.query.return_value.filter_by.return_value.first
    if len(returns) == 1:
        first.return_value = returns[0]
    else:
        first.side_effect = list(returns)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_create_draft_blank(client_and_session):
    client, session = client_and_session
    # No base_workflow lookup needed when base_workflow_id is absent.
    resp = client.post("/api/v1/copilot/drafts", json={"title": "hello"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "hello"
    assert body["base_workflow_id"] is None
    assert body["base_version_at_fork"] is None
    assert body["version"] == 1
    assert body["graph_json"] == {"nodes": [], "edges": []}
    assert session.add.called
    assert session.commit.called


def test_create_draft_from_base_copies_graph(client_and_session):
    client, session = client_and_session
    base_id = uuid.uuid4()
    base_graph = {
        "nodes": [{"id": "node_1", "type": "agenticNode", "data": {"label": "LLM Agent"}}],
        "edges": [],
    }
    _set_first(session, _mock_workflow(workflow_id=base_id, version=7, graph_json=base_graph))

    resp = client.post(
        "/api/v1/copilot/drafts",
        json={"title": "fork", "base_workflow_id": str(base_id)},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["base_workflow_id"] == str(base_id)
    assert body["base_version_at_fork"] == 7
    assert body["graph_json"] == base_graph


def test_create_draft_invalid_base_id_422(client_and_session):
    client, _ = client_and_session
    resp = client.post(
        "/api/v1/copilot/drafts",
        json={"title": "fork", "base_workflow_id": "not-a-uuid"},
    )
    assert resp.status_code == 422


def test_create_draft_missing_base_404(client_and_session):
    client, session = client_and_session
    _set_first(session, None)
    resp = client.post(
        "/api/v1/copilot/drafts",
        json={"title": "fork", "base_workflow_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


def test_get_draft_not_found(client_and_session):
    client, session = client_and_session
    _set_first(session, None)
    resp = client.get(f"/api/v1/copilot/drafts/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_delete_draft(client_and_session):
    client, session = client_and_session
    draft = _mock_draft()
    _set_first(session, draft)
    resp = client.delete(f"/api/v1/copilot/drafts/{draft.id}")
    assert resp.status_code == 204
    session.delete.assert_called_once_with(draft)


# ---------------------------------------------------------------------------
# Optimistic concurrency via expected_version
# ---------------------------------------------------------------------------


def test_patch_draft_version_conflict(client_and_session):
    client, session = client_and_session
    draft = _mock_draft(version=3)
    _set_first(session, draft)

    resp = client.patch(
        f"/api/v1/copilot/drafts/{draft.id}",
        json={"title": "renamed", "expected_version": 2},
    )
    assert resp.status_code == 409
    assert "expected 2, got 3" in resp.json()["detail"]


def test_patch_draft_ok_bumps_version(client_and_session):
    client, session = client_and_session
    draft = _mock_draft(version=3)
    _set_first(session, draft)

    resp = client.patch(
        f"/api/v1/copilot/drafts/{draft.id}",
        json={"title": "renamed", "expected_version": 3},
    )
    assert resp.status_code == 200
    assert draft.title == "renamed"
    assert draft.version == 4


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------


def test_tool_dispatch_unknown_tool_400(client_and_session):
    client, session = client_and_session
    draft = _mock_draft()
    _set_first(session, draft)
    resp = client.post(
        f"/api/v1/copilot/drafts/{draft.id}/tools/no_such_tool",
        json={"args": {}},
    )
    assert resp.status_code == 400


def test_tool_dispatch_readonly_does_not_bump_version(client_and_session):
    client, session = client_and_session
    draft = _mock_draft(version=5)
    _set_first(session, draft)
    resp = client.post(
        f"/api/v1/copilot/drafts/{draft.id}/tools/list_node_types",
        json={"args": {}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tool"] == "list_node_types"
    assert body["draft_version"] == 5
    assert body["validation"] is None
    assert draft.version == 5  # unchanged


def test_tool_dispatch_mutation_bumps_version_and_returns_validation(client_and_session):
    client, session = client_and_session
    draft = _mock_draft(version=5)
    _set_first(session, draft)
    resp = client.post(
        f"/api/v1/copilot/drafts/{draft.id}/tools/add_node",
        json={"args": {"node_type": "llm_agent"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tool"] == "add_node"
    assert body["draft_version"] == 6  # bumped
    assert body["validation"] == {"errors": [], "warnings": []}
    assert body["result"]["node_id"] == "node_1"
    assert draft.version == 6
    assert len(draft.graph_json["nodes"]) == 1


def test_tool_dispatch_bad_args_400(client_and_session):
    client, session = client_and_session
    draft = _mock_draft()
    _set_first(session, draft)
    resp = client.post(
        f"/api/v1/copilot/drafts/{draft.id}/tools/add_node",
        json={"args": {}},  # missing node_type
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Promote — net-new path
# ---------------------------------------------------------------------------


def test_promote_new_requires_name(client_and_session):
    client, session = client_and_session
    draft = _mock_draft()
    _set_first(session, draft)
    resp = client.post(
        f"/api/v1/copilot/drafts/{draft.id}/promote",
        json={},
    )
    assert resp.status_code == 400


def test_promote_new_success(client_and_session):
    client, session = client_and_session
    draft = _mock_draft()
    # Two first() calls: draft lookup, then name-clash lookup.
    _set_first(session, draft, None)

    # The new WorkflowDefinition added to the session needs an id the
    # response can serialise. SQLAlchemy normally assigns via default
    # on flush; with the mock we emulate refresh setting version=1.
    def _refresh(obj):
        # Mirror what db.refresh would do post-flush.
        if not getattr(obj, "id", None):
            obj.id = uuid.uuid4()
        if not getattr(obj, "version", None):
            obj.version = 1
    session.refresh.side_effect = _refresh

    resp = client.post(
        f"/api/v1/copilot/drafts/{draft.id}/promote",
        json={"name": "My Flow"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["created"] is True
    assert body["version"] == 1
    assert session.delete.called  # draft consumed


def test_promote_new_name_collision_409(client_and_session):
    client, session = client_and_session
    draft = _mock_draft()
    clash = _mock_workflow(name="My Flow")
    _set_first(session, draft, clash)
    resp = client.post(
        f"/api/v1/copilot/drafts/{draft.id}/promote",
        json={"name": "My Flow"},
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Promote — new-version path + race guard
# ---------------------------------------------------------------------------


def test_promote_new_version_success(client_and_session):
    client, session = client_and_session
    base_id = uuid.uuid4()
    draft = _mock_draft(
        base_workflow_id=base_id,
        base_version_at_fork=5,
        graph_json={
            "nodes": [{"id": "node_1", "type": "agenticNode", "data": {"label": "LLM Agent"}}],
            "edges": [],
        },
    )
    base = _mock_workflow(workflow_id=base_id, version=5)
    # Two first() calls: draft, then base.
    _set_first(session, draft, base)

    def _refresh(obj):
        # Version was incremented already; refresh is a no-op in our test.
        return obj
    session.refresh.side_effect = _refresh

    resp = client.post(
        f"/api/v1/copilot/drafts/{draft.id}/promote",
        json={},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["created"] is False
    assert body["version"] == 6  # bumped
    assert body["workflow_id"] == str(base_id)
    assert base.version == 6
    # Snapshot added + draft deleted.
    assert session.add.called
    assert session.delete.called


def test_promote_new_version_base_diverged_409(client_and_session):
    client, session = client_and_session
    base_id = uuid.uuid4()
    draft = _mock_draft(
        base_workflow_id=base_id,
        base_version_at_fork=5,
    )
    base = _mock_workflow(workflow_id=base_id, version=7)  # colleague saved in between
    _set_first(session, draft, base)

    resp = client.post(
        f"/api/v1/copilot/drafts/{draft.id}/promote",
        json={},
    )
    assert resp.status_code == 409
    assert "advanced from v5 to v7" in resp.json()["detail"]


def test_promote_new_version_base_deleted_404(client_and_session):
    client, session = client_and_session
    base_id = uuid.uuid4()
    draft = _mock_draft(
        base_workflow_id=base_id,
        base_version_at_fork=5,
    )
    _set_first(session, draft, None)  # base gone
    resp = client.post(
        f"/api/v1/copilot/drafts/{draft.id}/promote",
        json={},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Promote validation guard
# ---------------------------------------------------------------------------


def test_promote_refuses_invalid_graph(client_and_session):
    """Validation errors (not just warnings) block the promote path.

    The existing validator returns warnings only; to exercise this we
    pretend the validator produced an error. Done by graph shape that
    forces a failure through the helper ``_promote_as_new`` — name
    collision before validation wouldn't hit this branch. We instead
    poke a monkey-patched validator.
    """
    from app.copilot import tool_layer

    client, session = client_and_session
    draft = _mock_draft()
    _set_first(session, draft)

    original = tool_layer.validate_graph
    tool_layer.validate_graph = lambda g: {"errors": ["boom"], "warnings": []}
    try:
        resp = client.post(
            f"/api/v1/copilot/drafts/{draft.id}/promote",
            json={"name": "My Flow"},
        )
        assert resp.status_code == 400
        assert "validation failed" in resp.json()["detail"].lower()
    finally:
        tool_layer.validate_graph = original


# ---------------------------------------------------------------------------
# COPILOT-03.e — scenario list + run-all endpoints
# ---------------------------------------------------------------------------


def _mock_scenario(name: str, *, has_expected: bool = False):
    s = MagicMock()
    s.id = uuid.uuid4()
    s.name = name
    s.payload_json = {"trigger": name}
    s.expected_output_contains_json = {"ok": True} if has_expected else None
    s.created_at = datetime.now(timezone.utc)
    return s


def test_list_draft_scenarios_returns_ordered_list(client_and_session):
    client, session = client_and_session
    draft = _mock_draft()

    s1 = _mock_scenario("empty payload", has_expected=True)
    s2 = _mock_scenario("oversized attachment")
    # Two query() calls: draft lookup (.first()), scenarios (.all()).
    session.query.return_value.filter_by.return_value.first.return_value = draft
    session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = [s1, s2]

    resp = client.get(f"/api/v1/copilot/drafts/{draft.id}/scenarios")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["name"] == "empty payload"
    assert body[0]["has_expected"] is True
    assert body[1]["has_expected"] is False


def test_list_draft_scenarios_empty(client_and_session):
    client, session = client_and_session
    draft = _mock_draft()
    session.query.return_value.filter_by.return_value.first.return_value = draft
    session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = []

    resp = client.get(f"/api/v1/copilot/drafts/{draft.id}/scenarios")
    assert resp.status_code == 200
    assert resp.json() == []


def test_run_all_draft_scenarios_aggregates_pass_fail(client_and_session):
    """03.e — run_all hits runner_tools.run_scenario per row and
    buckets the results by status. Mocks run_scenario to return a
    canned status per call."""
    from app.copilot import runner_tools

    client, session = client_and_session
    draft = _mock_draft()

    rows = [
        _mock_scenario("a"),
        _mock_scenario("b"),
        _mock_scenario("c"),
    ]
    session.query.return_value.filter_by.return_value.first.return_value = draft
    session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = rows

    # run_scenario is called 3× — canned: pass, fail, error.
    canned = [
        {"scenario_id": str(rows[0].id), "name": "a", "status": "pass", "mismatches": []},
        {"scenario_id": str(rows[1].id), "name": "b", "status": "fail",
         "mismatches": [{"path": "$.count", "expected": 5, "actual": 3}]},
        {"scenario_id": str(rows[2].id), "name": "c", "status": "error",
         "message": "Draft validation failed", "execution": {"error": "x"}},
    ]

    with patch.object(runner_tools, "run_scenario", side_effect=canned):
        resp = client.post(
            f"/api/v1/copilot/drafts/{draft.id}/scenarios/run_all",
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 3
    assert body["pass_count"] == 1
    assert body["fail_count"] == 1
    assert body["stale_count"] == 0
    assert body["error_count"] == 1
    assert [r["status"] for r in body["results"]] == ["pass", "fail", "error"]
    # Fail result carries mismatches through.
    assert len(body["results"][1]["mismatches"]) == 1


def test_run_all_draft_scenarios_empty_draft(client_and_session):
    client, session = client_and_session
    draft = _mock_draft()
    session.query.return_value.filter_by.return_value.first.return_value = draft
    session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = []

    resp = client.post(f"/api/v1/copilot/drafts/{draft.id}/scenarios/run_all")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 0
    assert body["results"] == []


def test_list_scenarios_404_on_missing_draft(client_and_session):
    client, session = client_and_session
    session.query.return_value.filter_by.return_value.first.return_value = None

    resp = client.get(f"/api/v1/copilot/drafts/{uuid.uuid4()}/scenarios")
    assert resp.status_code == 404
