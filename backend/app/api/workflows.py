"""Workflow CRUD and execution endpoints."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.database import SessionLocal, get_db, set_tenant_context
from app.security.tenant import get_tenant_id
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowSnapshot, ExecutionLog, InstanceCheckpoint
from app.api.schemas import (
    WorkflowCreate,
    WorkflowUpdate,
    WorkflowOut,
    ExecuteRequest,
    CallbackRequest,
    RetryRequest,
    ResumePausedRequest,
    InstanceOut,
    SyncExecuteOut,
    InstanceDetailOut,
    InstanceContextOut,
    ExecutionLogOut,
    SnapshotOut,
    GraphAtVersionOut,
    CheckpointOut,
    CheckpointDetailOut,
    ChildInstanceSummary,
)

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


def _utcnow():
    return datetime.now(timezone.utc)


def _resolve_graph_json_for_version(
    db: Session,
    wf: WorkflowDefinition,
    tenant_id: str,
    version: int | None,
    *,
    strict: bool,
) -> dict:
    """Resolve the graph JSON for a definition version.

    When ``strict`` is False and the historical snapshot is unavailable, fall back
    to the live definition graph instead of failing the request.
    """
    if version is None or version == wf.version:
        return wf.graph_json

    if version < 1 or version > wf.version:
        if strict:
            raise HTTPException(
                404,
                f"No graph stored for definition version {version} (current is {wf.version})",
            )
        return wf.graph_json

    snap = (
        db.query(WorkflowSnapshot)
        .filter_by(workflow_def_id=wf.id, tenant_id=tenant_id, version=version)
        .first()
    )
    if not snap:
        if strict:
            raise HTTPException(
                404,
                f"Snapshot for definition version {version} not found",
            )
        return wf.graph_json
    return snap.graph_json


# ---------------------------------------------------------------------------
# Workflow Definition CRUD
# ---------------------------------------------------------------------------

@router.post("", response_model=WorkflowOut, status_code=201)
def create_workflow(
    body: WorkflowCreate,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    import logging
    _log = logging.getLogger(__name__)

    from app.engine.config_validator import validate_graph_configs
    warnings = validate_graph_configs(body.graph_json)
    if warnings:
        _log.warning("Graph config warnings on create: %s", warnings)

    from app.engine.embedding_cache_helper import precompute_node_embeddings
    emb_warnings = precompute_node_embeddings(body.graph_json, tenant_id, db)
    if emb_warnings:
        _log.warning("Embedding precompute warnings on create: %s", emb_warnings)

    wf = WorkflowDefinition(
        tenant_id=tenant_id,
        name=body.name,
        description=body.description,
        graph_json=body.graph_json,
    )
    db.add(wf)
    db.commit()
    db.refresh(wf)
    return wf


@router.get("", response_model=list[WorkflowOut])
def list_workflows(
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    return (
        db.query(WorkflowDefinition)
        .filter_by(tenant_id=tenant_id)
        .order_by(WorkflowDefinition.updated_at.desc())
        .all()
    )


@router.get("/{workflow_id}", response_model=WorkflowOut)
def get_workflow(
    workflow_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    wf = (
        db.query(WorkflowDefinition)
        .filter_by(id=workflow_id, tenant_id=tenant_id)
        .first()
    )
    if not wf:
        raise HTTPException(404, "Workflow not found")
    return wf


@router.patch("/{workflow_id}", response_model=WorkflowOut)
def update_workflow(
    workflow_id: uuid.UUID,
    body: WorkflowUpdate,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    wf = (
        db.query(WorkflowDefinition)
        .filter_by(id=workflow_id, tenant_id=tenant_id)
        .first()
    )
    if not wf:
        raise HTTPException(404, "Workflow not found")

    if body.name is not None:
        wf.name = body.name
    if body.description is not None:
        wf.description = body.description
    if body.graph_json is not None:
        import logging
        _log = logging.getLogger(__name__)

        from app.engine.config_validator import validate_graph_configs
        warnings = validate_graph_configs(body.graph_json)
        if warnings:
            _log.warning("Graph config warnings on update: %s", warnings)

        from app.engine.embedding_cache_helper import precompute_node_embeddings
        emb_warnings = precompute_node_embeddings(body.graph_json, tenant_id, db)
        if emb_warnings:
            _log.warning("Embedding precompute warnings on update: %s", emb_warnings)

        # Save snapshot of current version before overwriting
        snap = WorkflowSnapshot(
            workflow_def_id=wf.id,
            tenant_id=tenant_id,
            version=wf.version,
            graph_json=wf.graph_json,
        )
        db.add(snap)
        wf.graph_json = body.graph_json
        wf.version += 1

    db.commit()
    db.refresh(wf)
    return wf


@router.delete("/{workflow_id}", status_code=204)
def delete_workflow(
    workflow_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    wf = (
        db.query(WorkflowDefinition)
        .filter_by(id=workflow_id, tenant_id=tenant_id)
        .first()
    )
    if not wf:
        raise HTTPException(404, "Workflow not found")
    db.delete(wf)
    db.commit()


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

@router.post("/{workflow_id}/execute")
async def execute_workflow(
    workflow_id: uuid.UUID,
    body: ExecuteRequest,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    from app.security.rate_limiter import check_execution_quota
    check_execution_quota(db, tenant_id)

    wf = (
        db.query(WorkflowDefinition)
        .filter_by(id=workflow_id, tenant_id=tenant_id)
        .first()
    )
    if not wf:
        raise HTTPException(404, "Workflow not found")

    instance = WorkflowInstance(
        tenant_id=tenant_id,
        workflow_def_id=wf.id,
        trigger_payload=body.trigger_payload,
        status="queued",
        definition_version_at_start=wf.version,
    )
    db.add(instance)
    db.commit()
    db.refresh(instance)

    if body.sync:
        from app.engine.dag_runner import execute_graph

        instance_id_str = str(instance.id)

        def _sync_run() -> WorkflowInstance:
            session = SessionLocal()
            try:
                set_tenant_context(session, tenant_id)
                execute_graph(session, instance_id_str, body.deterministic_mode)
                inst = (
                    session.query(WorkflowInstance)
                    .filter_by(id=instance.id)
                    .first()
                )
                if not inst:
                    raise RuntimeError(f"Instance {instance_id_str} missing after sync run")
                return inst
            finally:
                session.close()

        try:
            fresh = await asyncio.wait_for(
                run_in_threadpool(_sync_run),
                timeout=float(body.sync_timeout),
            )
        except asyncio.TimeoutError:
            return JSONResponse(
                status_code=504,
                content={
                    "detail": (
                        f"Synchronous execution exceeded sync_timeout={body.sync_timeout}s. "
                        "The run may still be completing in the background."
                    ),
                    "instance_id": str(instance.id),
                    "hint": f"Poll GET …/instances/{instance.id} to track the orphaned run.",
                },
            )

        output = {
            k: v
            for k, v in (fresh.context_json or {}).items()
            if not str(k).startswith("_")
        }
        payload = SyncExecuteOut(
            instance_id=fresh.id,
            status=fresh.status,
            started_at=fresh.started_at,
            completed_at=fresh.completed_at,
            output=output,
        )
        return JSONResponse(
            status_code=200,
            content=payload.model_dump(mode="json"),
        )

    from app.workers.tasks import execute_workflow_task
    execute_workflow_task.delay(tenant_id, str(instance.id), body.deterministic_mode)

    return JSONResponse(
        status_code=202,
        content=InstanceOut.model_validate(instance).model_dump(mode="json"),
    )


@router.post("/{workflow_id}/instances/{instance_id}/callback", response_model=InstanceOut)
def callback_workflow(
    workflow_id: uuid.UUID,
    instance_id: uuid.UUID,
    body: CallbackRequest,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    instance = (
        db.query(WorkflowInstance)
        .filter_by(id=instance_id, workflow_def_id=workflow_id, tenant_id=tenant_id, status="suspended")
        .first()
    )
    if not instance:
        raise HTTPException(404, f"Suspended instance {instance_id} not found for this workflow")

    from app.workers.tasks import resume_workflow_task
    resume_workflow_task.delay(tenant_id, str(instance.id), body.approval_payload, body.context_patch)

    instance.status = "running"
    db.commit()
    db.refresh(instance)
    return instance


@router.post("/{workflow_id}/instances/{instance_id}/retry", response_model=InstanceOut)
def retry_workflow(
    workflow_id: uuid.UUID,
    instance_id: uuid.UUID,
    body: RetryRequest = RetryRequest(),
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """Retry a failed workflow instance from the point of failure.

    Optionally accepts a `from_node_id` to retry from a specific node
    instead of the most recently failed one.
    """
    instance = (
        db.query(WorkflowInstance)
        .filter_by(
            id=instance_id,
            workflow_def_id=workflow_id,
            tenant_id=tenant_id,
            status="failed",
        )
        .first()
    )
    if not instance:
        raise HTTPException(404, "No failed instance found for this workflow")

    from app.workers.tasks import retry_workflow_task
    retry_workflow_task.delay(tenant_id, str(instance.id), body.from_node_id)

    instance.status = "running"
    db.commit()
    db.refresh(instance)
    return instance


@router.post("/{workflow_id}/instances/{instance_id}/pause", response_model=InstanceOut)
def pause_workflow(
    workflow_id: uuid.UUID,
    instance_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """Request cooperative pause: the runner stops after the current node finishes."""
    instance = (
        db.query(WorkflowInstance)
        .filter_by(
            id=instance_id,
            workflow_def_id=workflow_id,
            tenant_id=tenant_id,
        )
        .first()
    )
    if not instance:
        raise HTTPException(404, "Instance not found")
    if instance.status not in ("queued", "running"):
        raise HTTPException(
            409,
            f"Cannot pause instance in status {instance.status!r} (only queued or running)",
        )
    instance.pause_requested = True
    db.commit()
    db.refresh(instance)
    return instance


@router.post("/{workflow_id}/instances/{instance_id}/resume-paused", response_model=InstanceOut)
def resume_paused_workflow(
    workflow_id: uuid.UUID,
    instance_id: uuid.UUID,
    body: ResumePausedRequest = ResumePausedRequest(),
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """Resume a workflow that was paused between nodes (not HITL ``suspended``)."""
    instance = (
        db.query(WorkflowInstance)
        .filter_by(
            id=instance_id,
            workflow_def_id=workflow_id,
            tenant_id=tenant_id,
            status="paused",
        )
        .first()
    )
    if not instance:
        raise HTTPException(
            404,
            f"Paused instance {instance_id} not found for this workflow",
        )

    from app.workers.tasks import resume_paused_workflow_task

    resume_paused_workflow_task.delay(tenant_id, str(instance.id), body.context_patch)

    instance.status = "running"
    db.commit()
    db.refresh(instance)
    return instance


@router.post("/{workflow_id}/instances/{instance_id}/cancel", response_model=InstanceOut)
def cancel_workflow(
    workflow_id: uuid.UUID,
    instance_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """Request cooperative cancellation: the runner stops after the current node finishes.

    For ``queued`` or ``running``, sets ``cancel_requested``. For ``paused``, abandons the run immediately.
    """
    instance = (
        db.query(WorkflowInstance)
        .filter_by(
            id=instance_id,
            workflow_def_id=workflow_id,
            tenant_id=tenant_id,
        )
        .first()
    )
    if not instance:
        raise HTTPException(404, "Instance not found")
    if instance.status == "paused":
        instance.status = "cancelled"
        instance.cancel_requested = False
        instance.pause_requested = False
        instance.completed_at = _utcnow()
        db.commit()
        db.refresh(instance)
        return instance
    if instance.status not in ("queued", "running"):
        raise HTTPException(
            409,
            f"Cannot cancel instance in status {instance.status!r}",
        )
    instance.cancel_requested = True
    db.commit()
    db.refresh(instance)
    return instance


# ---------------------------------------------------------------------------
# Status / Logs
# ---------------------------------------------------------------------------

@router.get("/{workflow_id}/status", response_model=list[InstanceOut])
def list_instances(
    workflow_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    return (
        db.query(WorkflowInstance)
        .filter_by(workflow_def_id=workflow_id, tenant_id=tenant_id)
        .order_by(WorkflowInstance.created_at.desc())
        .limit(50)
        .all()
    )


@router.get("/{workflow_id}/versions", response_model=list[SnapshotOut])
def list_versions(
    workflow_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """List all saved snapshots for a workflow (excludes graph_json for performance)."""
    wf = (
        db.query(WorkflowDefinition)
        .filter_by(id=workflow_id, tenant_id=tenant_id)
        .first()
    )
    if not wf:
        raise HTTPException(404, "Workflow not found")

    return (
        db.query(WorkflowSnapshot)
        .filter_by(workflow_def_id=workflow_id, tenant_id=tenant_id)
        .order_by(WorkflowSnapshot.version.desc())
        .limit(50)
        .all()
    )


@router.get(
    "/{workflow_id}/graph-at-version/{version}",
    response_model=GraphAtVersionOut,
)
def get_graph_at_definition_version(
    workflow_id: uuid.UUID,
    version: int,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """Return the workflow graph as it existed at a given definition version.

    For the **current** ``WorkflowDefinition.version``, returns the live ``graph_json``.
    For older versions, returns the immutable row from ``workflow_snapshots`` (the graph
    that was replaced when the definition moved to ``version + 1``).
    """
    wf = (
        db.query(WorkflowDefinition)
        .filter_by(id=workflow_id, tenant_id=tenant_id)
        .first()
    )
    if not wf:
        raise HTTPException(404, "Workflow not found")

    graph_json = _resolve_graph_json_for_version(
        db,
        wf,
        tenant_id,
        version,
        strict=True,
    )
    return GraphAtVersionOut(version=version, graph_json=graph_json)


@router.post("/{workflow_id}/rollback/{version}", response_model=WorkflowOut)
def rollback_version(
    workflow_id: uuid.UUID,
    version: int,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """Restore a workflow to a previously saved snapshot version.

    Creates a new snapshot of the current state before restoring,
    then increments the version number (rollback is a forward operation).
    """
    wf = (
        db.query(WorkflowDefinition)
        .filter_by(id=workflow_id, tenant_id=tenant_id)
        .first()
    )
    if not wf:
        raise HTTPException(404, "Workflow not found")

    snap = (
        db.query(WorkflowSnapshot)
        .filter_by(workflow_def_id=workflow_id, tenant_id=tenant_id, version=version)
        .first()
    )
    if not snap:
        raise HTTPException(404, f"Snapshot for version {version} not found")

    # Save current state as a snapshot before restoring
    current_snap = WorkflowSnapshot(
        workflow_def_id=wf.id,
        tenant_id=tenant_id,
        version=wf.version,
        graph_json=wf.graph_json,
    )
    db.add(current_snap)

    wf.graph_json = snap.graph_json
    wf.version += 1

    db.commit()
    db.refresh(wf)
    return wf


@router.get("/{workflow_id}/instances/{instance_id}/context", response_model=InstanceContextOut)
def get_instance_context(
    workflow_id: uuid.UUID,
    instance_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """Return the current execution context snapshot for HITL review.

    Strips internal runtime keys (prefixed with '_') before returning.
    When the instance is suspended, also extracts the approvalMessage from
    the suspended node's config so the UI can surface it to the operator.
    """
    instance = (
        db.query(WorkflowInstance)
        .filter_by(id=instance_id, workflow_def_id=workflow_id, tenant_id=tenant_id)
        .first()
    )
    if not instance:
        raise HTTPException(404, "Instance not found")

    # Extract approvalMessage from the suspended node's config
    approval_message: str | None = None
    if instance.current_node_id and instance.status == "suspended":
        graph = _resolve_graph_json_for_version(
            db,
            instance.definition,
            tenant_id,
            instance.definition_version_at_start,
            strict=False,
        )
        node = next(
            (n for n in graph.get("nodes", []) if n.get("id") == instance.current_node_id),
            None,
        )
        if node:
            approval_message = node.get("data", {}).get("config", {}).get("approvalMessage")

    # Strip internal runtime keys before exposing to the operator
    context_json = {
        k: v
        for k, v in (instance.context_json or {}).items()
        if not k.startswith("_")
    }

    return InstanceContextOut(
        instance_id=instance.id,
        status=instance.status,
        current_node_id=instance.current_node_id,
        approval_message=approval_message,
        context_json=context_json,
    )


@router.get(
    "/{workflow_id}/instances/{instance_id}/checkpoints",
    response_model=list[CheckpointOut],
)
def list_checkpoints(
    workflow_id: uuid.UUID,
    instance_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """List all per-node checkpoints for an execution instance.

    Returns one entry per successfully completed node, in chronological
    order.  Context payloads are omitted for brevity — use the detail
    endpoint to retrieve the full snapshot for a specific checkpoint.
    """
    instance = (
        db.query(WorkflowInstance)
        .filter_by(id=instance_id, workflow_def_id=workflow_id, tenant_id=tenant_id)
        .first()
    )
    if not instance:
        raise HTTPException(404, "Instance not found")

    checkpoints = (
        db.query(InstanceCheckpoint)
        .filter_by(instance_id=instance_id)
        .order_by(InstanceCheckpoint.saved_at)
        .all()
    )
    return [CheckpointOut.model_validate(cp) for cp in checkpoints]


@router.get(
    "/{workflow_id}/instances/{instance_id}/checkpoints/{checkpoint_id}",
    response_model=CheckpointDetailOut,
)
def get_checkpoint_detail(
    workflow_id: uuid.UUID,
    instance_id: uuid.UUID,
    checkpoint_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """Return a specific checkpoint with its full context snapshot.

    The context_json represents the execution context immediately after
    the node identified by node_id completed.  Internal runtime keys
    (prefixed with '_') are stripped before returning.
    """
    instance = (
        db.query(WorkflowInstance)
        .filter_by(id=instance_id, workflow_def_id=workflow_id, tenant_id=tenant_id)
        .first()
    )
    if not instance:
        raise HTTPException(404, "Instance not found")

    checkpoint = (
        db.query(InstanceCheckpoint)
        .filter_by(id=checkpoint_id, instance_id=instance_id)
        .first()
    )
    if not checkpoint:
        raise HTTPException(404, "Checkpoint not found")

    return CheckpointDetailOut(
        id=checkpoint.id,
        instance_id=checkpoint.instance_id,
        node_id=checkpoint.node_id,
        saved_at=checkpoint.saved_at,
        context_json=checkpoint.context_json,
    )


@router.get("/{workflow_id}/instances/{instance_id}", response_model=InstanceDetailOut)
def get_instance_detail(
    workflow_id: uuid.UUID,
    instance_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    instance = (
        db.query(WorkflowInstance)
        .filter_by(id=instance_id, workflow_def_id=workflow_id, tenant_id=tenant_id)
        .first()
    )
    if not instance:
        raise HTTPException(404, "Instance not found")

    logs = (
        db.query(ExecutionLog)
        .filter_by(instance_id=instance_id)
        .order_by(ExecutionLog.started_at)
        .all()
    )

    # Fetch child sub-workflow instances
    child_instances = (
        db.query(WorkflowInstance)
        .filter(WorkflowInstance.parent_instance_id == instance.id)
        .order_by(WorkflowInstance.created_at)
        .all()
    )
    children_out = []
    for child in child_instances:
        wf_name = None
        if child.definition:
            wf_name = child.definition.name
        children_out.append(ChildInstanceSummary(
            id=child.id,
            workflow_def_id=child.workflow_def_id,
            workflow_name=wf_name,
            parent_node_id=child.parent_node_id,
            status=child.status,
            started_at=child.started_at,
            completed_at=child.completed_at,
        ))

    return InstanceDetailOut(
        **{c.name: getattr(instance, c.name) for c in instance.__table__.columns},
        logs=[ExecutionLogOut.model_validate(log) for log in logs],
        children=children_out,
    )
