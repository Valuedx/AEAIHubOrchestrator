"""DAG execution engine — V0.4: branch-aware traversal + parallel execution.

Parses the React Flow JSON graph into a handle-aware adjacency structure,
executes nodes respecting condition branch outcomes (true/false edges),
and runs independent branches in parallel via ThreadPoolExecutor.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.workflow import WorkflowInstance, ExecutionLog, InstanceCheckpoint
from app.engine.node_handlers import dispatch_node
from app.engine.scrubber import scrub_secrets
from app.engine.exceptions import NodeSuspendedAsync

logger = logging.getLogger(__name__)

_MAX_PARALLEL = 8


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_clean_context(context: dict[str, Any]) -> dict[str, Any]:
    """Strip internal runtime keys ('_*') before DB storage.

    EXCEPT ``_runtime`` (CTX-MGMT.D) — that key carries durable runtime
    state that MUST survive suspend/resume:

      * ``_runtime["loop_item"]`` / ``loop_index`` / ``loop_iteration`` /
        ``loop_item_var`` — set by ForEach + Loop iteration runners.
      * ``_runtime["cycle_iterations"]`` — per-loopback-edge counters
        (cyclic graphs / CYCLIC-01.b).
      * ``_runtime["parent_chain"]`` — sub-workflow recursion guard.
      * ``_runtime["hitl_pending_call"]`` — re-fire payload for the
        engine's HITL gate (HITL-04).

    Without this exception, HITL inside a ForEach iteration restarts at
    index 0 on resume, cyclic counters reset to 0, and the HITL re-fire
    pattern breaks. ``_trace``, ``_instance_id``, ``_workflow_def_id``,
    ``_current_node_id`` stay top-level + ephemeral — they're rebuilt
    each invocation from instance metadata or the trace context.
    """
    return {
        k: v for k, v in context.items()
        if not k.startswith("_") or k == "_runtime"
    }


# CTX-MGMT.D — `_runtime` namespace plumbing.
#
# Producers write under `_runtime[...]` via `_get_runtime(context)`.
# Consumers read via `_get_runtime(context).get(...)` or via the
# convenience accessor below. Resume-time hoist recovers any legacy
# context_json that still has flat `_loop_iteration` etc. so existing
# in-flight suspended instances don't break when this code lands.

# Legacy flat keys that get hoisted into `_runtime` on first resume.
# Order matters: we hoist any of these we find, then drop the flat
# version so subsequent reads come from the new location only.
_LEGACY_RUNTIME_KEYS: tuple[tuple[str, str], ...] = (
    # (legacy_flat_key, new_runtime_key)
    ("_loop_item",      "loop_item"),
    ("_loop_index",     "loop_index"),
    ("_loop_iteration", "loop_iteration"),
    ("_loop_item_var",  "loop_item_var"),
    ("_cycle_iterations", "cycle_iterations"),
    ("_parent_chain",   "parent_chain"),
    ("hitl_pending_call", "hitl_pending_call"),  # NOT underscore-prefixed in legacy
)


def _get_runtime(context: dict[str, Any]) -> dict[str, Any]:
    """Get-or-init the resume-safe runtime sub-dict on ``context``.

    Always returns a real dict (creates an empty one if absent), so
    callers can do ``_get_runtime(context)["loop_item"] = item``
    without first checking presence.
    """
    runtime = context.get("_runtime")
    if not isinstance(runtime, dict):
        runtime = {}
        context["_runtime"] = runtime
    return runtime


def _hoist_legacy_runtime(context: dict[str, Any]) -> None:
    """Backward-compat — move legacy flat runtime keys under ``_runtime``.

    Called immediately after loading ``context`` from
    ``instance.context_json`` (every resume + initial-execute entry
    point in this module). Idempotent: if ``_runtime`` is already
    present, the legacy keys are still drained (the legacy ones win
    only if ``_runtime`` doesn't already carry the same key — that
    way a hand-edited context_json that set both shapes doesn't lose
    the canonical runtime data).
    """
    legacy_present = any(legacy in context for legacy, _ in _LEGACY_RUNTIME_KEYS)
    if not legacy_present:
        # Fast path — no migration to do. ``_runtime`` may or may not
        # exist; ``_get_runtime`` handles either when called.
        return
    runtime = _get_runtime(context)
    for legacy, new_key in _LEGACY_RUNTIME_KEYS:
        if legacy not in context:
            continue
        if new_key not in runtime:
            runtime[new_key] = context[legacy]
        # Always drop the legacy flat key so subsequent reads route
        # through `_runtime` only — single source of truth post-hoist.
        context.pop(legacy, None)


def _finalize_cancelled(
    db: Session,
    instance: WorkflowInstance,
    context: dict[str, Any],
) -> None:
    """Mark instance cancelled and persist context (cooperative cancel between nodes)."""
    trace = context.get("_trace")
    if trace:
        try:
            trace.update(output={"status": "cancelled"})
        except Exception:
            pass
    instance.status = "cancelled"
    instance.cancel_requested = False
    instance.pause_requested = False
    instance.context_json = _get_clean_context(context)
    instance.completed_at = _utcnow()

    # Cascade cancel to any running/queued child sub-workflow instances
    children = (
        db.query(WorkflowInstance)
        .filter(
            WorkflowInstance.parent_instance_id == instance.id,
            WorkflowInstance.status.in_(["running", "queued"]),
        )
        .all()
    )
    for child in children:
        child.cancel_requested = True

    # Cascade cancel to any outstanding async_jobs rows (AutomationEdge
    # and any other external system). We mark them cancelled in our DB
    # and make a best-effort call to terminate the external job —
    # failures are logged but don't block the cancel.
    from app.models.workflow import AsyncJob
    outstanding = (
        db.query(AsyncJob)
        .filter(
            AsyncJob.instance_id == instance.id,
            AsyncJob.status.in_(("submitted", "running")),
        )
        .all()
    )
    for job in outstanding:
        job.status = "cancelled"
        job.completed_at = _utcnow()
        try:
            _best_effort_cancel_external(db, job)
        except Exception as exc:
            logger.warning(
                "External cancel for async_job %s (%s) failed non-fatally: %s",
                job.id, job.system, exc,
            )

    db.commit()
    logger.info("Workflow %s cancelled (cooperative, between nodes)", instance.id)


def _best_effort_cancel_external(db: Session, job: "Any") -> None:
    """Ping the external system to terminate the job. Never raises."""
    from app.models.workflow import AsyncJob, WorkflowInstance  # local import

    if job.system != "automationedge":
        return  # no other systems wired yet

    instance = db.query(WorkflowInstance).filter_by(id=job.instance_id).first()
    if instance is None:
        return
    meta = job.metadata_json or {}
    from app.engine.automationedge_client import AEConnection, try_terminate
    conn = AEConnection(
        base_url=meta.get("base_url", ""),
        tenant_id=instance.tenant_id,
        credentials_secret_prefix=meta.get("credentials_secret_prefix", "AUTOMATIONEDGE"),
        auth_mode=meta.get("auth_mode", "ae_session"),
        org_code=meta.get("org_code"),
    )
    if not conn.base_url:
        return
    try_terminate(conn, job.external_job_id)


def _finalize_paused(
    db: Session,
    instance: WorkflowInstance,
    context: dict[str, Any],
) -> None:
    """Mark instance paused and persist context (cooperative pause between nodes)."""
    trace = context.get("_trace")
    if trace:
        try:
            trace.update(output={"status": "paused"})
        except Exception:
            pass
    instance.status = "paused"
    instance.pause_requested = False
    instance.cancel_requested = False
    instance.context_json = _get_clean_context(context)
    db.commit()
    logger.info("Workflow %s paused (cooperative, between nodes)", instance.id)


def _abort_if_cancel_or_pause(
    db: Session,
    instance: WorkflowInstance,
    context: dict[str, Any],
) -> bool:
    """If cancel or pause was requested, finalize and return True (cancel wins over pause)."""
    db.refresh(instance)
    if instance.cancel_requested:
        _finalize_cancelled(db, instance, context)
        return True
    if instance.pause_requested:
        _finalize_paused(db, instance, context)
        return True
    return False


# ---------------------------------------------------------------------------
# Graph parsing (handle-aware)
# ---------------------------------------------------------------------------

class _Edge:
    """One edge in the parsed graph.

    * ``id`` — React Flow edge id, used as the key for per-edge
      iteration counters in ``context["_cycle_iterations"]``.
      Falls back to a synthesised ``<source>→<target>:<handle>``
      key if the edge dict didn't carry an id (legacy graphs).
    * ``source`` / ``target`` — node ids.
    * ``source_handle`` — optional handle id used by Condition to
      route true/false branches.
    * ``kind`` — ``"forward"`` (default) or ``"loopback"``. Loopback
      edges (CYCLIC-01.a) carry a re-entry target back to an
      upstream node; they are EXCLUDED from the forward subgraph
      used for cycle detection + execution so the forward graph
      stays strictly acyclic. CYCLIC-01.b adds the runtime semantic
      of actually re-enqueuing the target.
    * ``max_iterations`` — per-cycle iteration cap (loopback edges
      only). Backend hard-cap 100 regardless of what the edge
      attribute says; validator clamps author-supplied values to
      1-100 in CYCLIC-01.c.
    """
    __slots__ = ("id", "source", "target", "source_handle", "kind", "max_iterations")

    def __init__(
        self,
        source: str,
        target: str,
        source_handle: str | None,
        kind: str = "forward",
        max_iterations: int | None = None,
        id: str | None = None,
    ):
        self.id = id or f"{source}->{target}:{source_handle or ''}"
        self.source = source
        self.target = target
        self.source_handle = source_handle
        self.kind = kind
        self.max_iterations = max_iterations

    @property
    def is_loopback(self) -> bool:
        return self.kind == "loopback"


# CYCLIC-01.a — default iteration cap for loopback edges when the
# author omits ``maxIterations``. Kept aligned with the existing
# Loop node's default so operators don't have to remember two
# different numbers. CYCLIC-01.b enforces it at runtime; CYCLIC-01.c
# adds a validator lint warning ``LOOPBACK_NO_CAP`` when the edge
# relies on this default.
LOOPBACK_DEFAULT_MAX_ITERATIONS = 10
# Hard ceiling regardless of author-supplied value. Keeps a runaway
# loop from burning the execution budget even if someone ships a
# graph with ``maxIterations: 999999``.
LOOPBACK_HARD_CAP = 100


def parse_graph(graph_json: dict) -> tuple[dict, list[_Edge]]:
    """Convert React Flow JSON into execution-friendly structures.

    Only ``type == "agenticNode"`` entries become executable nodes.
    Everything else (e.g. DV-03 sticky notes with ``type ==
    "stickyNote"``) is a canvas annotation and is filtered out here so
    it never enters the ready queue or the cycle check. Edges that
    reference filtered-out nodes are dropped too — a stray connection
    to a sticky can't corrupt in-degree computations downstream.

    **Loopback edges (CYCLIC-01.a):** edges with ``type == "loopback"``
    are parsed into ``_Edge.kind = "loopback"`` + ``max_iterations``
    carried through. ``_build_graph_structures`` excludes them from
    forward adjacency + in-degree so the forward subgraph stays
    acyclic. The runtime semantics (actually re-enqueueing the
    target on a "continue" branch) land in CYCLIC-01.b.

    Returns:
        nodes_map: {node_id: node_dict} — executable nodes only
        edges:     list of _Edge referencing ids in nodes_map
    """
    nodes_list: list[dict] = graph_json.get("nodes", [])
    edges_list: list[dict] = graph_json.get("edges", [])

    nodes_map: dict[str, dict] = {
        n["id"]: n for n in nodes_list
        # Default type is "agenticNode" for legacy workflows that don't
        # set ``type`` explicitly — those predate the sticky-note
        # addition and are all executable nodes.
        if n.get("type", "agenticNode") == "agenticNode"
    }
    edges: list[_Edge] = []
    for e in edges_list:
        if e["source"] not in nodes_map or e["target"] not in nodes_map:
            continue
        edge_type = e.get("type")
        if edge_type == "loopback":
            raw_max = e.get("maxIterations")
            try:
                max_iter = int(raw_max) if raw_max is not None else LOOPBACK_DEFAULT_MAX_ITERATIONS
            except (TypeError, ValueError):
                max_iter = LOOPBACK_DEFAULT_MAX_ITERATIONS
            # Clamp defensively even without the validator — a runtime
            # surprise from a 999k iteration cap would be worse than
            # a clamped one.
            max_iter = max(1, min(max_iter, LOOPBACK_HARD_CAP))
            edges.append(_Edge(
                id=e.get("id"),
                source=e["source"],
                target=e["target"],
                source_handle=e.get("sourceHandle"),
                kind="loopback",
                max_iterations=max_iter,
            ))
        else:
            edges.append(_Edge(
                id=e.get("id"),
                source=e["source"],
                target=e["target"],
                source_handle=e.get("sourceHandle"),
                kind="forward",
            ))
    return nodes_map, edges


def _build_graph_structures(
    nodes_map: dict, edges: list[_Edge]
) -> tuple[dict[str, list[_Edge]], dict[str, list[_Edge]], dict[str, int]]:
    """Build forward adjacency, reverse adjacency, and in-degree maps.

    **Loopback edges are EXCLUDED here (CYCLIC-01.a).** Including them
    would create cycles in the forward subgraph, which ``_detect_cycles``
    would (correctly) flag as an error. Downstream execution uses
    only the forward subgraph, so loopbacks are invisible to the
    runtime until CYCLIC-01.b wires up the re-enqueue semantics via
    a dedicated lookup (not via ``forward``).
    """
    forward: dict[str, list[_Edge]] = defaultdict(list)
    reverse: dict[str, list[_Edge]] = defaultdict(list)
    in_degree: dict[str, int] = {nid: 0 for nid in nodes_map}

    for edge in edges:
        if edge.is_loopback:
            continue
        forward[edge.source].append(edge)
        reverse[edge.target].append(edge)
        in_degree[edge.target] = in_degree.get(edge.target, 0) + 1

    return dict(forward), dict(reverse), in_degree


def loopback_edges(edges: list[_Edge]) -> list[_Edge]:
    """Filter a parsed edge list to just the loopback edges. Exposed
    for CYCLIC-01.b's runtime execution + CYCLIC-01.c's validator.
    """
    return [e for e in edges if e.is_loopback]


def _detect_cycles(nodes_map: dict, forward: dict, in_degree: dict) -> None:
    """Validate there are no cycles using Kahn's algorithm."""
    from collections import deque

    deg = dict(in_degree)
    queue = deque(nid for nid, d in deg.items() if d == 0)
    visited: set[str] = set()

    while queue:
        nid = queue.popleft()
        visited.add(nid)
        for edge in forward.get(nid, []):
            deg[edge.target] -= 1
            if deg[edge.target] == 0:
                queue.append(edge.target)

    if len(visited) != len(nodes_map):
        cycle_nodes = sorted(set(nodes_map) - visited)
        raise ValueError(
            f"Graph contains a cycle (visited {len(visited)}/{len(nodes_map)} nodes); "
            f"unreachable via topological order: {cycle_nodes}"
        )


