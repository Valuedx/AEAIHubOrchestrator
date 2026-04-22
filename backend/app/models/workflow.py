import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
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
    # DV-07 — when False, Schedule Triggers stop firing. Manual Run, PATCH,
    # and duplicate all continue to work. Default True keeps legacy behaviour.
    is_active = Column(Boolean, nullable=False, default=True, server_default=sa.text("TRUE"))
    # COPILOT-01b.ii.b — True for transient definitions created by the
    # copilot's ``execute_draft`` runner tool. Hidden from user-facing
    # lists (``list_workflows``), the scheduler, and the A2A agent card;
    # the engine itself does NOT filter on this so it can still load
    # these rows and run them. Reaped by ``runner_tools.
    # cleanup_ephemeral_workflows``.
    is_ephemeral = Column(Boolean, nullable=False, default=False, server_default=sa.text("FALSE"))
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
    # Distinguishes HITL-suspended (NULL, legacy default) from async-external-
    # suspended ('async_external'). Cleared on resume. Used by the UI to pick
    # between the Review dialog and the "waiting-on-external" badge.
    suspended_reason = Column(String(32), nullable=True)
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
    active_episode_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversation_episodes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
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
    episodes = relationship(
        "ConversationEpisode",
        back_populates="session",
        cascade="all, delete-orphan",
        foreign_keys="ConversationEpisode.session_ref_id",
        order_by="ConversationEpisode.created_at",
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


class ScheduledTrigger(Base):
    """Atomic claim row for Celery Beat's per-minute schedule fires.

    The ``UNIQUE(workflow_def_id, scheduled_for)`` constraint is the
    dedupe: when two Beat processes race at the same minute boundary,
    exactly one INSERT succeeds; the other raises IntegrityError and
    skips the fire. Replaces the fragile 55-second wall-clock check.

    ``scheduled_for`` is always truncated to minute precision (seconds
    and microseconds set to zero) so Beat ticks that differ by a few
    hundred milliseconds still collide.

    Rows older than a day are pruned by a Beat task; they are only
    needed to prevent duplicate fires within the current window.
    """

    __tablename__ = "scheduled_triggers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_def_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workflow_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    scheduled_for = Column(DateTime(timezone=True), nullable=False)
    instance_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workflow_instances.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint(
            "workflow_def_id", "scheduled_for",
            name="uq_scheduled_trigger_wf_minute",
        ),
        Index("ix_scheduled_trigger_created_at", "created_at"),
    )


class AsyncJob(Base):
    """Tracks an outstanding job on an external async system (AutomationEdge,
    future Jenkins/Temporal/...). One row per suspended node instance.

    Beat polls ``WHERE system=? AND status IN ('submitted','running') AND
    next_poll_at <= now() LIMIT 100`` to find work. On each poll the
    system-specific status handler updates ``status``, ``last_external_
    status``, ``last_polled_at``, and ``next_poll_at`` (now + the job's
    configured poll interval from ``metadata_json.poll_interval_seconds``).

    Diverted handling is a pause-the-clock model: when the external system
    reports a "held for human intervention" state (AE's ``Diverted``),
    ``diverted_since`` is set. When it exits that state the elapsed span
    is banked into ``total_diverted_ms`` so the active-runtime timeout
    budget ignores it.
    """

    __tablename__ = "async_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instance_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workflow_instances.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_id = Column(String(128), nullable=False)
    system = Column(String(32), nullable=False)
    external_job_id = Column(String(256), nullable=False)
    status = Column(String(32), nullable=False)
    metadata_json = Column(JSONB, nullable=False)
    submitted_at = Column(DateTime(timezone=True), nullable=False)
    last_polled_at = Column(DateTime(timezone=True), nullable=True)
    next_poll_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)

    # Diverted-aware timeout accounting
    last_external_status = Column(String(32), nullable=True)
    total_diverted_ms = Column(BigInteger, nullable=False, default=0)
    diverted_since = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "instance_id", "node_id",
            name="uq_async_job_instance_node",
        ),
        Index("ix_async_jobs_poll_queue", "system", "status", "next_poll_at"),
    )


