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
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db, get_tenant_db, set_tenant_context
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
# State mapping (A2A-01.a + 01.b)
# ---------------------------------------------------------------------------
#
# A2A v1.0 defines 8 task states:
#
#   Active:      submitted, working
#   Terminal:    completed, failed, canceled, rejected
#   Interrupted: input-required, auth-required
#   Unknown:     unknown (explicit "we don't know what state this is in")
#
# Our WorkflowInstance status field is coarser — we track lifecycle
# mostly via ``queued / running / completed / suspended / failed /
# cancelled``. The mapping below bridges the two, using
# ``suspended_reason`` to disambiguate the *kind* of suspension:
#
#   suspended + reason=None             → input-required  (HITL approval)
#   suspended + reason="async_external" → input-required  (waiting on
#                                          an external system —
#                                          AutomationEdge, webhook,
#                                          etc. Closest spec fit.)
#   suspended + reason="auth_required"  → auth-required   (reserved for
#                                          a future slice where a node
#                                          flags missing creds to the
#                                          caller; the string is
#                                          recognised here so the
#                                          producer side can land
#                                          without a matching A2A
#                                          change)
#   failed    + reason="rejected"       → rejected        (validation or
#                                          policy refused the draft
#                                          before execution; distinct
#                                          from a runtime failure)


_STATUS_MAP: dict[str, str] = {
    "queued":    "submitted",
    "running":   "working",
    "completed": "completed",
    "failed":    "failed",
    "cancelled": "canceled",
    "paused":    "input-required",
}