# ---------------------------------------------------------------------------
# Execution — ready-queue model
# ---------------------------------------------------------------------------

def execute_graph(db: Session, instance_id: str, deterministic_mode: bool = False) -> None:
    """Run a full workflow instance with branch-aware parallel execution.

    Args:
        deterministic_mode: When True, parallel node batches are submitted and
            their results processed in stable sorted node-ID order, giving fully
            reproducible execution logs.  Slightly reduces throughput for large
            parallel batches; leave False for production hot-paths.
    """
    from app.observability import trace_workflow, flush

    instance: WorkflowInstance | None = (
        db.query(WorkflowInstance).filter_by(id=instance_id).first()
    )
    if not instance:
        raise ValueError(f"WorkflowInstance {instance_id} not found")

    instance.status = "running"
    instance.started_at = _utcnow()
    db.commit()

    db.refresh(instance)
    if instance.cancel_requested:
        ctx_early: dict[str, Any] = dict(instance.context_json or {})
        _hoist_legacy_runtime(ctx_early)
        if instance.trigger_payload:
            ctx_early["trigger"] = instance.trigger_payload
        _finalize_cancelled(db, instance, _get_clean_context(ctx_early))
        return
    if instance.pause_requested:
        ctx_pause: dict[str, Any] = dict(instance.context_json or {})
        _hoist_legacy_runtime(ctx_pause)
        if instance.trigger_payload:
            ctx_pause["trigger"] = instance.trigger_payload
        _finalize_paused(db, instance, _get_clean_context(ctx_pause))
        return

    graph = instance.definition.graph_json
    nodes_map, edges = parse_graph(graph)
    forward, reverse, in_degree = _build_graph_structures(nodes_map, edges)
    _detect_cycles(nodes_map, forward, in_degree)

    context: dict[str, Any] = dict(instance.context_json or {})
    _hoist_legacy_runtime(context)
    if instance.trigger_payload:
        context["trigger"] = instance.trigger_payload
    # Expose instance_id so node handlers can route LLM tokens to the right Redis channel
    context["_instance_id"] = str(instance.id)
    context["_workflow_def_id"] = str(instance.workflow_def_id)

    # Sub-workflow recursion detection: build the parent chain if not already
    # present (child instances have parent_chain pre-set under _runtime in
    # their context_json — see _execute_sub_workflow).
    runtime = _get_runtime(context)
    if "parent_chain" not in runtime:
        parent_chain: list[str] = []
        ancestor = instance
        while ancestor.parent_instance_id:
            ancestor = db.query(WorkflowInstance).filter_by(id=ancestor.parent_instance_id).first()
            if not ancestor:
                break
            parent_chain.append(str(ancestor.workflow_def_id))
        runtime["parent_chain"] = parent_chain

    # CTX-MGMT.H — resolve the context-trace flag once at run start
    # and stamp it under _runtime. The fast-path check in record_write
    # is then a single dict lookup; default-off production paths add
    # zero overhead. Ephemeral instances always trace; production
    # opts in via tenant_policies.context_trace_enabled.
    if "context_trace_enabled" not in runtime:
        from app.engine.context_trace import resolve_trace_flag
        is_ephemeral = bool(getattr(instance.definition, "is_ephemeral", False))
        runtime["context_trace_enabled"] = resolve_trace_flag(
            db, tenant_id=instance.tenant_id, is_ephemeral=is_ephemeral,
        )
    # CTX-MGMT.K — resolve compaction flag (default ON, opt out via
    # tenant_policies.context_compaction_enabled). Same one-time
    # resolution at run start so the per-node check is a dict lookup.
    if "context_compaction_enabled" not in runtime:
        from app.engine.compaction import resolve_compaction_flag
        runtime["context_compaction_enabled"] = resolve_compaction_flag(
            db, tenant_id=instance.tenant_id,
        )
    # CTX-MGMT.F — resolve secret-scrub flag (default ON, opt out via
    # tenant_policies.context_secret_scrub_enabled). Per-write
    # check is a dict lookup; resolution is one-time at run start.
    if "context_secret_scrub_enabled" not in runtime:
        try:
            from app.engine.tenant_policy_resolver import get_effective_policy
            policy = get_effective_policy(instance.tenant_id)
            runtime["context_secret_scrub_enabled"] = bool(
                getattr(policy, "context_secret_scrub_enabled", True)
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "context_secret_scrub: tenant policy lookup failed for "
                "tenant=%r (%s); defaulting scrub ON",
                instance.tenant_id, exc,
            )
            runtime["context_secret_scrub_enabled"] = True

    det_tag = ["deterministic"] if deterministic_mode else []
    with trace_workflow(
        workflow_id=str(instance.workflow_def_id),
        instance_id=str(instance.id),
        tenant_id=instance.tenant_id,
        workflow_name=instance.definition.name,
        trigger_payload=instance.trigger_payload,
        tags=[f"nodes:{len(nodes_map)}"] + det_tag,
    ) as trace:
        context["_trace"] = trace
        _execute_ready_queue(
            db, instance, nodes_map, forward, reverse, in_degree, context,
            skipped=set(),
            deterministic_mode=deterministic_mode,
            loopbacks_by_source=_build_loopback_map(edges),
        )
        trace.update(output={"status": instance.status, "nodes_executed": len([k for k in context if k.startswith("node_")])})

    flush()


