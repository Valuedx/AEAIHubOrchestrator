"""SMART-01 — scenario memory auto-save + strict promote-gate.

Two code paths:

1. **Auto-save** inside ``execute_draft_sync`` — when
   ``smart_01_scenario_memory_enabled`` is true, a successful run
   persists a ``CopilotTestScenario`` keyed by a stable payload
   hash. Duplicate payload runs are no-ops (idempotent).

2. **Strict promote-gate** inside ``/api/v1/copilot/drafts/{id}/promote``
   — when ``smart_01_strict_promote_gate_enabled`` is true, the
   endpoint runs every saved scenario before landing the workflow
   and refuses with 400 on any non-pass result.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


TENANT = "tenant-smart01"


def _policy(
    *,
    scenario_memory: bool = False,
    strict_gate: bool = False,
):
    from app.engine.tenant_policy_resolver import EffectivePolicy
    return EffectivePolicy(
        execution_quota_per_hour=50, max_snapshots=20, mcp_pool_size=4,
        rate_limit_requests_per_window=100, rate_limit_window_seconds=60,
        smart_04_lints_enabled=True,
        smart_06_mcp_discovery_enabled=True,
        smart_02_pattern_library_enabled=True,
        smart_01_scenario_memory_enabled=scenario_memory,
        smart_01_strict_promote_gate_enabled=strict_gate,
        source={},
    )


# ---------------------------------------------------------------------------
# _payload_hash + _auto_save_scenario_from_run — pure helpers
# ---------------------------------------------------------------------------


def test_payload_hash_is_stable_and_sensitive_to_value():
    from app.copilot.runner_tools import _payload_hash

    h1 = _payload_hash({"x": 1, "y": 2})
    h2 = _payload_hash({"y": 2, "x": 1})  # key order irrelevant
    h3 = _payload_hash({"x": 1, "y": 3})

    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 10


def test_auto_save_skips_when_duplicate_payload_exists():
    """Same payload re-run must not grow the table."""
    from app.copilot import runner_tools

    draft = MagicMock()
    draft.id = uuid.uuid4()

    existing = MagicMock()  # non-None → dedupe short-circuits
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.return_value = existing

    runner_tools._auto_save_scenario_from_run(
        db, tenant_id=TENANT, draft=draft, payload={"x": 1},
    )

    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_auto_save_adds_row_when_none_exists():
    from app.copilot import runner_tools

    draft = MagicMock()
    draft.id = uuid.uuid4()

    db = MagicMock()
    # first() = None (no duplicate), count() = 0 (under cap)
    filter_by = db.query.return_value.filter_by.return_value
    filter_by.first.return_value = None
    filter_by.count.return_value = 0

    runner_tools._auto_save_scenario_from_run(
        db, tenant_id=TENANT, draft=draft, payload={"msg": "hello"},
    )

    db.add.assert_called_once()
    db.commit.assert_called_once()
    # The scenario name follows the auto-<hash> convention.
    [[added], _] = db.add.call_args
    assert added.name.startswith("auto-")
    assert added.expected_output_contains_json is None


def test_auto_save_respects_per_draft_cap():
    from app.copilot import runner_tools

    draft = MagicMock()
    draft.id = uuid.uuid4()

    db = MagicMock()
    filter_by = db.query.return_value.filter_by.return_value
    filter_by.first.return_value = None
    filter_by.count.return_value = runner_tools.MAX_SCENARIOS_PER_DRAFT

    runner_tools._auto_save_scenario_from_run(
        db, tenant_id=TENANT, draft=draft, payload={"p": 1},
    )

    # Cap reached — no INSERT.
    db.add.assert_not_called()


# ---------------------------------------------------------------------------
# execute_draft_sync — auto-save happy path gated on the flag
# ---------------------------------------------------------------------------


def test_execute_draft_auto_saves_when_flag_on():
    """When the SMART-01 flag is on and the run completes, the
    helper is called. We don't actually run the engine — we mock
    the thread pool's run result directly."""
    from app.copilot import runner_tools
    from app.copilot.runner_tools import _OverrideDraft

    draft = _OverrideDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []}, version=1,
    )
    db = MagicMock()

    # Mock everything that execute_draft_sync invokes, then assert
    # _auto_save_scenario_from_run was reached.
    with patch.object(
        runner_tools, "get_effective_policy",
        return_value=_policy(scenario_memory=True),
    ) if False else patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy(scenario_memory=True),
    ), patch.object(
        runner_tools, "_auto_save_scenario_from_run",
    ) as fake_save, patch(
        "app.copilot.tool_layer.validate_graph",
        return_value={"errors": [], "warnings": []},
    ), patch(
        "concurrent.futures.ThreadPoolExecutor",
    ) as fake_ex:
        # Make the executor return a completed run with trivial
        # context so execute_draft_sync walks the post-run path.
        future = MagicMock()
        future.result.return_value = {
            "status": "completed",
            "context_json": {"result": "ok"},
            "started_at": None, "completed_at": None,
        }
        ex_instance = MagicMock()
        ex_instance.submit.return_value = future
        fake_ex.return_value.__enter__.return_value = ex_instance

        result = runner_tools.execute_draft_sync(
            db, tenant_id=TENANT, draft=draft,
            args={"payload": {"msg": "auto"}},
        )

    assert result["status"] == "completed"
    fake_save.assert_called_once()
    _, kwargs = fake_save.call_args
    assert kwargs["payload"] == {"msg": "auto"}