def _map_a2a_state(instance: WorkflowInstance) -> str:
    """Translate a WorkflowInstance's persisted status + suspend
    reason into an A2A v1.0 task state string.

    See the table above for the full mapping. Anything we don't
    recognise returns ``"unknown"`` — the spec requires a valid
    enum value so defaulting to ``working`` would be a lie.
    """
    status = instance.status or "unknown"
    reason = instance.suspended_reason

    if status == "suspended":
        if reason == "auth_required":
            return "auth-required"
        # async_external + None (HITL) both surface as input-required;
        # the caller distinguishes via the message payload.
        return "input-required"

    if status == "failed" and reason == "rejected":
        return "rejected"

    return _STATUS_MAP.get(status, "unknown")


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
    """Validate the Bearer token against a2a_api_keys for the path tenant_id.

    The A2A surface identifies the tenant via URL path rather than the
    ``X-Tenant-Id`` header, so ``get_tenant_db`` (which keys on the
    header) can't be used. Set the RLS context explicitly instead.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "A2A request requires a Bearer token")
    set_tenant_context(db, tenant_id)
    raw_key = authorization.removeprefix("Bearer ").strip()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    row = (
        db.query(A2AApiKey)
        .filter_by(tenant_id=tenant_id, key_hash=key_hash)
        .first()
    )
    if not row:
        raise HTTPException(401, "Invalid or revoked A2A API key")
    
    # Enable RLS context for this A2A session
    set_tenant_context(db, tenant_id)
    return tenant_id


# ---------------------------------------------------------------------------
# Task object helpers
# ---------------------------------------------------------------------------

def _instance_to_task(
    instance: WorkflowInstance,
    session_id: str | None = None,
) -> dict:
    """Convert a WorkflowInstance into an A2A Task dict."""
    state = _map_a2a_state(instance)
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

    # For completed, surface the final response as a multi-part
    # artifact (A2A-01.c): one TextPart with the prose response the
    # caller probably wants to show the user, plus a DataPart
    # carrying the full non-internal context so pipelines that pipe
    # our output into their own workflows get the structured side
    # for free.
    artifacts = []
    if state == "completed" and instance.context_json:
        parts = _build_completed_artifact_parts(instance.context_json)
        if parts:
            artifacts = [{"index": 0, "parts": parts, "lastChunk": True}]

    return {
        "id": str(instance.id),
        "sessionId": session_id or str(instance.id),
        "status": status,
        "artifacts": artifacts,
    }


def _build_completed_artifact_parts(context_json: dict) -> list[dict]:
    """Build the Part list for a completed task's artifact.

    Emits up to two parts:

    1. TextPart with the final LLM response (same string
       ``_extract_final_response`` used to return).
    2. DataPart with every non-internal key in the context dict —
       the downstream workflow's structured output. Internal
       ``_``-prefixed keys are stripped so we don't leak
       ``_approval_message`` etc. to the A2A caller.

    If neither yields anything we return ``[]`` and
    ``_instance_to_task`` suppresses the artifact entirely.
    """
    parts: list[dict] = []

    response_text = _extract_final_response(context_json)
    if response_text:
        parts.append({"text": response_text})

    public = {
        k: v for k, v in context_json.items()
        if not str(k).startswith("_")
    }
    if public:
        parts.append({"data": public, "mimeType": "application/json"})

    return parts


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


def _read_metadata_skill_id(message: Any) -> str | None:
    """A2A v1.0 ``message/send`` clients carry the skill identifier
    inside ``message.metadata`` rather than as a top-level ``skillId``
    field. Extract it (accepting both camelCase and snake_case) so
    the dispatcher's method-name aliasing doesn't force callers to
    duplicate the id at two levels.
    """
    if not isinstance(message, dict):
        return None
    metadata = message.get("metadata")
    if not isinstance(metadata, dict):
        return None
    return metadata.get("skillId") or metadata.get("skill_id")


def _parse_message_parts(message: Any) -> dict[str, Any]:
    """Fan an inbound A2A v1.0 Message out into the richer shape
    downstream workflows want (A2A-01.c).

    Returns::

        {
          "text": str,        # concatenated text parts
          "data": list[Any],  # every DataPart's payload, in order
          "files": list[dict],# every FilePart's reference, in order
        }

    Text parts are joined with single spaces — the same behaviour
    the v0.2.x ``_tasks_send`` had, so existing workflows keep
    receiving ``trigger_payload.message`` as a plain string. Data
    and file parts are net-new in 01.c: workflows that want the
    richer payload read ``trigger_payload.message_parts`` instead.
    """
    text_bits: list[str] = []
    data_bits: list[Any] = []
    file_bits: list[dict[str, Any]] = []

    if not isinstance(message, dict):
        return {"text": "", "data": [], "files": []}

    for part in message.get("parts") or []:
        if not isinstance(part, dict):
            continue
        if isinstance(part.get("text"), str):
            text_bits.append(part["text"])
        if part.get("data") is not None:
            data_bits.append(part["data"])
        file_ref = part.get("file")
        if isinstance(file_ref, dict):
            # Preserve whichever of bytes / uri the caller supplied.
            # mimeType + name are best-effort; mirrored through so
            # engine-side handlers can route on them.
            file_bits.append({
                "name": file_ref.get("name"),
                "mimeType": file_ref.get("mimeType") or part.get("mimeType"),
                "bytes": file_ref.get("bytes"),
                "uri": file_ref.get("uri"),
            })

    return {
        "text": " ".join(text_bits).strip(),
        "data": data_bits,
        "files": file_bits,
    }


# ---------------------------------------------------------------------------
# Agent card
# ---------------------------------------------------------------------------

@router.get("/tenants/{tenant_id}/.well-known/agent-card.json")
def agent_card(
    tenant_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Return the A2A agent card for a tenant.

    Lists all workflows the tenant has marked as is_published=True.
    No authentication required — agent cards are public discovery documents.
    The tenant is taken from the URL path, not the ``X-Tenant-Id`` header,
    so the RLS context is set inline rather than via ``get_tenant_db``.

    **A2A spec v1.0** places the agent card at
    ``/.well-known/agent-card.json``; v0.2.x placed it at
    ``/.well-known/agent.json``. We serve both paths so clients on
    either spec version discover us without guessing (see the
    ``agent_card_legacy`` alias below).
    """
    return _build_agent_card(tenant_id, db, request)


