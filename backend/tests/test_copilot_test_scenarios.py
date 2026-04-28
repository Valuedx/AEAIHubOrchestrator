"""COPILOT-03.a — unit tests for the scenario persistence tools.

Validates:

  * ``save_test_scenario`` input-validation, duplicate-name guard,
    50-scenario cap, and happy-path DB write.
  * ``run_scenario`` lookup, stale/error surface, pass/fail diff
    against ``expected_output_contains`` using a mocked
    ``execute_draft_sync``.
  * ``list_scenarios`` happy path.
  * ``_diff_contains`` recursion: nested-dict missing key, list
    length mismatch, scalar mismatch, list positional match.

MagicMock-backed DB session — no Postgres needed. Matches the
existing runner-tool test shape.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.copilot import runner_tools


TENANT = "tenant-scen"


@dataclass
class _FakeDraft:
    id: uuid.UUID
    tenant_id: str
    graph_json: dict[str, Any]
    version: int


# ---------------------------------------------------------------------------
# _diff_contains
# ---------------------------------------------------------------------------


def test_diff_contains_empty_when_actual_subsumes_expected():
    mismatches = runner_tools._diff_contains(
        {"status": "ok", "user": {"name": "x"}},
        {"status": "ok", "user": {"name": "x", "age": 30}, "extra": 1},
        path="$",
    )
    assert mismatches == []


def test_diff_contains_missing_key_surfaced_with_path():
    mismatches = runner_tools._diff_contains(
        {"user": {"id": 7}},
        {"user": {}},
        path="$",
    )
    assert len(mismatches) == 1
    assert mismatches[0]["path"] == "$.user.id"
    assert mismatches[0]["reason"] == "missing"


def test_diff_contains_scalar_mismatch_surfaced():
    mismatches = runner_tools._diff_contains(
        {"status": "ok"}, {"status": "failed"}, path="$",
    )
    assert len(mismatches) == 1
    assert mismatches[0]["path"] == "$.status"
    assert mismatches[0]["expected"] == "ok"
    assert mismatches[0]["actual"] == "failed"


def test_diff_contains_list_shorter_than_expected_surfaced():
    mismatches = runner_tools._diff_contains(
        [1, 2, 3], [1, 2], path="$",
    )
    assert len(mismatches) == 1
    assert mismatches[0]["reason"] == "list shorter than expected"


def test_diff_contains_list_positional_match_allows_longer_actual():
    mismatches = runner_tools._diff_contains(
        [1, 2], [1, 2, 3, 4], path="$",
    )
    assert mismatches == []


def test_diff_contains_wrong_type_surfaces_as_mismatch():
    mismatches = runner_tools._diff_contains(
        {"x": 1}, [1, 2], path="$",
    )
    assert len(mismatches) == 1
    assert mismatches[0]["path"] == "$"


# ---------------------------------------------------------------------------
# save_test_scenario
# ---------------------------------------------------------------------------


def _mock_db_for_save(existing: Any = None, count: int = 0) -> MagicMock:
    """Build a MagicMock session whose query().filter_by().first()/count()
    chain returns the supplied stubs. save_test_scenario calls:

      - first() to check for duplicate name (expects None or a row)
      - count() to enforce the per-draft cap
    """
    db = MagicMock()
    query = db.query.return_value
    filter_by = query.filter_by.return_value
    filter_by.first.return_value = existing
    filter_by.count.return_value = count
    return db


def test_save_test_scenario_happy_path_writes_row():
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT, graph_json={}, version=1,
    )
    db = _mock_db_for_save(existing=None, count=0)

    result = runner_tools.save_test_scenario(
        db,
        tenant_id=TENANT,
        draft=draft,
        args={
            "name": "empty payload",
            "payload": {"message": ""},
            "expected_output_contains": {"status": "ok"},
        },
    )

    assert "scenario_id" in result
    assert result["name"] == "empty payload"
    db.add.assert_called_once()
    db.commit.assert_called_once()


def test_save_test_scenario_rejects_empty_name():
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={}, version=1)
    db = _mock_db_for_save()
    result = runner_tools.save_test_scenario(
        db, tenant_id=TENANT, draft=draft,
        args={"name": "   ", "payload": {}},
    )
    assert "error" in result
    db.add.assert_not_called()


def test_save_test_scenario_rejects_non_dict_payload():
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={}, version=1)
    db = _mock_db_for_save()
    result = runner_tools.save_test_scenario(
        db, tenant_id=TENANT, draft=draft,
        args={"name": "x", "payload": "not-a-dict"},
    )
    assert "error" in result
    assert "payload" in result["error"]
    db.add.assert_not_called()


def test_save_test_scenario_rejects_non_dict_expected():
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={}, version=1)
    db = _mock_db_for_save()
    result = runner_tools.save_test_scenario(
        db, tenant_id=TENANT, draft=draft,
        args={
            "name": "x",
            "payload": {},
            "expected_output_contains": "not-a-dict",
        },
    )
    assert "error" in result
    assert "expected_output_contains" in result["error"]


def test_save_test_scenario_refuses_duplicate_name():
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={}, version=1)
    existing = MagicMock()
    db = _mock_db_for_save(existing=existing, count=0)
    result = runner_tools.save_test_scenario(
        db, tenant_id=TENANT, draft=draft,
        args={"name": "dupe", "payload": {}},
    )
    assert "error" in result
    assert "already exists" in result["error"]
    db.add.assert_not_called()


def test_save_test_scenario_enforces_cap():
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={}, version=1)
    db = _mock_db_for_save(existing=None, count=runner_tools.MAX_SCENARIOS_PER_DRAFT)
    result = runner_tools.save_test_scenario(
        db, tenant_id=TENANT, draft=draft,
        args={"name": "over-the-cap", "payload": {}},
    )
    assert "error" in result
    assert "cap" in result["error"]
    db.add.assert_not_called()


# ---------------------------------------------------------------------------
# run_scenario
# ---------------------------------------------------------------------------


class _ScenarioStub:
    def __init__(self, *, expected: Any = None, payload: Any = None):
        self.id = uuid.uuid4()
        self.name = "my scenario"
        self.payload_json = payload if payload is not None else {}
        self.expected_output_contains_json = expected


def _mock_db_for_run(scenario: _ScenarioStub | None) -> MagicMock:
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.return_value = scenario
    return db


def test_run_scenario_requires_scenario_id():
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={}, version=1)
    db = _mock_db_for_run(None)
    result = runner_tools.run_scenario(
        db, tenant_id=TENANT, draft=draft, args={},
    )
    assert "error" in result


def test_run_scenario_rejects_invalid_uuid():
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={}, version=1)
    db = _mock_db_for_run(None)
    result = runner_tools.run_scenario(
        db, tenant_id=TENANT, draft=draft,
        args={"scenario_id": "not-a-uuid"},
    )
    assert "error" in result
    assert "Invalid scenario_id" in result["error"]


def test_run_scenario_not_found():
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={}, version=1)
    db = _mock_db_for_run(None)
    result = runner_tools.run_scenario(
        db, tenant_id=TENANT, draft=draft,
        args={"scenario_id": str(uuid.uuid4())},
    )
    assert "error" in result
    assert "not found" in result["error"]


def test_run_scenario_pass_when_expected_matches():
    scenario = _ScenarioStub(
        expected={"status": "ok"},
        payload={"msg": "hi"},
    )
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={}, version=1)
    db = _mock_db_for_run(scenario)

    with patch.object(
        runner_tools, "execute_draft_sync",
        return_value={
            "instance_id": str(uuid.uuid4()),
            "status": "completed",
            "output": {"status": "ok", "extra": 42},
            "elapsed_ms": 120,
        },
    ) as fake_exec:
        result = runner_tools.run_scenario(
            db, tenant_id=TENANT, draft=draft,
            args={"scenario_id": str(scenario.id)},
        )

    assert result["status"] == "pass"
    assert result["mismatches"] == []
    # execute_draft was called with the scenario's payload.
    _, kwargs = fake_exec.call_args
    assert kwargs["args"]["payload"] == {"msg": "hi"}


def test_run_scenario_fail_surfaces_mismatches():
    scenario = _ScenarioStub(expected={"status": "ok", "count": 5})
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={}, version=1)
    db = _mock_db_for_run(scenario)

    with patch.object(
        runner_tools, "execute_draft_sync",
        return_value={
            "instance_id": str(uuid.uuid4()),
            "status": "completed",
            "output": {"status": "ok", "count": 3},
            "elapsed_ms": 50,
        },
    ):
        result = runner_tools.run_scenario(
            db, tenant_id=TENANT, draft=draft,
            args={"scenario_id": str(scenario.id)},
        )

    assert result["status"] == "fail"
    assert len(result["mismatches"]) == 1
    assert result["mismatches"][0]["path"] == "$.count"


def test_run_scenario_engine_failure_surfaces_as_fail_with_status_mismatch():
    scenario = _ScenarioStub(expected=None)
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={}, version=1)
    db = _mock_db_for_run(scenario)

    with patch.object(
        runner_tools, "execute_draft_sync",
        return_value={
            "instance_id": str(uuid.uuid4()),
            "status": "failed",
            "error": "node_2 raised ValueError",
            "output": {},
        },
    ):
        result = runner_tools.run_scenario(
            db, tenant_id=TENANT, draft=draft,
            args={"scenario_id": str(scenario.id)},
        )

    assert result["status"] == "fail"
    assert result["mismatches"][0]["path"] == "$.status"


def test_run_scenario_no_expected_returns_pass_with_actual():
    scenario = _ScenarioStub(expected=None)
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={}, version=1)
    db = _mock_db_for_run(scenario)

    with patch.object(
        runner_tools, "execute_draft_sync",
        return_value={
            "instance_id": str(uuid.uuid4()),
            "status": "completed",
            "output": {"anything": True},
        },
    ):
        result = runner_tools.run_scenario(
            db, tenant_id=TENANT, draft=draft,
            args={"scenario_id": str(scenario.id)},
        )

    assert result["status"] == "pass"
    assert result["actual_output"] == {"anything": True}


def test_run_scenario_propagates_pre_run_validation_error():
    scenario = _ScenarioStub(expected={"x": 1})
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={}, version=1)
    db = _mock_db_for_run(scenario)

    # execute_draft_sync returns an {error: ...} shape (no instance_id)
    # for pre-run validation failures — run_scenario should wrap it as
    # status=error.
    with patch.object(
        runner_tools, "execute_draft_sync",
        return_value={"error": "Draft validation failed"},
    ):
        result = runner_tools.run_scenario(
            db, tenant_id=TENANT, draft=draft,
            args={"scenario_id": str(scenario.id)},
        )

    assert result["status"] == "error"
    assert "validation" in result["message"].lower()


# ---------------------------------------------------------------------------
# list_scenarios
# ---------------------------------------------------------------------------


def test_list_scenarios_returns_ordered_list():
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={}, version=1)

    from datetime import datetime, timezone

    def _stub(name: str, created: datetime, expected: Any = None):
        s = MagicMock()
        s.id = uuid.uuid4()
        s.name = name
        s.payload_json = {"p": name}
        s.expected_output_contains_json = expected
        s.created_at = created
        return s

    rows = [
        _stub("first", datetime(2026, 4, 1, tzinfo=timezone.utc), expected={"x": 1}),
        _stub("second", datetime(2026, 4, 2, tzinfo=timezone.utc)),
    ]
    db = MagicMock()
    db.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = rows

    result = runner_tools.list_scenarios(
        db, tenant_id=TENANT, draft=draft, args={},
    )
    assert result["count"] == 2
    assert result["scenarios"][0]["name"] == "first"
    assert result["scenarios"][0]["has_expected"] is True
    assert result["scenarios"][1]["has_expected"] is False


# ---------------------------------------------------------------------------
# dispatch routing — confirms the three new tools reach their handlers
# ---------------------------------------------------------------------------


def test_dispatch_routes_save_scenario():
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={}, version=1)
    db = _mock_db_for_save(existing=None, count=0)

    result = runner_tools.dispatch(
        "save_test_scenario",
        db=db, tenant_id=TENANT, draft=draft,
        args={"name": "via dispatch", "payload": {}},
    )
    assert "scenario_id" in result


def test_dispatch_routes_run_scenario():
    scenario = _ScenarioStub(expected={"ok": True})
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={}, version=1)
    db = _mock_db_for_run(scenario)

    with patch.object(
        runner_tools, "execute_draft_sync",
        return_value={"instance_id": "i", "status": "completed", "output": {"ok": True}},
    ):
        result = runner_tools.dispatch(
            "run_scenario",
            db=db, tenant_id=TENANT, draft=draft,
            args={"scenario_id": str(scenario.id)},
        )
    assert result["status"] == "pass"


def test_dispatch_routes_list_scenarios():
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={}, version=1)
    db = MagicMock()
    db.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = []
    result = runner_tools.dispatch(
        "list_scenarios",
        db=db, tenant_id=TENANT, draft=draft, args={},
    )
    assert result == {"count": 0, "scenarios": []}
