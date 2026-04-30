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
import re
import time
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.copilot.tool_layer import get_node_schema  # re-exported so tests can patch runner_tools.get_node_schema
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

    result: dict[str, Any] = {
        "instance_id": instance_id,
        "status": run_result.get("status", "unknown"),
        "elapsed_ms": elapsed_ms,
        "output": output,
        "started_at": run_result.get("started_at"),
        "completed_at": run_result.get("completed_at"),
    }

    # SMART-01 — auto-save successful runs as regression scenarios
    # so a regression gate has something to run at promote time.
    # Deduped by a stable hash of the payload so repeated runs of
    # the same input don't litter the table. No expected_output is
    # locked in — the auto-saved scenario just verifies "this
    # payload still runs without crashing", which is the minimum
    # viable regression. A user who wants tighter assertions can
    # call save_test_scenario by hand with expected_output_contains.
    try:
        if result["status"] == "completed":
            from app.engine.tenant_policy_resolver import get_effective_policy

            policy = get_effective_policy(tenant_id)
            if policy.smart_01_scenario_memory_enabled:
                _auto_save_scenario_from_run(
                    db,
                    tenant_id=tenant_id,
                    draft=draft,
                    payload=payload,
                )
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.info(
            "SMART-01 auto-save skipped for draft=%s (tenant=%s): %s",
            draft.id, tenant_id, exc,
        )

    return result


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
    "search_docs",
    "get_node_examples",
    # SMART-04
    "check_draft",
    # SMART-06
    "discover_mcp_tools",
    # SMART-02
    "recall_patterns",
    # COPILOT-03.a
    "save_test_scenario",
    "run_scenario",
    "list_scenarios",
    # COPILOT-03.b
    "run_debug_scenario",
    "get_node_error",
    # COPILOT-03.c
    "suggest_fix",
    # COPILOT-V2 — debugging power tools
    "diff_drafts",
    "replay_node_with_overrides",
    "evaluate_run",
    "suggest_issue_filing",
    # CTX-MGMT.A — overflow artifact inspection
    "inspect_node_artifact",
}


# ---------------------------------------------------------------------------
# discover_mcp_tools — SMART-06
# ---------------------------------------------------------------------------


def discover_mcp_tools(
    db: Session,  # noqa: ARG001 — part of runner-tool contract
    *,
    tenant_id: str,
    draft: WorkflowDraft,  # noqa: ARG001 — current draft not needed today
    args: dict[str, Any],
) -> dict[str, Any]:
    """List the tools available on the tenant's MCP server(s) so the
    agent can surface relevant ones proactively during drafting.

    Returns
    -------

    ::

        {
          "discovery_enabled": bool,
          "server_label": str | null,       # the server that was queried
          "tools": [
            {"name", "title", "description", "category",
             "safety_tier", "tags"},
            ...
          ]
        }

    ``discovery_enabled: false`` means the tenant has opted out via
    ``tenant_policies.smart_06_mcp_discovery_enabled``; ``tools`` is
    ``[]`` in that case. ``server_label`` is ``None`` when the
    tenant has no configured MCP server (the underlying resolver
    still returns the env-fallback server, but the agent should
    frame its narration accordingly).

    Caching: ``engine.mcp_client.list_tools`` already memoises per
    ``(tenant_id, pool_key)`` with a 5-minute TTL, so every call
    across turns within a session hits warm cache. No further
    layering needed here.
    """
    from app.engine.tenant_policy_resolver import get_effective_policy

    policy = get_effective_policy(tenant_id)
    if not policy.smart_06_mcp_discovery_enabled:
        return {
            "discovery_enabled": False,
            "server_label": None,
            "tools": [],
        }

    server_label = args.get("server_label") or None
    if server_label is not None and not isinstance(server_label, str):
        return {"error": "discover_mcp_tools 'server_label' must be a string"}

    from app.engine.mcp_client import list_tools

    try:
        raw_tools = list_tools(tenant_id=tenant_id, server_label=server_label)
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning(
            "SMART-06 discover_mcp_tools: list_tools failed for tenant=%r "
            "label=%r: %s",
            tenant_id, server_label, exc,
        )
        return {
            "discovery_enabled": True,
            "server_label": server_label,
            "tools": [],
            "error": f"list_tools failed: {exc}",
        }

    # Normalise shape so the agent + UI don't have to guess at
    # optional fields on the MCP side. Everything except name is
    # best-effort; missing → empty string / None.
    tools = [
        {
            "name": t.get("name", ""),
            "title": t.get("title") or t.get("name", ""),
            "description": t.get("description", ""),
            "category": t.get("category", ""),
            "safety_tier": t.get("safety_tier", ""),
            "tags": list(t.get("tags") or []),
        }
        for t in raw_tools
    ]

    return {
        "discovery_enabled": True,
        "server_label": server_label,
        "tools": tools,
    }


# ---------------------------------------------------------------------------
# check_draft — SMART-04 wrapper around validate_graph + lints
# ---------------------------------------------------------------------------


