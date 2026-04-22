"""COPILOT-01b.ii — stateful runner tools for the agent.

Separate module from ``tool_layer.py`` because these tools need a DB
session and a tenant context — they actually run node handlers, so
they touch the engine, the credential vault, and MCP clients. The
pure tool layer stays pure (graph dict in, graph dict out) so the
agent runner can chain many pure-tool mutations before committing;
runner tools are the opt-in expensive-side-effect escape hatch.

**This slice (01b.ii.a) ships ``test_node`` only.** ``execute_draft``
and ``get_execution_logs`` land in 01b.ii.b — they need an
``is_ephemeral`` flag on ``workflow_definitions`` (plus filters in
list / scheduler / A2A / published surfaces) so that the temporary
WorkflowDefinition rows they produce don't pollute the UI, which is
a bigger migration than this slice warrants on its own.

Design contract
---------------

Each runner tool takes:

  * ``db`` — a Session already scoped to the tenant (via
    ``get_tenant_db``'s ``set_tenant_context`` call or equivalent).
  * ``tenant_id`` — for handler dispatch (credentials, MCP
    resolution, vault lookups).
  * ``draft`` — the live ``WorkflowDraft`` row so tools can read
    ``draft.graph_json`` and optionally mutate it (though 01b.ii
    tools do not mutate).
  * ``args`` — the tool-specific dict the LLM emitted as
    ``function_call.input``.

Returns a dict ready to be sent back to the LLM as a tool_result.
Errors are returned as ``{"error": "..."}`` rather than raised so
the LLM reads the error and self-corrects — same failure-surface
discipline as ``tool_layer.dispatch``.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.copilot import WorkflowDraft

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# test_node — run one handler in isolation against pinned upstream data
# ---------------------------------------------------------------------------


def test_node_against_draft(
    db: Session,
    *,
    tenant_id: str,
    draft: WorkflowDraft,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Run one node from the draft's graph in isolation.

    Mirrors the DV-02 ``POST /workflows/{id}/nodes/{node_id}/test``
    endpoint but operates on ``draft.graph_json`` directly — no
    published workflow required. Builds a synthetic execution context
    from pinned upstream outputs plus a caller-supplied ``pins`` and
    ``trigger_payload``, then dispatches exactly one node handler.

    Args
    ----
    ``args`` is the tool-call dict the LLM emits:

    ::

        {
          "node_id": "node_7",
          "trigger_payload": {...},          # optional; empty dict if absent
          "pins": {"node_3": {...}, "node_5": {...}}  # optional; merged
                                             # with draft's pinnedOutput
        }

    Result shape
    ------------

    Success::

        {"node_id": "...", "output": <handler return>, "elapsed_ms": N}

    Handler raised::

        {"node_id": "...", "error": "...", "elapsed_ms": N}

    Bad args / missing node::

        {"error": "..."}   (no node_id key; caller surfaces 400-shape)
    """
    node_id = args.get("node_id")
    if not node_id:
        return {"error": "test_node requires 'node_id'"}

    graph = draft.graph_json or {}
    nodes = graph.get("nodes") or []
    target = next((n for n in nodes if n.get("id") == node_id), None)
    if target is None:
        return {"error": f"Node '{node_id}' not in draft graph"}

    # Lazy imports — runner tools aren't always reachable in unit tests
    # that only exercise the pure tool layer.
    from app.engine.exceptions import NodeSuspendedAsync
    from app.engine.node_handlers import dispatch_node

    # Build synthetic context.
    #
    # Precedence: args.pins override any pinnedOutput on the graph (so
    # the LLM can test "what if node_3 returned X?" without editing
    # the draft). Then pinnedOutputs fill in anything else the
    # expression evaluator might reach.
    pins_override = args.get("pins") or {}
    if not isinstance(pins_override, dict):
        return {"error": "test_node 'pins' must be an object keyed by node_id"}

    context: dict[str, Any] = {}
    for n in nodes:
        data = n.get("data") or {}
        pin = data.get("pinnedOutput")
        if isinstance(pin, dict):
            context[n["id"]] = dict(pin)
    for pin_node_id, pin_value in pins_override.items():
        if isinstance(pin_value, dict):
            context[pin_node_id] = dict(pin_value)
        else:
            context[pin_node_id] = pin_value

    context["trigger"] = args.get("trigger_payload") or {}
    # Synthetic internals — the agent handlers
    # (_handle_agent, _handle_save_conversation_state, ...) read
    # these directly and need non-null values even during a probe.
    context["_instance_id"] = str(uuid.uuid4())
    context["_current_node_id"] = node_id
    context["_workflow_def_id"] = f"draft:{draft.id}"

    started = time.monotonic()
    node_data = target.get("data") or {}
    try:
        output = dispatch_node(node_data, context, tenant_id, db=db)
    except NodeSuspendedAsync as sus:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            "node_id": node_id,
            "error": (
                f"Node suspended on external system "
                f"'{sus.system}' (external_job_id={sus.external_job_id}). "
                "An async_jobs row was created as a side effect — expected "
                "for AutomationEdge-style nodes even during a probe."
            ),
            "elapsed_ms": elapsed_ms,
        }
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "copilot runner: test_node dispatch raised for node=%s "
            "(draft=%s, tenant=%s): %s",
            node_id, draft.id, tenant_id, exc,
        )
        return {
            "node_id": node_id,
            "error": str(exc),
            "elapsed_ms": elapsed_ms,
        }

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return {
        "node_id": node_id,
        "output": output,
        "elapsed_ms": elapsed_ms,
    }