def resume_graph(
    db: Session,
    instance_id: str,
    approval_payload: dict,
    context_patch: dict | None = None,
) -> None:
    """Resume a suspended workflow from the node that caused suspension.

    Args:
        approval_payload: Forwarded into context["approval"] so downstream
            nodes can inspect the human's decision.
        context_patch: Optional shallow-merge dict applied to the context
            before re-entering the ready queue.  Use to inject corrected
            node outputs or override any context key without rerunning
            earlier nodes.
    """
    instance: WorkflowInstance | None = (
        db.query(WorkflowInstance).filter_by(id=instance_id).first()
    )
    if not instance or instance.status != "suspended":
        raise ValueError(
            f"WorkflowInstance {instance_id} not found or not suspended"
        )

    instance.status = "running"
    # Clear the async-external flag (if set) so the UI flips back to
    # running. NULL remains the default for plain HITL resumes too.
    instance.suspended_reason = None
    # HITL-01.b — clear the suspended-at stamp on resume so the
    # dashboard stops counting age once the human has responded.
    instance.suspended_at = None
    db.commit()

    graph = instance.definition.graph_json
    nodes_map, edges = parse_graph(graph)
    forward, reverse, in_degree = _build_graph_structures(nodes_map, edges)

    context: dict[str, Any] = dict(instance.context_json or {})
    _hoist_legacy_runtime(context)
    context["approval"] = approval_payload

    # Apply operator-supplied context overrides (HITL context patch)
    if context_patch:
        context.update(context_patch)
        logger.info(
            "Workflow %s resumed with context_patch covering keys: %s",
            instance_id, list(context_patch.keys()),
        )

    already_executed = set(context.keys()) - {"trigger", "approval"}

    # HITL-resume fix: when a tool inside a ReAct node returned
    # AWAITING_APPROVAL, the suspending node never completed and
    # should be RE-EXECUTED on resume (this time with
    # context["approval"]["approved"]=True so the gate passes
    # through). Without this, the engine treats the suspending node
    # as already-done and prunes the downstream subgraph, leaving the
    # destructive action unfired.
    if instance.current_node_id and instance.current_node_id in already_executed:
        already_executed.discard(instance.current_node_id)
        # Drop its stale (partial) output so the re-execution starts fresh.
        context.pop(instance.current_node_id, None)
        logger.info(
            "Resume: re-running suspended node %s (HITL approval received)",
            instance.current_node_id,
        )
    elif instance.current_node_id:
        # Node wasn't in context anyway — no-op, but log for traceability.
        logger.info(
            "Resume: suspended node %s not in context; engine will pick it up via in-degree",
            instance.current_node_id,
        )

    _execute_ready_queue(
        db, instance, nodes_map, forward, reverse, in_degree, context,
        skipped=already_executed,
        loopbacks_by_source=_build_loopback_map(edges),
    )


def resume_paused_graph(
    db: Session,
    instance_id: str,
    context_patch: dict | None = None,
) -> None:
    """Resume a paused workflow (user pause between nodes — not HITL suspended)."""
    instance: WorkflowInstance | None = (
        db.query(WorkflowInstance).filter_by(id=instance_id).first()
    )
    if not instance or instance.status != "paused":
        raise ValueError(
            f"WorkflowInstance {instance_id} not found or not paused"
        )

    instance.status = "running"
    db.commit()

    graph = instance.definition.graph_json
    nodes_map, edges = parse_graph(graph)
    forward, reverse, in_degree = _build_graph_structures(nodes_map, edges)

    context: dict[str, Any] = dict(instance.context_json or {})
    _hoist_legacy_runtime(context)
    context.pop("_trace", None)
    context["_instance_id"] = str(instance.id)
    if context_patch:
        context.update(context_patch)
        logger.info(
            "Workflow %s resumed from pause with context_patch keys: %s",
            instance_id,
            list(context_patch.keys()),
        )

    already_executed = set(context.keys()) - {"trigger", "approval"}

    from app.observability import trace_workflow, flush

    with trace_workflow(
        workflow_id=str(instance.workflow_def_id),
        instance_id=str(instance.id),
        tenant_id=instance.tenant_id,
        workflow_name=instance.definition.name,
        trigger_payload=instance.trigger_payload,
        tags=["resume-paused"],
    ) as trace:
        context["_trace"] = trace
        _execute_ready_queue(
            db, instance, nodes_map, forward, reverse, in_degree, context,
            skipped=already_executed,
            loopbacks_by_source=_build_loopback_map(edges),
        )
        trace.update(
            output={
                "status": instance.status,
                "resumed_from": "paused",
            }
        )

    flush()
    logger.info(
        "Workflow %s resumed from pause with final status %s",
        instance.id,
        instance.status,
    )


def retry_graph(
    db: Session, instance_id: str, from_node_id: str | None = None
) -> None:
    """Retry a failed workflow from the point of failure.

    Re-uses the accumulated context up to the failed node, clears the
    failed node's output, and re-runs the ready queue from that point.
    """
    instance: WorkflowInstance | None = (
        db.query(WorkflowInstance).filter_by(id=instance_id).first()
    )
    if not instance or instance.status != "failed":
        raise ValueError(
            f"WorkflowInstance {instance_id} not found or not in failed status"
        )

    # Determine which node to retry from
    retry_node = from_node_id or instance.current_node_id
    if not retry_node:
        raise ValueError("No node to retry from — instance has no current_node_id")

    # Clean up the failed node from context and logs
    context: dict[str, Any] = dict(instance.context_json or {})
    _hoist_legacy_runtime(context)
    context.pop(retry_node, None)

    # Delete the failed execution log entry so it can be re-created
    db.query(ExecutionLog).filter_by(
        instance_id=instance.id, node_id=retry_node, status="failed"
    ).delete()

    instance.status = "running"
    instance.completed_at = None
    db.commit()

    graph = instance.definition.graph_json
    nodes_map, edges = parse_graph(graph)
    forward, reverse, in_degree = _build_graph_structures(nodes_map, edges)

    # All nodes whose output is already in context are "already executed"
    already_executed = {
        k for k in context.keys()
        if k.startswith("node_") or k == "trigger"
    }

    from app.observability import trace_workflow, flush
    with trace_workflow(
        workflow_id=str(instance.workflow_def_id),
        instance_id=str(instance.id),
        tenant_id=instance.tenant_id,
        workflow_name=instance.definition.name,
        trigger_payload=instance.trigger_payload,
        tags=["retry", f"from:{retry_node}"],
    ) as trace:
        context["_trace"] = trace
        _execute_ready_queue(
            db, instance, nodes_map, forward, reverse, in_degree, context,
            skipped=already_executed,
            loopbacks_by_source=_build_loopback_map(edges),
        )
        trace.update(output={"status": instance.status, "retried_from": retry_node})

    flush()
    logger.info("Retry of workflow %s from node %s completed with status %s",
                instance.id, retry_node, instance.status)


