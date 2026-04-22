"""COPILOT-01b.ii.a — unit tests for the copilot runner tools.

Focuses on ``test_node_against_draft`` since it's the only runner
tool shipping today. Mocks ``dispatch_node`` so tests don't need a
live DB, LLM provider, or MCP server — we're verifying the
context-assembly logic, argument coercion, and error-surface
behaviour, not the node-handler dispatch itself.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.copilot import runner_tools


TENANT = "tenant-runner"


@dataclass
class _FakeDraft:
    id: uuid.UUID
    tenant_id: str
    graph_json: dict[str, Any]
    version: int


def _graph_with(*node_dicts: dict[str, Any]) -> dict[str, Any]:
    return {
        "nodes": list(node_dicts),
        "edges": [],
    }


def _node(
    node_id: str,
    label: str = "LLM Agent",
    *,
    config: dict[str, Any] | None = None,
    pinned_output: Any = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "label": label,
        "nodeCategory": "agent",
        "config": dict(config or {}),
        "status": "idle",
    }
    if pinned_output is not None:
        data["pinnedOutput"] = pinned_output
    return {
        "id": node_id,
        "type": "agenticNode",
        "position": {"x": 0, "y": 0},
        "data": data,
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_test_node_happy_path_returns_output():
    draft = _FakeDraft(
        id=uuid.uuid4(),
        tenant_id=TENANT,
        graph_json=_graph_with(_node("node_1")),
        version=1,
    )

    captured: dict[str, Any] = {}

    def _fake_dispatch(node_data, context, tenant_id, **kw):
        captured["node_data"] = node_data
        captured["context"] = dict(context)
        captured["tenant_id"] = tenant_id
        return {"response": "hello"}

    with patch("app.engine.node_handlers.dispatch_node", side_effect=_fake_dispatch):
        result = runner_tools.test_node_against_draft(
            db=MagicMock(),
            tenant_id=TENANT,
            draft=draft,
            args={"node_id": "node_1"},
        )

    assert result["node_id"] == "node_1"
    assert result["output"] == {"response": "hello"}
    assert "elapsed_ms" in result
    assert result.get("error") is None

    # Synthetic context populated with trigger + internals.
    ctx = captured["context"]
    assert ctx["trigger"] == {}
    assert ctx["_current_node_id"] == "node_1"
    assert ctx["_workflow_def_id"].startswith("draft:")
    assert "_instance_id" in ctx


def test_test_node_uses_pinned_outputs_from_graph():
    draft = _FakeDraft(
        id=uuid.uuid4(),
        tenant_id=TENANT,
        graph_json=_graph_with(
            _node("node_1", label="Webhook Trigger", pinned_output={"message": "ping"}),
            _node("node_2", label="LLM Agent"),
        ),
        version=1,
    )

    captured: dict[str, Any] = {}

    def _fake_dispatch(node_data, context, tenant_id, **kw):
        captured["context"] = dict(context)
        return {"ok": True}

    with patch("app.engine.node_handlers.dispatch_node", side_effect=_fake_dispatch):
        runner_tools.test_node_against_draft(
            db=MagicMock(), tenant_id=TENANT, draft=draft,
            args={"node_id": "node_2"},
        )

    assert captured["context"]["node_1"] == {"message": "ping"}


def test_test_node_caller_pins_override_graph_pins():
    """The LLM may want to probe 'what if node_1 returns X?' without
    modifying the draft. args['pins'] takes precedence over anything
    pinned on the graph."""
    draft = _FakeDraft(
        id=uuid.uuid4(),
        tenant_id=TENANT,
        graph_json=_graph_with(
            _node("node_1", label="Webhook Trigger", pinned_output={"message": "graph-pin"}),
            _node("node_2", label="LLM Agent"),
        ),
        version=1,
    )
    captured: dict[str, Any] = {}

    def _fake_dispatch(node_data, context, tenant_id, **kw):
        captured["context"] = dict(context)
        return {"ok": True}

    with patch("app.engine.node_handlers.dispatch_node", side_effect=_fake_dispatch):
        runner_tools.test_node_against_draft(
            db=MagicMock(), tenant_id=TENANT, draft=draft,
            args={
                "node_id": "node_2",
                "pins": {"node_1": {"message": "override"}},
            },
        )

    assert captured["context"]["node_1"] == {"message": "override"}


def test_test_node_passes_trigger_payload_through():
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json=_graph_with(_node("node_1")), version=1,
    )
    captured: dict[str, Any] = {}

    def _fake_dispatch(node_data, context, tenant_id, **kw):
        captured["context"] = dict(context)
        return {}

    with patch("app.engine.node_handlers.dispatch_node", side_effect=_fake_dispatch):
        runner_tools.test_node_against_draft(
            db=MagicMock(), tenant_id=TENANT, draft=draft,
            args={
                "node_id": "node_1",
                "trigger_payload": {"user_id": "u_42"},
            },
        )

    assert captured["context"]["trigger"] == {"user_id": "u_42"}


# ---------------------------------------------------------------------------
# Bad-input / missing-node paths
# ---------------------------------------------------------------------------


def test_test_node_missing_node_id_returns_error():
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json=_graph_with(_node("node_1")), version=1,
    )
    result = runner_tools.test_node_against_draft(
        db=MagicMock(), tenant_id=TENANT, draft=draft, args={},
    )
    assert "error" in result
    assert "node_id" in result["error"]


def test_test_node_unknown_node_returns_error():
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json=_graph_with(_node("node_1")), version=1,
    )
    result = runner_tools.test_node_against_draft(
        db=MagicMock(), tenant_id=TENANT, draft=draft,
        args={"node_id": "node_missing"},
    )
    assert "error" in result
    assert "node_missing" in result["error"]


def test_test_node_pins_must_be_dict():
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json=_graph_with(_node("node_1")), version=1,
    )
    result = runner_tools.test_node_against_draft(
        db=MagicMock(), tenant_id=TENANT, draft=draft,
        args={"node_id": "node_1", "pins": ["not", "a", "dict"]},
    )
    assert "error" in result


# ---------------------------------------------------------------------------
# Handler-raised errors surface, not propagate
# ---------------------------------------------------------------------------


def test_test_node_handler_exception_returned_as_error():
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json=_graph_with(_node("node_1")), version=1,
    )

    def _raising_dispatch(*_a, **_kw):
        raise ValueError("bad config: missing 'model'")

    with patch("app.engine.node_handlers.dispatch_node", side_effect=_raising_dispatch):
        result = runner_tools.test_node_against_draft(
            db=MagicMock(), tenant_id=TENANT, draft=draft,
            args={"node_id": "node_1"},
        )

    assert result["node_id"] == "node_1"
    assert "output" not in result or result["output"] is None
    assert "error" in result
    assert "bad config" in result["error"]
    assert "elapsed_ms" in result


def test_test_node_suspended_async_reported_explicitly():
    """AutomationEdge-style nodes raise NodeSuspendedAsync even during
    probes (creates an async_jobs row as a real side effect). We
    report it so the agent knows this was expected behaviour, not a
    bug."""
    from app.engine.exceptions import NodeSuspendedAsync

    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json=_graph_with(_node("node_1", label="AutomationEdge")),
        version=1,
    )

    def _suspended_dispatch(*_a, **_kw):
        raise NodeSuspendedAsync(
            async_job_id=str(uuid.uuid4()),
            system="automationedge",
            external_job_id="ae_job_123",
        )

    with patch("app.engine.node_handlers.dispatch_node", side_effect=_suspended_dispatch):
        result = runner_tools.test_node_against_draft(
            db=MagicMock(), tenant_id=TENANT, draft=draft,
            args={"node_id": "node_1"},
        )

    assert "error" in result
    assert "automationedge" in result["error"]
    assert "ae_job_123" in result["error"]
    assert "expected" in result["error"].lower()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def test_dispatch_routes_test_node():
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json=_graph_with(_node("node_1")), version=1,
    )
    with patch("app.engine.node_handlers.dispatch_node", return_value={"ok": True}):
        result = runner_tools.dispatch(
            "test_node",
            db=MagicMock(), tenant_id=TENANT, draft=draft,
            args={"node_id": "node_1"},
        )
    assert result["output"] == {"ok": True}


def test_dispatch_unknown_runner_tool_raises_keyerror():
    """Caller (agent._dispatch_tool) uses the KeyError to fall back
    to the pure tool layer — distinguishing "not a runner tool" from
    "runner tool failed"."""
    with pytest.raises(KeyError):
        runner_tools.dispatch(
            "not_a_runner_tool",
            db=MagicMock(), tenant_id=TENANT,
            draft=_FakeDraft(
                id=uuid.uuid4(), tenant_id=TENANT,
                graph_json={"nodes": [], "edges": []}, version=1,
            ),
            args={},
        )


def test_runner_tool_names_is_not_empty():
    # Guardrail: at least one runner tool in 01b.ii.a.
    assert "test_node" in runner_tools.RUNNER_TOOL_NAMES


# ---------------------------------------------------------------------------
# get_automationedge_handoff_info — deterministic-automation fork
# ---------------------------------------------------------------------------


class _FakeIntegration:
    def __init__(self, *, label, is_default, config_json):
        self.label = label
        self.is_default = is_default
        self.config_json = config_json


def _stub_integrations_query(session_mock: MagicMock, integrations: list):
    """Wire session.query(TenantIntegration).filter_by(...).order_by(...).all()
    to return our fake integrations."""
    query = session_mock.query.return_value
    query.filter_by.return_value = query
    query.order_by.return_value = query
    query.all.return_value = integrations


def test_handoff_info_returns_connections_and_default_copilot_url():
    db = MagicMock()
    _stub_integrations_query(db, [
        _FakeIntegration(
            label="prod-ae",
            is_default=True,
            config_json={
                "baseUrl": "https://ae.example.com",
                "orgCode": "ACME",
                "copilotUrl": "https://ae.example.com/copilot",
            },
        ),
        _FakeIntegration(
            label="staging-ae",
            is_default=False,
            config_json={
                "baseUrl": "https://staging-ae.example.com",
                "orgCode": "ACME-STG",
            },
        ),
    ])
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []}, version=1,
    )

    result = runner_tools.get_automationedge_handoff_info(
        db, tenant_id=TENANT, draft=draft, args={},
    )

    assert result["orchestrator_node_type"] == "automationedge"
    assert result["ae_copilot_url"] == "https://ae.example.com/copilot"
    assert len(result["existing_connections"]) == 2
    assert result["existing_connections"][0]["label"] == "prod-ae"
    assert result["existing_connections"][0]["is_default"] is True
    assert result["existing_connections"][1]["copilot_url"] is None
    assert "INLINE" in result["guidance"]
    assert "HANDOFF" in result["guidance"]


def test_handoff_info_falls_through_to_env_default():
    """No per-tenant copilotUrl anywhere → use settings.ae_copilot_url."""
    db = MagicMock()
    _stub_integrations_query(db, [
        _FakeIntegration(
            label="prod-ae",
            is_default=True,
            config_json={"baseUrl": "https://ae.example.com", "orgCode": "ACME"},
        ),
    ])
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []}, version=1,
    )

    with patch("app.config.settings") as mock_settings:
        mock_settings.ae_copilot_url = "https://ae-copilot.example.com/"
        result = runner_tools.get_automationedge_handoff_info(
            db, tenant_id=TENANT, draft=draft, args={},
        )

    assert result["ae_copilot_url"] == "https://ae-copilot.example.com/"


def test_handoff_info_no_connections_surfaces_empty_list_and_null_url():
    """Tenant hasn't registered any AE integration and no env fallback —
    the agent should still get a usable answer it can narrate ("you'll
    need to add a connection first")."""
    db = MagicMock()
    _stub_integrations_query(db, [])
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []}, version=1,
    )

    with patch("app.config.settings") as mock_settings:
        mock_settings.ae_copilot_url = ""
        result = runner_tools.get_automationedge_handoff_info(
            db, tenant_id=TENANT, draft=draft, args={},
        )

    assert result["existing_connections"] == []
    assert result["ae_copilot_url"] is None
    # Guidance is still returned so the agent has something to narrate.
    assert result["guidance"]


