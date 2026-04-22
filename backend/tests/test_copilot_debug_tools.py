"""COPILOT-03.b — unit tests for ``run_debug_scenario`` +
``get_node_error`` runner tools.

Both tools are thin — run_debug_scenario folds caller overrides into
a deep-copied graph before delegating to ``execute_draft_sync`` (which
is patched out for isolation), and get_node_error walks three DB
queries to narrow onto one ``ExecutionLog`` row. The tests pin:

  * Override merging (pins → pinnedOutput, node_overrides → data.config).
  * Unknown-node-id short-circuit for both override maps.
  * Deep-copy isolation — the original draft.graph_json is never
    mutated.
  * get_node_error happy paths (failed + completed nodes) plus the
    same ephemeral-only safety gate that get_execution_logs has.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.copilot import runner_tools


TENANT = "tenant-debug"


@dataclass
class _FakeDraft:
    id: uuid.UUID
    tenant_id: str
    graph_json: dict[str, Any]
    version: int = 1


def _node(node_id: str, *, pinned: Any = None, config: dict | None = None) -> dict:
    data: dict[str, Any] = {"label": "LLM", "config": dict(config or {})}
    if pinned is not None:
        data["pinnedOutput"] = pinned
    return {"id": node_id, "type": "agenticNode", "data": data}


# ---------------------------------------------------------------------------
# run_debug_scenario
# ---------------------------------------------------------------------------


def test_run_debug_scenario_forwards_payload_to_execute_draft():
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [_node("node_1")], "edges": []},
    )
    db = MagicMock()

    with patch.object(
        runner_tools, "execute_draft_sync",
        return_value={
            "instance_id": "i-1",
            "status": "completed",
            "output": {},
            "elapsed_ms": 10,
        },
    ) as fake_exec:
        result = runner_tools.run_debug_scenario(
            db, tenant_id=TENANT, draft=draft,
            args={"payload": {"x": 1}},
        )

    assert result["status"] == "completed"
    assert result["overrides_applied"] == {"pins": [], "node_overrides": []}
    _, kwargs = fake_exec.call_args
    assert kwargs["args"]["payload"] == {"x": 1}


def test_run_debug_scenario_merges_pins_without_mutating_draft():
    original_graph = {
        "nodes": [
            _node("node_1"),
            _node("node_2"),
        ],
        "edges": [],
    }
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json=original_graph)
    db = MagicMock()

    captured_shim: dict[str, Any] = {}

    def _fake_exec(db, *, tenant_id, draft, args):
        captured_shim["graph"] = draft.graph_json
        return {"instance_id": "i", "status": "completed", "output": {}}

    with patch.object(runner_tools, "execute_draft_sync", side_effect=_fake_exec):
        result = runner_tools.run_debug_scenario(
            db, tenant_id=TENANT, draft=draft,
            args={"pins": {"node_1": {"canned": "output"}}},
        )

    assert result["overrides_applied"]["pins"] == ["node_1"]
    # Shim graph has the pin applied.
    pinned_node = next(
        n for n in captured_shim["graph"]["nodes"] if n["id"] == "node_1"
    )
    assert pinned_node["data"]["pinnedOutput"] == {"canned": "output"}
    # Original draft.graph_json is untouched — deep copy isolation.
    assert "pinnedOutput" not in original_graph["nodes"][0]["data"]


def test_run_debug_scenario_applies_node_overrides_to_config():
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={
            "nodes": [_node("node_1", config={"retries": 1, "model": "claude"})],
            "edges": [],
        },
    )
    db = MagicMock()

    captured: dict[str, Any] = {}

    def _fake_exec(db, *, tenant_id, draft, args):
        captured["graph"] = draft.graph_json
        return {"instance_id": "i", "status": "completed", "output": {}}

    with patch.object(runner_tools, "execute_draft_sync", side_effect=_fake_exec):
        result = runner_tools.run_debug_scenario(
            db, tenant_id=TENANT, draft=draft,
            args={
                "node_overrides": {"node_1": {"retries": 5}},
            },
        )

    assert result["overrides_applied"]["node_overrides"] == ["node_1"]
    applied = captured["graph"]["nodes"][0]["data"]["config"]
    # Override merged in; unrelated keys preserved.
    assert applied == {"retries": 5, "model": "claude"}


def test_run_debug_scenario_rejects_unknown_node_id_in_pins():
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [_node("node_1")], "edges": []},
    )
    db = MagicMock()

    with patch.object(runner_tools, "execute_draft_sync") as fake_exec:
        result = runner_tools.run_debug_scenario(
            db, tenant_id=TENANT, draft=draft,
            args={"pins": {"node_99": {}}},
        )

    assert "error" in result
    assert "node_99" in result["error"]
    fake_exec.assert_not_called()


def test_run_debug_scenario_rejects_non_dict_overrides():
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={"nodes": [], "edges": []})
    db = MagicMock()

    # pins must be an object
    result = runner_tools.run_debug_scenario(
        db, tenant_id=TENANT, draft=draft,
        args={"pins": "not-a-dict"},
    )
    assert "error" in result

    # node_overrides must be an object
    result = runner_tools.run_debug_scenario(
        db, tenant_id=TENANT, draft=draft,
        args={"node_overrides": ["list", "not-dict"]},
    )
    assert "error" in result


def test_run_debug_scenario_rejects_non_dict_override_value():
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [_node("node_1")], "edges": []},
    )
    db = MagicMock()

    with patch.object(runner_tools, "execute_draft_sync") as fake_exec:
        result = runner_tools.run_debug_scenario(
            db, tenant_id=TENANT, draft=draft,
            args={"node_overrides": {"node_1": "not-a-dict"}},
        )

    assert "error" in result
    assert "node_overrides" in result["error"]
    fake_exec.assert_not_called()


def test_run_debug_scenario_passes_deterministic_and_timeout():
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [_node("node_1")], "edges": []},
    )
    db = MagicMock()

    with patch.object(
        runner_tools, "execute_draft_sync",
        return_value={"instance_id": "i", "status": "completed", "output": {}},
    ) as fake_exec:
        runner_tools.run_debug_scenario(
            db, tenant_id=TENANT, draft=draft,
            args={
                "deterministic_mode": True,
                "timeout_seconds": 120,
            },
        )

    _, kwargs = fake_exec.call_args
    assert kwargs["args"]["deterministic_mode"] is True
    assert kwargs["args"]["timeout_seconds"] == 120


# ---------------------------------------------------------------------------
# get_node_error
# ---------------------------------------------------------------------------


@dataclass
class _FakeLog:
    node_id: str = "node_1"
    node_type: str = "llm_agent"
    status: str = "failed"
    input_json: dict = field(default_factory=lambda: {"model": "claude-sonnet"})
    output_json: Any = None
    error: str = "ValueError: no auth configured"
    started_at: datetime | None = field(default_factory=lambda: datetime(2026, 4, 23, tzinfo=timezone.utc))
    completed_at: datetime | None = field(default_factory=lambda: datetime(2026, 4, 23, 0, 0, 5, tzinfo=timezone.utc))


@dataclass
class _FakeInstance:
    id: uuid.UUID
    tenant_id: str
    workflow_def_id: uuid.UUID


@dataclass
class _FakeWfDef:
    is_ephemeral: bool = True


def _mock_db_for_node_error(
    *,
    instance: _FakeInstance | None,
    wf_def: _FakeWfDef | None,
    log: _FakeLog | None,
) -> MagicMock:
    """Wire up three-query-chain returns: instance lookup, wf_def
    lookup, execution-log lookup."""
    db = MagicMock()

    # db.query(WorkflowInstance).filter_by(...).first() → instance
    # db.query(WorkflowDefinition).filter_by(...).first() → wf_def
    # db.query(ExecutionLog).filter_by(...).order_by(...).first() → log
    returns = [instance, wf_def]
    log_chain = MagicMock()
    log_chain.order_by.return_value.first.return_value = log

    def _query_side_effect(model):
        result = MagicMock()
        if model.__name__ == "ExecutionLog":
            result.filter_by.return_value = log_chain
        else:
            nxt = returns.pop(0)
            result.filter_by.return_value.first.return_value = nxt
        return result

    db.query.side_effect = _query_side_effect
    return db


def test_get_node_error_requires_instance_and_node_id():
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={})
    db = MagicMock()

    result = runner_tools.get_node_error(
        db, tenant_id=TENANT, draft=draft, args={"node_id": "x"},
    )
    assert "error" in result
    result = runner_tools.get_node_error(
        db, tenant_id=TENANT, draft=draft, args={"instance_id": str(uuid.uuid4())},
    )
    assert "error" in result


def test_get_node_error_rejects_invalid_uuid():
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={})
    db = MagicMock()
    result = runner_tools.get_node_error(
        db, tenant_id=TENANT, draft=draft,
        args={"instance_id": "bogus", "node_id": "node_1"},
    )
    assert "Invalid instance_id" in result["error"]


def test_get_node_error_missing_instance():
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={})
    db = _mock_db_for_node_error(instance=None, wf_def=None, log=None)
    result = runner_tools.get_node_error(
        db, tenant_id=TENANT, draft=draft,
        args={"instance_id": str(uuid.uuid4()), "node_id": "node_1"},
    )
    assert "not found" in result["error"]


def test_get_node_error_refuses_non_ephemeral_instance():
    instance = _FakeInstance(
        id=uuid.uuid4(), tenant_id=TENANT, workflow_def_id=uuid.uuid4(),
    )
    db = _mock_db_for_node_error(
        instance=instance,
        wf_def=_FakeWfDef(is_ephemeral=False),
        log=None,
    )
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={})
    result = runner_tools.get_node_error(
        db, tenant_id=TENANT, draft=draft,
        args={"instance_id": str(instance.id), "node_id": "node_1"},
    )
    assert "not a copilot-initiated run" in result["error"]


def test_get_node_error_no_log_for_node():
    instance = _FakeInstance(
        id=uuid.uuid4(), tenant_id=TENANT, workflow_def_id=uuid.uuid4(),
    )
    db = _mock_db_for_node_error(
        instance=instance, wf_def=_FakeWfDef(is_ephemeral=True), log=None,
    )
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={})
    result = runner_tools.get_node_error(
        db, tenant_id=TENANT, draft=draft,
        args={"instance_id": str(instance.id), "node_id": "node_9"},
    )
    assert "no execution log" in result["error"]


def test_get_node_error_returns_failure_details():
    instance = _FakeInstance(
        id=uuid.uuid4(), tenant_id=TENANT, workflow_def_id=uuid.uuid4(),
    )
    log = _FakeLog()
    db = _mock_db_for_node_error(
        instance=instance, wf_def=_FakeWfDef(is_ephemeral=True), log=log,
    )
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={})

    result = runner_tools.get_node_error(
        db, tenant_id=TENANT, draft=draft,
        args={"instance_id": str(instance.id), "node_id": "node_1"},
    )

    assert result["status"] == "failed"
    assert result["error"] == "ValueError: no auth configured"
    assert result["node_type"] == "llm_agent"
    assert result["resolved_config"] == {"model": "claude-sonnet"}


def test_get_node_error_successful_node_surfaces_note():
    instance = _FakeInstance(
        id=uuid.uuid4(), tenant_id=TENANT, workflow_def_id=uuid.uuid4(),
    )
    log = _FakeLog(
        status="completed", error=None,
        output_json={"result": "ok"},
    )
    db = _mock_db_for_node_error(
        instance=instance, wf_def=_FakeWfDef(is_ephemeral=True), log=log,
    )
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={})

    result = runner_tools.get_node_error(
        db, tenant_id=TENANT, draft=draft,
        args={"instance_id": str(instance.id), "node_id": "node_1"},
    )

    assert result["status"] == "completed"
    assert "note" in result
    assert result["output_json"] == {"result": "ok"}


# ---------------------------------------------------------------------------
# Dispatch routing
# ---------------------------------------------------------------------------


def test_dispatch_routes_run_debug_scenario():
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [_node("n1")], "edges": []},
    )
    db = MagicMock()
    with patch.object(
        runner_tools, "execute_draft_sync",
        return_value={"instance_id": "i", "status": "completed", "output": {}},
    ):
        result = runner_tools.dispatch(
            "run_debug_scenario",
            db=db, tenant_id=TENANT, draft=draft, args={"pins": {"n1": {}}},
        )
    assert result["overrides_applied"]["pins"] == ["n1"]


def test_dispatch_routes_get_node_error():
    draft = _FakeDraft(id=uuid.uuid4(), tenant_id=TENANT, graph_json={})
    instance = _FakeInstance(
        id=uuid.uuid4(), tenant_id=TENANT, workflow_def_id=uuid.uuid4(),
    )
    db = _mock_db_for_node_error(
        instance=instance, wf_def=_FakeWfDef(is_ephemeral=True), log=_FakeLog(),
    )
    result = runner_tools.dispatch(
        "get_node_error",
        db=db, tenant_id=TENANT, draft=draft,
        args={"instance_id": str(instance.id), "node_id": "node_1"},
    )
    assert result["status"] == "failed"
