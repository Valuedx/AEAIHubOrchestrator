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
# execute_draft — trial-run the draft graph end-to-end
# ---------------------------------------------------------------------------
#
# Flow
# ----
#
# 1. Validate the draft's graph via ``tool_layer.validate_graph``. If
#    there are hard errors, bail with a useful message — no point
#    wasting an engine run on a known-broken graph.
# 2. Materialise a throwaway ``WorkflowDefinition`` with
#    ``is_ephemeral=True``, a name prefix that makes it obvious in
#    the DB (``__copilot_draft_<id>_<ts>__``), ``is_active=False``
#    (defensive — the scheduler filter also excludes it).
# 3. Create a ``WorkflowInstance`` pointing at the ephemeral WD.
# 4. Run ``execute_graph`` in a background thread with a caller-
#    supplied timeout, following the same pattern the live
#    ``POST /api/v1/workflows/{id}/execute?sync=true`` endpoint uses.
# 5. On success: return the instance id, terminal status, and the
#    non-internal keys from ``context_json`` as ``output``.
#    On timeout: return status=``timeout`` + a hint that the run may
#    still be completing — the agent can follow up with
#    ``get_execution_logs``.
#    On engine exception: return status=``failed`` with the error
#    message.
#
# The ephemeral WorkflowDefinition and its instance + execution_logs
# stay around after the call so ``get_execution_logs`` works. Cleanup
# is operator-scheduled via ``cleanup_ephemeral_workflows`` (a Beat
# task is a follow-up).


