import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    String,
    Integer,
    DateTime,
    ForeignKey,
    Text,
    Index,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import backref, relationship

from app.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class WorkflowDefinition(Base):
    __tablename__ = "workflow_definitions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    name = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    graph_json = Column(JSONB, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    # When True, this workflow is listed in the tenant's A2A agent card as a skill
    is_published = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    instances = relationship("WorkflowInstance", back_populates="definition")

    __table_args__ = (
        Index("ix_wf_def_tenant_name", "tenant_id", "name"),
    )


class WorkflowInstance(Base):
    __tablename__ = "workflow_instances"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    workflow_def_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workflow_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    status = Column(
        String(32),
        nullable=False,
        default="queued",
        index=True,
    )
    trigger_payload = Column(JSONB, nullable=True)
    # WorkflowDefinition.version at queue time (for replay / graph alignment).
    definition_version_at_start = Column(Integer, nullable=True)
    context_json = Column(JSONB, nullable=False, default=dict)
    current_node_id = Column(String(128), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    # Set by POST …/cancel; the DAG runner checks between nodes and sets status cancelled.
    cancel_requested = Column(Boolean, nullable=False, default=False)
    # Set by POST …/pause; runner checks between nodes and sets status paused.
    pause_requested = Column(Boolean, nullable=False, default=False)
    # Sub-workflow lineage: links a child instance back to the parent that spawned it.
    parent_instance_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workflow_instances.id", ondelete="CASCADE"),
        nullable=True,
    )
    parent_node_id = Column(String(128), nullable=True)

    definition = relationship("WorkflowDefinition", back_populates="instances")
    execution_logs = relationship("ExecutionLog", back_populates="instance")
    children = relationship(
        "WorkflowInstance",
        foreign_keys=[parent_instance_id],
        backref=backref("parent_instance_rel", remote_side=[id]),
        lazy="dynamic",
    )

    __table_args__ = (
        Index("ix_wf_inst_tenant_status", "tenant_id", "status"),
        Index("ix_wf_inst_parent", "parent_instance_id"),
    )


class WorkflowSnapshot(Base):
    """Immutable point-in-time copy of a workflow graph, saved before each overwrite."""

    __tablename__ = "workflow_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_def_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workflow_definitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id = Column(String(64), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    graph_json = Column(JSONB, nullable=False)
    saved_at = Column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_snapshot_def_version", "workflow_def_id", "version", unique=True),
    )


class ExecutionLog(Base):
    __tablename__ = "execution_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instance_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workflow_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    node_id = Column(String(128), nullable=False)
    node_type = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    input_json = Column(JSONB, nullable=True)
    output_json = Column(JSONB, nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    instance = relationship("WorkflowInstance", back_populates="execution_logs")


class InstanceCheckpoint(Base):
    """Point-in-time snapshot of workflow context after a node completes.

    One row is written per successful node completion.  The context_json
    captures everything in the execution context at that moment (internal
    ``_``-prefixed keys are stripped before storage).  Rows are cascade-
    deleted when the parent WorkflowInstance is deleted.
    """

    __tablename__ = "instance_checkpoints"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instance_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workflow_instances.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    node_id = Column(String(128), nullable=False)
    context_json = Column(JSONB, nullable=False, default=dict)
    saved_at = Column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_checkpoint_instance_node", "instance_id", "node_id"),
    )


class ConversationSession(Base):
    """Persistent multi-turn conversation history for the Stateful Re-Trigger Pattern.

    A session_id ties together all DAG runs that belong to the same chat thread.
    Message rows live in ``conversation_messages``; the session row stores
    metadata and rolling summary state.
    """

    __tablename__ = "conversation_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(String(256), nullable=False)
    tenant_id = Column(String(64), nullable=False, index=True)
    message_count = Column(Integer, nullable=False, default=0)
    last_message_at = Column(DateTime(timezone=True), nullable=True)
    summary_text = Column(Text, nullable=True)
    summary_updated_at = Column(DateTime(timezone=True), nullable=True)
    summary_through_turn = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    messages_rel = relationship(
        "ConversationMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ConversationMessage.turn_index",
    )
    memory_records = relationship(
        "MemoryRecord",
        back_populates="session",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_conv_session_tenant_session", "tenant_id", "session_id", unique=True),
    )


class A2AApiKey(Base):
    """Hashed inbound API keys issued to external A2A agents per tenant.

    The raw key is returned only at creation time and never stored.
    Only the SHA-256 hex digest is persisted so a DB breach cannot
    expose working credentials.
    """

    __tablename__ = "a2a_api_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    label = Column(String(128), nullable=False)       # human-readable name, e.g. "teams-bot"
    key_hash = Column(String(64), nullable=False)     # SHA-256 hex of the raw key
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_a2a_key_hash", "key_hash", unique=True),
        UniqueConstraint("tenant_id", "label", name="uq_a2a_key_tenant_label"),
    )