def _execute_ready_queue(
    db: Session,
    instance: WorkflowInstance,
    nodes_map: dict,
    forward: dict[str, list[_Edge]],
    reverse: dict[str, list[_Edge]],
    in_degree: dict[str, int],
    context: dict[str, Any],
    skipped: set[str],
    deterministic_mode: bool = False,
    loopbacks_by_source: dict[str, list[_Edge]] | None = None,
) -> None:
    """Process nodes in ready-order, respecting condition branches and
    running independent nodes in parallel.

    ``loopbacks_by_source`` (CYCLIC-01.b) maps each source node id
    to the loopback edges originating there. After a node completes
    and its forward edges propagate, any matching loopbacks are
    evaluated and fired (cycle body cleared + iteration counter
    bumped). ``None`` is equivalent to ``{}`` — the resume / retry
    paths that don't re-parse loopbacks fall back to the original
    forward-only semantics.
    """
    loopbacks_by_source = loopbacks_by_source or {}

    satisfied: dict[str, set[str]] = defaultdict(set)
    pruned: set[str] = set()

    ready: list[str] = []
    for nid, deg in in_degree.items():
        if deg == 0 and nid not in skipped:
            ready.append(nid)
        elif nid in skipped:
            satisfied[nid] = set()
            _propagate_edges(nid, forward, nodes_map, context, satisfied, pruned)

    # HITL-resume-03: after propagating skipped nodes, a mid-graph resume
    # point (e.g. node_worker suspended before saving its output) may have
    # all predecessors satisfied but a non-zero in-degree, so it never lands
    # in the initial `ready` list above. Bootstrap from _find_ready_nodes so
    # those nodes are picked up even when `ready` would otherwise be empty.
    if not ready:
        ready = _find_ready_nodes(nodes_map, reverse, satisfied, context, skipped, pruned)

    while ready:
        ready = [nid for nid in ready if nid not in pruned]
        if not ready:
            break

        if _abort_if_cancel_or_pause(db, instance, context):
            return

        # ForEach / Loop nodes must always be processed individually so that
        # their post-execution iteration dispatch fires.  If such a node is in
        # the ready batch alongside other nodes, pull just the first one out
        # and let the remaining nodes be picked up on the next loop iteration.
        iteration_node_ids = [
            nid for nid in ready
            if nodes_map.get(nid, {}).get("data", {}).get("nodeCategory") == "logic"
            and nodes_map.get(nid, {}).get("data", {}).get("label")
            in ("ForEach", "Loop", "While")
        ]
        if iteration_node_ids:
            ready = [iteration_node_ids[0]]

        if len(ready) == 1:
            node_id = ready[0]
            result = _execute_single_node(
                db, instance, nodes_map, node_id, context,
            )
            if result == "suspended":
                return
            if result == "failed":
                return
            if _abort_if_cancel_or_pause(db, instance, context):
                return

            # ── ForEach / Loop iteration ──
            node_data = nodes_map.get(node_id, {}).get("data", {})
            node_label = node_data.get("label", "")
            if node_data.get("nodeCategory") == "logic" and node_label == "ForEach":
                _run_forEach_iterations(
                    db, instance, nodes_map, forward, reverse,
                    in_degree, context, skipped, pruned, satisfied,
                    forEach_node_id=node_id,
                )
            elif node_data.get("nodeCategory") == "logic" and node_label in {"Loop", "While"}:
                # NODES-01.b — While reuses Loop's iteration runner.
                # The handler returns the same {continueExpression,
                # maxIterations} shape regardless of label.
                _run_loop_iterations(
                    db, instance, nodes_map, forward, reverse,
                    in_degree, context, skipped, pruned, satisfied,
                    loop_node_id=node_id,
                )
            else:
                _propagate_edges(node_id, forward, nodes_map, context, satisfied, pruned)
                # CYCLIC-01.b — evaluate loopbacks after forward
                # edges propagate. Loopback firing mutates context
                # (clears cycle body), satisfied (un-satisfies
                # internal-to-cycle edges), and pruned (un-prunes
                # previously-dead branches so they re-evaluate on
                # the next iteration's condition output).
                _fire_loopbacks(
                    db, instance, node_id, loopbacks_by_source,
                    nodes_map, forward, context, satisfied, pruned,
                )

            if instance.status in ("cancelled", "paused"):
                return
        else:
            results = _execute_parallel(
                db, instance, nodes_map, ready, context,
                deterministic_mode=deterministic_mode,
            )
            for node_id, result in results.items():
                if result == "suspended":
                    instance.context_json = _get_clean_context(context)
                    db.commit()
                    return
                if result == "failed":
                    return
            for node_id in ready:
                if results.get(node_id) == "completed":
                    _propagate_edges(node_id, forward, nodes_map, context, satisfied, pruned)
                    # CYCLIC-01.b — same loopback dispatch for the
                    # parallel branch. Loopback sources should
                    # rarely end up in a parallel batch (usually
                    # a Condition is the source), but cover it.
                    _fire_loopbacks(
                        db, instance, node_id, loopbacks_by_source,
                        nodes_map, forward, context, satisfied, pruned,
                    )

            if _abort_if_cancel_or_pause(db, instance, context):
                return

        ready = _find_ready_nodes(
            nodes_map, reverse, satisfied, context, skipped, pruned,
        )

    all_executed = set(k for k in context if k.startswith("node_") or k == "trigger")
    non_pruned = set(nodes_map.keys()) - pruned - skipped
    trigger_nodes = {nid for nid, n in nodes_map.items() if n.get("data", {}).get("nodeCategory") == "trigger"}
    expected = (non_pruned - trigger_nodes) | {nid for nid in trigger_nodes if nid in context}

    if not any(
        db.query(ExecutionLog).filter_by(instance_id=instance.id, status="failed").first()
        for _ in [1]
    ):
        instance.status = "completed"
    instance.context_json = _get_clean_context(context)
    instance.completed_at = _utcnow()
    db.commit()
    logger.info("Workflow %s completed (pruned %d nodes)", instance.id, len(pruned))


def _propagate_edges(
    node_id: str,
    forward: dict[str, list[_Edge]],
    nodes_map: dict,
    context: dict[str, Any],
    satisfied: dict[str, set[str]],
    pruned: set[str],
) -> None:
    """After a node completes, mark downstream edges as satisfied and prune
    edges that don't match a condition branch."""
    node_output = context.get(node_id, {})
    node_data = nodes_map.get(node_id, {}).get("data", {})
    # NODES-01.a — both Condition and Switch route by ``branch`` in
    # their output dict. Generalising the check keeps the pruning
    # logic in one place: Condition yields "true" / "false", Switch
    # yields the matched case value (or "default"). Any future
    # branch-style node just needs to set ``is_branch_node`` logic
    # here and populate ``branch`` in its handler output.
    label = node_data.get("label")
    is_branch_node = (
        node_data.get("nodeCategory") == "logic"
        and label in {"Condition", "Switch"}
    )

    chosen_branch = None
    if is_branch_node and isinstance(node_output, dict):
        chosen_branch = node_output.get("branch")

    for edge in forward.get(node_id, []):
        if is_branch_node and chosen_branch is not None:
            if edge.source_handle is not None and edge.source_handle != chosen_branch:
                _prune_subtree(edge.target, forward, pruned)
                continue

        satisfied[edge.target].add(node_id)


def _prune_subtree(
    node_id: str,
    forward: dict[str, list[_Edge]],
    pruned: set[str],
) -> None:
    """Mark a node and all its downstream-only descendants as pruned (skipped)."""
    if node_id in pruned:
        return
    pruned.add(node_id)
    for edge in forward.get(node_id, []):
        _prune_subtree(edge.target, forward, pruned)


# ---------------------------------------------------------------------------
# CYCLIC-01.b — loopback edge execution
# ---------------------------------------------------------------------------


def _build_loopback_map(edges: list[_Edge]) -> dict[str, list[_Edge]]:
    """source_node_id → list of loopback edges originating there. Built
    once per execution; O(1) lookup when a node completes so the
    per-node overhead when loopbacks are empty is a single dict miss.
    """
    out: dict[str, list[_Edge]] = defaultdict(list)
    for edge in edges:
        if edge.is_loopback:
            out[edge.source].append(edge)
    return dict(out)


def _compute_cycle_body(
    target: str,
    source: str,
    forward: dict[str, list[_Edge]],
) -> set[str]:
    """Nodes that belong to the cycle a ``source→target`` loopback
    closes. Computed as ``forward_descendants(target) ∩ ({source} ∪
    forward_ancestors(source))`` over the forward subgraph — every
    node that sits on at least one forward path from target to
    source, inclusive of both endpoints.

    Worked example — a diamond with tail cycling back to head::

        A → B → D          loopback: D → A
          ↘ C ↗

        forward_descendants(A) = {A, B, C, D}
        forward_ancestors(D) ∪ {D} = {A, B, C, D}
        cycle_body = {A, B, C, D}   ← the whole diamond re-fires
    """
    # Forward reachable from target (includes target).
    descendants: set[str] = set()
    stack = [target]
    while stack:
        nid = stack.pop()
        if nid in descendants:
            continue
        descendants.add(nid)
        for e in forward.get(nid, []):
            stack.append(e.target)

    # Reverse reachable from source (includes source). Building a
    # reverse adjacency inline keeps this function self-contained and
    # avoids forcing callers to pre-compute it.
    reverse_adj: dict[str, list[str]] = defaultdict(list)
    for src_id, out_edges in forward.items():
        for e in out_edges:
            reverse_adj[e.target].append(src_id)
    ancestors: set[str] = set()
    stack = [source]
    while stack:
        nid = stack.pop()
        if nid in ancestors:
            continue
        ancestors.add(nid)
        for src_id in reverse_adj.get(nid, []):
            stack.append(src_id)

    return descendants & ancestors


def _should_fire_loopback(
    edge: _Edge,
    source_node_data: dict,
    source_output: Any,
) -> bool:
    """Decide whether a loopback edge's continue predicate fires.

    Two gates, in order:

    1. If the source is a Condition and the edge has a
       ``source_handle``, the edge fires iff the Condition's
       chosen branch matches the handle. This is the same gating
       used by ``_propagate_edges`` for forward edges — it lets
       authors write "if still_thinking → loop back to planner"
       as a true/false branch without needing a separate
       continue-expression API.
    2. Otherwise the edge fires unconditionally. A future slice
       can add a dedicated ``continueExpression`` field if authors
       need expression-level gating on non-Condition sources.
    """
    is_branch_node = (
        source_node_data.get("nodeCategory") == "logic"
        and source_node_data.get("label") in {"Condition", "Switch"}
    )
    if is_branch_node and edge.source_handle is not None:
        chosen = source_output.get("branch") if isinstance(source_output, dict) else None
        return chosen == edge.source_handle
    # No source-handle routing — fire unconditionally.
    return True