def test_execute_draft_does_not_auto_save_when_flag_off():
    from app.copilot import runner_tools
    from app.copilot.runner_tools import _OverrideDraft

    draft = _OverrideDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []}, version=1,
    )
    db = MagicMock()

    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy(scenario_memory=False),
    ), patch.object(
        runner_tools, "_auto_save_scenario_from_run",
    ) as fake_save, patch(
        "app.copilot.tool_layer.validate_graph",
        return_value={"errors": [], "warnings": []},
    ), patch(
        "concurrent.futures.ThreadPoolExecutor",
    ) as fake_ex:
        future = MagicMock()
        future.result.return_value = {
            "status": "completed", "context_json": {},
            "started_at": None, "completed_at": None,
        }
        ex_instance = MagicMock()
        ex_instance.submit.return_value = future
        fake_ex.return_value.__enter__.return_value = ex_instance

        runner_tools.execute_draft_sync(
            db, tenant_id=TENANT, draft=draft, args={"payload": {}},
        )

    fake_save.assert_not_called()


def test_execute_draft_skips_auto_save_on_failed_run():
    """Only completed runs auto-save — a failure is not a regression
    case worth locking in."""
    from app.copilot import runner_tools
    from app.copilot.runner_tools import _OverrideDraft

    draft = _OverrideDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []}, version=1,
    )
    db = MagicMock()

    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy(scenario_memory=True),
    ), patch.object(
        runner_tools, "_auto_save_scenario_from_run",
    ) as fake_save, patch(
        "app.copilot.tool_layer.validate_graph",
        return_value={"errors": [], "warnings": []},
    ), patch(
        "concurrent.futures.ThreadPoolExecutor",
    ) as fake_ex:
        future = MagicMock()
        future.result.return_value = {
            "status": "failed", "context_json": {},
            "started_at": None, "completed_at": None,
        }
        ex_instance = MagicMock()
        ex_instance.submit.return_value = future
        fake_ex.return_value.__enter__.return_value = ex_instance

        runner_tools.execute_draft_sync(
            db, tenant_id=TENANT, draft=draft, args={"payload": {}},
        )

    fake_save.assert_not_called()


# ---------------------------------------------------------------------------
# Strict promote-gate — /api/v1/copilot/drafts/{id}/promote
# ---------------------------------------------------------------------------


@pytest.fixture
def client_and_session():
    from app.api.copilot_drafts import router
    from app.database import get_db, get_tenant_db
    from app.security.tenant import get_tenant_id

    app = FastAPI()
    app.include_router(router)
    session = MagicMock()

    def _fake_get_db():
        yield session

    app.dependency_overrides[get_tenant_id] = lambda: TENANT
    app.dependency_overrides[get_db] = _fake_get_db
    app.dependency_overrides[get_tenant_db] = _fake_get_db
    return TestClient(app), session


