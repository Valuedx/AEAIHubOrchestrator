"""A2A (Agent-to-Agent) protocol surface.

Implements the Google A2A protocol v0.2 for the orchestrator, giving
external agents a standardised way to discover and invoke workflows.

Layout
------
Inbound A2A surface (called by external agents):

    GET  /tenants/{tenant_id}/.well-known/agent.json   — Agent card (no auth)
    POST /tenants/{tenant_id}/a2a                      — JSON-RPC 2.0 dispatcher

    Supported JSON-RPC methods:
        tasks/send            create a WorkflowInstance and return a Task object
        tasks/get             return the current Task object for an instance
        tasks/cancel          request cancellation of a running instance
        tasks/sendSubscribe   like tasks/send but streams SSE status updates

    Auth: Bearer token validated against a2a_api_keys.key_hash for that tenant.

Key management (called by the tenant using their normal API credentials):

    POST   /api/v1/a2a/keys          generate a new key (raw key shown once)
    GET    /api/v1/a2a/keys          list keys for the tenant (no key material)
    DELETE /api/v1/a2a/keys/{key_id} revoke a key

Workflow publishing (thin wrapper over WorkflowDefinition.is_published):

    PATCH  /api/v1/workflows/{workflow_id}/publish   set is_published = true/false

WorkflowInstance status → A2A Task state mapping
-------------------------------------------------
    queued    → submitted
    running   → working
    completed → completed
    suspended → input-required   (human_approval node waiting for callback)
    failed    → failed
    cancelled → canceled
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models.workflow import (
    A2AApiKey,
    WorkflowDefinition,
    WorkflowInstance,
)
from app.security.tenant import get_tenant_id
from app.api.schemas import (
    A2AApiKeyCreate,
    A2AApiKeyCreated,
    A2AApiKeyOut,
    A2AJsonRpcRequest,
    A2AJsonRpcResponse,
    A2ATask,
    A2ATaskStatus,
    A2AArtifact,
    A2AMessagePart,
    A2ASendParams,
    WorkflowPublishRequest,
    WorkflowOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["a2a"])

# ---------------------------------------------------------------------------
# State mapping
# ---------------------------------------------------------------------------

_STATUS_MAP: dict[str, str] = {
    "queued":    "submitted",
    "running":   "working",
    "completed": "completed",
    "suspended": "input-required",
    "failed":    "failed",
    "cancelled": "canceled",
    "paused":    "input-required",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Auth dependency for inbound A2A calls
# ---------------------------------------------------------------------------

def _get_a2a_tenant(
    tenant_id: str,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> str:
    """Validate the Bearer token against a2a_api_keys for the path tenant_id."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "A2A request requires a Bearer token")
    raw_key = authorization.removeprefix("Bearer ").strip()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    row = (
        db.query(A2AApiKey)
        .filter_by(tenant_id=tenant_id, key_hash=key_hash)
        .first()
    )
    if not row:
        raise HTTPException(401, "Invalid or revoked A2A API key")
    return tenant_id


# ---------------------------------------------------------------------------
# Task object helpers
# ---------------------------------------------------------------------------

def _instance_to_task(
    instance: WorkflowInstance,
    session_id: str | None = None,
) -> dict:
    """Convert a WorkflowInstance into an A2A Task dict."""
    state = _STATUS_MAP.get(instance.status, "working")
    timestamp = _utcnow().isoformat()

    status: dict = {"state": state, "timestamp": timestamp}

    # For input-required (suspended), surface the approval message if present
    if state == "input-required" and instance.context_json:
        approval_msg = instance.context_json.get("_approval_message")
        if approval_msg:
            status["message"] = {
                "role": "agent",
                "parts": [{"text": approval_msg}],
            }

    # For completed, surface the final LLM response from context
    artifacts = []
    if state == "completed" and instance.context_json:
        response_text = _extract_final_response(instance.context_json)
        if response_text:
            artifacts = [{"index": 0, "parts": [{"text": response_text}], "lastChunk": True}]

    return {
        "id": str(instance.id),
        "sessionId": session_id or str(instance.id),
        "status": status,
        "artifacts": artifacts,
    }