def test_handoff_info_prefers_default_connection_copilot_url():
    """When multiple connections have their own copilotUrl, the default
    connection's URL wins."""
    db = MagicMock()
    _stub_integrations_query(db, [
        _FakeIntegration(
            label="prod-ae",
            is_default=True,
            config_json={
                "baseUrl": "https://prod.ae.example.com",
                "orgCode": "ACME",
                "copilotUrl": "https://prod-copilot.example.com",
            },
        ),
        _FakeIntegration(
            label="staging-ae",
            is_default=False,
            config_json={
                "baseUrl": "https://stg.ae.example.com",
                "orgCode": "ACME-STG",
                "copilotUrl": "https://stg-copilot.example.com",
            },
        ),
    ])
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []}, version=1,
    )

    result = runner_tools.get_automationedge_handoff_info(
        db, tenant_id=TENANT, draft=draft, args={},
    )
    assert result["ae_copilot_url"] == "https://prod-copilot.example.com"


def test_dispatch_routes_handoff_info():
    db = MagicMock()
    _stub_integrations_query(db, [])
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []}, version=1,
    )
    with patch("app.config.settings") as mock_settings:
        mock_settings.ae_copilot_url = ""
        result = runner_tools.dispatch(
            "get_automationedge_handoff_info",
            db=db, tenant_id=TENANT, draft=draft, args={},
        )
    assert result["orchestrator_node_type"] == "automationedge"