@router.get("/tenants/{tenant_id}/.well-known/agent.json")
def agent_card_legacy(
    tenant_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """A2A v0.2.x legacy discovery path. Delegates to the same
    builder as the v1.0 path so both responses are identical."""
    return _build_agent_card(tenant_id, db, request)


def _build_agent_card(
    tenant_id: str, db: Session, request: Request,
) -> dict:
    """Shared agent-card body used by both discovery paths.

    Layout follows A2A v1.0 (A2A-01.b): provider + securitySchemes +
    security + defaultInputModes + defaultOutputModes + capabilities
    + skills. Optional fields (provider fields + documentationUrl)
    suppress themselves when unset so a minimally-configured card is
    still spec-valid.
    """
    from app.config import settings

    set_tenant_context(db, tenant_id)
    # Ephemeral rows default is_published=False so the filter below
    # already excludes them, but pin the invariant explicitly — a
    # future bug that flips is_published on an ephemeral shouldn't
    # leak the copilot's throwaway workflow into a tenant's public
    # agent card.
    workflows = (
        db.query(WorkflowDefinition)
        .filter_by(tenant_id=tenant_id, is_published=True, is_ephemeral=False)
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
            "outputModes": ["text", "data"],
        }
        for wf in workflows
    ]

    card: dict = {
        "name": f"AE Orchestrator — {tenant_id}",
        "description": "Agentic workflow orchestration engine",
        "url": agent_url,
        "version": "1.0",
        # Input / output modes the agent accepts by default at the
        # skill level. Individual skills can override (we echo the
        # same defaults per skill today since every published
        # workflow consumes text and emits text+data).
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text", "data"],
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": False,
            # We don't split public vs. extended cards today — the
            # same card body is returned regardless of auth — so
            # extendedAgentCard is false and we don't implement
            # ``agent/getExtendedAgentCard``.
            "extendedAgentCard": False,
        },
        # Bearer is the only scheme today. Declared here so v1.0
        # clients' security negotiation picks it automatically
        # rather than 401-ing and guessing.
        "securitySchemes": {
            "bearer": {
                "type": "http",
                "scheme": "bearer",
                "description": (
                    "A2A API key issued via POST /api/v1/a2a/keys. "
                    "Send as Authorization: Bearer <key>."
                ),
            },
        },
        "security": [{"bearer": []}],
        "skills": skills,
    }

    # ---- Optional fields (suppress when unset) ----

    provider: dict = {}
    if settings.a2a_provider_organization:
        provider["organization"] = settings.a2a_provider_organization
    if settings.a2a_provider_url:
        provider["url"] = settings.a2a_provider_url
    if settings.a2a_provider_email:
        provider["email"] = settings.a2a_provider_email
    if provider:
        card["provider"] = provider

    if settings.a2a_documentation_url:
        card["documentationUrl"] = settings.a2a_documentation_url

    return card


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
    """Single JSON-RPC 2.0 endpoint. Routes by body.method.

    **Method aliasing (A2A-01.a).** We accept both the v0.2.x and v1.0
    method names so existing clients keep working while 2026-era
    v1.0 clients (Google ADK, LangGraph A2A, Bedrock AgentCore,
    Inkeep, etc.) discover and call us without a shim. The v1.0
    aliases are:

      * ``message/send``           → same handler as ``tasks/send``
      * ``message/sendStreaming``  → same handler as ``tasks/sendSubscribe``

    Param-shape compatibility: v1.0 senders typically pass the skill
    identifier in ``message.metadata.skillId`` rather than as a
    top-level ``skillId`` field. ``_tasks_send`` + ``_tasks_send_subscribe``
    read from both locations so either shape works.
    """
    method = body.method
    params = body.params

    # Send a message / create a task. v0.2.x + v1.0 names share one handler.
    if method in {"tasks/send", "message/send"}:
        result = _tasks_send(params, tenant_id, db)
        return {"jsonrpc": "2.0", "id": body.id, "result": result}

    if method == "tasks/get":
        result = _tasks_get(params, tenant_id, db)
        return {"jsonrpc": "2.0", "id": body.id, "result": result}

    if method == "tasks/cancel":
        result = _tasks_cancel(params, tenant_id, db)
        return {"jsonrpc": "2.0", "id": body.id, "result": result}

    # Streaming. v0.2.x + v1.0 names share one SSE handler.
    if method in {"tasks/sendSubscribe", "message/sendStreaming"}:
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
    message      = params.get("message") or {}
    # Skill routing: v0.2.x clients pass ``skillId`` / ``skill_id`` at the
    # top level; v1.0 ``message/send`` has no top-level skill field so
    # clients put it inside ``message.metadata.skillId`` (Google ADK's
    # convention). Accept either shape so the dispatcher can alias the
    # method names without forcing callers to change their payload.
    raw_skill_id = (
        params.get("skillId")
        or params.get("skill_id")
        or _read_metadata_skill_id(message)
    )
    session_id   = params.get("sessionId") or params.get("session_id") or str(uuid.uuid4())

    if not raw_skill_id:
        raise HTTPException(
            400,
            "send requires a skillId (top-level `skillId`, or "
            "`message.metadata.skillId` for v1.0 clients)",
        )

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

    # A2A-01.c — fan the message out across text / data / file
    # parts. `message` stays a plain string for back compat with
    # existing workflows that read trigger_payload.message; the
    # rich shape lands on trigger_payload.message_parts so newer
    # workflows can read data + file payloads without parsing.
    parsed = _parse_message_parts(message)
    trigger_payload: dict[str, Any] = {
        "session_id": session_id,
        "message": parsed["text"],
        "message_parts": parsed,
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
    execute_workflow_task.delay(tenant_id, str(instance.id))

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
                set_tenant_context(poll_db, tenant_id)
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
    db: Session = Depends(get_tenant_db),
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
    db: Session = Depends(get_tenant_db),
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
    db: Session = Depends(get_tenant_db),
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
    db: Session = Depends(get_tenant_db),
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