def _fire_loopbacks(
    db: Session,
    instance: WorkflowInstance,
    node_id: str,
    loopbacks_by_source: dict[str, list[_Edge]],
    nodes_map: dict,
    forward: dict[str, list[_Edge]],
    context: dict[str, Any],
    satisfied: dict[str, set[str]],
    pruned: set[str],
) -> bool:
    """Evaluate every loopback edge originating at ``node_id``.

    For each edge whose continue predicate fires AND whose
    per-cycle iteration counter is under ``max_iterations``:

    * Increment the counter in ``context["_cycle_iterations"]``.
    * Write a ``loopback_iteration`` ExecutionLog row on the
      loopback target so the debug UI can show
      "iteration 1 → 2 → …".
    * Clear every cycle-body node's output from ``context`` and
      drop its satisfaction bookkeeping for edges internal to the
      cycle, so the next ``_find_ready_nodes`` call finds the
      target fresh.

    Returns ``True`` if at least one loopback fired (the caller
    must re-scan ``_find_ready_nodes`` before deciding the
    workflow is done).
    """
    loopbacks = loopbacks_by_source.get(node_id)
    if not loopbacks:
        return False

    source_node = nodes_map.get(node_id, {})
    source_data = source_node.get("data", {}) or {}
    source_output = context.get(node_id, {})

    fired_any = False
    for edge in loopbacks:
        if not _should_fire_loopback(edge, source_data, source_output):
            continue

        # Per-cycle iteration counter — under _runtime so it survives
        # suspend/resume (HITL inside a cyclic body must NOT reset
        # the counter). CTX-MGMT.D.
        counters = _get_runtime(context).setdefault("cycle_iterations", {})
        current = int(counters.get(edge.id, 0))
        cap = edge.max_iterations or LOOPBACK_DEFAULT_MAX_ITERATIONS
        cap = min(cap, LOOPBACK_HARD_CAP)  # defence-in-depth

        if current >= cap:
            # Cap hit — fall through to forward edges. Log an
            # audit row so the UI can say "loopback capped after
            # N iterations" instead of silently continuing.
            _log_loopback_cap_hit(db, instance, edge, current, cap)
            logger.info(
                "Workflow %s: loopback edge %s hit cap %d — continuing forward",
                instance.id, edge.id, cap,
            )
            continue

        counters[edge.id] = current + 1
        logger.info(
            "Workflow %s: loopback edge %s firing iteration %d/%d (%s→%s)",
            instance.id, edge.id, current + 1, cap, edge.source, edge.target,
        )
        _log_loopback_iteration(db, instance, edge, current + 1, cap)

        cycle_body = _compute_cycle_body(edge.target, edge.source, forward)

        # Clear cycle-body node outputs from context — "un-execute"
        # them so _find_ready_nodes sees them as pending again.
        for nid in cycle_body:
            context.pop(nid, None)
            # Pruned status is cycle-local: re-entering the cycle
            # should let previously pruned branches re-evaluate
            # fresh against the new iteration's condition output.
            pruned.discard(nid)

        # Un-prune exit subtrees too. When a Condition inside the
        # cycle chose the loopback branch on iteration N, its
        # ``_propagate_edges`` pruned the exit-branch subtree —
        # which then stays pruned across subsequent iterations,
        # so the cycle can never actually exit cleanly even if a
        # later iteration's Condition chooses the exit branch. By
        # un-pruning every forward-reachable node from any
        # cycle-body source that isn't itself in the body, we let
        # the next iteration's ``_propagate_edges`` make the
        # decision fresh.
        exit_frontier: list[str] = []
        for src_id in cycle_body:
            for e in forward.get(src_id, []):
                if e.target not in cycle_body:
                    # Drop any prior satisfaction from this
                    # cycle-body source, so the exit doesn't look
                    # spuriously satisfied when the stale source
                    # is already cleared from context.
                    satisfied.get(e.target, set()).discard(e.source)
                    if e.target in pruned:
                        exit_frontier.append(e.target)
        seen_unprune: set[str] = set()
        while exit_frontier:
            cur = exit_frontier.pop()
            if cur in seen_unprune:
                continue
            seen_unprune.add(cur)
            pruned.discard(cur)
            for e in forward.get(cur, []):
                if e.target not in cycle_body:
                    exit_frontier.append(e.target)

        # Un-satisfy forward edges internal to the cycle so
        # downstream cycle nodes wait for their upstream cycle
        # nodes to re-fire. Edges crossing the cycle boundary
        # (in from outside, or out to outside) stay satisfied —
        # those upstream sources are still executed.
        for src_id in cycle_body:
            for e in forward.get(src_id, []):
                if e.target in cycle_body:
                    satisfied.get(e.target, set()).discard(e.source)

        fired_any = True

    if fired_any:
        db.commit()
    return fired_any


def _log_loopback_iteration(
    db: Session,
    instance: WorkflowInstance,
    edge: _Edge,
    iteration: int,
    cap: int,
) -> None:
    """Write a ``loopback_iteration`` ExecutionLog row on the
    loopback target so the debug UI can show per-iteration progress
    alongside the per-node logs.
    """
    log = ExecutionLog(
        instance_id=instance.id,
        node_id=edge.target,
        node_type="loopback",
        status="loopback_iteration",
        input_json=None,
        output_json={
            "edge_id": edge.id,
            "source_node_id": edge.source,
            "iteration": iteration,
            "max_iterations": cap,
        },
        error=None,
        started_at=_utcnow(),
        completed_at=_utcnow(),
    )
    db.add(log)


def _log_loopback_cap_hit(
    db: Session,
    instance: WorkflowInstance,
    edge: _Edge,
    iterations_used: int,
    cap: int,
) -> None:
    """Same shape as the iteration log but ``status=
    loopback_cap_reached``. Lets the debug UI render "stopped
    looping after N passes" inline with the other node logs.
    """
    log = ExecutionLog(
        instance_id=instance.id,
        node_id=edge.target,
        node_type="loopback",
        status="loopback_cap_reached",
        input_json=None,
        output_json={
            "edge_id": edge.id,
            "source_node_id": edge.source,
            "iterations_used": iterations_used,
            "max_iterations": cap,
        },
        error=None,
        started_at=_utcnow(),
        completed_at=_utcnow(),
    )
    db.add(log)


def _is_waitany_merge(node: dict) -> bool:
    """CTX-MGMT.E — Merge node with ``strategy: waitAny`` fires when
    ANY active upstream source is satisfied (vs the default waitAll
    where all must be satisfied). The right primitive for fan-in
    after a Switch / Condition: only one branch arm fires, so a
    fan-in node expecting ALL arms would otherwise wait forever.

    Already-existing registry entry (`shared/node_registry.json`
    line ~356, type=merge, label=Merge) had `strategy` enum
    ``waitAll | waitAny`` but the waitAny path was never
    implemented. CTX-MGMT.E wires it up.
    """
    data = node.get("data") or {}
    if (data.get("nodeCategory") or "") != "logic":
        return False
    if (data.get("label") or "") != "Merge":
        return False
    config = data.get("config") or {}
    strategy = (config.get("strategy") or "waitAll").lower()
    return strategy == "waitany"


def _find_ready_nodes(
    nodes_map: dict,
    reverse: dict[str, list[_Edge]],
    satisfied: dict[str, set[str]],
    context: dict[str, Any],
    skipped: set[str],
    pruned: set[str],
) -> list[str]:
    """Find nodes whose incoming edges are satisfied per the node's
    ready-check policy and that haven't been executed yet.

    Default policy (waitAll): every active (non-pruned) incoming
    source must be satisfied. CTX-MGMT.E adds waitAny support for
    Merge nodes — fires when ANY active source is satisfied.
    """
    ready = []
    executed = set(context.keys())

    for nid in nodes_map:
        if nid in executed or nid in skipped or nid in pruned:
            continue

        incoming = reverse.get(nid, [])
        if not incoming:
            continue

        active_sources = {
            e.source for e in incoming if e.source not in pruned
        }
        if not active_sources:
            ready.append(nid)
            continue

        sat = satisfied.get(nid, set())
        if _is_waitany_merge(nodes_map.get(nid, {})):
            # Any-of: fire on the first satisfied source.
            if active_sources & sat:
                ready.append(nid)
        else:
            # All-of: every active source must be satisfied.
            if active_sources <= sat:
                ready.append(nid)

    return ready


# ---------------------------------------------------------------------------
# Single-node execution
# ---------------------------------------------------------------------------

