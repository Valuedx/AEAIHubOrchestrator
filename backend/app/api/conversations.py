"""Conversation session management endpoints.

These endpoints expose the persistent conversation history used by the
Stateful Re-Trigger Pattern (Load/Save Conversation State nodes).
They let external clients inspect or clear chat histories without needing
direct database access.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import (
    ArchiveConversationEpisodeOut,
    ArchiveConversationEpisodeRequest,
    ConversationEpisodeOut,
    ConversationSessionOut,
    ConversationSessionSummary,
)
from app.database import get_db, get_tenant_db
from app.engine.memory_service import archive_active_episode, get_or_create_session
from app.models.memory import ConversationEpisode, ConversationMessage
from app.models.workflow import ConversationSession
from app.security.tenant import get_tenant_id

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationSessionSummary])
def list_sessions(
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
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
            message_count=s.message_count,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
        for s in sessions
    ]


@router.get("/{session_id}", response_model=ConversationSessionOut)
def get_session(
    session_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    """Retrieve the full message history for a conversation session."""
    session = (
        db.query(ConversationSession)
        .filter_by(session_id=session_id, tenant_id=tenant_id)
        .first()
    )
    if not session:
        raise HTTPException(404, "Conversation session not found")

    messages = (
        db.query(ConversationMessage)
        .filter_by(session_ref_id=session.id)
        .order_by(ConversationMessage.turn_index)
        .all()
    )
    return ConversationSessionOut(
        session_id=session.session_id,
        tenant_id=session.tenant_id,
        messages=[
            {
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.message_at.isoformat() if msg.message_at else None,
            }
            for msg in messages
        ],
        message_count=session.message_count,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.delete("/{session_id}", status_code=204)
def delete_session(
    session_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
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


@router.get("/{session_id}/episodes", response_model=list[ConversationEpisodeOut])
def list_session_episodes(
    session_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    session = (
        db.query(ConversationSession)
        .filter_by(session_id=session_id, tenant_id=tenant_id)
        .first()
    )
    if not session:
        raise HTTPException(404, "Conversation session not found")

    episodes = (
        db.query(ConversationEpisode)
        .filter_by(session_ref_id=session.id, tenant_id=tenant_id)
        .order_by(ConversationEpisode.created_at.desc())
        .all()
    )
    return [
        ConversationEpisodeOut(
            id=episode.id,
            session_id=session.session_id,
            status=episode.status,
            start_turn=episode.start_turn,
            end_turn=episode.end_turn,
            title=episode.title,
            checkpoint_summary_text=episode.checkpoint_summary_text,
            summary_through_turn=episode.summary_through_turn,
            archive_reason=episode.archive_reason,
            last_activity_at=episode.last_activity_at,
            archived_at=episode.archived_at,
            archived_memory_record_id=episode.archived_memory_record_id,
            created_at=episode.created_at,
            updated_at=episode.updated_at,
        )
        for episode in episodes
    ]


@router.post("/{session_id}/archive-active-episode", response_model=ArchiveConversationEpisodeOut)
def archive_session_episode(
    session_id: str,
    body: ArchiveConversationEpisodeRequest,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    session = get_or_create_session(
        db,
        tenant_id=tenant_id,
        session_id=session_id,
        lock=True,
    )
    episode, memory_rows = archive_active_episode(
        db,
        tenant_id=tenant_id,
        session=session,
        workflow_def_id=None,
        instance_id=None,
        node_id="api_archive_episode",
        context={},
        reason=body.reason,
        provided_summary=(body.summary_text or "").strip(),
        provided_title=(body.title or "").strip(),
        memory_profile_id=str(body.memory_profile_id) if body.memory_profile_id else None,
    )
    db.commit()

    return ArchiveConversationEpisodeOut(
        session_id=session_id,
        archived=bool(episode and episode.status == "archived"),
        episode_id=episode.id if episode else None,
        title=episode.title if episode else None,
        archive_reason=episode.archive_reason if episode and episode.status == "archived" else None,
        archived_at=episode.archived_at if episode and episode.status == "archived" else None,
        memory_record_ids=[row.id for row in memory_rows],
        memory_records_created=len(memory_rows),
        summary_text=episode.checkpoint_summary_text if episode else "",
    )
