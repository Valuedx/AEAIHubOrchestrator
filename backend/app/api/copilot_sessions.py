"""COPILOT-01b.i — session + turn REST surface.

Complements ``copilot_drafts.py`` by adding the chat-session layer on
top: users create one session per draft (optionally many sequential
sessions), send user-message turns, and stream the agent's response
as SSE events.

Endpoint map
------------

``POST   /api/v1/copilot/sessions``                    create session bound to a draft
``GET    /api/v1/copilot/sessions``                    list sessions (optional draft_id filter)
``GET    /api/v1/copilot/sessions/{id}``               read session meta
``DELETE /api/v1/copilot/sessions/{id}``               mark session abandoned (keeps history)
``GET    /api/v1/copilot/sessions/{id}/turns``         list turns chronologically
``POST   /api/v1/copilot/sessions/{id}/turns``         send a user message + stream response (SSE)
``GET    /api/v1/copilot/sessions/providers``          list supported providers + default model per

Streaming
---------

``POST …/turns`` returns ``text/event-stream`` with one JSON event per
line. Event shapes are documented on ``app/copilot/agent.py`` and
mirrored in the frontend TS types.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.copilot.agent import (
    AgentRunner,
    UnsupportedProviderError,
    declared_tools,
    default_model_for,
    supported_providers,
    validate_session_model,
)
from app.database import get_tenant_db
from app.models.copilot import CopilotSession, CopilotTurn, WorkflowDraft
from app.security.tenant import get_tenant_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/copilot/sessions", tags=["copilot-sessions"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SessionCreate(BaseModel):
    draft_id: str
    provider: str = Field(default="anthropic")
    # Optional override; falls through to DEFAULT_MODEL_BY_PROVIDER.
    model: str | None = None


class SessionOut(BaseModel):
    id: str
    tenant_id: str
    draft_id: str
    provider: str
    model: str
    status: str
    created_at: str
    updated_at: str


class TurnOut(BaseModel):
    id: str
    session_id: str
    turn_index: int
    role: str
    content_json: dict[str, Any]
    tool_calls_json: dict[str, Any] | list[dict[str, Any]] | None
    token_usage_json: dict[str, Any] | None
    created_at: str


class TurnIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=8192)


class ProvidersOut(BaseModel):
    providers: list[str]
    default_model: dict[str, str]
    tools: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------


@router.get("/providers", response_model=ProvidersOut)
def get_providers():
    """List providers the agent runner knows how to drive, with the
    default model per provider and the declared tool surface. The
    frontend uses this to populate the session-create picker and to
    render a 'here's what the agent can do' drawer."""
    providers = supported_providers()
    return ProvidersOut(
        providers=providers,
        default_model={p: default_model_for(p) for p in providers},
        tools=declared_tools(),
    )


