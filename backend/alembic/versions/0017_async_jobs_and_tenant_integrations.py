"""Async external-system plumbing: async_jobs, tenant_integrations,
workflow_instances.suspended_reason.

Revision ID: 0017
Revises: 0016
Create Date: 2026-04-20

Lands the core schema for the "waiting-on-external" execution path used
by the AutomationEdge node and any future async-RPC integration
(Jenkins, Temporal, custom RPA, ...). See ``app/engine/automationedge_
client.py`` for the first consumer.

Design notes:
  * ``async_jobs`` is system-agnostic — keyed by ``(system,
    external_job_id)``. A single Beat task polls all rows regardless of
    system, dispatching to system-specific ``get_status`` handlers.
  * Diverted handling (AE's "held for human intervention" state) uses a
    pause-the-clock model: ``total_diverted_ms`` banks every span spent
    in Diverted, ``diverted_since`` marks the current one.
  * ``tenant_integrations`` holds per-tenant connection defaults
    (baseUrl, orgCode, credentials secret prefix, etc.) so nodes can
    reference an integration by label without re-declaring connection
    info per node.
  * ``workflow_instances.suspended_reason`` lets the UI distinguish
    HITL-suspended (legacy NULL default) from async-external-suspended
    (``'async_external'``).

The single Beat task that polls async_jobs runs cross-tenant and must
use the BYPASSRLS role documented in SETUP_GUIDE §5.2a — same pattern
as ``check_scheduled_workflows`` and ``prune_old_snapshots``. Per-
tenant API callers go through ``get_tenant_db`` which scopes RLS
correctly on ``tenant_integrations`` reads.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. async_jobs ------------------------------------------------------
    op.create_table(
        "async_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "instance_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_id", sa.String(128), nullable=False),
        sa.Column("system", sa.String(32), nullable=False),
        sa.Column("external_job_id", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB, nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_poll_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        # Diverted-aware timeout accounting
        sa.Column("last_external_status", sa.String(32), nullable=True),
        sa.Column(
            "total_diverted_ms",
            sa.BigInteger,
            nullable=False,
            server_default="0",
        ),
        sa.Column("diverted_since", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "instance_id", "node_id",
            name="uq_async_job_instance_node",
        ),
    )
    op.create_index(
        "ix_async_jobs_poll_queue",
        "async_jobs",
        ["system", "status", "next_poll_at"],
    )

    # 2. tenant_integrations --------------------------------------------
    op.create_table(
        "tenant_integrations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("system", sa.String(32), nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("config_json", postgresql.JSONB, nullable=False),
        sa.Column(
            "is_default",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id", "system", "label",
            name="uq_tenant_integration_label",
        ),
    )
    # Only one default per (tenant, system).
    op.execute(
        "CREATE UNIQUE INDEX ux_tenant_integration_default "
        "ON tenant_integrations (tenant_id, system) "
        "WHERE is_default = true"
    )
    # RLS — same pattern as migrations 0001 / 0014.
    op.execute("ALTER TABLE tenant_integrations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_integrations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_tenant_integrations ON tenant_integrations "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )

    # 3. workflow_instances.suspended_reason ----------------------------
    op.add_column(
        "workflow_instances",
        sa.Column("suspended_reason", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflow_instances", "suspended_reason")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_tenant_integrations ON tenant_integrations")
    op.execute("ALTER TABLE tenant_integrations DISABLE ROW LEVEL SECURITY")
    op.execute("DROP INDEX IF EXISTS ux_tenant_integration_default")
    op.drop_table("tenant_integrations")

    op.drop_index("ix_async_jobs_poll_queue", table_name="async_jobs")
    op.drop_table("async_jobs")
