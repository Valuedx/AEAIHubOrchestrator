"""ORM models for COPILOT-01 — draft workspaces + copilot chat history.

Three tenant-scoped tables that back the workflow-authoring copilot.
Schema lives in Alembic migration 0022. All three tables carry
denormalised ``tenant_id`` columns so the RLS policy from migration
0022 can key on a simple equality check (no joins inside the policy,
which is both slower and a subtle correctness risk).

Deliberately NOT modelled here:

* ``last_copilot_session_id`` back-pointer on ``WorkflowDraft`` — would
  create a FK cycle with ``CopilotSession.draft_id``. The latest
  session is recovered with ``ORDER BY created_at DESC LIMIT 1``.
* ``token_budget`` / ``token_used`` columns — deferred to COPILOT-01b
  where the agent runner lands and the budget knob has a concrete
  enforcement story.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class WorkflowDraft(Base):
    """A graph being edited (by a copilot session or a human) that
    hasn't been promoted into ``workflow_definitions`` yet.

    ``version`` is the optimistic-concurrency token: every tool
    dispatch that mutates the graph bumps it, and a stale write
    returns 409 via the API layer. ``base_version_at_fork`` captures
    the version of ``base_workflow_id`` at draft-creation time so the
    promote step can refuse to clobber a base that a colleague has
    edited in another tab in the meantime.
    """

    __tablename__ = "workflow_drafts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    base_workflow_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workflow_definitions.id", ondelete="SET NULL"),
        nullable=True,
    )
    base_version_at_fork = Column(Integer, nullable=True)
    title = Column(String(256), nullable=False)
    graph_json = Column(JSONB, nullable=False, default=lambda: {"nodes": [], "edges": []})
    version = Column(Integer, nullable=False, default=1)
    created_by = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    sessions = relationship(
        "CopilotSession",
        back_populates="draft",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_draft_tenant_updated", "tenant_id", "updated_at"),
    )


class CopilotSession(Base):
    """One chat session against a draft. A draft can have many sequential
    sessions — an earlier session may be abandoned and the user may
    reopen the draft later with a different provider."""

    __tablename__ = "copilot_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    draft_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workflow_drafts.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider = Column(String(32), nullable=False)
    model = Column(String(128), nullable=False)
    status = Column(String(16), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    draft = relationship("WorkflowDraft", back_populates="sessions")
    turns = relationship(
        "CopilotTurn",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="CopilotTurn.turn_index",
    )

    __table_args__ = (
        Index("ix_session_tenant_draft", "tenant_id", "draft_id"),
    )


class CopilotTurn(Base):
    """One message in a copilot session. ``role`` is user / assistant /
    tool. ``content_json`` is role-shaped: text for user/assistant,
    ``{name, args, result}`` for tool turns. ``tool_calls_json`` is
    set when an assistant turn emits function-calling requests."""

    __tablename__ = "copilot_turns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("copilot_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    turn_index = Column(Integer, nullable=False)
    role = Column(String(16), nullable=False)
    content_json = Column(JSONB, nullable=False)
    tool_calls_json = Column(JSONB, nullable=True)
    token_usage_json = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    session = relationship("CopilotSession", back_populates="turns")

    __table_args__ = (
        UniqueConstraint("session_id", "turn_index", name="uq_turn_session_idx"),
        Index("ix_turn_tenant_session", "tenant_id", "session_id"),
    )