def _execute_single_node(
    db: Session,
    instance: WorkflowInstance,
    nodes_map: dict,
    node_id: str,
    context: dict[str, Any],
) -> str:
    """Execute one node. Returns 'completed', 'suspended', or 'failed'."""
    from app.observability import span_node

    node = nodes_map[node_id]
    node_data: dict = node.get("data", {})
    node_category: str = node_data.get("nodeCategory", "action")
    node_label: str = node_data.get("label", "")

    log_entry = ExecutionLog(
        instance_id=instance.id,
        node_id=node_id,
        node_type=f"{node_data.get('nodeCategory', 'unknown')}:{node_label}",
        status="running",
        input_json=_build_node_input(node_data, context),
        started_at=_utcnow(),
    )
    db.add(log_entry)
    instance.current_node_id = node_id
    db.commit()

    trace = context.get("_trace")

    if node_category == "action" and node_data.get("config", {}).get("approvalMessage") is not None:
        if "approval" not in context:
            instance.status = "suspended"
            # HITL-01.b — stamp the moment we transition to suspended
            # so the pending-approvals dashboard can show age and
            # HITL-01.c's sweep can compute timeout elapsed.
            instance.suspended_at = datetime.now(timezone.utc)
            instance.context_json = _get_clean_context(context)
            log_entry.status = "suspended"
            db.commit()
            logger.info("Workflow %s suspended at node %s for human approval", instance.id, node_id)
            return "suspended"

    # Expose node_id so _handle_agent can route streaming tokens correctly.
    # Safe here — _execute_single_node is always sequential.
    context["_current_node_id"] = node_id

    with span_node(
        trace,
        node_id=node_id,
        node_type=f"{node_category}:{node_label}",
        node_label=node_label,
        input_data=_build_node_input(node_data, context),
    ) as span:
        try:
            output = dispatch_node(node_data, context, instance.tenant_id, db=db)

            # CTX-MGMT.F — write-time secret scrub. Runs FIRST in the
            # post-handler pipeline so every downstream step (schema
            # validation, overflow artifact, reducer, alias, trace,
            # compaction) sees the scrubbed value. Key-based:
            # `password`/`token`/`api_key`/etc. whole-value redacted.
            # Tenant-policy opt-out via context_secret_scrub_enabled
            # (default ON). The scrubber is pure/functional — original
            # `output` reference unchanged.
            if _get_runtime(context).get("context_secret_scrub_enabled", True):
                output = scrub_secrets(output)

            # CTX-MGMT.I — validate handler output against the node's
            # declared `outputSchema` (JSON Schema, optional). Soft by
            # default: failures stamp `_schema_mismatch` on the
            # output and log a warning. Authors opt into strict-fail
            # via `outputSchemaStrict: true`. Empty/missing schema =
            # no-op (one dict lookup of cost).
            from app.engine.output_schema import (
                annotate_output_with_validation,
                OutputSchemaError,
                validate_node_output,
            )
            _node_cfg = node_data.get("config") or {}
            _output_schema = _node_cfg.get("outputSchema")
            if _output_schema:
                _is_valid, _schema_errs = validate_node_output(output, _output_schema)
                if not _is_valid:
                    if bool(_node_cfg.get("outputSchemaStrict")):
                        raise OutputSchemaError(
                            f"Node {node_id}: outputSchema validation failed — "
                            + "; ".join(_schema_errs[:3])
                        )
                    logger.warning(
                        "Node %s output failed schema validation: %s",
                        node_id, _schema_errs[:3],
                    )
                    output = annotate_output_with_validation(
                        output, is_valid=False, errors=_schema_errs,
                    )

            # CTX-MGMT.A — per-node output budget. If the handler's
            # output exceeds the configured budget, persist the full
            # payload to node_output_artifacts and replace the in-
            # context value with a small stub. Downstream Jinja can
            # still read top-level scalar keys via the stub's preview;
            # the copilot can fetch the full output via the
            # inspect_node_artifact runner tool.
            from app.engine.output_artifact import maybe_overflow
            in_context_value, overflow_meta = maybe_overflow(
                db,
                tenant_id=instance.tenant_id,
                instance_id=instance.id,
                node_id=node_id,
                node_data=node_data,
                output=output,
            )
            # CTX-MGMT.L — per-node reducer determines how successive
            # writes to context[node_id] combine. Default `overwrite`
            # = last-write-wins (current behavior); other reducers
            # (`append`, `merge`, `max`, `min`, `counter`) enable
            # parallel-branch aggregation, audit trails, counters
            # without ad-hoc merge logic in handlers.
            from app.engine.reducers import apply_reducer, resolve_reducer
            reducer_name = resolve_reducer(node_data)
            context[node_id] = apply_reducer(
                reducer_name, context.get(node_id), in_context_value,
            )
            # CTX-MGMT.C — exposeAs alias. When the node config
            # specifies `data.config.exposeAs: "<alias>"`, also write
            # the same value under that alias key so downstream Jinja
            # can read it via a semantic name (`{{ case.id }}` rather
            # than `{{ node_4r.id }}`). Default unset = no alias =
            # current behavior. Engine intentionally writes BOTH the
            # canonical node_id slot AND the alias — backward-
            # compatible with templates that already reference the
            # node id directly.
            _expose_as = (node_data.get("config") or {}).get("exposeAs")
            if isinstance(_expose_as, str) and _expose_as.strip():
                context[_expose_as.strip()] = context[node_id]
            # CTX-MGMT.H — record the write into instance_context_trace
            # if tracing is on for this instance (ephemeral or tenant-
            # opted-in). Fast no-op when disabled. Failure is logged
            # but never raises — observability isn't a correctness gate.
            from app.engine.context_trace import record_write
            record_write(
                db, context,
                tenant_id=instance.tenant_id,
                instance_id=instance.id,
                node_id=node_id,
                size_bytes=(overflow_meta.get("size_bytes") if overflow_meta else None),
                reducer=reducer_name,
                overflowed=bool(overflow_meta),
            )
            # CTX-MGMT.K — track per-node size approximation + run
            # compaction pass if cumulative context exceeds threshold.
            # Both calls fast-no-op when compaction is disabled or
            # we're under threshold.
            from app.engine.compaction import (
                estimate_output_size,
                maybe_compact,
                track_write_size,
            )
            written_size = estimate_output_size(context[node_id])
            ctx_runtime = _get_runtime(context)
            track_write_size(ctx_runtime, node_id, written_size)
            maybe_compact(
                db, context,
                tenant_id=instance.tenant_id,
                instance_id=instance.id,
            )
            # _promote_orchestrator_user_reply still reads the full
            # output (not the stub or reduced value) — Bridge nodes
            # are designed to produce small replies, but if a Bridge
            # somehow exceeded budget the user still sees the original
            # reply text in the promoted root key.
            _promote_orchestrator_user_reply(context, output)

            log_entry.status = "completed"
            # ExecutionLog records what's IN context (the stub on
            # overflow) — the artifact is the canonical full record.
            log_entry.output_json = scrub_secrets(in_context_value)
            log_entry.completed_at = _utcnow()
            db.commit()
            checkpoint_id = _save_checkpoint(db, instance.id, node_id, context)
            span_meta: dict = {"status": "completed", "has_output": output is not None}
            if checkpoint_id:
                span_meta["checkpoint_id"] = checkpoint_id
            if overflow_meta:
                span_meta.update(overflow_meta)
            span.update(output=span_meta)
            return "completed"

        except NodeSuspendedAsync as sus:
            # Handler has already persisted an async_jobs row. Mark the
            # instance suspended with an async_external reason so the UI
            # can distinguish this from HITL and the Beat poller / webhook
            # can resume it later.
            log_entry.status = "suspended"
            log_entry.output_json = {
                "async_job_id": sus.async_job_id,
                "system": sus.system,
                "external_job_id": sus.external_job_id,
            }
            log_entry.completed_at = _utcnow()

            instance.status = "suspended"
            # HITL-tool gate: when a tool returns AWAITING_APPROVAL the
            # ReAct loop raises NodeSuspendedAsync(system="human_approval").
            # Treat that the same as a Human Approval node suspension —
            # leave suspended_reason NULL so it shows up in the pending-
            # approvals dashboard. Only true async-external suspensions
            # (webhook-driven, AE callback, etc.) get the
            # async_external tag.
            instance.suspended_reason = (
                None if sus.system == "human_approval" else "async_external"
            )
            # HITL-01.b — same age-stamping as the HITL path so
            # the pending-approvals dashboard + 01.c timeout sweep
            # treat async suspensions the same way.
            instance.suspended_at = datetime.now(timezone.utc)
            instance.context_json = _get_clean_context(context)
            db.commit()
            span.update(output={
                "status": "suspended",
                "reason": "async_external",
                "system": sus.system,
                "external_job_id": sus.external_job_id,
            })
            logger.info(
                "Node %s in workflow %s suspended on %s job %s",
                node_id, instance.id, sus.system, sus.external_job_id,
            )
            return "suspended"

        except Exception as exc:
            log_entry.status = "failed"
            log_entry.error = str(exc)
            log_entry.completed_at = _utcnow()

            instance.status = "failed"
            instance.context_json = _get_clean_context(context)
            instance.completed_at = _utcnow()
            db.commit()
            span.update(output={"status": "failed", "error": str(exc)})
            logger.exception("Node %s failed in workflow %s", node_id, instance.id)
            return "failed"


# ---------------------------------------------------------------------------
# Parallel execution
# ---------------------------------------------------------------------------