def execute_draft_sync(
    db: Session,
    *,
    tenant_id: str,
    draft: WorkflowDraft,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Trial-run the draft's graph via the engine. Blocks up to
    ``args.timeout_seconds`` (default 30) before returning a
    timeout result.

    Args
    ----
    ``args`` shape::

        {
          "payload": {...},             # trigger_payload; default {}
          "deterministic_mode": bool,   # default False
          "timeout_seconds": int,       # default 30, max 300
        }

    Result shapes
    -------------

    Success::

        {"instance_id": "...", "status": "completed"|"failed"|...,
         "elapsed_ms": N, "output": {...}, "started_at": "...",
         "completed_at": "..."}

    Timeout::

        {"instance_id": "...", "status": "timeout", "elapsed_ms": N,
         "hint": "..."}

    Pre-run validation failure::

        {"error": "Draft validation failed: [...]"}
    """
    import time as _time
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FuturesTimeout

    from sqlalchemy.exc import SQLAlchemyError

    from app.copilot import tool_layer
    from app.database import SessionLocal, set_tenant_context
    from app.models.workflow import WorkflowDefinition, WorkflowInstance

    payload = args.get("payload") or {}
    if not isinstance(payload, dict):
        return {"error": "execute_draft 'payload' must be an object"}

    deterministic = bool(args.get("deterministic_mode", False))
    raw_timeout = args.get("timeout_seconds", 30)
    try:
        timeout = int(raw_timeout)
    except (TypeError, ValueError):
        return {"error": "execute_draft 'timeout_seconds' must be an integer"}
    # Cap at 5 minutes — agent turns shouldn't block longer than that,
    # and runaway runs should keep going in the background (poll via
    # get_execution_logs).
    timeout = max(1, min(timeout, 300))

    validation = tool_layer.validate_graph(draft.graph_json or {})
    if validation.get("errors"):
        return {
            "error": (
                "Draft validation failed before execution; fix the "
                f"errors and retry: {validation['errors']}"
            ),
            "validation": validation,
        }

    graph = draft.graph_json or {"nodes": [], "edges": []}
    short_id = str(draft.id).split("-", 1)[0]
    temp_name = f"__copilot_draft_{short_id}_{int(_time.time())}__"

    temp_wf = WorkflowDefinition(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=temp_name,
        description=f"Ephemeral copilot trial run for draft {draft.id}",
        graph_json=graph,
        version=1,
        is_ephemeral=True,
        is_active=False,  # defensive; scheduler also filters on is_ephemeral
    )
    db.add(temp_wf)
    db.flush()  # allocate temp_wf.id so the instance can FK to it

    instance = WorkflowInstance(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        workflow_def_id=temp_wf.id,
        trigger_payload=payload,
        status="queued",
        definition_version_at_start=1,
    )
    db.add(instance)
    # Commit before running — the engine spawns its own SessionLocal
    # in the worker thread and needs to see these rows.
    db.commit()

    instance_id = str(instance.id)
    started_mono = _time.monotonic()

    def _run() -> dict[str, Any]:
        from app.engine.dag_runner import execute_graph

        session = SessionLocal()
        try:
            set_tenant_context(session, tenant_id)
            execute_graph(session, instance_id, deterministic)
            inst = (
                session.query(WorkflowInstance)
                .filter_by(id=instance.id)
                .first()
            )
            if inst is None:
                return {"status": "missing", "context_json": {}}
            return {
                "status": inst.status,
                "context_json": dict(inst.context_json or {}),
                "started_at": inst.started_at.isoformat() if inst.started_at else None,
                "completed_at": inst.completed_at.isoformat() if inst.completed_at else None,
            }
        finally:
            session.close()

    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_run)
            try:
                run_result = future.result(timeout=timeout)
            except FuturesTimeout:
                # The engine keeps running in the background thread.
                # We don't cancel — there's no safe way in Python — but
                # we surface the timeout to the agent so it can poll.
                elapsed_ms = int((_time.monotonic() - started_mono) * 1000)
                return {
                    "instance_id": instance_id,
                    "status": "timeout",
                    "elapsed_ms": elapsed_ms,
                    "hint": (
                        f"Execution exceeded timeout_seconds={timeout}. "
                        "The run may still complete in the background. "
                        f"Call get_execution_logs with instance_id='{instance_id}' "
                        "to check progress."
                    ),
                }
    except SQLAlchemyError as exc:
        elapsed_ms = int((_time.monotonic() - started_mono) * 1000)
        logger.exception("execute_draft DB error on instance %s", instance_id)
        return {
            "instance_id": instance_id,
            "status": "failed",
            "elapsed_ms": elapsed_ms,
            "error": f"Database error during run: {exc}",
        }
    except Exception as exc:
        elapsed_ms = int((_time.monotonic() - started_mono) * 1000)
        logger.exception("execute_draft run failed on instance %s", instance_id)
        return {
            "instance_id": instance_id,
            "status": "failed",
            "elapsed_ms": elapsed_ms,
            "error": str(exc),
        }

    elapsed_ms = int((_time.monotonic() - started_mono) * 1000)
    # Strip internal _-prefixed keys from context before returning to
    # the agent — mirrors the sync-execute endpoint's behaviour.
    raw_ctx = run_result.get("context_json") or {}
    output = {k: v for k, v in raw_ctx.items() if not str(k).startswith("_")}

    return {
        "instance_id": instance_id,
        "status": run_result.get("status", "unknown"),
        "elapsed_ms": elapsed_ms,
        "output": output,
        "started_at": run_result.get("started_at"),
        "completed_at": run_result.get("completed_at"),
    }


# ---------------------------------------------------------------------------
# get_execution_logs — read back the logs from an execute_draft run
# ---------------------------------------------------------------------------


def get_execution_logs(
    db: Session,
    *,
    tenant_id: str,
    draft: WorkflowDraft,  # noqa: ARG001 — part of the runner-tool contract
    args: dict[str, Any],
) -> dict[str, Any]:
    """Return the per-node execution log for a ``WorkflowInstance`` the
    copilot created via ``execute_draft``.

    Only instances whose parent ``WorkflowDefinition`` is marked
    ``is_ephemeral=True`` are accessible to the agent — otherwise any
    workflow run the tenant ever made would be readable by the LLM
    via function-calling, which is more surface area than we want to
    expose. Production-log inspection has its own dedicated endpoints.

    Args
    ----
    ``args`` shape::

        {
          "instance_id": "...",        # required
          "node_id": "...",            # optional — filter to one node
        }

    Result shape
    ------------

    ::

        {
          "instance_id": "...",
          "status": "completed" | "failed" | "running" | ...,
          "log_count": N,
          "logs": [
            {"node_id", "node_type", "status", "output_json",
             "error", "started_at", "completed_at"},
            ...
          ]
        }
    """
    from app.models.workflow import ExecutionLog, WorkflowDefinition, WorkflowInstance

    instance_id_str = args.get("instance_id")
    if not instance_id_str:
        return {"error": "get_execution_logs requires 'instance_id'"}

    try:
        instance_uuid = uuid.UUID(str(instance_id_str))
    except ValueError:
        return {"error": f"Invalid instance_id: {instance_id_str!r}"}

    instance = (
        db.query(WorkflowInstance)
        .filter_by(id=instance_uuid, tenant_id=tenant_id)
        .first()
    )
    if instance is None:
        return {"error": f"Instance '{instance_id_str}' not found"}

    wf_def = (
        db.query(WorkflowDefinition)
        .filter_by(id=instance.workflow_def_id, tenant_id=tenant_id)
        .first()
    )
    if wf_def is None or not wf_def.is_ephemeral:
        # Intentionally vague — don't tell the agent "that instance
        # belongs to a different workflow" because that's a tenant
        # info leak if the id was probed. Just say not-copilot.
        return {
            "error": (
                f"Instance '{instance_id_str}' is not a copilot-initiated run. "
                "get_execution_logs only returns logs for drafts the "
                "copilot executed via execute_draft."
            ),
        }

    query = db.query(ExecutionLog).filter_by(instance_id=instance_uuid)
    node_id_filter = args.get("node_id")
    if node_id_filter:
        query = query.filter_by(node_id=node_id_filter)
    query = query.order_by(ExecutionLog.started_at)

    logs = query.all()
    return {
        "instance_id": instance_id_str,
        "status": instance.status,
        "log_count": len(logs),
        "logs": [
            {
                "node_id": log.node_id,
                "node_type": log.node_type,
                "status": log.status,
                # output_json and error are the things the agent most
                # wants for debugging. Omit input_json to keep payload
                # small — the agent already knows what it sent.
                "output_json": log.output_json,
                "error": log.error,
                "started_at": log.started_at.isoformat() if log.started_at else None,
                "completed_at": log.completed_at.isoformat() if log.completed_at else None,
            }
            for log in logs
        ],
    }


# ---------------------------------------------------------------------------
# Cleanup utility — operator-scheduled reaper for ephemeral runs
# ---------------------------------------------------------------------------


def cleanup_ephemeral_workflows(
    db: Session,
    *,
    older_than_seconds: int = 7 * 24 * 3600,
) -> int:
    """Delete ephemeral ``WorkflowDefinition`` rows older than the
    given age. Cascading FK deletes take ``WorkflowInstance`` and
    ``ExecutionLog`` with them.

    Returns the number of rows deleted.

    Intended to be called from a Beat task or a manual admin command;
    no scheduling is wired up in 01b.ii.b. For now the operator docs
    point at a one-liner like::

        from app.database import SessionLocal, set_tenant_context
        from app.copilot.runner_tools import cleanup_ephemeral_workflows
        # NOTE: caller must have BYPASSRLS role, or iterate per tenant.
    """
    from datetime import datetime, timedelta, timezone

    from app.models.workflow import WorkflowDefinition

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
    doomed = (
        db.query(WorkflowDefinition)
        .filter(
            WorkflowDefinition.is_ephemeral.is_(True),
            WorkflowDefinition.created_at < cutoff,
        )
        .all()
    )
    count = len(doomed)
    for row in doomed:
        db.delete(row)
    db.commit()
    return count


# ---------------------------------------------------------------------------
# Dispatch — runner-tool analogue of tool_layer.dispatch
# ---------------------------------------------------------------------------


RUNNER_TOOL_NAMES = {
    "test_node",
    "get_automationedge_handoff_info",
    "execute_draft",
    "get_execution_logs",
}
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
    if tool_name == "execute_draft":
        return execute_draft_sync(
            db, tenant_id=tenant_id, draft=draft, args=args,
        )
    if tool_name == "get_execution_logs":
        return get_execution_logs(
            db, tenant_id=tenant_id, draft=draft, args=args,
        )
    raise KeyError(tool_name)
