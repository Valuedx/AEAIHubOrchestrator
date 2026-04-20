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
    """Strip internal runtime keys (prefixed with '_') before DB storage."""
    return {k: v for k, v in context.items() if not k.startswith("_")}


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
    __slots__ = ("source", "target", "source_handle")

    def __init__(self, source: str, target: str, source_handle: str | None):
        self.source = source
        self.target = target
        self.source_handle = source_handle


def parse_graph(graph_json: dict) -> tuple[dict, list[_Edge]]:
    """Convert React Flow JSON into execution-friendly structures.

    Returns:
        nodes_map: {node_id: node_dict}
        edges:     list of _Edge with sourceHandle info
    """
    nodes_list: list[dict] = graph_json.get("nodes", [])
    edges_list: list[dict] = graph_json.get("edges", [])

    nodes_map: dict[str, dict] = {n["id"]: n for n in nodes_list}
    edges = [
        _Edge(
            source=e["source"],
            target=e["target"],
            source_handle=e.get("sourceHandle"),
        )
        for e in edges_list
    ]
    return nodes_map, edges


def _build_graph_structures(
    nodes_map: dict, edges: list[_Edge]
) -> tuple[dict[str, list[_Edge]], dict[str, list[_Edge]], dict[str, int]]:
    """Build forward adjacency, reverse adjacency, and in-degree maps."""
    forward: dict[str, list[_Edge]] = defaultdict(list)
    reverse: dict[str, list[_Edge]] = defaultdict(list)
    in_degree: dict[str, int] = {nid: 0 for nid in nodes_map}

    for edge in edges:
        forward[edge.source].append(edge)
        reverse[edge.target].append(edge)
        in_degree[edge.target] = in_degree.get(edge.target, 0) + 1

    return dict(forward), dict(reverse), in_degree


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
        if instance.trigger_payload:
            ctx_early["trigger"] = instance.trigger_payload
        _finalize_cancelled(db, instance, _get_clean_context(ctx_early))
        return
    if instance.pause_requested:
        ctx_pause: dict[str, Any] = dict(instance.context_json or {})
        if instance.trigger_payload:
            ctx_pause["trigger"] = instance.trigger_payload
        _finalize_paused(db, instance, _get_clean_context(ctx_pause))
        return

    graph = instance.definition.graph_json
    nodes_map, edges = parse_graph(graph)
    forward, reverse, in_degree = _build_graph_structures(nodes_map, edges)
    _detect_cycles(nodes_map, forward, in_degree)

    context: dict[str, Any] = dict(instance.context_json or {})
    if instance.trigger_payload:
        context["trigger"] = instance.trigger_payload
    # Expose instance_id so node handlers can route LLM tokens to the right Redis channel
    context["_instance_id"] = str(instance.id)
    context["_workflow_def_id"] = str(instance.workflow_def_id)

    # Sub-workflow recursion detection: build the parent chain if not already
    # present (child instances have _parent_chain pre-set in their context_json).
    if "_parent_chain" not in context:
        parent_chain: list[str] = []
        ancestor = instance
        while ancestor.parent_instance_id:
            ancestor = db.query(WorkflowInstance).filter_by(id=ancestor.parent_instance_id).first()
            if not ancestor:
                break
            parent_chain.append(str(ancestor.workflow_def_id))
        context["_parent_chain"] = parent_chain

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
    db.commit()

    graph = instance.definition.graph_json
    nodes_map, edges = parse_graph(graph)
    forward, reverse, in_degree = _build_graph_structures(nodes_map, edges)

    context: dict[str, Any] = dict(instance.context_json or {})
    context["approval"] = approval_payload

    # Apply operator-supplied context overrides (HITL context patch)
    if context_patch:
        context.update(context_patch)
        logger.info(
            "Workflow %s resumed with context_patch covering keys: %s",
            instance_id, list(context_patch.keys()),
        )

    already_executed = set(context.keys()) - {"trigger", "approval"}
    _execute_ready_queue(
        db, instance, nodes_map, forward, reverse, in_degree, context,
        skipped=already_executed,
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
) -> None:
    """Process nodes in ready-order, respecting condition branches and
    running independent nodes in parallel."""

    satisfied: dict[str, set[str]] = defaultdict(set)
    pruned: set[str] = set()

    ready: list[str] = []
    for nid, deg in in_degree.items():
        if deg == 0 and nid not in skipped:
            ready.append(nid)
        elif nid in skipped:
            satisfied[nid] = set()
            _propagate_edges(nid, forward, nodes_map, context, satisfied, pruned)

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
            and nodes_map.get(nid, {}).get("data", {}).get("label") in ("ForEach", "Loop")
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
            elif node_data.get("nodeCategory") == "logic" and node_label == "Loop":
                _run_loop_iterations(
                    db, instance, nodes_map, forward, reverse,
                    in_degree, context, skipped, pruned, satisfied,
                    loop_node_id=node_id,
                )
            else:
                _propagate_edges(node_id, forward, nodes_map, context, satisfied, pruned)

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
    is_condition = (
        node_data.get("nodeCategory") == "logic"
        and node_data.get("label") == "Condition"
    )

    chosen_branch = None
    if is_condition and isinstance(node_output, dict):
        chosen_branch = node_output.get("branch")

    for edge in forward.get(node_id, []):
        if is_condition and chosen_branch is not None:
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