def test_runner_tool_names_includes_handoff():
    assert "get_automationedge_handoff_info" in runner_tools.RUNNER_TOOL_NAMES


# ---------------------------------------------------------------------------
# execute_draft_sync + get_execution_logs + cleanup
# (COPILOT-01b.ii.b)
# ---------------------------------------------------------------------------


def test_execute_draft_blocks_on_validation_errors():
    """Validation errors must short-circuit before we materialise the
    ephemeral WorkflowDefinition — otherwise we'd produce a junk row
    just to throw it away."""
    from app.copilot import tool_layer

    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json=_graph_with(_node("node_1")), version=1,
    )
    db = MagicMock()

    with patch.object(
        tool_layer, "validate_graph",
        return_value={"errors": ["Node node_1 missing required field"], "warnings": []},
    ):
        result = runner_tools.execute_draft_sync(
            db, tenant_id=TENANT, draft=draft, args={},
        )

    assert "error" in result
    assert "validation failed" in result["error"].lower()
    # No rows added — validation gated the expensive path.
    assert not db.add.called


def test_execute_draft_bad_payload_type_returns_error():
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json=_graph_with(_node("node_1")), version=1,
    )
    result = runner_tools.execute_draft_sync(
        MagicMock(), tenant_id=TENANT, draft=draft,
        args={"payload": "not-a-dict"},
    )
    assert "error" in result