def _mock_draft_with_scenarios(
    session: MagicMock,
    *,
    scenarios: list[Any],
    base_workflow_id: uuid.UUID | None = None,
):
    """Stage the session so the promote flow sees:

    1. First .first() call — returns the draft row (found by id)
    2. Second .first() call — name-clash lookup (returns None)
    3. order_by().all() — returns the list of scenarios for strict gate
    """
    from datetime import datetime, timezone

    draft = MagicMock()
    draft.id = uuid.uuid4()
    draft.tenant_id = TENANT
    draft.base_workflow_id = base_workflow_id
    draft.base_version_at_fork = None
    draft.title = "d"
    draft.graph_json = {"nodes": [], "edges": []}
    draft.version = 1
    draft.created_at = datetime.now(timezone.utc)
    draft.updated_at = datetime.now(timezone.utc)

    filter_by = session.query.return_value.filter_by.return_value
    # .first() is called by _get_or_404 AND name-clash lookup
    filter_by.first.side_effect = [draft, None]
    # .order_by().all() is the scenarios query
    filter_by.order_by.return_value.all.return_value = scenarios

    session.refresh.side_effect = lambda o: setattr(o, "id", uuid.uuid4()) or setattr(o, "version", 1)
    return draft


def test_promote_strict_gate_blocks_on_failing_scenarios(client_and_session):
    from app.copilot import runner_tools

    client, session = client_and_session

    s1 = MagicMock(); s1.id = uuid.uuid4(); s1.name = "scenario-a"
    draft = _mock_draft_with_scenarios(session, scenarios=[s1])

    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy(strict_gate=True),
    ), patch.object(
        runner_tools, "run_scenario",
        return_value={
            "status": "fail",
            "mismatches": [{"path": "$.x", "expected": 1, "actual": 2}],
        },
    ):
        resp = client.post(
            f"/api/v1/copilot/drafts/{draft.id}/promote",
            json={"name": "My Flow"},
        )

    assert resp.status_code == 400
    body = resp.json()
    # FastAPI wraps dict details verbatim, so the structured
    # "failing_scenarios" payload flows through.
    assert "strict promote-gate" in body["detail"]["error"]
    assert body["detail"]["failing_scenarios"][0]["name"] == "scenario-a"
    assert body["detail"]["failing_scenarios"][0]["mismatch_count"] == 1


def test_promote_strict_gate_passes_when_all_scenarios_pass(client_and_session):
    from app.copilot import runner_tools

    client, session = client_and_session

    s1 = MagicMock(); s1.id = uuid.uuid4(); s1.name = "a"
    s2 = MagicMock(); s2.id = uuid.uuid4(); s2.name = "b"
    draft = _mock_draft_with_scenarios(session, scenarios=[s1, s2])

    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy(strict_gate=True),
    ), patch.object(
        runner_tools, "run_scenario",
        side_effect=[
            {"status": "pass", "mismatches": []},
            {"status": "pass", "mismatches": []},
        ],
    ):
        resp = client.post(
            f"/api/v1/copilot/drafts/{draft.id}/promote",
            json={"name": "My Flow"},
        )

    # Happy path — promote lands at 201.
    assert resp.status_code == 201


def test_promote_strict_gate_skips_when_no_scenarios_saved(client_and_session):
    """No scenarios saved = no-op. Strict mode is "if you've got
    tests, they must pass", not "you must have tests"."""
    from app.copilot import runner_tools

    client, session = client_and_session
    draft = _mock_draft_with_scenarios(session, scenarios=[])

    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy(strict_gate=True),
    ), patch.object(runner_tools, "run_scenario") as fake_run:
        resp = client.post(
            f"/api/v1/copilot/drafts/{draft.id}/promote",
            json={"name": "My Flow"},
        )

    assert resp.status_code == 201
    fake_run.assert_not_called()


def test_promote_skips_strict_gate_when_flag_off(client_and_session):
    """Flag off = the scenarios query never fires and run_scenario
    is never called, even when scenarios exist."""
    from app.copilot import runner_tools

    client, session = client_and_session
    s1 = MagicMock(); s1.id = uuid.uuid4(); s1.name = "a"
    draft = _mock_draft_with_scenarios(session, scenarios=[s1])

    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy(strict_gate=False),
    ), patch.object(runner_tools, "run_scenario") as fake_run:
        resp = client.post(
            f"/api/v1/copilot/drafts/{draft.id}/promote",
            json={"name": "My Flow"},
        )

    assert resp.status_code == 201
    fake_run.assert_not_called()