def _extract_final_response(context_json: dict) -> str:
    """Pull the last LLM agent response from the context dict."""
    # Walk node keys in reverse order to find the most recent LLM response
    node_keys = sorted(
        (k for k in context_json if k.startswith("node_")),
        reverse=True,
    )
    for key in node_keys:
        val = context_json[key]
        if isinstance(val, dict):
            text = val.get("response") or val.get("output")
            if isinstance(text, str) and text:
                return text
    return ""


# ---------------------------------------------------------------------------
# Agent card
# ---------------------------------------------------------------------------

@router.get("/tenants/{tenant_id}/.well-known/agent.json")
def agent_card(
    tenant_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Return the A2A agent card for a tenant.

    Lists all workflows the tenant has marked as is_published=True.
    No authentication required — agent cards are public discovery documents.
    """
    workflows = (
        db.query(WorkflowDefinition)
        .filter_by(tenant_id=tenant_id, is_published=True)
        .order_by(WorkflowDefinition.name)
        .all()
    )

    base_url = str(request.base_url).rstrip("/")
    agent_url = f"{base_url}/tenants/{tenant_id}/a2a"

    skills = [
        {
            "id": str(wf.id),
            "name": wf.name,
            "description": wf.description or "",
            "inputModes": ["text"],
            "outputModes": ["text"],
        }
        for wf in workflows
    ]

    return {
        "name": f"AE Orchestrator — {tenant_id}",
        "description": "Agentic workflow orchestration engine",
        "url": agent_url,
        "version": "1.0",
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        "skills": skills,
    }


# ---------------------------------------------------------------------------
# JSON-RPC dispatcher
# ---------------------------------------------------------------------------

@router.post("/tenants/{tenant_id}/a2a")
async def a2a_dispatcher(
    tenant_id: str,
    body: A2AJsonRpcRequest,
    request: Request,
    verified_tenant: str = Depends(_get_a2a_tenant),
    db: Session = Depends(get_db),
):
    """Single JSON-RPC 2.0 endpoint. Routes by body.method."""
    method = body.method
    params = body.params

    if method == "tasks/send":
        result = _tasks_send(params, tenant_id, db)
        return {"jsonrpc": "2.0", "id": body.id, "result": result}

    if method == "tasks/get":
        result = _tasks_get(params, tenant_id, db)
        return {"jsonrpc": "2.0", "id": body.id, "result": result}

    if method == "tasks/cancel":
        result = _tasks_cancel(params, tenant_id, db)
        return {"jsonrpc": "2.0", "id": body.id, "result": result}

    if method == "tasks/sendSubscribe":
        # Streaming — returns SSE, not JSON
        return await _tasks_send_subscribe(params, tenant_id, body.id, request, db)

    return {
        "jsonrpc": "2.0",
        "id": body.id,
        "error": {
            "code": -32601,
            "message": f"Method not found: {method}",
        },
    }


# ---------------------------------------------------------------------------
# tasks/send
# ---------------------------------------------------------------------------

def _tasks_send(params: dict, tenant_id: str, db: Session) -> dict:
    """Create a WorkflowInstance and return the initial Task object."""
    raw_skill_id = params.get("skillId") or params.get("skill_id")
    session_id   = params.get("sessionId") or params.get("session_id") or str(uuid.uuid4())
    message      = params.get("message") or {}

    if not raw_skill_id:
        raise HTTPException(400, "tasks/send requires a skillId")

    try:
        skill_uuid = uuid.UUID(str(raw_skill_id))
    except ValueError:
        raise HTTPException(400, f"skillId '{raw_skill_id}' is not a valid UUID")

    wf = (
        db.query(WorkflowDefinition)
        .filter_by(id=skill_uuid, tenant_id=tenant_id, is_published=True)
        .first()
    )
    if not wf:
        raise HTTPException(404, f"Skill '{raw_skill_id}' not found or not published")

    # Extract text from A2A message parts (guard against malformed message)
    parts = message.get("parts", []) if isinstance(message, dict) else []
    text = " ".join(
        p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")
    ).strip()

    trigger_payload = {
        "session_id": session_id,
        "message": text,
        "a2a": True,
    }
    # Propagate caller-supplied task id for idempotency tracking
    if params.get("id"):
        trigger_payload["a2a_task_id"] = params["id"]

    from app.security.rate_limiter import check_execution_quota
    check_execution_quota(db, tenant_id)

    instance = WorkflowInstance(
        tenant_id=tenant_id,
        workflow_def_id=wf.id,
        trigger_payload=trigger_payload,
        status="queued",
        definition_version_at_start=wf.version,
    )
    db.add(instance)
    db.commit()
    db.refresh(instance)

    from app.workers.tasks import execute_workflow_task
    execute_workflow_task.delay(str(instance.id))

    logger.info(
        "A2A tasks/send: tenant=%s skill=%s instance=%s session=%s",
        tenant_id, raw_skill_id, instance.id, session_id,
    )
    return _instance_to_task(instance, session_id)


# ---------------------------------------------------------------------------
# tasks/get
# ---------------------------------------------------------------------------

def _tasks_get(params: dict, tenant_id: str, db: Session) -> dict:
    """Return the current Task object for an existing instance."""
    raw_task_id = params.get("id")
    session_id  = params.get("sessionId") or params.get("session_id")

    if not raw_task_id:
        raise HTTPException(400, "tasks/get requires an id")

    try:
        task_uuid = uuid.UUID(str(raw_task_id))
    except ValueError:
        raise HTTPException(400, f"id '{raw_task_id}' is not a valid UUID")

    instance = (
        db.query(WorkflowInstance)
        .filter_by(id=task_uuid, tenant_id=tenant_id)
        .first()
    )
    if not instance:
        raise HTTPException(404, f"Task '{raw_task_id}' not found")

    return _instance_to_task(instance, session_id)


# ---------------------------------------------------------------------------
# tasks/cancel
# ---------------------------------------------------------------------------

def _tasks_cancel(params: dict, tenant_id: str, db: Session) -> dict:
    """Request cancellation of a running task."""
    raw_task_id = params.get("id")
    if not raw_task_id:
        raise HTTPException(400, "tasks/cancel requires an id")

    try:
        task_uuid = uuid.UUID(str(raw_task_id))
    except ValueError:
        raise HTTPException(400, f"id '{raw_task_id}' is not a valid UUID")

    instance = (
        db.query(WorkflowInstance)
        .filter_by(id=task_uuid, tenant_id=tenant_id)
        .first()
    )
    if not instance:
        raise HTTPException(404, f"Task '{raw_task_id}' not found")

    if instance.status in ("completed", "failed", "cancelled"):
        # Already terminal — return current state
        return _instance_to_task(instance)

    instance.cancel_requested = True
    db.commit()
    db.refresh(instance)

    logger.info("A2A tasks/cancel: task=%s tenant=%s", task_id, tenant_id)
    return _instance_to_task(instance)


# ---------------------------------------------------------------------------
# tasks/sendSubscribe (streaming)
# ---------------------------------------------------------------------------

async def _tasks_send_subscribe(
    params: dict,
    tenant_id: str,
    rpc_id: int | str | None,
    request: Request,
    db: Session,
) -> StreamingResponse:
    """Create an instance then stream A2A-formatted SSE status events."""
    # Reuse tasks/send logic to create the instance
    task = _tasks_send(params, tenant_id, db)
    # task["id"] is str(instance.id) — convert to UUID once for all poll queries
    instance_uuid = uuid.UUID(task["id"])
    session_id    = task.get("sessionId", task["id"])

    async def event_generator():
        # Yield the initial submitted state immediately
        yield _sse_task_event(task)

        last_status = task["status"]["state"]
        _TERMINAL = {"completed", "failed", "canceled", "input-required"}

        while True:
            if await request.is_disconnected():
                break

            await asyncio.sleep(1.0)

            poll_db = SessionLocal()
            try:
                inst = poll_db.query(WorkflowInstance).filter_by(id=instance_uuid).first()
                if not inst:
                    break
                current = _instance_to_task(inst, session_id)
                state = current["status"]["state"]

                if state != last_status:
                    last_status = state
                    yield _sse_task_event(current)

                if state in _TERMINAL:
                    # Emit artifact event for completed tasks
                    if state == "completed" and current.get("artifacts"):
                        yield _sse_artifact_event(instance_id, session_id, current["artifacts"])
                    break
            finally:
                poll_db.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse_task_event(task: dict) -> str:
    return f"event: task\ndata: {json.dumps(task)}\n\n"


def _sse_artifact_event(task_id: str, session_id: str, artifacts: list) -> str:
    payload = {
        "id": task_id,
        "sessionId": session_id,
        "artifact": artifacts[0] if artifacts else {},
    }
    return f"event: artifact\ndata: {json.dumps(payload)}\n\n"


# ---------------------------------------------------------------------------
# API key management  (uses standard tenant auth, not A2A auth)
# ---------------------------------------------------------------------------

_keys_router = APIRouter(prefix="/api/v1/a2a/keys", tags=["a2a-keys"])


@_keys_router.post("", response_model=A2AApiKeyCreated, status_code=201)
def create_api_key(
    body: A2AApiKeyCreate,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """Generate a new A2A inbound key.

    The raw key is returned exactly once and never stored.
    Share it with the external agent that will call this tenant's A2A surface.
    """
    # Check for duplicate label within this tenant
    existing = db.query(A2AApiKey).filter_by(tenant_id=tenant_id, label=body.label).first()
    if existing:
        raise HTTPException(409, f"A key named '{body.label}' already exists for this tenant")

    raw_key = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    row = A2AApiKey(
        tenant_id=tenant_id,
        label=body.label,
        key_hash=key_hash,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    logger.info("A2A: created key id=%s label=%r tenant=%s", row.id, body.label, tenant_id)
    return A2AApiKeyCreated(
        id=row.id,
        label=row.label,
        raw_key=raw_key,
        created_at=row.created_at,
    )


@_keys_router.get("", response_model=list[A2AApiKeyOut])
def list_api_keys(
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """List all A2A keys for the tenant (no key material returned)."""
    return (
        db.query(A2AApiKey)
        .filter_by(tenant_id=tenant_id)
        .order_by(A2AApiKey.created_at.desc())
        .all()
    )


@_keys_router.delete("/{key_id}", status_code=204)
def revoke_api_key(
    key_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """Revoke an A2A key. External agents using this key will immediately get 401."""
    row = db.query(A2AApiKey).filter_by(id=key_id, tenant_id=tenant_id).first()
    if not row:
        raise HTTPException(404, "Key not found")
    db.delete(row)
    db.commit()
    logger.info("A2A: revoked key id=%s label=%r tenant=%s", key_id, row.label, tenant_id)


# ---------------------------------------------------------------------------
# Workflow publish/unpublish
# ---------------------------------------------------------------------------

_publish_router = APIRouter(prefix="/api/v1/workflows", tags=["a2a-publish"])


@_publish_router.patch("/{workflow_id}/publish", response_model=WorkflowOut)
def publish_workflow(
    workflow_id: uuid.UUID,
    body: WorkflowPublishRequest,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """Set or clear the is_published flag on a workflow.

    Published workflows appear in the tenant's A2A agent card as skills
    and can be invoked by authorised external agents.
    """
    wf = (
        db.query(WorkflowDefinition)
        .filter_by(id=workflow_id, tenant_id=tenant_id)
        .first()
    )
    if not wf:
        raise HTTPException(404, "Workflow not found")

    wf.is_published = body.is_published
    db.commit()
    db.refresh(wf)

    action = "published" if body.is_published else "unpublished"
    logger.info("A2A: workflow %s %s by tenant %s", workflow_id, action, tenant_id)
    return wf


# Combine into the module-level router that main.py imports
router.include_router(_keys_router)
router.include_router(_publish_router)