def check_draft(
    db: Session,
    *,
    tenant_id: str,
    draft: WorkflowDraft,
    args: dict[str, Any],  # noqa: ARG001 — tool contract; no args today
) -> dict[str, Any]:
    """Return ``{errors, warnings, lints}`` for the current draft.

    Supersedes ``validate_graph`` for agent use — the pure
    ``tool_layer.validate_graph`` still exists for API-layer callers
    (the draft's auto-validation on CRUD) but the agent's system
    prompt directs it here because this version also surfaces the
    SMART-04 structure lints (no trigger, disconnected node, orphan
    edge, missing credential).

    When the tenant has ``smart_04_lints_enabled=False``, the lint
    step is skipped and ``lints`` is an empty list. Schema-level
    errors and warnings still come through — disabling lints doesn't
    disable schema validation.
    """
    from app.copilot import lints as lints_mod
    from app.copilot import tool_layer
    from app.engine.tenant_policy_resolver import get_effective_policy

    graph = draft.graph_json or {"nodes": [], "edges": []}
    base = tool_layer.validate_graph(graph)

    policy = get_effective_policy(tenant_id)
    if not policy.smart_04_lints_enabled:
        return {
            "errors": base.get("errors", []),
            "warnings": base.get("warnings", []),
            "lints": [],
            "lints_enabled": False,
        }

    try:
        findings = lints_mod.run_lints(graph, tenant_id=tenant_id, db=db)
    except Exception as exc:  # noqa: BLE001 — defensive
        # Lint failure must not poison the agent turn. Log and
        # return validation-only.
        logger.warning(
            "SMART-04 check_draft: run_lints failed for tenant=%r: %s",
            tenant_id, exc,
        )
        return {
            "errors": base.get("errors", []),
            "warnings": base.get("warnings", []),
            "lints": [],
            "lints_enabled": True,
            "lint_runtime_error": str(exc),
        }

    return {
        "errors": base.get("errors", []),
        "warnings": base.get("warnings", []),
        "lints": [lint.to_dict() for lint in findings],
        "lints_enabled": True,
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
    if tool_name == "search_docs":
        from app.copilot import docs_index
        from app.engine.tenant_policy_resolver import get_effective_policy

        query = args.get("query") or ""
        top_k = args.get("top_k") or 5
        # SMART-05 — opt-in vector search; resolver falls back to env
        # default when the tenant row has no explicit value.
        policy = get_effective_policy(tenant_id)
        return docs_index.search_docs(
            query, top_k=top_k, use_vector=policy.smart_05_vector_docs_enabled,
        )
    if tool_name == "get_node_examples":
        from app.copilot import docs_index
        from app.engine.tenant_policy_resolver import get_effective_policy

        node_type = args.get("node_type") or ""
        if not node_type:
            return {"error": "get_node_examples requires 'node_type'"}
        policy = get_effective_policy(tenant_id)
        return docs_index.get_node_examples(
            node_type, use_vector=policy.smart_05_vector_docs_enabled,
        )
    if tool_name == "check_draft":
        return check_draft(
            db, tenant_id=tenant_id, draft=draft, args=args,
        )
    if tool_name == "discover_mcp_tools":
        return discover_mcp_tools(
            db, tenant_id=tenant_id, draft=draft, args=args,
        )
    if tool_name == "recall_patterns":
        from app.copilot import pattern_library

        query = args.get("query") or args.get("nl_intent") or ""
        top_k = args.get("top_k") or pattern_library.SMART_02_RECALL_DEFAULT_TOP_K
        if not isinstance(query, str):
            return {"error": "recall_patterns 'query' must be a string"}
        return pattern_library.recall_patterns(
            db, tenant_id=tenant_id, query=query, top_k=top_k,
        )
    if tool_name == "save_test_scenario":
        return save_test_scenario(
            db, tenant_id=tenant_id, draft=draft, args=args,
        )
    if tool_name == "run_scenario":
        return run_scenario(
            db, tenant_id=tenant_id, draft=draft, args=args,
        )
    if tool_name == "list_scenarios":
        return list_scenarios(
            db, tenant_id=tenant_id, draft=draft, args=args,
        )
    if tool_name == "run_debug_scenario":
        return run_debug_scenario(
            db, tenant_id=tenant_id, draft=draft, args=args,
        )
    if tool_name == "get_node_error":
        return get_node_error(
            db, tenant_id=tenant_id, draft=draft, args=args,
        )
    if tool_name == "suggest_fix":
        return suggest_fix(
            db, tenant_id=tenant_id, draft=draft, args=args,
        )
    # COPILOT-V2 — debugging power tools.
    if tool_name == "diff_drafts":
        return diff_drafts(
            db, tenant_id=tenant_id, draft=draft, args=args,
        )
    if tool_name == "replay_node_with_overrides":
        return replay_node_with_overrides(
            db, tenant_id=tenant_id, draft=draft, args=args,
        )
    if tool_name == "evaluate_run":
        return evaluate_run(
            db, tenant_id=tenant_id, draft=draft, args=args,
        )
    if tool_name == "suggest_issue_filing":
        return suggest_issue_filing(
            db, tenant_id=tenant_id, draft=draft, args=args,
        )
    if tool_name == "inspect_node_artifact":
        return inspect_node_artifact(
            db, tenant_id=tenant_id, draft=draft, args=args,
        )
    raise KeyError(tool_name)


# ---------------------------------------------------------------------------
# COPILOT-03.a — test scenario persistence + replay
# ---------------------------------------------------------------------------


# Cap per draft so a runaway agent can't fill the table with hundreds
# of near-duplicate scenarios in a single chat. 50 is well above what
# a real workflow needs (you'd rarely hand-craft more than 10–20) but
# low enough to make a loop bug obvious in tests.
MAX_SCENARIOS_PER_DRAFT = 50


def save_test_scenario(
    db: Session,
    *,
    tenant_id: str,
    draft: WorkflowDraft,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Persist a named regression scenario against the current draft.

    Args
    ----
    ::

        {
          "name": "empty slack message",         # required, unique per draft
          "payload": {...},                      # required; trigger payload
          "expected_output_contains": {...}      # optional; partial-match
                                                  # assertion applied to output
        }

    Result
    ------

    Success::

        {"scenario_id": "...", "name": "...", "created_at": "..."}

    Error (validation / duplicate / cap hit)::

        {"error": "..."}
    """
    from app.models.copilot import CopilotTestScenario

    name = args.get("name")
    if not isinstance(name, str) or not name.strip():
        return {"error": "save_test_scenario requires a non-empty 'name'"}
    name = name.strip()
    if len(name) > 128:
        return {"error": "save_test_scenario 'name' must be <= 128 chars"}

    payload = args.get("payload")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return {"error": "save_test_scenario 'payload' must be an object"}

    expected = args.get("expected_output_contains")
    if expected is not None and not isinstance(expected, dict):
        return {
            "error": "save_test_scenario 'expected_output_contains' must be an object",
        }

    # COPILOT-V2 — optional predicate-based assertions. List of
    # {type, args} entries evaluated by app.copilot.predicates after
    # run_scenario produces an output. Shape-validated up-front so the
    # user sees a typo immediately, not on the first run.
    expected_predicates = args.get("expected_output_predicates")
    from app.copilot import predicates as _pred_mod
    pred_err = _pred_mod.validate_predicates(expected_predicates)
    if pred_err:
        return {"error": f"save_test_scenario: {pred_err}"}

    # Unique-per-draft check. The DB has a partial unique index
    # (0027 migration) but we short-circuit with a friendly error
    # instead of surfacing IntegrityError.
    existing = (
        db.query(CopilotTestScenario)
        .filter_by(tenant_id=tenant_id, draft_id=draft.id, name=name)
        .first()
    )
    if existing is not None:
        return {
            "error": (
                f"A scenario named '{name}' already exists on this draft. "
                "Pick a different name or delete the existing one first."
            ),
        }

    scenario_count = (
        db.query(CopilotTestScenario)
        .filter_by(tenant_id=tenant_id, draft_id=draft.id)
        .count()
    )
    if scenario_count >= MAX_SCENARIOS_PER_DRAFT:
        return {
            "error": (
                f"This draft already has {scenario_count} scenarios "
                f"(cap {MAX_SCENARIOS_PER_DRAFT}). Delete some before "
                "saving more."
            ),
        }

    scenario = CopilotTestScenario(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        draft_id=draft.id,
        workflow_id=None,
        name=name,
        payload_json=payload,
        pins_json={},
        expected_output_contains_json=expected,
        expected_predicates_json=expected_predicates,
    )
    db.add(scenario)
    db.commit()

    return {
        "scenario_id": str(scenario.id),
        "name": scenario.name,
        "created_at": scenario.created_at.isoformat()
        if scenario.created_at
        else None,
    }


def run_scenario(
    db: Session,
    *,
    tenant_id: str,
    draft: WorkflowDraft,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Execute a saved scenario against the draft and diff against its
    ``expected_output_contains``.

    Args
    ----
    ::

        {
          "scenario_id": "...",   # required
        }

    Result
    ------

    ``status`` is one of:

    * ``pass`` — expected match succeeded (or scenario had no
      expected block, in which case we return ``pass`` with the
      actual output for reference).
    * ``fail`` — scenario ran but output didn't match expected.
      ``mismatches`` lists ``[{path, expected, actual}]``.
    * ``stale`` — scenario can't run because the draft changed
      incompatibly. Returned today only as a hook for 03.e (we
      don't have pin-referenced-node detection in 03.a because
      pins aren't supported yet).
    * ``error`` — underlying execute_draft hit an unexpected
      failure; ``message`` carries the detail.

    ``execution`` is the raw ``execute_draft_sync`` result so the
    agent can surface logs / instance_id without a separate call.
    """
    from app.models.copilot import CopilotTestScenario

    scenario_id_str = args.get("scenario_id")
    if not scenario_id_str:
        return {"error": "run_scenario requires 'scenario_id'"}
    try:
        scenario_uuid = uuid.UUID(str(scenario_id_str))
    except ValueError:
        return {"error": f"Invalid scenario_id: {scenario_id_str!r}"}

    scenario = (
        db.query(CopilotTestScenario)
        .filter_by(
            id=scenario_uuid, tenant_id=tenant_id, draft_id=draft.id,
        )
        .first()
    )
    if scenario is None:
        return {
            "error": (
                f"Scenario '{scenario_id_str}' not found on this draft. "
                "Call list_scenarios to see what's saved."
            ),
        }

    exec_args = {"payload": scenario.payload_json or {}}
    execution = execute_draft_sync(
        db, tenant_id=tenant_id, draft=draft, args=exec_args,
    )

    # Surface execute-side errors as a scenario-level error so the
    # agent gets one clean result shape.
    if execution.get("error") and not execution.get("instance_id"):
        return {
            "scenario_id": str(scenario.id),
            "name": scenario.name,
            "status": "error",
            "message": execution["error"],
            "execution": execution,
        }

    exec_status = execution.get("status")
    actual_output = execution.get("output") or {}

    # Non-terminal or engine-failed run — scenario can't be said to
    # pass even without an expected block.
    if exec_status not in ("completed", "success", None):
        return {
            "scenario_id": str(scenario.id),
            "name": scenario.name,
            "status": "fail",
            "mismatches": [
                {
                    "path": "$.status",
                    "expected": "completed",
                    "actual": exec_status,
                },
            ],
            "execution": execution,
        }

    expected = scenario.expected_output_contains_json
    expected_predicates = getattr(scenario, "expected_predicates_json", None)

    # Run dict-shape assertion (legacy) AND predicate assertions
    # (COPILOT-V2). Both are optional. A scenario with neither just
    # records the actual output and returns "pass".
    mismatches: list[dict[str, Any]] = []
    if expected:
        mismatches = _diff_contains(expected, actual_output, path="$")

    from app.copilot import predicates as _pred_mod
    predicate_eval = _pred_mod.evaluate_predicates(actual_output, expected_predicates)

    contains_pass = not mismatches  # True if dict-match passed OR no expected
    predicate_pass = predicate_eval["overall"] in ("pass", "noop")
    has_expectations = bool(expected) or bool(expected_predicates)

    if not has_expectations:
        status = "pass"
    else:
        status = "pass" if (contains_pass and predicate_pass) else "fail"

    return {
        "scenario_id": str(scenario.id),
        "name": scenario.name,
        "status": status,
        "mismatches": mismatches,
        "predicate_results": predicate_eval["results"],
        "predicate_summary": {
            "pass_count": predicate_eval["pass_count"],
            "fail_count": predicate_eval["fail_count"],
            "overall": predicate_eval["overall"],
        },
        "actual_output": actual_output,
        "execution": execution,
    }


def list_scenarios(
    db: Session,
    *,
    tenant_id: str,
    draft: WorkflowDraft,
    args: dict[str, Any],  # noqa: ARG001 — no args today
) -> dict[str, Any]:
    """List saved scenarios on this draft — what the agent needs to
    pick one for ``run_scenario`` without guessing an id.
    """
    from app.models.copilot import CopilotTestScenario

    rows = (
        db.query(CopilotTestScenario)
        .filter_by(tenant_id=tenant_id, draft_id=draft.id)
        .order_by(CopilotTestScenario.created_at)
        .all()
    )
    return {
        "count": len(rows),
        "scenarios": [
            {
                "scenario_id": str(r.id),
                "name": r.name,
                "payload": r.payload_json or {},
                "has_expected": r.expected_output_contains_json is not None,
                "predicate_count": len(getattr(r, "expected_predicates_json", None) or []),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


def _diff_contains(
    expected: Any, actual: Any, *, path: str,
) -> list[dict[str, Any]]:
    """Recursive partial-match: every key/value in ``expected`` must
    appear in ``actual``. Lists match positionally — each expected
    item must equal the actual item at the same index (so expected
    can be a shorter-or-equal-length list).

    Returns a list of ``{path, expected, actual}`` mismatches. Empty
    means the actual subsumes the expected.
    """
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [{"path": path, "expected": expected, "actual": actual}]
        mismatches: list[dict[str, Any]] = []
        for key, sub_expected in expected.items():
            if key not in actual:
                mismatches.append({
                    "path": f"{path}.{key}",
                    "expected": sub_expected,
                    "actual": None,
                    "reason": "missing",
                })
                continue
            mismatches.extend(
                _diff_contains(
                    sub_expected, actual[key], path=f"{path}.{key}",
                )
            )
        return mismatches
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return [{"path": path, "expected": expected, "actual": actual}]
        if len(actual) < len(expected):
            return [{
                "path": path,
                "expected": expected,
                "actual": actual,
                "reason": "list shorter than expected",
            }]
        mismatches = []
        for i, sub_expected in enumerate(expected):
            mismatches.extend(
                _diff_contains(
                    sub_expected, actual[i], path=f"{path}[{i}]",
                )
            )
        return mismatches
    # Scalars — exact equality.
    if expected != actual:
        return [{"path": path, "expected": expected, "actual": actual}]
    return []


# ---------------------------------------------------------------------------
# COPILOT-03.b — ad-hoc debug scenarios + per-node error inspection
# ---------------------------------------------------------------------------


class _OverrideDraft:
    """Shim around ``WorkflowDraft`` so ``execute_draft_sync`` can run
    against a caller-modified copy of the graph without mutating the
    original draft row. Has only the attributes execute_draft_sync
    reads: ``id``, ``tenant_id``, ``graph_json``, ``version``.
    """

    __slots__ = ("id", "tenant_id", "graph_json", "version")

    def __init__(self, *, id, tenant_id, graph_json, version):
        self.id = id
        self.tenant_id = tenant_id
        self.graph_json = graph_json
        self.version = version


def run_debug_scenario(
    db: Session,
    *,
    tenant_id: str,
    draft: WorkflowDraft,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Ad-hoc debug run — execute_draft with optional upstream pins and
    per-node config overrides. Use when the user says "run it with
    pin node_3 to X" or "try it with the retries bumped to 5" — the
    overrides stay local to this one run and never touch the
    persisted draft graph.

    Args
    ----
    ::

        {
          "payload": {...},              # trigger payload, default {}
          "pins": {                       # optional; merged into each
            "node_3": {...},              # matching node's
            ...                           # data.pinnedOutput
          },
          "node_overrides": {             # optional; merged into each
            "node_5": {                   # matching node's data.config
              "retries": 5,
              ...
            },
          },
          "deterministic_mode": bool,   # passthrough to execute_draft
          "timeout_seconds": int,       # passthrough to execute_draft
        }

    Returns the ``execute_draft_sync`` result verbatim, plus an
    ``overrides_applied`` echo for the agent's narration.
    """
    import copy

    pins = args.get("pins") or {}
    if not isinstance(pins, dict):
        return {"error": "run_debug_scenario 'pins' must be an object keyed by node_id"}

    node_overrides = args.get("node_overrides") or {}
    if not isinstance(node_overrides, dict):
        return {
            "error": "run_debug_scenario 'node_overrides' must be an object keyed by node_id",
        }

    # Deep-copy so mutations don't leak into the live draft row.
    modified_graph = copy.deepcopy(draft.graph_json or {"nodes": [], "edges": []})
    graph_node_ids = {
        n.get("id") for n in (modified_graph.get("nodes") or []) if n.get("id")
    }

    unknown_pins = [nid for nid in pins if nid not in graph_node_ids]
    unknown_overrides = [nid for nid in node_overrides if nid not in graph_node_ids]
    if unknown_pins or unknown_overrides:
        return {
            "error": (
                "Unknown node ids in overrides: "
                f"pins={unknown_pins}, node_overrides={unknown_overrides}. "
                "Call check_draft or list_node_types to see valid ids."
            ),
        }

    applied_pins: list[str] = []
    applied_overrides: list[str] = []
    for node in modified_graph.get("nodes", []):
        nid = node.get("id")
        data = node.setdefault("data", {})
        if nid in pins:
            data["pinnedOutput"] = pins[nid]
            applied_pins.append(nid)
        if nid in node_overrides:
            override = node_overrides[nid]
            if not isinstance(override, dict):
                return {
                    "error": (
                        f"node_overrides for '{nid}' must be an object "
                        "of config-field updates"
                    ),
                }
            data.setdefault("config", {})
            data["config"] = {**data["config"], **override}
            applied_overrides.append(nid)

    shim = _OverrideDraft(
        id=draft.id,
        tenant_id=draft.tenant_id,
        graph_json=modified_graph,
        version=draft.version,
    )
    exec_args = {
        "payload": args.get("payload") or {},
    }
    if args.get("deterministic_mode") is not None:
        exec_args["deterministic_mode"] = bool(args["deterministic_mode"])
    if args.get("timeout_seconds") is not None:
        exec_args["timeout_seconds"] = args["timeout_seconds"]

    execution = execute_draft_sync(
        db, tenant_id=tenant_id, draft=shim, args=exec_args,
    )

    # Echo back so the agent's narration can mention "ran with these
    # overrides" without replaying the full args dict.
    execution["overrides_applied"] = {
        "pins": applied_pins,
        "node_overrides": applied_overrides,
    }
    return execution


def get_node_error(
    db: Session,
    *,
    tenant_id: str,
    draft: WorkflowDraft,  # noqa: ARG001 — part of runner-tool contract
    args: dict[str, Any],
) -> dict[str, Any]:
    """Narrow to one failed node in a prior ``execute_draft``/
    ``run_debug_scenario`` run. Returns the error message, the
    resolved config the node actually ran with (``input_json`` on
    the ``ExecutionLog`` row), and the partial output if any — the
    three things needed to propose a concrete fix.

    Safety: same as ``get_execution_logs`` — only ``is_ephemeral``
    instances are accessible. Arbitrary production instance_ids are
    rejected so the agent can't pull failure details from workflows
    the user never ran through the copilot.

    Args
    ----
    ::

        {
          "instance_id": "...",   # required
          "node_id": "...",       # required
        }

    Result shape
    ------------

    Failed node::

        {
          "instance_id": "...",
          "node_id": "...",
          "node_type": "llm_agent",
          "status": "failed",
          "error": "no auth configured",
          "resolved_config": {...},   # input_json — post-expression
                                        # resolution, what the handler saw
          "output_json": null,          # usually null on failure
          "started_at": "...",
          "completed_at": "...",
        }

    Node succeeded — agent should look at downstream node instead::

        {"status": "completed", "note": "Node succeeded — the failure "
         "is likely downstream.", ...}

    Node not found / not run::

        {"error": "Node 'node_9' has no execution log in instance X."}
    """
    from app.models.workflow import ExecutionLog, WorkflowDefinition, WorkflowInstance

    instance_id_str = args.get("instance_id")
    node_id = args.get("node_id")
    if not instance_id_str:
        return {"error": "get_node_error requires 'instance_id'"}
    if not node_id:
        return {"error": "get_node_error requires 'node_id'"}

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
        return {
            "error": (
                f"Instance '{instance_id_str}' is not a copilot-initiated run. "
                "get_node_error only inspects drafts the copilot executed."
            ),
        }

    log = (
        db.query(ExecutionLog)
        .filter_by(instance_id=instance_uuid, node_id=node_id)
        .order_by(ExecutionLog.started_at.desc())
        .first()
    )
    if log is None:
        return {
            "error": (
                f"Node '{node_id}' has no execution log in instance "
                f"'{instance_id_str}'. The node may not have been reached."
            ),
        }

    if log.status != "failed" and not log.error:
        return {
            "instance_id": instance_id_str,
            "node_id": node_id,
            "node_type": log.node_type,
            "status": log.status,
            "note": (
                "Node succeeded — the failure (if any) is likely on a "
                "downstream node. Call get_execution_logs for the full "
                "instance picture."
            ),
            "resolved_config": log.input_json,
            "output_json": log.output_json,
            "started_at": log.started_at.isoformat() if log.started_at else None,
            "completed_at": log.completed_at.isoformat() if log.completed_at else None,
        }

    return {
        "instance_id": instance_id_str,
        "node_id": node_id,
        "node_type": log.node_type,
        "status": log.status,
        "error": log.error,
        "resolved_config": log.input_json,
        "output_json": log.output_json,
        "started_at": log.started_at.isoformat() if log.started_at else None,
        "completed_at": log.completed_at.isoformat() if log.completed_at else None,
    }


# ---------------------------------------------------------------------------
# SMART-01 — auto-save scenarios from successful execute_draft runs
# ---------------------------------------------------------------------------


def _payload_hash(payload: dict[str, Any]) -> str:
    """Stable short hash of the trigger payload. Used as the scenario
    name suffix so the same payload run twice dedupes to one row
    instead of growing the table."""
    import hashlib
    import json as _json

    canonical = _json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]


def _auto_save_scenario_from_run(
    db: Session,
    *,
    tenant_id: str,
    draft: WorkflowDraft,
    payload: dict[str, Any],
) -> None:
    """Persist an auto-named scenario for a successful execute_draft
    run when SMART-01 scenario-memory is enabled. Idempotent — a
    scenario already present with the same payload hash is a no-op
    so repeated runs of the same input don't balloon the table.

    Errors are swallowed by the caller. This function is best-effort:
    a scenario save failure must never cost the user their successful
    execute_draft result.
    """
    from app.models.copilot import CopilotTestScenario

    name = f"auto-{_payload_hash(payload)}"

    existing = (
        db.query(CopilotTestScenario)
        .filter_by(tenant_id=tenant_id, draft_id=draft.id, name=name)
        .first()
    )
    if existing is not None:
        return

    # Respect the per-draft cap so auto-save can't blow past the
    # ceiling a manual save path enforces.
    count = (
        db.query(CopilotTestScenario)
        .filter_by(tenant_id=tenant_id, draft_id=draft.id)
        .count()
    )
    if count >= MAX_SCENARIOS_PER_DRAFT:
        logger.info(
            "SMART-01 auto-save skipped for draft=%s — cap %d reached",
            draft.id, MAX_SCENARIOS_PER_DRAFT,
        )
        return

    scenario = CopilotTestScenario(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        draft_id=draft.id,
        workflow_id=None,
        name=name,
        payload_json=payload,
        pins_json={},
        # Intentionally no expected_output_contains — an auto-saved
        # scenario just verifies "this payload still runs". If the
        # user wants tighter regression semantics they can call
        # save_test_scenario by hand with an assertion.
        expected_output_contains_json=None,
    )
    db.add(scenario)
    db.commit()


# ---------------------------------------------------------------------------
# COPILOT-03.c — suggest_fix LLM subcall
# ---------------------------------------------------------------------------
#
# Design contract
# ---------------
#
# * NEVER auto-applies — the agent has to round-trip the user before
#   calling ``update_node_config`` with the proposed patch. This is a
#   hard rule baked into the prompt and the tool's result envelope
#   (``applied: false`` is always returned).
# * The LLM's patch is filtered to keys that exist in the node's
#   ``config_schema.properties`` — anything else is dropped with a
#   ``dropped_keys`` list so the agent can explain "the model also
#   suggested X, but that's not a valid config field so I didn't
#   include it".
# * Per-draft cap (``MAX_SUGGEST_FIX_PER_DRAFT``) to prevent a runaway
#   auto-heal loop from racking up dozens of LLM calls. Enforced by
#   counting prior ``suggest_fix`` tool-turns tied to any session on
#   this draft. Beyond the cap, the agent must hand off to the user.
# * Uses the tenant's Anthropic credential via the ADMIN-03 resolver —
#   no per-call provider override in 03.c. OpenAI / Google wiring can
#   follow the existing provider-adapter pattern when 01b.iv remainder
#   lands.


MAX_SUGGEST_FIX_PER_DRAFT = 5


_SUGGEST_FIX_SYSTEM_PROMPT = (
    "You are a focused node-config-fix assistant embedded inside a "
    "workflow authoring copilot. You are given ONE failing node, the "
    "error it raised, the config it currently has, and the full "
    "config schema for its node type. Propose a minimal patch to the "
    "config that would plausibly fix the failure.\n\n"
    "Respond with strict JSON in this shape (no prose, no code "
    "fences, no preamble):\n"
    "{\n"
    "  \"patch\": {<field>: <new value>, ...},\n"
    "  \"rationale\": \"<one-sentence explanation>\",\n"
    "  \"confidence\": \"high\" | \"medium\" | \"low\"\n"
    "}\n\n"
    "Rules:\n"
    "- Only set fields that appear in config_schema.properties.\n"
    "- Prefer the smallest possible patch — don't rewrite unrelated "
    "fields.\n"
    "- If you genuinely don't know the fix, set patch to {} and "
    "explain in rationale.\n"
    "- Never invent API keys, secrets, or hostnames."
)


def _resolve_suggest_fix_provider(
    db: Session, *, tenant_id: str, draft: WorkflowDraft,
) -> tuple[str, str]:
    """Pick the provider + model for a ``suggest_fix`` subcall.

    Precedence, most-specific-first:

    1. The most-recent active ``CopilotSession`` bound to this draft.
       If the user is mid-chat on Vertex, suggest_fix uses Vertex
       too — no cross-provider billing surprise.
    2. The ``copilot_default_provider`` / default-model pair from
       settings (``anthropic`` / ``claude-sonnet-4-6`` today).

    Returns ``(provider, model)``. Providers supported here match the
    main agent runner's adapter registry — ``anthropic`` / ``google``
    / ``vertex``.
    """
    from app.config import settings
    from app.copilot.agent import DEFAULT_MODEL_BY_PROVIDER as DEFAULT_MODELS
    from app.models.copilot import CopilotSession

    session = (
        db.query(CopilotSession)
        .filter_by(
            tenant_id=tenant_id, draft_id=draft.id, status="active",
        )
        .order_by(CopilotSession.created_at.desc())
        .first()
    )
    if session is not None:
        provider = session.provider
        model = session.model or DEFAULT_MODELS.get(provider, "")
        if provider in {"anthropic", "google", "vertex"} and model:
            return provider, model

    # Process-level fallback. Default stays on Anthropic so operators
    # without Vertex creds keep working; tenants on Vertex sessions
    # automatically get Vertex via the path above.
    default_provider = getattr(
        settings, "copilot_default_provider", "anthropic",
    )
    default_model = DEFAULT_MODELS.get(
        default_provider,
        "claude-sonnet-4-6",
    )
    return default_provider, default_model


def _call_suggest_fix_llm(
    *,
    tenant_id: str,
    node_type: str,
    node_id: str,
    error: str,
    current_config: dict[str, Any],
    config_schema: dict[str, Any],
    provider: str = "anthropic",
    model: str | None = None,
) -> dict[str, Any]:
    """One-shot LLM call to propose a node-config patch. Dispatches
    by ``provider`` so the subcall uses the same engine as the
    parent session (Anthropic / Google / Vertex), which keeps
    billing attribution clean and lets Gemini-native tenants stay
    on Gemini for the suggest path.

    Split out as a module-level function so tests can mock via
    ``patch.object(runner_tools, "_call_suggest_fix_llm", ...)``.
    """
    import json as _json

    from app.copilot.agent import DEFAULT_MODEL_BY_PROVIDER as DEFAULT_MODELS
    from app.config import settings  # noqa: F401 — reserved for future provider-specific knobs

    resolved_model = model or DEFAULT_MODELS.get(provider) or "claude-sonnet-4-6"
    user_payload = _json.dumps({
        "node_id": node_id,
        "node_type": node_type,
        "error": error,
        "current_config": current_config,
        "config_schema": config_schema,
    }, default=str)

    if provider == "anthropic":
        return _call_suggest_fix_anthropic(
            tenant_id=tenant_id,
            model=resolved_model,
            user_payload=user_payload,
        )
    if provider in {"google", "vertex"}:
        return _call_suggest_fix_google(
            tenant_id=tenant_id,
            model=resolved_model,
            user_payload=user_payload,
            backend="vertex" if provider == "vertex" else "genai",
        )
    raise ValueError(
        f"suggest_fix: unsupported provider {provider!r}. "
        "Expected one of anthropic / google / vertex."
    )


def _call_suggest_fix_anthropic(
    *, tenant_id: str, model: str, user_payload: str,
) -> dict[str, Any]:
    from anthropic import Anthropic

    from app.engine.llm_credentials_resolver import get_anthropic_api_key

    client = Anthropic(api_key=get_anthropic_api_key(tenant_id))
    resp = client.messages.create(
        model=model,
        system=_SUGGEST_FIX_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_payload}],
        max_tokens=1024,
        temperature=0.1,
    )

    raw_text = ""
    for block in resp.content:
        as_dict = _block_to_dict_safe(block)
        if as_dict.get("type") == "text":
            raw_text += as_dict.get("text", "")

    return {
        "raw_text": raw_text.strip(),
        "usage": {
            "input_tokens": getattr(resp.usage, "input_tokens", 0),
            "output_tokens": getattr(resp.usage, "output_tokens", 0),
        },
    }


def _call_suggest_fix_google(
    *, tenant_id: str, model: str, user_payload: str, backend: str,
) -> dict[str, Any]:
    """Gemini 3.x-capable suggest_fix via the shared `google-genai`
    SDK. ``backend="vertex"`` routes through Vertex AI (ADC +
    per-tenant project via VERTEX-02); ``backend="genai"`` routes
    through Google AI Studio (api_key). Same response-normalisation
    shape as the Anthropic path so the parent caller doesn't branch.
    """
    from google.genai import types

    from app.engine.llm_providers import _google_client

    client = _google_client(backend, tenant_id=tenant_id)
    contents = [types.Content(
        role="user",
        parts=[types.Part.from_text(text=user_payload)],
    )]
    config = types.GenerateContentConfig(
        system_instruction=_SUGGEST_FIX_SYSTEM_PROMPT,
        temperature=0.1,
        max_output_tokens=1024,
    )

    resp = client.models.generate_content(
        model=model, contents=contents, config=config,
    )

    text_blocks: list[str] = []
    if resp.candidates and resp.candidates[0].content:
        for part in resp.candidates[0].content.parts:
            if getattr(part, "text", None):
                text_blocks.append(part.text)

    usage = getattr(resp, "usage_metadata", None)
    return {
        "raw_text": "".join(text_blocks).strip(),
        "usage": {
            "input_tokens": getattr(usage, "prompt_token_count", 0) if usage else 0,
            "output_tokens": getattr(usage, "candidates_token_count", 0) if usage else 0,
        },
    }


def _block_to_dict_safe(block: Any) -> dict[str, Any]:
    """Local copy of agent._block_to_dict to keep this module
    independent of agent.py (the import cycle would be pointless)."""
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump()
    # Last-resort: try to coax a dict out of __dict__.
    return {
        "type": getattr(block, "type", ""),
        "text": getattr(block, "text", ""),
    }


def _parse_suggest_fix_response(raw_text: str) -> dict[str, Any] | None:
    """Robustly parse the JSON-only response. Tolerates leading /
    trailing code fences or a single leading 'json' line — we don't
    want a single over-cautious LLM to burn the budget."""
    import json as _json

    text = raw_text.strip()
    if text.startswith("```"):
        # Strip leading fence (with optional language hint).
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[: -3]
        text = text.strip()
    if not text:
        return None
    try:
        parsed = _json.loads(text)
    except _json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def suggest_fix(
    db: Session,
    *,
    tenant_id: str,
    draft: WorkflowDraft,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Propose a config patch for one failing node — never auto-applies.

    Args
    ----
    ::

        {
          "node_id": "node_5",              # required; must exist on draft
          "error": "no auth configured",    # required
        }

    Result
    ------

    Proposal generated::

        {
          "node_id": "...",
          "node_type": "llm_agent",
          "proposed_patch": {"provider": "anthropic"},
          "rationale": "...",
          "confidence": "high" | "medium" | "low",
          "dropped_keys": [],     # keys the LLM proposed that aren't in the schema
          "applied": false,        # always false — agent must confirm
          "usage": {...},
        }

    Cap hit::

        {"error": "suggest_fix cap reached...", "prior_calls": N}

    Bad args / node not found / no schema::

        {"error": "..."}
    """
    from sqlalchemy import func

    from app.copilot.tool_layer import UnknownNodeTypeError
    from app.models.copilot import CopilotSession, CopilotTurn

    node_id = args.get("node_id")
    error = args.get("error")
    if not node_id:
        return {"error": "suggest_fix requires 'node_id'"}
    if not error or not isinstance(error, str):
        return {"error": "suggest_fix requires 'error' as a string"}

    graph = draft.graph_json or {}
    target = next(
        (n for n in (graph.get("nodes") or []) if n.get("id") == node_id),
        None,
    )
    if target is None:
        return {"error": f"Node '{node_id}' not in draft graph"}

    node_data = target.get("data") or {}
    current_config = dict(node_data.get("config") or {})
    # node_type is stored in a few ways across the schema history —
    # the agent's add_node writes it to data.nodeType; the validator
    # looks at data.label. Prefer explicit type fields in order.
    node_type = (
        node_data.get("nodeType")
        or node_data.get("type")
        or target.get("type")
    )
    if not node_type or node_type == "agenticNode":
        # agenticNode is the React Flow node KIND, not our registry
        # type. Fall back to the canonical label → registry lookup via
        # get_node_schema. If we genuinely can't figure out the type,
        # bail cleanly.
        label = node_data.get("label")
        if label:
            node_type = label
    if not node_type:
        return {
            "error": (
                f"Could not determine node_type for '{node_id}'. "
                "Node data has no 'nodeType' / 'type' / 'label' field "
                "to resolve against the registry."
            ),
        }

    try:
        schema_entry = get_node_schema(node_type)
    except UnknownNodeTypeError:
        return {
            "error": (
                f"Unknown node_type '{node_type}' on node '{node_id}'. "
                "Call list_node_types to find a valid type."
            ),
        }

    config_schema = schema_entry.get("config_schema") or {}

    # Cap gate — count prior suggest_fix tool turns on any session
    # tied to this draft. JSONB->>'name' works on Postgres; SQLite in
    # tests mocks this out entirely, so the path is never exercised
    # against the SQLite dialect.
    prior_calls = (
        db.query(func.count(CopilotTurn.id))
        .join(CopilotSession, CopilotTurn.session_id == CopilotSession.id)
        .filter(
            CopilotSession.draft_id == draft.id,
            CopilotTurn.role == "tool",
            CopilotTurn.content_json["name"].astext == "suggest_fix",
        )
        .scalar()
    ) or 0
    if prior_calls >= MAX_SUGGEST_FIX_PER_DRAFT:
        return {
            "error": (
                f"suggest_fix cap reached ({prior_calls}/"
                f"{MAX_SUGGEST_FIX_PER_DRAFT} per draft). Hand off to "
                "the user — too many fix suggestions in one draft "
                "usually means the underlying issue is structural."
            ),
            "prior_calls": prior_calls,
            "cap": MAX_SUGGEST_FIX_PER_DRAFT,
        }

    provider, model = _resolve_suggest_fix_provider(
        db, tenant_id=tenant_id, draft=draft,
    )

    try:
        llm_result = _call_suggest_fix_llm(
            tenant_id=tenant_id,
            node_type=node_type,
            node_id=node_id,
            error=error,
            current_config=current_config,
            config_schema=config_schema,
            provider=provider,
            model=model,
        )
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.info(
            "suggest_fix LLM call failed for node=%s (tenant=%s): %s",
            node_id, tenant_id, exc,
        )
        return {
            "error": f"LLM call for suggest_fix failed: {exc}",
        }

    parsed = _parse_suggest_fix_response(llm_result["raw_text"])
    if parsed is None:
        return {
            "error": (
                "Could not parse suggest_fix LLM response as JSON. "
                "Raw response: "
                f"{llm_result['raw_text'][:200]}"
            ),
        }

    raw_patch = parsed.get("patch")
    if not isinstance(raw_patch, dict):
        raw_patch = {}

    allowed_keys = set((config_schema.get("properties") or {}).keys())
    proposed_patch: dict[str, Any] = {}
    dropped_keys: list[str] = []
    for key, value in raw_patch.items():
        if allowed_keys and key not in allowed_keys:
            dropped_keys.append(key)
            continue
        proposed_patch[key] = value

    rationale = parsed.get("rationale")
    if not isinstance(rationale, str):
        rationale = ""
    confidence = parsed.get("confidence")
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"

    return {
        "node_id": node_id,
        "node_type": node_type,
        "proposed_patch": proposed_patch,
        "rationale": rationale,
        "confidence": confidence,
        "dropped_keys": dropped_keys,
        "applied": False,
        "usage": llm_result.get("usage") or {},
        "provider": provider,
        "model": model,
        "prior_calls": prior_calls,
        "cap": MAX_SUGGEST_FIX_PER_DRAFT,
    }


# ---------------------------------------------------------------------------
# COPILOT-V2 — diff_drafts
# ---------------------------------------------------------------------------


def diff_drafts(
    db: Session,
    *,
    tenant_id: str,
    draft: WorkflowDraft,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Diff the current draft against another draft OR the published
    workflow it forked from.

    Args
    ----
    ::

        {
          "against": "draft" | "base_workflow",   # default base_workflow
          "other_draft_id": "<uuid>",             # required when against=draft
        }

    Result
    ------
    The structured diff from ``diff_layer.diff_graphs`` plus
    ``left_label`` / ``right_label`` describing which side is which.
    """
    from app.copilot.diff_layer import diff_graphs
    from app.models.workflow import WorkflowDefinition

    against = (args.get("against") or "base_workflow").strip().lower()
    if against not in ("base_workflow", "draft"):
        return {"error": "diff_drafts 'against' must be 'base_workflow' or 'draft'"}

    left_graph = draft.graph_json or {"nodes": [], "edges": []}
    left_label = f"current draft (v{draft.version})"

    if against == "draft":
        other_id = args.get("other_draft_id")
        if not other_id:
            return {"error": "diff_drafts against='draft' requires 'other_draft_id'"}
        try:
            other_uuid = uuid.UUID(str(other_id))
        except ValueError:
            return {"error": f"diff_drafts: invalid other_draft_id {other_id!r}"}
        if other_uuid == draft.id:
            return {"error": "diff_drafts: other_draft_id is the current draft — nothing to diff"}
        other = (
            db.query(WorkflowDraft)
            .filter_by(id=other_uuid, tenant_id=tenant_id)
            .first()
        )
        if other is None:
            return {"error": f"diff_drafts: draft {other_id!r} not found for this tenant"}
        right_graph = other.graph_json or {"nodes": [], "edges": []}
        right_label = f"draft {str(other.id).split('-')[0]} (v{other.version})"
    else:
        if not draft.base_workflow_id:
            return {
                "error": (
                    "diff_drafts: this draft has no base_workflow_id "
                    "(it was created from scratch, not forked from a "
                    "published workflow). Pass against='draft' with "
                    "other_draft_id instead."
                ),
            }
        base = (
            db.query(WorkflowDefinition)
            .filter_by(id=draft.base_workflow_id, tenant_id=tenant_id)
            .first()
        )
        if base is None:
            return {
                "error": (
                    f"diff_drafts: base workflow {draft.base_workflow_id!r} "
                    "not found (may have been deleted since fork)."
                ),
            }
        right_graph = base.graph_json or {"nodes": [], "edges": []}
        right_label = f"base workflow “{base.name}” (v{base.version})"

    return diff_graphs(
        left_graph,
        right_graph,
        left_label=left_label,
        right_label=right_label,
    )


# ---------------------------------------------------------------------------
# COPILOT-V2 — replay_node_with_overrides
# ---------------------------------------------------------------------------


def replay_node_with_overrides(
    db: Session,
    *,
    tenant_id: str,
    draft: WorkflowDraft,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Re-run ONE node from a prior copilot-initiated instance with
    config overrides, using the captured upstream context as inputs.

    Use this for tight prompt-iteration loops: edit a Worker's
    systemPrompt, replay just the Worker against a captured run, see
    the new output in seconds instead of re-executing the whole graph.

    Args
    ----
    ::

        {
          "instance_id": "<uuid>",          # required — copilot-ephemeral instance
          "node_id": "<id>",                # required — node to replay
          "config_overrides": {...},        # optional — partial config merged
                                            #   into the node's data.config
          "deterministic_mode": false,      # optional — passthrough to handler
        }

    Returns
    -------
    ::

        {
          "instance_id": "<orig>",
          "node_id": "<id>",
          "node_type": "<label>",
          "elapsed_ms": N,
          "overrides_applied": {...},
          "output": <handler output>,
          "error": "<error string if handler raised>",
        }

    Same safety as ``get_execution_logs``: only ``is_ephemeral=True``
    instances are readable so the agent can't probe production.
    """
    from app.engine.exceptions import NodeSuspendedAsync
    from app.engine.node_handlers import dispatch_node
    from app.models.workflow import WorkflowDefinition, WorkflowInstance

    instance_id = args.get("instance_id")
    node_id = args.get("node_id")
    if not instance_id or not node_id:
        return {"error": "replay_node_with_overrides requires 'instance_id' and 'node_id'"}
    try:
        instance_uuid = uuid.UUID(str(instance_id))
    except ValueError:
        return {"error": f"replay_node_with_overrides: invalid instance_id {instance_id!r}"}

    overrides = args.get("config_overrides") or {}
    if not isinstance(overrides, dict):
        return {"error": "replay_node_with_overrides 'config_overrides' must be an object"}

    instance = (
        db.query(WorkflowInstance)
        .filter_by(id=instance_uuid, tenant_id=tenant_id)
        .first()
    )
    if instance is None:
        return {"error": f"Instance {instance_id!r} not found for this tenant"}

    wd = (
        db.query(WorkflowDefinition)
        .filter_by(id=instance.workflow_def_id, tenant_id=tenant_id)
        .first()
    )
    if wd is None or not wd.is_ephemeral:
        return {
            "error": (
                "replay_node_with_overrides only supports copilot-initiated "
                "(ephemeral) instances. Run execute_draft first to get a "
                "replay-able instance_id."
            ),
        }

    # Use the GRAPH from the instance (matches the captured run) but
    # source the node config from the CURRENT draft so override edits
    # made via update_node_config are picked up automatically. If the
    # node id only exists in the captured graph (someone deleted it on
    # the draft), fall back to the captured config.
    captured_graph = wd.graph_json or {"nodes": [], "edges": []}
    captured_node = next(
        (n for n in captured_graph.get("nodes") or [] if n.get("id") == node_id),
        None,
    )
    if captured_node is None:
        return {"error": f"Node {node_id!r} not in captured graph for instance {instance_id!r}"}

    draft_graph = draft.graph_json or {"nodes": [], "edges": []}
    draft_node = next(
        (n for n in draft_graph.get("nodes") or [] if n.get("id") == node_id),
        None,
    )
    base_node = draft_node or captured_node
    base_data = (base_node.get("data") or {}).copy()
    base_config = dict(base_data.get("config") or {})
    base_config.update(overrides)
    base_data["config"] = base_config

    # Build context from the captured run's context_json — that's
    # what the node would have seen at runtime, including upstream
    # outputs the node depends on.
    ctx_source = dict(instance.context_json or {})
    # Strip internal keys + the target node's own prior output so the
    # handler doesn't see its own answer as input.
    ctx_source.pop(node_id, None)
    context: dict[str, Any] = {
        k: v for k, v in ctx_source.items()
        if not k.startswith("_") and k not in {"orchestrator_user_reply"}
    }
    # Re-seed the engine internals the handler needs.
    context["_instance_id"] = str(instance_uuid)
    context["_current_node_id"] = node_id
    context["_workflow_def_id"] = str(wd.id)
    if "trigger" not in context:
        context["trigger"] = (instance.trigger_payload or {}) if hasattr(instance, "trigger_payload") else {}

    started = time.monotonic()
    try:
        output = dispatch_node(base_data, context, tenant_id, db=db)
    except NodeSuspendedAsync as sus:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            "instance_id": str(instance_uuid),
            "node_id": node_id,
            "node_type": base_data.get("label"),
            "elapsed_ms": elapsed_ms,
            "overrides_applied": overrides,
            "error": (
                f"Node suspended on external system '{sus.system}' "
                f"(external_job_id={sus.external_job_id})."
            ),
        }
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "copilot replay_node_with_overrides handler raised: node=%s err=%s",
            node_id, exc,
        )
        return {
            "instance_id": str(instance_uuid),
            "node_id": node_id,
            "node_type": base_data.get("label"),
            "elapsed_ms": elapsed_ms,
            "overrides_applied": overrides,
            "error": str(exc),
        }

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return {
        "instance_id": str(instance_uuid),
        "node_id": node_id,
        "node_type": base_data.get("label"),
        "elapsed_ms": elapsed_ms,
        "overrides_applied": overrides,
        "output": output,
    }


# ---------------------------------------------------------------------------
# COPILOT-V2 — evaluate_run (LLM-as-judge)
# ---------------------------------------------------------------------------


_EVALUATE_RUN_SYSTEM_PROMPT = (
    "You are an evaluation judge for an agentic workflow output. Given:\n"
    "  - the workflow's final user-facing reply\n"
    "  - a free-form rubric (one or more criteria, NL or numbered list)\n"
    "  - optional metadata about the run (intent, tool calls)\n\n"
    "Return a STRICT JSON object with this shape — no prose, no markdown:\n"
    "{\n"
    "  \"verdicts\": [\n"
    "    {\"criterion\": \"<copy of the criterion>\", \"status\": \"pass|fail|partial\", \"why\": \"<one sentence>\"}\n"
    "  ],\n"
    "  \"overall\": \"pass|partial|fail\",\n"
    "  \"summary\": \"<one sentence summary of the verdict>\"\n"
    "}\n\n"
    "Rules:\n"
    "  - Treat each rubric criterion independently.\n"
    "  - Be strict on 'pass' — if the reply only partially satisfies a "
    "criterion, use 'partial'.\n"
    "  - 'overall' is 'pass' iff every verdict is 'pass'; 'fail' iff any "
    "is 'fail'; otherwise 'partial'.\n"
    "  - Do NOT critique style. Judge ONLY the rubric.\n"
    "  - If the rubric is empty / non-actionable, return overall='partial' "
    "with a single verdict explaining why."
)


def evaluate_run(
    db: Session,
    *,
    tenant_id: str,
    draft: WorkflowDraft,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Use an LLM to judge a prior run's output against a free-form
    rubric. Designed for behavior-quality checks the
    ``expected_output_contains`` / predicate matchers can't express
    cleanly ("the reply leads with the answer, not filler").

    Args
    ----
    ::

        {
          "instance_id": "<uuid>",       # required — copilot-ephemeral
          "rubric": "<NL string>",       # required — one or more criteria
        }

    Returns
    -------
    ::

        {
          "instance_id": "...",
          "rubric": "...",
          "verdicts": [{criterion, status, why}, ...],
          "overall": "pass|partial|fail",
          "summary": "...",
          "model_used": "...",
          "usage": {input_tokens, output_tokens},
        }

    Same ephemeral-only safety as ``get_execution_logs``.
    """
    import json as _json
    import re as _re
    from app.models.workflow import WorkflowDefinition, WorkflowInstance

    instance_id = args.get("instance_id")
    rubric = args.get("rubric")
    if not instance_id:
        return {"error": "evaluate_run requires 'instance_id'"}
    if not isinstance(rubric, str) or not rubric.strip():
        return {"error": "evaluate_run requires a non-empty 'rubric' string"}

    try:
        instance_uuid = uuid.UUID(str(instance_id))
    except ValueError:
        return {"error": f"evaluate_run: invalid instance_id {instance_id!r}"}

    instance = (
        db.query(WorkflowInstance)
        .filter_by(id=instance_uuid, tenant_id=tenant_id)
        .first()
    )
    if instance is None:
        return {"error": f"Instance {instance_id!r} not found for this tenant"}

    wd = (
        db.query(WorkflowDefinition)
        .filter_by(id=instance.workflow_def_id, tenant_id=tenant_id)
        .first()
    )
    if wd is None or not wd.is_ephemeral:
        return {
            "error": (
                "evaluate_run only supports copilot-initiated (ephemeral) "
                "instances."
            ),
        }

    ctx = instance.context_json or {}
    user_reply = ctx.get("orchestrator_user_reply") or ""
    if not user_reply:
        # Best-effort fallback — sometimes the reply lives on a
        # specific bridge node's text.
        for v in ctx.values():
            if isinstance(v, dict) and v.get("source") == "messageExpression":
                user_reply = v.get("text", "")
                if user_reply:
                    break
    intent = None
    router = ctx.get("node_router")
    if isinstance(router, dict):
        intents = router.get("intents") or []
        if intents:
            intent = intents[0]

    payload = _json.dumps({
        "user_reply": user_reply or "(no user-facing reply found in context)",
        "rubric": rubric.strip(),
        "metadata": {
            "intent": intent,
            "instance_status": instance.status,
        },
    }, default=str)

    provider, model = _resolve_suggest_fix_provider(
        db, tenant_id=tenant_id, draft=draft,
    )
    try:
        if provider == "anthropic":
            llm_result = _call_evaluate_run_anthropic(
                tenant_id=tenant_id, model=model, user_payload=payload,
            )
        elif provider in {"google", "vertex"}:
            llm_result = _call_evaluate_run_google(
                tenant_id=tenant_id, model=model, user_payload=payload,
                backend="vertex" if provider == "vertex" else "genai",
            )
        else:
            return {"error": f"evaluate_run: unsupported provider {provider!r}"}
    except Exception as exc:
        logger.warning("evaluate_run LLM subcall failed: %s", exc)
        return {"error": f"evaluate_run LLM call failed: {exc}"}

    raw_text = (llm_result.get("raw_text") or "").strip()
    parsed = _parse_evaluate_run_response(raw_text)
    if parsed is None:
        return {
            "error": (
                "evaluate_run: judge returned non-JSON output. "
                "Raw response prefix: " + raw_text[:300]
            ),
            "raw_text": raw_text,
        }

    return {
        "instance_id": str(instance_uuid),
        "rubric": rubric.strip(),
        "verdicts": parsed.get("verdicts") or [],
        "overall": parsed.get("overall") or "partial",
        "summary": parsed.get("summary") or "",
        # Mirror suggest_fix's result shape so callers can rely on
        # `result["provider"]` + `result["model"]` for both subcall
        # tools — important for the UI surfacing "this verdict came
        # from Vertex / Gemini 3.x" attribution.
        "provider": provider,
        "model": model,
        "usage": llm_result.get("usage") or {},
    }


def _call_evaluate_run_anthropic(
    *, tenant_id: str, model: str, user_payload: str,
) -> dict[str, Any]:
    from anthropic import Anthropic

    from app.engine.llm_credentials_resolver import get_anthropic_api_key

    client = Anthropic(api_key=get_anthropic_api_key(tenant_id))
    resp = client.messages.create(
        model=model,
        system=_EVALUATE_RUN_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_payload}],
        max_tokens=1024,
        temperature=0.1,
    )
    raw_text = ""
    for block in resp.content:
        as_dict = _block_to_dict_safe(block)
        if as_dict.get("type") == "text":
            raw_text += as_dict.get("text", "")
    return {
        "raw_text": raw_text.strip(),
        "usage": {
            "input_tokens": getattr(resp.usage, "input_tokens", 0),
            "output_tokens": getattr(resp.usage, "output_tokens", 0),
        },
    }


def _call_evaluate_run_google(
    *, tenant_id: str, model: str, user_payload: str, backend: str,
) -> dict[str, Any]:
    from google.genai import types

    from app.engine.llm_providers import _google_client

    client = _google_client(backend, tenant_id=tenant_id)
    contents = [types.Content(
        role="user",
        parts=[types.Part.from_text(text=user_payload)],
    )]
    config = types.GenerateContentConfig(
        system_instruction=_EVALUATE_RUN_SYSTEM_PROMPT,
        temperature=0.1,
        max_output_tokens=1024,
    )
    resp = client.models.generate_content(
        model=model, contents=contents, config=config,
    )
    text_blocks: list[str] = []
    if resp.candidates and resp.candidates[0].content:
        for part in resp.candidates[0].content.parts:
            if getattr(part, "text", None):
                text_blocks.append(part.text)
    usage = getattr(resp, "usage_metadata", None)
    return {
        "raw_text": "".join(text_blocks).strip(),
        "usage": {
            "input_tokens": getattr(usage, "prompt_token_count", 0) if usage else 0,
            "output_tokens": getattr(usage, "candidates_token_count", 0) if usage else 0,
        },
    }


def _parse_evaluate_run_response(raw_text: str) -> dict[str, Any] | None:
    """Tolerant JSON parse that strips code fences."""
    import json as _json
    import re as _re

    if not raw_text:
        return None
    cleaned = raw_text.strip()
    cleaned = _re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = _re.sub(r"\s*```\s*$", "", cleaned)
    cleaned = cleaned.strip()
    try:
        parsed = _json.loads(cleaned)
    except _json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


# ---------------------------------------------------------------------------
# COPILOT-V2 — suggest_issue_filing (GitHub deep-link with redaction)
# ---------------------------------------------------------------------------


# Patterns that look like secrets / sensitive identifiers. These
# regexes are heuristic — false negatives are possible. The ones we
# DO have are the high-confidence shapes; the lint never claims to be
# a full DLP pass. The `redactions_applied` field tells the user what
# we did so they can review the body before submitting.
#
# Vertex-shaped secrets (service-account private keys, GCS signed URLs)
# are first-class — service-account JSON keys are the most common
# secret-leak shape in a Vertex tenant's logs / configs.
_SECRET_PATTERNS = (
    # Anthropic API keys
    (re.compile(r"sk-ant-api\w+", re.IGNORECASE), "anthropic_api_key"),
    # OpenAI API keys
    (re.compile(r"sk-[A-Za-z0-9]{32,}", re.IGNORECASE), "openai_api_key"),
    # Google API keys (AIza...)
    (re.compile(r"AIza[0-9A-Za-z_\-]{30,}"), "google_api_key"),
    # GitHub PATs
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"), "github_token"),
    # Generic Bearer tokens in HTTP headers
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]{20,}", re.IGNORECASE), "bearer_token"),
    # JSON Web Tokens
    (re.compile(r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), "jwt"),
    # PEM-encoded private keys (Vertex service-account JSON, RSA, ECDSA,
    # OpenSSH). DOTALL because the body spans newlines. Match the entire
    # block so the redaction doesn't leave one half of the key visible.
    (
        re.compile(
            r"-----BEGIN[^-]*PRIVATE KEY-----[\s\S]+?-----END[^-]*PRIVATE KEY-----",
            re.IGNORECASE,
        ),
        "private_key",
    ),
    # GCP service-account JSON has a stable shape — even when the
    # private_key field is already redacted (above), the surrounding
    # JSON often carries the project_id + client_email. Mark the
    # whole `"type": "service_account"` block so a copy-pasted creds
    # JSON gets surfaced as a redaction instead of leaking quietly.
    (
        re.compile(r'"type"\s*:\s*"service_account"'),
        "gcp_service_account_marker",
    ),
    # GCS V4 signed URLs include the access token in query params.
    (
        re.compile(r"https?://storage\.googleapis\.com/[^\s\"']+X-Goog-Signature=[^\s&\"']+"),
        "gcs_signed_url",
    ),
    # OAuth refresh tokens (Google) — opaque high-entropy `1//` prefix.
    (re.compile(r"\b1//[0-9A-Za-z_\-]{40,}"), "google_oauth_refresh_token"),
    # Email addresses (not always secret but commonly customer PII)
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "email"),
)

def _redact_text(text: str) -> tuple[str, dict[str, int]]:
    """Apply the redaction regex set; return (text, counts_per_kind)."""
    if not isinstance(text, str):
        return text, {}
    counts: dict[str, int] = {}
    for pattern, kind in _SECRET_PATTERNS:
        new_text, n = pattern.subn(f"<redacted:{kind}>", text)
        if n:
            counts[kind] = counts.get(kind, 0) + n
            text = new_text
    return text, counts


def _redact_obj(obj: Any, counts: dict[str, int]) -> Any:
    """Recursively redact strings inside a JSON-shaped dict/list."""
    if isinstance(obj, str):
        new_text, sub_counts = _redact_text(obj)
        for k, v in sub_counts.items():
            counts[k] = counts.get(k, 0) + v
        return new_text
    if isinstance(obj, dict):
        return {k: _redact_obj(v, counts) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_obj(x, counts) for x in obj]
    return obj


_ISSUE_BODY_TEMPLATE = """\
> _This issue was drafted by the AE AI Hub workflow copilot. The
> body has been auto-redacted (see "Redactions applied" below); please
> double-check before submitting. Tenant identifiers and full graphs
> are NOT included._

## What happened

{summary}

{error_section}

## Repro / context

- **Copilot session:** provider=`{session_provider}` · model=`{session_model}`
- **Draft id (last 8):** `{draft_short}`
- **Draft version:** {draft_version}
- **Node count / edge count:** {node_count} / {edge_count}
- **Recent tool calls (last {tool_count}):**
{tool_call_block}

## Draft snapshot (shape only)

```
{shape_block}
```

## Redactions applied

{redactions_block}

---

_If you'd like the full draft graph, please attach it manually after
reviewing for sensitive content._
"""


def _shape_only_snapshot(graph: dict[str, Any]) -> str:
    """One-line-per-node shape summary so a maintainer can see the
    structure without seeing prompts / URLs / IDs that might leak."""
    lines: list[str] = []
    for n in (graph.get("nodes") or [])[:30]:
        data = n.get("data") or {}
        label = data.get("label") or "?"
        config = data.get("config") or {}
        config_summary = ", ".join(sorted(config.keys())[:5])
        if len(config) > 5:
            config_summary += f", … (+{len(config) - 5} more)"
        lines.append(f"  {n.get('id', '?')}  {label}  config[{config_summary}]")
    if len(graph.get("nodes") or []) > 30:
        lines.append(f"  … (+{len(graph['nodes']) - 30} more nodes elided)")
    return "\n".join(lines) or "  (empty graph)"


def _build_repo_url(repo: str, *, title: str, body: str, labels: list[str]) -> str:
    """Build the GitHub /issues/new deep-link with everything URL-encoded."""
    from urllib.parse import quote_plus

    base = f"https://github.com/{repo}/issues/new"
    qs_parts = [
        f"title={quote_plus(title)}",
        f"body={quote_plus(body)}",
    ]
    if labels:
        qs_parts.append(f"labels={quote_plus(','.join(labels))}")
    return base + "?" + "&".join(qs_parts)


def _resolve_issue_filing_settings(category: str) -> dict[str, Any]:
    """Pull config from app.config.settings. The two repo paths come
    from settings (env vars); ``copilot_issue_link_enabled`` is the
    master gate."""
    from app.config import settings

    enabled = bool(getattr(settings, "copilot_issue_link_enabled", False))
    if category == "bug":
        repo = getattr(settings, "copilot_issue_repo_bug", "") or getattr(
            settings, "copilot_issue_repo", "",
        )
        labels = ["bug", "from-copilot"]
    else:  # feature
        repo = getattr(settings, "copilot_issue_repo_feature", "") or getattr(
            settings, "copilot_issue_repo", "",
        )
        labels = ["enhancement", "from-copilot"]
    return {"enabled": enabled, "repo": str(repo or ""), "labels": labels}


def suggest_issue_filing(
    db: Session,
    *,
    tenant_id: str,
    draft: WorkflowDraft,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Build a tenant-gated GitHub issue deep-link with redacted draft
    + recent tool-call trace pre-filled.

    Args
    ----
    ::

        {
          "category": "bug" | "feature",   # required
          "summary": "<one-line user-visible summary>",   # required
          "error_context": "<optional engine error / traceback>",
        }

    Returns
    -------
    ::

        {
          "enabled": true|false,
          "link": "<github URL>",
          "body_preview": "<full markdown body, redacted>",
          "redactions_applied": [{kind, count}, ...],
          "repo": "owner/name",
          "category": "bug|feature",
        }

    When ``enabled=false`` the tenant has opted out via env config
    (``COPILOT_ISSUE_LINK_ENABLED=false``). The agent should NOT
    surface a link in that case.
    """
    from app.models.copilot import CopilotSession, CopilotTurn

    category = (args.get("category") or "").strip().lower()
    if category not in ("bug", "feature"):
        return {"error": "suggest_issue_filing 'category' must be 'bug' or 'feature'"}

    summary = args.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return {"error": "suggest_issue_filing requires a non-empty 'summary'"}
    summary = summary.strip()
    if len(summary) > 200:
        summary = summary[:197] + "…"

    error_context = args.get("error_context")
    if error_context is not None and not isinstance(error_context, str):
        return {"error": "suggest_issue_filing 'error_context' must be a string"}

    cfg = _resolve_issue_filing_settings(category)
    if not cfg["enabled"] or not cfg["repo"]:
        return {
            "enabled": False,
            "category": category,
            "reason": (
                "Issue filing is not configured for this deployment. "
                "Set COPILOT_ISSUE_LINK_ENABLED=true and "
                "COPILOT_ISSUE_REPO_BUG / COPILOT_ISSUE_REPO_FEATURE "
                "(or the shared COPILOT_ISSUE_REPO) to enable."
            ),
        }

    redaction_counts: dict[str, int] = {}
    redacted_summary, summary_counts = _redact_text(summary)
    for k, v in summary_counts.items():
        redaction_counts[k] = redaction_counts.get(k, 0) + v
    redacted_error: str | None = None
    if error_context:
        redacted_error, err_counts = _redact_text(error_context)
        for k, v in err_counts.items():
            redaction_counts[k] = redaction_counts.get(k, 0) + v

    # Recent tool-call trace from the most recent active session on
    # this draft. We surface tool NAMES + arg KEYS only — not arg
    # values — to keep the body focused on shape, not data.
    # Session provider + model are included so a maintainer triaging
    # the issue knows whether the user was on Anthropic / Google AI
    # Studio / Vertex AI — different LLM stacks have very different
    # failure modes, and "couldn't reproduce on Anthropic" vs
    # "couldn't reproduce on Vertex" is the very first triage question.
    session = (
        db.query(CopilotSession)
        .filter_by(tenant_id=tenant_id, draft_id=draft.id)
        .order_by(CopilotSession.created_at.desc())
        .first()
    )
    session_provider = (session.provider if session else "unknown") or "unknown"
    session_model = (session.model if session else "unknown") or "unknown"
    recent_tool_calls: list[dict[str, Any]] = []
    if session is not None:
        rows = (
            db.query(CopilotTurn)
            .filter_by(tenant_id=tenant_id, session_id=session.id, role="tool")
            .order_by(CopilotTurn.turn_index.desc())
            .limit(8)
            .all()
        )
        for row in reversed(rows):  # chronological
            content = row.content_json or {}
            recent_tool_calls.append({
                "name": content.get("name"),
                "arg_keys": sorted((content.get("args") or {}).keys()),
                "had_error": bool(content.get("error")),
            })

    if recent_tool_calls:
        tool_call_block = "\n".join(
            f"  - `{tc['name']}` args=[{', '.join(tc['arg_keys'])}]"
            + (" (errored)" if tc["had_error"] else "")
            for tc in recent_tool_calls
        )
    else:
        tool_call_block = "  (no recent tool calls)"

    error_section = ""
    if redacted_error:
        error_section = (
            f"### Error / engine output\n\n```\n{redacted_error[:3000]}\n```\n"
        )

    redactions_block = (
        "\n".join(f"  - `{k}`: {v} occurrence(s)" for k, v in sorted(redaction_counts.items()))
        if redaction_counts
        else "  (no redactions applied — no high-confidence secret patterns found)"
    )

    graph = draft.graph_json or {}
    body = _ISSUE_BODY_TEMPLATE.format(
        summary=redacted_summary,
        error_section=error_section,
        session_provider=session_provider,
        session_model=session_model,
        draft_short=str(draft.id).split("-")[0],
        draft_version=draft.version,
        node_count=len(graph.get("nodes") or []),
        edge_count=len(graph.get("edges") or []),
        tool_count=len(recent_tool_calls),
        tool_call_block=tool_call_block,
        shape_block=_shape_only_snapshot(graph),
        redactions_block=redactions_block,
    )

    title_prefix = "[copilot bug]" if category == "bug" else "[copilot feature]"
    title = f"{title_prefix} {redacted_summary[:120]}"
    link = _build_repo_url(
        cfg["repo"], title=title, body=body, labels=cfg["labels"],
    )

    return {
        "enabled": True,
        "category": category,
        "link": link,
        "body_preview": body,
        "redactions_applied": [
            {"kind": k, "count": v} for k, v in sorted(redaction_counts.items())
        ],
        "repo": cfg["repo"],
        "labels": cfg["labels"],
    }


# ---------------------------------------------------------------------------
# CTX-MGMT.A — inspect_node_artifact
# ---------------------------------------------------------------------------


def inspect_node_artifact(
    db: Session,
    *,
    tenant_id: str,
    draft: WorkflowDraft,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Fetch the full output payload for a node whose in-context value
    is an overflow stub (CTX-MGMT.A).

    When a node's output exceeded the per-node ``contextOutputBudget``,
    the engine replaced ``context[node_id]`` with a small stub
    ``{"_overflow": True, "_artifact_id": "<uuid>", ...}`` and persisted
    the full output to ``node_output_artifacts``. This tool reads that
    table and returns the original output so the agent can answer
    "show me what node X actually produced" questions.

    Args
    ----
    ::

        {
          "instance_id": "<uuid>",   # required — copilot-ephemeral instance
          "node_id": "<id>",         # required — the overflowed node
        }

    Same ephemeral-only safety as ``get_execution_logs`` —
    production instances are not readable from the copilot tool surface.

    Returns
    -------
    ::

        {
          "instance_id": "...",
          "node_id": "...",
          "size_bytes": N,
          "budget_bytes": M,
          "output_json": <full payload>,
          "created_at": "..."
        }

    Or ``{"error": "..."}`` if the artifact doesn't exist (the node
    didn't overflow, or the instance was production).
    """
    from app.models.workflow import (
        NodeOutputArtifact,
        WorkflowDefinition,
        WorkflowInstance,
    )

    instance_id = args.get("instance_id")
    node_id = args.get("node_id")
    if not instance_id or not node_id:
        return {"error": "inspect_node_artifact requires 'instance_id' and 'node_id'"}
    try:
        instance_uuid = uuid.UUID(str(instance_id))
    except ValueError:
        return {"error": f"inspect_node_artifact: invalid instance_id {instance_id!r}"}

    instance = (
        db.query(WorkflowInstance)
        .filter_by(id=instance_uuid, tenant_id=tenant_id)
        .first()
    )
    if instance is None:
        return {"error": f"Instance {instance_id!r} not found for this tenant"}

    wd = (
        db.query(WorkflowDefinition)
        .filter_by(id=instance.workflow_def_id, tenant_id=tenant_id)
        .first()
    )
    if wd is None or not wd.is_ephemeral:
        return {
            "error": (
                "inspect_node_artifact only supports copilot-initiated "
                "(ephemeral) instances."
            ),
        }

    artifact = (
        db.query(NodeOutputArtifact)
        .filter_by(
            tenant_id=tenant_id,
            instance_id=instance_uuid,
            node_id=str(node_id),
        )
        .order_by(NodeOutputArtifact.created_at.desc())
        .first()
    )
    if artifact is None:
        return {
            "error": (
                f"No overflow artifact for node {node_id!r} on instance "
                f"{instance_id!r}. The node may not have exceeded its "
                "contextOutputBudget — its output is fully inline in "
                "context_json. Use get_execution_logs to see it."
            ),
        }

    return {
        "instance_id": str(instance_uuid),
        "node_id": artifact.node_id,
        "size_bytes": artifact.size_bytes,
        "budget_bytes": artifact.budget_bytes,
        "output_json": artifact.output_json,
        "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
    }