def _find_ready_nodes(
    nodes_map: dict,
    reverse: dict[str, list[_Edge]],
    satisfied: dict[str, set[str]],
    context: dict[str, Any],
    skipped: set[str],
    pruned: set[str],
) -> list[str]:
    """Find nodes whose all incoming (non-pruned) edges are satisfied
    and that haven't been executed yet."""
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

        if active_sources <= satisfied.get(nid, set()):
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
            context[node_id] = output
            _promote_orchestrator_user_reply(context, output)

            log_entry.status = "completed"
            log_entry.output_json = scrub_secrets(output)
            log_entry.completed_at = _utcnow()
            db.commit()
            checkpoint_id = _save_checkpoint(db, instance.id, node_id, context)
            span_meta: dict = {"status": "completed", "has_output": output is not None}
            if checkpoint_id:
                span_meta["checkpoint_id"] = checkpoint_id
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
            instance.suspended_reason = "async_external"
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
            context[node_id] = output
            _promote_orchestrator_user_reply(context, output)
            log_entry.status = "completed"
            log_entry.completed_at = _utcnow()
            checkpoint_id = _save_checkpoint(db, instance.id, node_id, context)
            # Embed checkpoint_id in the log output so it is queryable via the
            # execution log API even though the Langfuse span has already closed.
            scrubbed = scrub_secrets(output)
            log_entry.output_json = (
                {**(scrubbed or {}), "_checkpoint_id": checkpoint_id}
                if checkpoint_id
                else scrubbed
            )
        elif status == "suspended":
            log_entry.status = "suspended"
            instance.status = "suspended"
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

    for idx, item in enumerate(items):
        if _abort_if_cancel_or_pause(db, instance, context):
            return

        # Inject current loop item into context
        context["_loop_item"] = item
        context["_loop_item_var"] = item_var
        context[item_var] = item
        context["_loop_index"] = idx

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

    # Clean up loop context
    context.pop("_loop_item", None)
    context.pop("_loop_item_var", None)
    context.pop("_loop_index", None)

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

    for idx in range(max_iterations):
        if _abort_if_cancel_or_pause(db, instance, context):
            return

        # Pre-check: evaluate condition before executing the body
        if not _eval_condition(idx):
            break

        context["_loop_index"] = idx
        context["_loop_iteration"] = idx + 1

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
                # Persist aggregated results so far before suspending
                for nid in downstream_node_ids:
                    context[nid] = {
                        "loop_results": all_iteration_results[nid],
                        "iterations": idx,
                    }
                context.pop("_loop_index", None)
                context.pop("_loop_iteration", None)
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
            context.pop("_loop_index", None)
            context.pop("_loop_iteration", None)
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

    # Clean up loop-scoped context variables
    context.pop("_loop_index", None)
    context.pop("_loop_iteration", None)

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

    # Include loop item if inside a ForEach iteration
    if "_loop_item" in context:
        node_input["loop_item"] = context["_loop_item"]
        node_input["loop_index"] = context.get("_loop_index", 0)
        node_input["loop_variable"] = context.get("_loop_item_var", "item")
    # Include loop index/iteration if inside a Loop iteration (Loop sets _loop_iteration; ForEach does not)
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