def test_execute_draft_bad_timeout_type_returns_error():
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json=_graph_with(_node("node_1")), version=1,
    )
    result = runner_tools.execute_draft_sync(
        MagicMock(), tenant_id=TENANT, draft=draft,
        args={"timeout_seconds": "soon"},
    )
    assert "error" in result


def test_execute_draft_happy_path_returns_instance_and_output():
    """execute_graph mocked to mark the instance completed with an
    output context. We verify the ephemeral WorkflowDefinition is
    created with is_ephemeral=True, the correct name prefix, and
    the agent gets back {instance_id, status, output, ...}."""
    from app.copilot import tool_layer

    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json=_graph_with(_node("node_1")), version=1,
    )

    added_rows: list[Any] = []
    db = MagicMock()
    db.add.side_effect = lambda r: added_rows.append(r)

    # Fake engine run via a mocked SessionLocal in the worker thread.
    # The worker thread's session queries WorkflowInstance by id and
    # we return a MagicMock with the final state.
    from app.models.workflow import WorkflowInstance

    final_instance = MagicMock(spec=WorkflowInstance)
    final_instance.status = "completed"
    final_instance.context_json = {
        "node_1": {"response": "ok"},
        "_instance_id": "...",  # internal — must be stripped from output
    }
    final_instance.started_at = None
    final_instance.completed_at = None

    fake_worker_session = MagicMock()
    fake_worker_session.query.return_value.filter_by.return_value.first.return_value = final_instance

    with patch.object(tool_layer, "validate_graph", return_value={"errors": [], "warnings": []}), \
         patch("app.database.SessionLocal", return_value=fake_worker_session), \
         patch("app.database.set_tenant_context"), \
         patch("app.engine.dag_runner.execute_graph"):
        result = runner_tools.execute_draft_sync(
            db, tenant_id=TENANT, draft=draft, args={"timeout_seconds": 10},
        )

    assert result["status"] == "completed"
    assert "instance_id" in result
    assert result["output"] == {"node_1": {"response": "ok"}}
    # Internal _prefix keys stripped from the output payload.
    assert "_instance_id" not in result["output"]

    # Temp workflow and instance both added to the session.
    from app.models.workflow import WorkflowDefinition

    wf_rows = [r for r in added_rows if isinstance(r, WorkflowDefinition)]
    inst_rows = [r for r in added_rows if isinstance(r, WorkflowInstance)]
    assert len(wf_rows) == 1
    assert wf_rows[0].is_ephemeral is True
    assert wf_rows[0].is_active is False
    assert wf_rows[0].name.startswith("__copilot_draft_")
    assert len(inst_rows) == 1