@router.post("", response_model=SessionOut, status_code=201)
def create_session(
    body: SessionCreate,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    try:
        draft_uuid = uuid.UUID(body.draft_id)
    except ValueError:
        raise HTTPException(422, "Invalid draft_id")

    draft = (
        db.query(WorkflowDraft)
        .filter_by(id=draft_uuid, tenant_id=tenant_id)
        .first()
    )
    if draft is None:
        raise HTTPException(404, "Draft not found")

    try:
        model = body.model or default_model_for(body.provider)
        # Explicit user picks must be registry-valid + copilot-capable
        # + satisfy the tenant's family allowlist (MODEL-01.e).
        # Defaulted models come from the registry and don't need
        # re-checking — but we still run the allowlist check on them
        # so a tenant pinned to "2.5" doesn't get a default 3.x model.
        validate_session_model(body.provider, model, tenant_id=tenant_id)
    except UnsupportedProviderError as exc:
        raise HTTPException(400, str(exc))

    session = CopilotSession(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        draft_id=draft.id,
        provider=body.provider,
        model=model,
        status="active",
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return _session_out(session)


@router.get("", response_model=list[SessionOut])
def list_sessions(
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
    draft_id: str | None = Query(default=None),
):
    query = db.query(CopilotSession).filter_by(tenant_id=tenant_id)
    if draft_id:
        try:
            query = query.filter_by(draft_id=uuid.UUID(draft_id))
        except ValueError:
            raise HTTPException(422, "Invalid draft_id")
    rows = query.order_by(CopilotSession.updated_at.desc()).all()
    return [_session_out(r) for r in rows]


@router.get("/{session_id}", response_model=SessionOut)
def get_session(
    session_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    return _session_out(_get_session_or_404(db, tenant_id, session_id))


@router.delete("/{session_id}", status_code=204)
def abandon_session(
    session_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    """Mark the session abandoned. Turns are preserved so the user can
    read the prior conversation — starting a new session on the same
    draft continues the story in a fresh context window. Actual row
    deletion cascades only when the parent draft is deleted."""
    session = _get_session_or_404(db, tenant_id, session_id)
    session.status = "abandoned"
    db.commit()


# ---------------------------------------------------------------------------
# Turns
# ---------------------------------------------------------------------------


@router.get("/{session_id}/turns", response_model=list[TurnOut])
def list_turns(
    session_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    session = _get_session_or_404(db, tenant_id, session_id)
    rows = (
        db.query(CopilotTurn)
        .filter_by(tenant_id=tenant_id, session_id=session.id)
        .order_by(CopilotTurn.turn_index)
        .all()
    )
    return [_turn_out(r) for r in rows]


@router.post("/{session_id}/turns")
def send_turn(
    session_id: str,
    body: TurnIn,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    """Send a user message; stream the agent's response as SSE.

    Event shapes are documented at ``app/copilot/agent.py`` and
    mirrored in ``frontend/src/lib/api.ts``. Each event is sent as
    one ``data:`` line; consumers should use EventSource's default
    message handler.

    Persistence happens incrementally — every turn is flushed as it
    is produced so a disconnected client doesn't lose partial
    progress. A final commit lands when the stream completes (or
    the stream is closed by the client).
    """
    session = _get_session_or_404(db, tenant_id, session_id)
    if session.status != "active":
        raise HTTPException(
            409,
            f"Session is {session.status}; create a new session "
            "to continue drafting.",
        )

    draft = (
        db.query(WorkflowDraft)
        .filter_by(id=session.draft_id, tenant_id=tenant_id)
        .first()
    )
    if draft is None:
        raise HTTPException(404, "Draft bound to this session is gone")

    runner = AgentRunner(
        db,
        tenant_id=tenant_id,
        session=session,
        draft=draft,
    )

    def event_stream():
        try:
            for event in runner.send_turn(body.text):
                yield _as_sse(event)
        except UnsupportedProviderError as exc:
            yield _as_sse({
                "type": "error",
                "message": str(exc),
                "recoverable": False,
            })
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("Agent runner crashed for session=%s", session_id)
            yield _as_sse({
                "type": "error",
                "message": f"Agent runner crashed: {exc}",
                "recoverable": False,
            })
        finally:
            # Commit whatever turns were flushed. If the client
            # disconnected mid-stream we still want the partial
            # progress persisted.
            try:
                db.commit()
            except Exception:  # pragma: no cover
                logger.exception("Final commit failed for session=%s", session_id)
                db.rollback()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_session_or_404(
    db: Session, tenant_id: str, session_id: str,
) -> CopilotSession:
    try:
        uid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(422, "Invalid session_id")
    session = (
        db.query(CopilotSession)
        .filter_by(id=uid, tenant_id=tenant_id)
        .first()
    )
    if session is None:
        raise HTTPException(404, "Session not found")
    return session


def _session_out(session: CopilotSession) -> SessionOut:
    return SessionOut(
        id=str(session.id),
        tenant_id=session.tenant_id,
        draft_id=str(session.draft_id),
        provider=session.provider,
        model=session.model,
        status=session.status,
        created_at=session.created_at.isoformat() if session.created_at else "",
        updated_at=session.updated_at.isoformat() if session.updated_at else "",
    )


def _turn_out(turn: CopilotTurn) -> TurnOut:
    return TurnOut(
        id=str(turn.id),
        session_id=str(turn.session_id),
        turn_index=turn.turn_index,
        role=turn.role,
        content_json=turn.content_json or {},
        tool_calls_json=turn.tool_calls_json,
        token_usage_json=turn.token_usage_json,
        created_at=turn.created_at.isoformat() if turn.created_at else "",
    )


def _as_sse(event: dict[str, Any]) -> str:
    """One SSE ``data:`` line. No event-type framing — consumers read
    ``event.type`` from the payload, which keeps event parsing
    symmetrical with the background-worker case where we can't set
    the SSE ``event:`` field easily."""
    return f"data: {json.dumps(event, default=str)}\n\n"