def _execute_parallel(
    db: Session,
    instance: WorkflowInstance,
    nodes_map: dict,
    ready_nodes: list[str],
    context: dict[str, Any],
    deterministic_mode: bool = False,
) -> dict[str, str]:
    """Execute multiple independent nodes concurrently.

    Each thread gets its own DB session for writing ExecutionLog entries.
    The shared `context` dict is written to thread-safely since each node
    writes to a unique key (its own node_id).

    V0.9 (Component 7): Trace object is passed explicitly via context["_trace"]
    so Langfuse spans are correctly stitched even in worker threads.

    V0.9.3 deterministic_mode: when True, nodes are submitted and their results
    processed in stable sorted node-ID order, giving fully reproducible log
    sequences regardless of which thread finishes first.  When False (default),
    the original as_completed ordering is used for maximum throughput.
    """
    results: dict[str, str] = {}

    # Deterministic mode: fix the processing order up-front
    ordered_nodes = sorted(ready_nodes) if deterministic_mode else list(ready_nodes)

    log_entries: dict[str, ExecutionLog] = {}
    for node_id in ordered_nodes:
        node = nodes_map[node_id]
        node_data = node.get("data", {})
        log_entry = ExecutionLog(
            instance_id=instance.id,
            node_id=node_id,
            node_type=f"{node_data.get('nodeCategory', 'unknown')}:{node_data.get('label', '')}",
            status="running",
            input_json=_build_node_input(node_data, context),
            started_at=_utcnow(),
        )
        db.add(log_entry)
        log_entries[node_id] = log_entry
    instance.current_node_id = ordered_nodes[0]
    db.commit()

    # Capture the trace for explicit propagation into threads (Component 7)
    trace = context.get("_trace")

    def _run_node(node_id: str) -> tuple[str, str, dict | None, str | None]:
        node = nodes_map[node_id]
        node_data = node.get("data", {})
        node_category = node_data.get("nodeCategory", "action")
        node_label = node_data.get("label", "")

        if node_category == "action" and node_data.get("config", {}).get("approvalMessage") is not None:
            if "approval" not in context:
                return node_id, "suspended", None, None

        # V0.9 (Component 7): Use explicit trace object from context
        from app.observability import span_node
        from app.database import set_tenant_context
        thread_db = SessionLocal()
        try:
            set_tenant_context(thread_db, instance.tenant_id)
            with span_node(
                trace,
                node_id=node_id,
                node_type=f"{node_category}:{node_label}",
                node_label=node_label,
                input_data=_build_node_input(node_data, context),
            ) as span:
                try:
                    output = dispatch_node(node_data, context, instance.tenant_id, db=thread_db)
                    span.update(output={"status": "completed", "has_output": output is not None})
                    return node_id, "completed", output, None
                except Exception as exc:
                    logger.exception("Node %s failed in workflow %s", node_id, instance.id)
                    span.update(output={"status": "failed", "error": str(exc)})
                    return node_id, "failed", None, str(exc)
        finally:
            thread_db.close()

    def _apply_result(node_id: str, status: str, output: dict | None, error: str | None) -> None:
        results[node_id] = status
        log_entry = log_entries[node_id]
        if status == "completed" and output is not None:
            # CTX-MGMT.F — write-time secret scrub on the parallel
            # path too. Same FIRST-in-pipeline ordering so every
            # downstream step (schema, overflow, reducer, alias,
            # trace, compaction) sees the scrubbed value.
            if _get_runtime(context).get("context_secret_scrub_enabled", True):
                output = scrub_secrets(output)

            # CTX-MGMT.I — schema validation on the parallel path too.
            from app.engine.output_schema import (
                annotate_output_with_validation,
                OutputSchemaError,
                validate_node_output,
            )
            _node_lookup = nodes_map.get(node_id) or {}
            _node_data_p = _node_lookup.get("data") or {}
            _node_cfg_p = _node_data_p.get("config") or {}
            _output_schema_p = _node_cfg_p.get("outputSchema")
            if _output_schema_p:
                _is_valid_p, _errs_p = validate_node_output(output, _output_schema_p)
                if not _is_valid_p:
                    if bool(_node_cfg_p.get("outputSchemaStrict")):
                        # Strict-fail on parallel path: mark this branch failed.
                        results[node_id] = "failed"
                        log_entry.status = "failed"
                        log_entry.error = (
                            f"outputSchema validation failed: {'; '.join(_errs_p[:3])}"
                        )
                        log_entry.completed_at = _utcnow()
                        return
                    logger.warning(
                        "Node %s output failed schema validation: %s",
                        node_id, _errs_p[:3],
                    )
                    output = annotate_output_with_validation(
                        output, is_valid=False, errors=_errs_p,
                    )

            # CTX-MGMT.A — overflow check on the parallel-branch path
            # too. Same shape as the sequential path; the artifact
            # write happens on the main session because the worker
            # thread's session has already closed.
            from app.engine.output_artifact import maybe_overflow
            node_lookup = nodes_map.get(node_id) or {}
            node_data_for_budget = node_lookup.get("data") or {}
            in_context_value, overflow_meta = maybe_overflow(
                db,
                tenant_id=instance.tenant_id,
                instance_id=instance.id,
                node_id=node_id,
                node_data=node_data_for_budget,
                output=output,
            )
            # CTX-MGMT.L — apply the configured reducer on the
            # parallel-branch path. Same default-overwrite semantics
            # so existing graphs are unaffected.
            from app.engine.reducers import apply_reducer, resolve_reducer
            reducer_name = resolve_reducer(node_data_for_budget)
            context[node_id] = apply_reducer(
                reducer_name, context.get(node_id), in_context_value,
            )
            # CTX-MGMT.C — exposeAs alias on the parallel path too.
            _expose_as_p = (node_data_for_budget.get("config") or {}).get("exposeAs")
            if isinstance(_expose_as_p, str) and _expose_as_p.strip():
                context[_expose_as_p.strip()] = context[node_id]
            # CTX-MGMT.H — record the write on the parallel path too.
            from app.engine.context_trace import record_write
            record_write(
                db, context,
                tenant_id=instance.tenant_id,
                instance_id=instance.id,
                node_id=node_id,
                size_bytes=(overflow_meta.get("size_bytes") if overflow_meta else None),
                reducer=reducer_name,
                overflowed=bool(overflow_meta),
            )
            # CTX-MGMT.K — same compaction pass on the parallel path.
            from app.engine.compaction import (
                estimate_output_size,
                maybe_compact,
                track_write_size,
            )
            written_size_p = estimate_output_size(context[node_id])
            track_write_size(_get_runtime(context), node_id, written_size_p)
            maybe_compact(
                db, context,
                tenant_id=instance.tenant_id,
                instance_id=instance.id,
            )
            _promote_orchestrator_user_reply(context, output)
            log_entry.status = "completed"
            log_entry.completed_at = _utcnow()
            checkpoint_id = _save_checkpoint(db, instance.id, node_id, context)
            # Embed checkpoint_id in the log output so it is queryable via the
            # execution log API even though the Langfuse span has already closed.
            scrubbed = scrub_secrets(in_context_value)
            log_payload: dict[str, Any] = scrubbed if isinstance(scrubbed, dict) else {"value": scrubbed}
            if checkpoint_id:
                log_payload = {**log_payload, "_checkpoint_id": checkpoint_id}
            if overflow_meta:
                log_payload = {**log_payload, **overflow_meta}
            log_entry.output_json = log_payload
        elif status == "suspended":
            log_entry.status = "suspended"
            instance.status = "suspended"
            # HITL-01.b — third suspend path (handler returned
            # "suspended" directly, e.g. the HITL approval handler).
            # Stamp the timestamp here too so every suspend path is
            # symmetric for the dashboard + timeout sweep.
            if instance.suspended_at is None:
                instance.suspended_at = datetime.now(timezone.utc)
            instance.context_json = _get_clean_context(context)
        elif status == "failed":
            log_entry.status = "failed"
            log_entry.error = error
            log_entry.completed_at = _utcnow()
            instance.status = "failed"
            instance.context_json = _get_clean_context(context)
            instance.completed_at = _utcnow()

    with ThreadPoolExecutor(max_workers=min(len(ready_nodes), _MAX_PARALLEL)) as pool:
        if deterministic_mode:
            # Submit in sorted order; block on each future in submission order so
            # log writes are stable regardless of which thread finishes first.
            futures_ordered = [(nid, pool.submit(_run_node, nid)) for nid in ordered_nodes]
            for nid, future in futures_ordered:
                node_id, status, output, error = future.result()
                _apply_result(node_id, status, output, error)
        else:
            # Default: process results as they complete for maximum throughput.
            futures = {pool.submit(_run_node, nid): nid for nid in ready_nodes}
            for future in as_completed(futures):
                node_id, status, output, error = future.result()
                _apply_result(node_id, status, output, error)

    db.commit()
    return results


# ---------------------------------------------------------------------------
# ForEach iteration (V0.9 — Component 1)
# ---------------------------------------------------------------------------

def _run_forEach_iterations(
    db: Session,
    instance: WorkflowInstance,
    nodes_map: dict,
    forward: dict[str, list[_Edge]],
    reverse: dict[str, list[_Edge]],
    in_degree: dict[str, int],
    context: dict[str, Any],
    skipped: set[str],
    pruned: set[str],
    satisfied: dict[str, set[str]],
    forEach_node_id: str,
) -> None:
    """Execute downstream nodes of a ForEach node once per array item.

    For each item in the ForEach output's 'items' list, injects the item
    into the context and runs all immediately-downstream nodes sequentially.
    Results from each iteration are collected into a list.
    """
    forEach_output = context.get(forEach_node_id, {})
    items = forEach_output.get("items", [])
    item_var = forEach_output.get("itemVariable", "item")

    if not items:
        # No items — just propagate as normal to satisfy edges
        _propagate_edges(forEach_node_id, forward, nodes_map, context, satisfied, pruned)
        return

    # Collect downstream node IDs
    downstream_edges = forward.get(forEach_node_id, [])
    downstream_node_ids = [e.target for e in downstream_edges]

    # Collect all iteration results
    all_iteration_results: dict[str, list] = {nid: [] for nid in downstream_node_ids}

    # CTX-MGMT.D — loop counters live under _runtime so they survive
    # suspend/resume. If a downstream node suspends on HITL, the iteration
    # index is preserved and the resumed run picks up at the same item.
    runtime = _get_runtime(context)
    # On resume, pick up where we left off — previous _execute_ran ForEach
    # may have suspended part-way through the items list.
    start_idx = int(runtime.get("loop_index", -1)) + 1 if runtime.get("loop_item_var") == item_var else 0
    if start_idx < 0 or start_idx >= len(items):
        start_idx = 0

    for idx in range(start_idx, len(items)):
        item = items[idx]
        if _abort_if_cancel_or_pause(db, instance, context):
            return

        # Inject current loop item into context
        runtime["loop_item"] = item
        runtime["loop_item_var"] = item_var
        context[item_var] = item
        runtime["loop_index"] = idx

        for downstream_nid in downstream_node_ids:
            # Clear any previous iteration output
            context.pop(downstream_nid, None)

            result = _execute_single_node(
                db, instance, nodes_map, downstream_nid, context,
            )

            if result == "completed":
                iteration_output = context.get(downstream_nid)
                all_iteration_results[downstream_nid].append(iteration_output)
            elif result == "failed":
                all_iteration_results[downstream_nid].append({"error": "failed", "iteration": idx})
            elif result == "suspended":
                return  # Stop the forEach loop if any iteration suspends

            if _abort_if_cancel_or_pause(db, instance, context):
                return

    # Store aggregated results
    for nid in downstream_node_ids:
        context[nid] = {"forEach_results": all_iteration_results[nid], "iterations": len(items)}

    # Clean up loop context (CTX-MGMT.D — under _runtime now).
    runtime.pop("loop_item", None)
    runtime.pop("loop_item_var", None)
    runtime.pop("loop_index", None)

    # Propagate edges from the forEach node AND from downstream nodes
    _propagate_edges(forEach_node_id, forward, nodes_map, context, satisfied, pruned)
    for nid in downstream_node_ids:
        satisfied[nid] = set()
        _propagate_edges(nid, forward, nodes_map, context, satisfied, pruned)

    logger.info(
        "ForEach node %s completed: %d iterations across %d downstream nodes",
        forEach_node_id, len(items), len(downstream_node_ids),
    )


