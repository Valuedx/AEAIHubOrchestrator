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
