"""Advanced memory models: conversation rows, profiles, semantic records, entity facts."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class ConversationMessage(Base):
    """Append-only conversation turn row."""

    __tablename__ = "conversation_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_ref_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversation_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id = Column(String(64), nullable=False, index=True)
    session_id = Column(String(256), nullable=False, index=True)
    turn_index = Column(Integer, nullable=False)
    role = Column(String(32), nullable=False)
    content = Column(Text, nullable=False)
    message_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    workflow_def_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    instance_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    node_id = Column(String(128), nullable=True)
    idempotency_key = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    session = relationship("ConversationSession", back_populates="messages_rel")

    __table_args__ = (
        Index("ix_conv_msg_session_turn", "session_ref_id", "turn_index", unique=True),
        Index(
            "ix_conv_msg_session_idem_role",
            "session_ref_id",
            "idempotency_key",
            "role",
            unique=True,
        ),
    )


class MemoryProfile(Base):
    """Tenant or workflow scoped memory policy."""

    __tablename__ = "memory_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    workflow_def_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workflow_definitions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    is_default = Column(Boolean, nullable=False, default=False)
    instructions_text = Column(Text, nullable=True)
    enabled_scopes = Column(JSONB, nullable=False, default=list)
    max_recent_tokens = Column(Integer, nullable=False, default=1200)
    max_semantic_hits = Column(Integer, nullable=False, default=4)
    include_entity_memory = Column(Boolean, nullable=False, default=True)
    summary_trigger_messages = Column(Integer, nullable=False, default=12)
    summary_recent_turns = Column(Integer, nullable=False, default=6)
    summary_max_tokens = Column(Integer, nullable=False, default=400)
    semantic_score_threshold = Column(Float, nullable=False, default=0.0)
    embedding_provider = Column(String(32), nullable=False, default="openai")
    embedding_model = Column(String(128), nullable=False, default="text-embedding-3-small")
    vector_store = Column(String(32), nullable=False, default="pgvector")
    entity_mappings_json = Column(JSONB, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        Index("ix_mem_profile_tenant_name", "tenant_id", "name"),
        Index("ix_mem_profile_tenant_wf_default", "tenant_id", "workflow_def_id", "is_default"),
    )


class MemoryRecord(Base):
    """Semantic or episodic memory item."""

    __tablename__ = "memory_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    scope = Column(String(32), nullable=False, index=True)
    scope_key = Column(String(256), nullable=False, index=True)
    kind = Column(String(32), nullable=False, index=True)
    content = Column(Text, nullable=False)
    metadata_json = Column(JSONB, nullable=False, default=dict)
    session_ref_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversation_sessions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    workflow_def_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    entity_type = Column(String(128), nullable=True, index=True)
    entity_key = Column(String(256), nullable=True, index=True)
    source_instance_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    source_node_id = Column(String(128), nullable=True)
    embedding_provider = Column(String(32), nullable=False, default="openai")
    embedding_model = Column(String(128), nullable=False, default="text-embedding-3-small")
    vector_store = Column(String(32), nullable=False, default="pgvector")
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    session = relationship("ConversationSession", back_populates="memory_records")

    __table_args__ = (
        Index("ix_mem_record_scope_lookup", "tenant_id", "scope", "scope_key"),
        Index("ix_mem_record_entity_lookup", "tenant_id", "entity_type", "entity_key"),
    )


class EntityFact(Base):
    """Authoritative relational entity fact store."""

    __tablename__ = "entity_facts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    entity_type = Column(String(128), nullable=False, index=True)
    entity_key = Column(String(256), nullable=False, index=True)
    fact_name = Column(String(128), nullable=False, index=True)
    fact_value = Column(Text, nullable=False)
    confidence = Column(Float, nullable=False, default=1.0)
    valid_from = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    valid_to = Column(DateTime(timezone=True), nullable=True)
    superseded_by = Column(UUID(as_uuid=True), nullable=True)
    session_ref_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversation_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    workflow_def_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    source_instance_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    source_node_id = Column(String(128), nullable=True)
    metadata_json = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index(
            "ix_entity_fact_active_lookup",
            "tenant_id",
            "entity_type",
            "entity_key",
            "fact_name",
            "valid_to",
        ),
    )