class TenantPolicy(Base):
    """ADMIN-01 — per-tenant overrides for operational knobs.

    Exactly one row per tenant. Every override is nullable — a null
    means "use the env-default value" (resolved at read time by
    ``engine/tenant_policy_resolver.get_effective_policy``). Callers
    that need an effective value must go through the resolver, never
    read columns off this model directly.

    Scope: ``execution_quota_per_hour``, ``max_snapshots``,
    ``mcp_pool_size``. Other env knobs (rate limits, LLM keys, ADC)
    are intentionally not on this table — see ADMIN-02 / ADMIN-03.
    """

    __tablename__ = "tenant_policies"

    tenant_id = Column(String(64), primary_key=True)
    execution_quota_per_hour = Column(Integer, nullable=True)
    max_snapshots = Column(Integer, nullable=True)
    mcp_pool_size = Column(Integer, nullable=True)
    # ADMIN-02 — per-tenant API rate limit. Null = env default.
    rate_limit_requests_per_window = Column(Integer, nullable=True)
    rate_limit_window_seconds = Column(Integer, nullable=True)
    # SMART-04 — proactive authoring lints run after every copilot
    # mutation (no_trigger / disconnected_node / orphan_edge /
    # missing_credential). Default TRUE because lints are zero-LLM-
    # cost and strictly additive to UX. Cost-conscious tenants can
    # opt out via PATCH /api/v1/tenant-policy.
    smart_04_lints_enabled = Column(
        Boolean, nullable=False, default=True, server_default=sa.text("TRUE"),
    )
    # SMART-06 — proactive MCP tool discovery: the agent can call
    # list_tools on the tenant's registered MCP servers to surface
    # relevant tools during drafting. Cached per session, zero-LLM-
    # cost. Default TRUE.
    smart_06_mcp_discovery_enabled = Column(
        Boolean, nullable=False, default=True, server_default=sa.text("TRUE"),
    )
    # SMART-02 — accepted-patterns library: every successful promote
    # saves the graph + NL intent so the agent can retrieve nearest
    # prior patterns as few-shot for future drafts. Save + retrieve
    # are pure DB I/O; default TRUE.
    smart_02_pattern_library_enabled = Column(
        Boolean, nullable=False, default=True, server_default=sa.text("TRUE"),
    )
    # SMART-01 — scenario memory + strict promote-gate. BOTH default
    # FALSE (opt-in per tenant) because both behaviours spend engine
    # tokens. scenario_memory auto-saves a regression case after
    # every successful execute_draft (deduped by payload hash);
    # strict_promote_gate makes promote refuse on any failing
    # scenario with no override.
    smart_01_scenario_memory_enabled = Column(
        Boolean, nullable=False, default=False, server_default=sa.text("FALSE"),
    )
    smart_01_strict_promote_gate_enabled = Column(
        Boolean, nullable=False, default=False, server_default=sa.text("FALSE"),
    )
    # SMART-05 — vector-backed docs search. Default FALSE because
    # embedding calls cost real tokens at search time; the 01b.iii
    # file-backed word-overlap search is the fallback (and auto-
    # fallback when embedding provider is unreachable, so enabling
    # the flag is strictly additive).
    smart_05_vector_docs_enabled = Column(
        Boolean, nullable=False, default=False, server_default=sa.text("FALSE"),
    )
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class TenantMcpServer(Base):
    """MCP-02 — per-tenant MCP server registration.

    One row per server a tenant wants to route MCP Tool nodes to.
    ``auth_mode`` discriminates how ``config_json`` is interpreted:

        * ``none`` — bare URL, no auth; config_json is empty.
        * ``static_headers`` — config_json.headers is a dict of HTTP
          headers applied on every outbound request. Values may embed
          ``{{ env.SECRET_NAME }}`` placeholders that are resolved at
          call time via the Fernet-encrypted tenant_secrets vault.
        * ``oauth_2_1`` — reserved for MCP-03. The column accepts the
          value but the runtime currently raises NotImplementedError.

    At most one row per tenant may have ``is_default=True`` — enforced
    by the partial unique index ``ux_tenant_mcp_server_default`` from
    migration 0019. Nodes with a blank ``mcpServerLabel`` resolve to
    the default row; if no default exists, ``settings.mcp_server_url``
    is used as a legacy fallback so pre-DV/MCP tenants keep working.
    """

    __tablename__ = "tenant_mcp_servers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    label = Column(String(128), nullable=False)
    url = Column(String(1024), nullable=False)
    auth_mode = Column(String(32), nullable=False, default="none")
    config_json = Column(JSONB, nullable=False, default=dict)
    is_default = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("tenant_id", "label", name="uq_tenant_mcp_server_label"),
    )


class TenantMcpServerToolFingerprint(Base):
    """MCP-06 forward declaration — SHA-256 of each tool's definition so
    drift between fetches can be detected (tool-poisoning mitigation).

    Empty at MCP-02. Populated by the audit path added in MCP-06.
    """

    __tablename__ = "tenant_mcp_server_tool_fingerprints"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenant_mcp_servers.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_name = Column(String(256), nullable=False)
    fingerprint_sha256 = Column(String(64), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("server_id", "tool_name", name="uq_mcp_tool_fingerprint"),
    )


class TenantIntegration(Base):
    """Per-tenant connection defaults for an external system.

    Lets workflow nodes reference an integration by label
    (``integrationLabel``) without redeclaring baseUrl / credentials per
    node. A node's own config overrides any matching field on the
    integration — per-node > tenant-default.

    ``config_json`` schema is system-specific; for AutomationEdge it holds
    ``{baseUrl, orgCode, credentialsSecretPrefix, authMode, source, userId}``.
    Secret values never live here — only the prefix name that looks them
    up in the Fernet-encrypted tenant_secrets vault.

    ``(tenant_id, system)`` may have at most one row with ``is_default =
    true`` — enforced by a partial unique index on the DB side.
    """

    __tablename__ = "tenant_integrations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    system = Column(String(32), nullable=False)
    label = Column(String(128), nullable=False)
    config_json = Column(JSONB, nullable=False)
    is_default = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "system", "label",
            name="uq_tenant_integration_label",
        ),
    )