def test_execute_draft_timeout_returns_hint_with_instance_id():
    """When the engine call exceeds the configured timeout, the agent
    gets back status='timeout' with a hint to call get_execution_logs.
    We force the timeout by patching execute_graph to block."""
    from app.copilot import tool_layer

    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json=_graph_with(_node("node_1")), version=1,
    )
    db = MagicMock()

    def _slow_execute_graph(*_a, **_kw):
        import time as _t
        _t.sleep(3)

    # Worker session's query still needs to exist even though we hit
    # timeout before it's consulted.
    fake_worker_session = MagicMock()

    with patch.object(tool_layer, "validate_graph", return_value={"errors": [], "warnings": []}), \
         patch("app.database.SessionLocal", return_value=fake_worker_session), \
         patch("app.database.set_tenant_context"), \
         patch("app.engine.dag_runner.execute_graph", side_effect=_slow_execute_graph):
        result = runner_tools.execute_draft_sync(
            db, tenant_id=TENANT, draft=draft, args={"timeout_seconds": 1},
        )

    assert result["status"] == "timeout"
    assert "instance_id" in result
    assert "hint" in result
    assert "get_execution_logs" in result["hint"]


def test_execute_draft_accepts_oversized_timeout_without_crashing():
    """The runner clamps ``timeout_seconds`` to ``[1, 300]`` internally
    so a pathological caller value (9999, -5, 0) doesn't blow up the
    ThreadPoolExecutor or block the agent for an unreasonable time.
    Directly observing the clamped value means patching the
    ThreadPoolExecutor, which is too much mock plumbing — instead we
    assert the call returns normally for each edge value."""
    from app.copilot import tool_layer

    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json=_graph_with(_node("node_1")), version=1,
    )

    # Fake worker session's query returns an instance-shaped thing so
    # the "post-run read" doesn't choke on MagicMock attribute types.
    from app.models.workflow import WorkflowInstance

    stub_instance = MagicMock(spec=WorkflowInstance)
    stub_instance.status = "completed"
    stub_instance.context_json = {}
    stub_instance.started_at = None
    stub_instance.completed_at = None
    fake_worker_session = MagicMock()
    fake_worker_session.query.return_value.filter_by.return_value.first.return_value = stub_instance

    with patch.object(tool_layer, "validate_graph", return_value={"errors": [], "warnings": []}), \
         patch("app.database.SessionLocal", return_value=fake_worker_session), \
         patch("app.database.set_tenant_context"), \
         patch("app.engine.dag_runner.execute_graph"):
        for oversized in (9999, -5, 0):
            result = runner_tools.execute_draft_sync(
                MagicMock(), tenant_id=TENANT, draft=draft,
                args={"timeout_seconds": oversized},
            )
            assert "error" not in result, (
                f"Clamp should not surface as an error for "
                f"timeout_seconds={oversized}: got {result}"
            )
            assert result.get("status") == "completed"


def test_get_execution_logs_returns_logs_for_ephemeral_instance():
    """Happy path: the instance was created by execute_draft (so its
    parent WD is ephemeral); get_execution_logs returns structured
    log rows the agent can read to debug."""
    from app.models.workflow import ExecutionLog, WorkflowDefinition, WorkflowInstance

    instance_id = uuid.uuid4()

    instance = MagicMock(spec=WorkflowInstance)
    instance.id = instance_id
    instance.tenant_id = TENANT
    instance.workflow_def_id = uuid.uuid4()
    instance.status = "completed"

    wf_def = MagicMock(spec=WorkflowDefinition)
    wf_def.id = instance.workflow_def_id
    wf_def.tenant_id = TENANT
    wf_def.is_ephemeral = True

    log = MagicMock(spec=ExecutionLog)
    log.node_id = "node_1"
    log.node_type = "LLM Agent"
    log.status = "completed"
    log.output_json = {"response": "hi"}
    log.error = None
    log.started_at = None
    log.completed_at = None

    db = MagicMock()
    # Sequence: WorkflowInstance lookup → WorkflowDefinition lookup
    # → ExecutionLog list.
    def _first_side_effect(*_a, **_kw):
        return next(_first_side_effect._iter)
    _first_side_effect._iter = iter([instance, wf_def])

    filter_by = db.query.return_value.filter_by.return_value
    filter_by.first.side_effect = _first_side_effect
    filter_by.order_by.return_value.all.return_value = [log]

    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []}, version=1,
    )
    result = runner_tools.get_execution_logs(
        db, tenant_id=TENANT, draft=draft,
        args={"instance_id": str(instance_id)},
    )

    assert result["instance_id"] == str(instance_id)
    assert result["status"] == "completed"
    assert result["log_count"] == 1
    assert result["logs"][0]["node_id"] == "node_1"
    assert result["logs"][0]["output_json"] == {"response": "hi"}


