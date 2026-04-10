"""Conversation session management endpoints.

These endpoints expose the persistent conversation history used by the
Stateful Re-Trigger Pattern (Load/Save Conversation State nodes).
They let external clients inspect or clear chat histories without needing
direct database access.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.security.tenant import get_tenant_id
from app.models.workflow import ConversationSession
from app.api.schemas import ConversationSessionOut, ConversationSessionSummary

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationSessionSummary])
def list_sessions(
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """List all conversation sessions for the tenant (summary only, no messages)."""
    sessions = (
        db.query(ConversationSession)
        .filter_by(tenant_id=tenant_id)
        .order_by(ConversationSession.updated_at.desc())
        .limit(100)
        .all()
    )
    return [
        ConversationSessionSummary(
            session_id=s.session_id,
            message_count=len(s.messages or []),
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
        for s in sessions
    ]


@router.get("/{session_id}", response_model=ConversationSessionOut)
def get_session(
    session_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """Retrieve the full message history for a conversation session."""
    session = (
        db.query(ConversationSession)
        .filter_by(session_id=session_id, tenant_id=tenant_id)
        .first()
    )
    if not session:
        raise HTTPException(404, "Conversation session not found")

    messages = session.messages or []
    return ConversationSessionOut(
        session_id=session.session_id,
        tenant_id=session.tenant_id,
        messages=messages,
        message_count=len(messages),
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.delete("/{session_id}", status_code=204)
def delete_session(
    session_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """Clear all message history for a conversation session.

    The session row is deleted entirely.  The next DAG run that references
    this session_id will auto-create a fresh empty session.
    """
    session = (
        db.query(ConversationSession)
        .filter_by(session_id=session_id, tenant_id=tenant_id)
        .first()
    )
    if not session:
        raise HTTPException(404, "Conversation session not found")
    db.delete(session)
    db.commit()