# ---------------------------------------------------------------------------
# get_automationedge_handoff_info — deterministic-automation deflection
# ---------------------------------------------------------------------------
#
# The orchestrator is AI-native, but many real-world tenant workflows
# embed a deterministic RPA step (SAP posting, form submission, file
# transfer, ERP update). Two valid paths:
#
#   Path A — inline.  Add an ``automationedge`` node here. Needs the
#            name / id of an AE workflow the tenant already has.
#   Path B — handoff. Open the AutomationEdge Copilot (separate
#            product) to design the RPA steps first, then come back
#            and reference the resulting AE workflow from path A.
#
# This tool hands the agent everything it needs to propose the fork
# to the user: what AE connections the tenant already has registered,
# and the deep-link URL for path B. The agent decides how to present
# it; the prompt guides the wording.


def get_automationedge_handoff_info(
    db: Session,
    *,
    tenant_id: str,
    draft: WorkflowDraft,  # noqa: ARG001 — part of the runner-tool contract
    args: dict[str, Any],  # noqa: ARG001 — no args needed today
) -> dict[str, Any]:
    """Return the AE-handoff context:

    ::

        {
          "orchestrator_node_type": "automationedge",
          "existing_connections": [
            {
              "label": "prod-ae",
              "base_url": "...",
              "org_code": "...",
              "is_default": true,
              "copilot_url": "<from config_json or null>"
            },
            ...
          ],
          "ae_copilot_url": "<first configured; per-tenant > env default>",
          "guidance": "...how to decide between inline vs. handoff..."
        }

    Works even if the tenant has zero AE connections — the agent then
    surfaces the generic handoff URL (or explains there's no AE
    integration configured yet).
    """
    from app.config import settings
    from app.models.workflow import TenantIntegration

    connections = (
        db.query(TenantIntegration)
        .filter_by(tenant_id=tenant_id, system="automationedge")
        .order_by(TenantIntegration.is_default.desc(), TenantIntegration.label)
        .all()
    )

    per_connection: list[dict[str, Any]] = []
    best_copilot_url: str | None = None
    for conn in connections:
        cfg = conn.config_json or {}
        conn_copilot_url = cfg.get("copilotUrl") or None
        if conn.is_default and conn_copilot_url and best_copilot_url is None:
            best_copilot_url = conn_copilot_url
        per_connection.append({
            "label": conn.label,
            "base_url": cfg.get("baseUrl"),
            "org_code": cfg.get("orgCode"),
            "is_default": bool(conn.is_default),
            "copilot_url": conn_copilot_url,
        })

    # Precedence: per-tenant default connection's copilotUrl →
    # any connection's copilotUrl → process-wide env default → None.
    if best_copilot_url is None:
        for c in per_connection:
            if c["copilot_url"]:
                best_copilot_url = c["copilot_url"]
                break
    if best_copilot_url is None and settings.ae_copilot_url:
        best_copilot_url = settings.ae_copilot_url

    guidance = (
        "Two paths for this kind of step:\n"
        "1) INLINE — add an `automationedge` node in this draft and "
        "point it at an existing AE workflow the tenant already has. "
        "Use this when the AE workflow already exists; you need the "
        "`workflowName` (or id) to put in the node config.\n"
        "2) HANDOFF — if the user hasn't built the AE workflow yet, "
        "point them at the AutomationEdge Copilot (separate product) "
        "to design the RPA steps first. Come back afterwards with the "
        "workflow name, then use path 1. Do NOT attempt to design the "
        "inner RPA steps yourself — that's AE's responsibility, not "
        "this orchestrator's.\n"
        "Same fork applies inside a Sub-Workflow: if the sub-workflow "
        "is entirely deterministic RPA, an `automationedge` node is "
        "usually a better fit than a full sub-graph."
    )

    return {
        "orchestrator_node_type": "automationedge",
        "existing_connections": per_connection,
        "ae_copilot_url": best_copilot_url,
        "guidance": guidance,
    }


# ---------------------------------------------------------------------------
# Dispatch — runner-tool analogue of tool_layer.dispatch
# ---------------------------------------------------------------------------


RUNNER_TOOL_NAMES = {"test_node", "get_automationedge_handoff_info"}
"""Tool names the runner-tool dispatch layer handles. Add to this set
and register a branch in ``dispatch`` below when shipping more
stateful tools (``execute_draft`` and ``get_execution_logs`` in
01b.ii.b)."""


def dispatch(
    tool_name: str,
    *,
    db: Session,
    tenant_id: str,
    draft: WorkflowDraft,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Route a runner-tool call by name. Raises ``KeyError`` for
    unknown names so the caller can distinguish "not a runner tool"
    from "runner tool failed" — the agent falls back to the pure tool
    layer for anything not in ``RUNNER_TOOL_NAMES``."""
    if tool_name == "test_node":
        return test_node_against_draft(
            db, tenant_id=tenant_id, draft=draft, args=args,
        )
    if tool_name == "get_automationedge_handoff_info":
        return get_automationedge_handoff_info(
            db, tenant_id=tenant_id, draft=draft, args=args,
        )
    raise KeyError(tool_name)