def test_get_execution_logs_rejects_non_ephemeral_instance():
    """Safety: the agent must not be able to read logs from production
    runs. Only instances whose parent WD is ephemeral are accessible."""
    from app.models.workflow import WorkflowDefinition, WorkflowInstance

    instance_id = uuid.uuid4()

    instance = MagicMock(spec=WorkflowInstance)
    instance.id = instance_id
    instance.tenant_id = TENANT
    instance.workflow_def_id = uuid.uuid4()
    instance.status = "completed"

    wf_def = MagicMock(spec=WorkflowDefinition)
    wf_def.id = instance.workflow_def_id
    wf_def.tenant_id = TENANT
    wf_def.is_ephemeral = False  # production run — refused

    db = MagicMock()
    filter_by = db.query.return_value.filter_by.return_value
    filter_by.first.side_effect = iter([instance, wf_def])

    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []}, version=1,
    )
    result = runner_tools.get_execution_logs(
        db, tenant_id=TENANT, draft=draft,
        args={"instance_id": str(instance_id)},
    )

    assert "error" in result
    assert "not a copilot-initiated run" in result["error"]


def test_get_execution_logs_missing_instance_id():
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []}, version=1,
    )
    result = runner_tools.get_execution_logs(
        MagicMock(), tenant_id=TENANT, draft=draft, args={},
    )
    assert "error" in result
    assert "instance_id" in result["error"]


def test_get_execution_logs_invalid_instance_id():
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []}, version=1,
    )
    result = runner_tools.get_execution_logs(
        MagicMock(), tenant_id=TENANT, draft=draft,
        args={"instance_id": "not-a-uuid"},
    )
    assert "error" in result
    assert "Invalid" in result["error"]


def test_cleanup_ephemeral_workflows_deletes_old_rows():
    """The cleanup utility removes ephemerals older than the cutoff
    and returns the count. Cascade deletes on WorkflowInstance +
    ExecutionLog are the DB's job — we just verify the orchestration."""
    from app.models.workflow import WorkflowDefinition

    old_wf = MagicMock(spec=WorkflowDefinition)
    old_wf.id = uuid.uuid4()

    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [old_wf]

    count = runner_tools.cleanup_ephemeral_workflows(
        db, older_than_seconds=0,
    )

    assert count == 1
    db.delete.assert_called_once_with(old_wf)
    assert db.commit.called


def test_runner_tool_names_includes_execute_and_logs():
    assert "execute_draft" in runner_tools.RUNNER_TOOL_NAMES
    assert "get_execution_logs" in runner_tools.RUNNER_TOOL_NAMES


def test_dispatch_routes_search_docs_and_get_node_examples():
    """01b.iii — docs-grounding tools. ``search_docs`` and
    ``get_node_examples`` are filesystem-backed (no DB) but still
    routed through the runner-tool dispatcher so the agent sees them
    alongside the stateful tools."""
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []}, version=1,
    )
    with patch("app.copilot.docs_index.search_docs") as mock_search:
        mock_search.return_value = {"query": "x", "match_count": 0, "results": []}
        result = runner_tools.dispatch(
            "search_docs",
            db=MagicMock(), tenant_id=TENANT, draft=draft,
            args={"query": "how does the Intent Classifier work?", "top_k": 3},
        )
    assert mock_search.called
    assert result["match_count"] == 0

    with patch("app.copilot.docs_index.get_node_examples") as mock_examples:
        mock_examples.return_value = {
            "node_type": "llm_agent",
            "registry_entry": None,
            "related_sections": [],
        }
        result = runner_tools.dispatch(
            "get_node_examples",
            db=MagicMock(), tenant_id=TENANT, draft=draft,
            args={"node_type": "llm_agent"},
        )
    assert mock_examples.called
    assert result["node_type"] == "llm_agent"


def test_get_node_examples_missing_node_type_returns_error():
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []}, version=1,
    )
    result = runner_tools.dispatch(
        "get_node_examples",
        db=MagicMock(), tenant_id=TENANT, draft=draft,
        args={},
    )
    assert "error" in result


def test_runner_tool_names_includes_docs_tools():
    assert "search_docs" in runner_tools.RUNNER_TOOL_NAMES
    assert "get_node_examples" in runner_tools.RUNNER_TOOL_NAMES


# ---------------------------------------------------------------------------
# SMART-04 — check_draft (validate_graph + lints wrapper)
# ---------------------------------------------------------------------------