# ---------------------------------------------------------------------------
# Loop iteration (V0.9.9)
# ---------------------------------------------------------------------------

def _run_loop_iterations(
    db: Session,
    instance: WorkflowInstance,
    nodes_map: dict,
    forward: dict[str, list[_Edge]],
    reverse: dict[str, list[_Edge]],
    in_degree: dict[str, int],
    context: dict[str, Any],
    skipped: set[str],
    pruned: set[str],
    satisfied: dict[str, set[str]],
    loop_node_id: str,
) -> None:
    """Execute downstream body nodes repeatedly while a condition holds.

    Uses pre-check semantics: ``continueExpression`` is evaluated before each
    iteration.  If it returns False on the first check the body never executes.
    An empty expression is treated as always-True (run for ``maxIterations``).

    After the loop, each body node's context key is overwritten with::

        {"loop_results": [<iter-0-output>, ...], "iterations": N}

    Downstream nodes reference these aggregated results the same way they
    would any other node output.
    """
    from app.engine.safe_eval import safe_eval, SafeEvalError

    loop_output = context.get(loop_node_id, {})
    continue_expr: str = loop_output.get("continueExpression", "")
    max_iterations: int = min(int(loop_output.get("maxIterations", 10)), 25)

    downstream_edges = forward.get(loop_node_id, [])
    downstream_node_ids = [e.target for e in downstream_edges]

    if not downstream_node_ids:
        _propagate_edges(loop_node_id, forward, nodes_map, context, satisfied, pruned)
        return

    def _eval_condition(idx: int) -> bool:
        if not continue_expr:
            return True  # No guard — run unconditionally up to max_iterations
        upstream = {k: v for k, v in context.items() if k.startswith("node_")}
        # Expose the loop counter to the user-supplied expression. The
        # underscore-prefixed names are the legacy public API surface;
        # we keep them for back-compat with existing continueExpression
        # strings even though the canonical storage is `_runtime`.
        eval_env: dict = {
            "output": upstream,
            "context": context,
            "trigger": context.get("trigger", {}),
            "_loop_index": idx,
            "_loop_iteration": idx + 1,
        }
        eval_env.update(upstream)
        try:
            return bool(safe_eval(continue_expr, eval_env))
        except SafeEvalError as exc:
            logger.warning("Loop continueExpression rejected by safe_eval: %s", exc)
            return False
        except Exception as exc:
            logger.warning("Loop continueExpression evaluation error: %s", exc)
            return False

    all_iteration_results: dict[str, list] = {nid: [] for nid in downstream_node_ids}
    actual_iterations = 0

    # CTX-MGMT.D — loop counters live under _runtime so they survive
    # suspend/resume. On resume after HITL inside a Loop body, pick
    # up at the iteration we left off. The marker `loop_node_id`
    # disambiguates so a different loop's lingering counter doesn't
    # bleed across loop nodes.
    runtime = _get_runtime(context)
    if runtime.get("loop_node_id") == loop_node_id:
        start_idx = int(runtime.get("loop_index", -1)) + 1
    else:
        start_idx = 0
    if start_idx < 0 or start_idx >= max_iterations:
        start_idx = 0
    runtime["loop_node_id"] = loop_node_id

    for idx in range(start_idx, max_iterations):
        if _abort_if_cancel_or_pause(db, instance, context):
            return

        # Pre-check: evaluate condition before executing the body
        if not _eval_condition(idx):
            break

        runtime["loop_index"] = idx
        runtime["loop_iteration"] = idx + 1

        # Clear previous iteration outputs so nodes re-execute cleanly
        for body_nid in downstream_node_ids:
            context.pop(body_nid, None)

        failed = False
        for body_nid in downstream_node_ids:
            result = _execute_single_node(db, instance, nodes_map, body_nid, context)

            if result == "completed":
                all_iteration_results[body_nid].append(context.get(body_nid))
            elif result == "failed":
                all_iteration_results[body_nid].append({"error": "failed", "iteration": idx})
                failed = True
                break  # Stop body execution for this iteration
            elif result == "suspended":
                # Persist aggregated results so far before suspending —
                # _runtime keeps loop_index so the resumed run resumes
                # at the right iteration.
                for nid in downstream_node_ids:
                    context[nid] = {
                        "loop_results": all_iteration_results[nid],
                        "iterations": idx,
                    }
                return  # Suspend propagates via instance status

            if _abort_if_cancel_or_pause(db, instance, context):
                return

        actual_iterations = idx + 1

        if failed:
            # Store partial results and exit — instance already marked failed
            for nid in downstream_node_ids:
                context[nid] = {
                    "loop_results": all_iteration_results[nid],
                    "iterations": actual_iterations,
                }
            runtime.pop("loop_index", None)
            runtime.pop("loop_iteration", None)
            runtime.pop("loop_node_id", None)
            _propagate_edges(loop_node_id, forward, nodes_map, context, satisfied, pruned)
            for nid in downstream_node_ids:
                satisfied[nid] = set()
                _propagate_edges(nid, forward, nodes_map, context, satisfied, pruned)
            return

    # Store aggregated results under each body node's key
    for nid in downstream_node_ids:
        context[nid] = {
            "loop_results": all_iteration_results[nid],
            "iterations": actual_iterations,
        }

    # Clean up loop-scoped runtime markers — Loop is done.
    runtime.pop("loop_index", None)
    runtime.pop("loop_iteration", None)
    runtime.pop("loop_node_id", None)

    # Propagate edges from Loop node and from body nodes so downstream proceeds
    _propagate_edges(loop_node_id, forward, nodes_map, context, satisfied, pruned)
    for nid in downstream_node_ids:
        satisfied[nid] = set()
        _propagate_edges(nid, forward, nodes_map, context, satisfied, pruned)

    logger.info(
        "Loop node %s completed: %d/%d iterations, %d body nodes",
        loop_node_id, actual_iterations, max_iterations, len(downstream_node_ids),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _promote_orchestrator_user_reply(
    context: dict[str, Any], output: dict[str, Any] | None
) -> None:
    """Expose Bridge User Reply output at context root for external callers.

    ``InstanceContextOut.context_json`` strips ``_*`` keys only, so this key is
    visible to polling clients without scraping individual node outputs.
    """
    if not isinstance(output, dict):
        return
    text = output.get("orchestrator_user_reply")
    if isinstance(text, str) and text.strip():
        context["orchestrator_user_reply"] = text.strip()


def _build_node_input(node_data: dict, context: dict[str, Any]) -> dict:
    """Build the (scrubbed) input payload for log storage / Langfuse spans.

    This does NOT feed the node handler — the handler receives the raw
    ``context`` directly. The result is intended strictly for logging, so
    we redact any values whose key looks like a secret before returning.
    """
    node_input = {
        "config": node_data.get("config", {}),
        "upstream_outputs": {
            k: v for k, v in context.items() if k.startswith("node_")
        },
        "trigger": context.get("trigger"),
    }

    # Include loop item if inside a ForEach iteration. CTX-MGMT.D — loop
    # counters live under `_runtime`; check there first, then fall back
    # to the legacy flat keys for any in-flight context that hasn't been
    # hoisted yet (defence in depth — the entry-point hoist normally
    # handles this).
    runtime = context.get("_runtime") or {}
    if "loop_item" in runtime:
        node_input["loop_item"] = runtime["loop_item"]
        node_input["loop_index"] = runtime.get("loop_index", 0)
        node_input["loop_variable"] = runtime.get("loop_item_var", "item")
    elif "_loop_item" in context:
        node_input["loop_item"] = context["_loop_item"]
        node_input["loop_index"] = context.get("_loop_index", 0)
        node_input["loop_variable"] = context.get("_loop_item_var", "item")
    # Include loop index/iteration if inside a Loop iteration (Loop sets
    # loop_iteration; ForEach does not).
    elif "loop_iteration" in runtime:
        node_input["loop_index"] = runtime.get("loop_index", 0)
        node_input["loop_iteration"] = runtime["loop_iteration"]
    elif "_loop_iteration" in context:
        node_input["loop_index"] = context["_loop_index"]
        node_input["loop_iteration"] = context["_loop_iteration"]

    return scrub_secrets(node_input)


def _save_checkpoint(
    db: Session, instance_id: Any, node_id: str, context: dict[str, Any]
) -> str | None:
    """Persist a context snapshot immediately after a node succeeds.

    Strips internal runtime keys (prefixed with '_') before storage so
    the snapshot contains only user-visible data.  Failures here are
    non-fatal — a warning is logged and execution continues.

    Returns:
        The checkpoint UUID as a string, or None if the write failed.
    """
    try:
        clean_context = {k: v for k, v in context.items() if not k.startswith("_")}
        checkpoint = InstanceCheckpoint(
            instance_id=instance_id,
            node_id=node_id,
            context_json=clean_context,
            saved_at=_utcnow(),
        )
        db.add(checkpoint)
        db.commit()
        logger.debug("Checkpoint saved: instance=%s node=%s id=%s", instance_id, node_id, checkpoint.id)
        return str(checkpoint.id)
    except Exception as exc:
        logger.warning(
            "Failed to save checkpoint for instance=%s node=%s: %s",
            instance_id, node_id, exc,
        )
        db.rollback()
        return None