def test_check_draft_returns_schema_and_lints_when_flag_on():
    """Happy path: lints flag on, draft has a real issue — returns
    {errors, warnings, lints, lints_enabled: true} with the lint
    surfaced."""
    from app.engine.tenant_policy_resolver import EffectivePolicy

    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={
            # Single LLM node, no trigger — no_trigger lint fires.
            "nodes": [{"id": "node_1", "type": "agenticNode",
                       "data": {"label": "LLM Agent", "nodeCategory": "agent",
                                "config": {}}}],
            "edges": [],
        },
        version=1,
    )
    fake_policy = EffectivePolicy(
        execution_quota_per_hour=50, max_snapshots=20,
        mcp_pool_size=4, rate_limit_requests_per_window=100,
        rate_limit_window_seconds=60,
        smart_04_lints_enabled=True,
        smart_06_mcp_discovery_enabled=True,
        source={},
    )
    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=fake_policy,
    ), patch(
        "app.engine.llm_credentials_resolver.get_credentials_status",
        return_value={},
    ):
        result = runner_tools.check_draft(
            MagicMock(), tenant_id=TENANT, draft=draft, args={},
        )

    assert result["lints_enabled"] is True
    assert isinstance(result["errors"], list)
    assert isinstance(result["warnings"], list)
    codes = {l["code"] for l in result["lints"]}
    assert "no_trigger" in codes


def test_check_draft_skips_lints_when_flag_off():
    """Cost-conscious tenant opts out — check_draft still runs
    schema validation but lints is []."""
    from app.engine.tenant_policy_resolver import EffectivePolicy

    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={
            "nodes": [{"id": "node_1", "type": "agenticNode",
                       "data": {"label": "LLM Agent", "nodeCategory": "agent",
                                "config": {}}}],
            "edges": [],
        },
        version=1,
    )
    fake_policy = EffectivePolicy(
        execution_quota_per_hour=50, max_snapshots=20,
        mcp_pool_size=4, rate_limit_requests_per_window=100,
        rate_limit_window_seconds=60,
        smart_04_lints_enabled=False,
        smart_06_mcp_discovery_enabled=True,
        source={},
    )
    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=fake_policy,
    ):
        result = runner_tools.check_draft(
            MagicMock(), tenant_id=TENANT, draft=draft, args={},
        )
    assert result["lints_enabled"] is False
    assert result["lints"] == []


def test_check_draft_lint_crash_does_not_poison_turn():
    """If the lint module itself raises (bug in a rule), the runner
    must degrade gracefully rather than 500 the agent's turn."""
    from app.engine.tenant_policy_resolver import EffectivePolicy

    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []}, version=1,
    )
    fake_policy = EffectivePolicy(
        execution_quota_per_hour=50, max_snapshots=20,
        mcp_pool_size=4, rate_limit_requests_per_window=100,
        rate_limit_window_seconds=60,
        smart_04_lints_enabled=True,
        smart_06_mcp_discovery_enabled=True,
        source={},
    )
    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=fake_policy,
    ), patch(
        "app.copilot.lints.run_lints", side_effect=RuntimeError("rule blew up"),
    ):
        result = runner_tools.check_draft(
            MagicMock(), tenant_id=TENANT, draft=draft, args={},
        )
    assert result["lints"] == []
    assert result["lints_enabled"] is True
    assert "lint_runtime_error" in result
    assert "rule blew up" in result["lint_runtime_error"]


def test_dispatch_routes_check_draft():
    from app.engine.tenant_policy_resolver import EffectivePolicy

    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []}, version=1,
    )
    fake_policy = EffectivePolicy(
        execution_quota_per_hour=50, max_snapshots=20,
        mcp_pool_size=4, rate_limit_requests_per_window=100,
        rate_limit_window_seconds=60,
        smart_04_lints_enabled=True,
        smart_06_mcp_discovery_enabled=True,
        source={},
    )
    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=fake_policy,
    ):
        result = runner_tools.dispatch(
            "check_draft",
            db=MagicMock(), tenant_id=TENANT, draft=draft, args={},
        )
    assert "errors" in result
    assert "warnings" in result
    assert "lints" in result
    assert "lints_enabled" in result


def test_runner_tool_names_includes_check_draft():
    assert "check_draft" in runner_tools.RUNNER_TOOL_NAMES


# ---------------------------------------------------------------------------
# SMART-06 — discover_mcp_tools
# ---------------------------------------------------------------------------


def _policy_with(smart_06: bool = True, smart_04: bool = True):
    from app.engine.tenant_policy_resolver import EffectivePolicy
    return EffectivePolicy(
        execution_quota_per_hour=50, max_snapshots=20,
        mcp_pool_size=4, rate_limit_requests_per_window=100,
        rate_limit_window_seconds=60,
        smart_04_lints_enabled=smart_04,
        smart_06_mcp_discovery_enabled=smart_06,
        source={},
    )


def test_discover_mcp_tools_returns_tenant_tools_when_flag_on():
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []}, version=1,
    )
    raw = [
        {"name": "enrich_ip", "title": "Enrich IP", "description": "Look up threat intel",
         "category": "security", "safety_tier": "safe_read", "tags": ["threat", "ip"]},
        {"name": "create_ticket", "description": "Open a ticket"},
    ]
    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy_with(smart_06=True),
    ), patch("app.engine.mcp_client.list_tools", return_value=raw):
        result = runner_tools.discover_mcp_tools(
            MagicMock(), tenant_id=TENANT, draft=draft, args={},
        )
    assert result["discovery_enabled"] is True
    assert result["server_label"] is None
    assert len(result["tools"]) == 2
    # Normalisation: missing fields default to "" or [].
    assert result["tools"][1]["title"] == "create_ticket"
    assert result["tools"][1]["tags"] == []
    assert result["tools"][0]["category"] == "security"


def test_discover_mcp_tools_returns_empty_when_flag_off():
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []}, version=1,
    )
    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy_with(smart_06=False),
    ), patch("app.engine.mcp_client.list_tools") as mock_list:
        result = runner_tools.discover_mcp_tools(
            MagicMock(), tenant_id=TENANT, draft=draft, args={},
        )
    assert result["discovery_enabled"] is False
    assert result["tools"] == []
    # Never queries MCP when the flag is off.
    assert not mock_list.called


def test_discover_mcp_tools_honours_server_label():
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []}, version=1,
    )
    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy_with(smart_06=True),
    ), patch("app.engine.mcp_client.list_tools", return_value=[]) as mock_list:
        result = runner_tools.discover_mcp_tools(
            MagicMock(), tenant_id=TENANT, draft=draft,
            args={"server_label": "prod-mcp"},
        )
    assert result["server_label"] == "prod-mcp"
    call_kwargs = mock_list.call_args.kwargs
    assert call_kwargs["server_label"] == "prod-mcp"


def test_discover_mcp_tools_rejects_non_string_label():
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []}, version=1,
    )
    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy_with(smart_06=True),
    ):
        result = runner_tools.discover_mcp_tools(
            MagicMock(), tenant_id=TENANT, draft=draft,
            args={"server_label": 42},
        )
    assert "error" in result


def test_discover_mcp_tools_list_tools_crash_degrades_gracefully():
    """If the MCP server is unreachable, don't blow up the agent
    turn — return discovery_enabled=true, tools=[], error=msg so
    the LLM can tell the user why MCP discovery is empty."""
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []}, version=1,
    )
    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy_with(smart_06=True),
    ), patch(
        "app.engine.mcp_client.list_tools",
        side_effect=RuntimeError("connection refused"),
    ):
        result = runner_tools.discover_mcp_tools(
            MagicMock(), tenant_id=TENANT, draft=draft, args={},
        )
    assert result["discovery_enabled"] is True
    assert result["tools"] == []
    assert "error" in result
    assert "connection refused" in result["error"]


def test_dispatch_routes_discover_mcp_tools():
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []}, version=1,
    )
    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy_with(smart_06=True),
    ), patch("app.engine.mcp_client.list_tools", return_value=[]):
        result = runner_tools.dispatch(
            "discover_mcp_tools",
            db=MagicMock(), tenant_id=TENANT, draft=draft, args={},
        )
    assert result["discovery_enabled"] is True
    assert result["tools"] == []


def test_runner_tool_names_includes_discover_mcp_tools():
    assert "discover_mcp_tools" in runner_tools.RUNNER_TOOL_NAMES


def test_dispatch_routes_execute_draft_and_get_execution_logs():
    """The agent's runner-tool dispatch must route both new tool
    names — otherwise the agent would call the tool and get a
    KeyError back that isn't caught."""
    draft = _FakeDraft(
        id=uuid.uuid4(), tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []}, version=1,
    )
    with patch.object(
        runner_tools, "execute_draft_sync",
        return_value={"status": "completed", "instance_id": "..."},
    ) as mock_run:
        result = runner_tools.dispatch(
            "execute_draft",
            db=MagicMock(), tenant_id=TENANT, draft=draft, args={},
        )
    assert result["status"] == "completed"
    assert mock_run.called

    with patch.object(
        runner_tools, "get_execution_logs",
        return_value={"instance_id": "...", "status": "completed", "log_count": 0, "logs": []},
    ) as mock_logs:
        result = runner_tools.dispatch(
            "get_execution_logs",
            db=MagicMock(), tenant_id=TENANT, draft=draft,
            args={"instance_id": str(uuid.uuid4())},
        )
    assert mock_logs.called
