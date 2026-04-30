"""CTX-MGMT.A — node output artifacts table for overflow payloads.

Revision ID: 0035
Revises: 0034
Create Date: 2026-05-01

Background
----------

Today every node's output is written to ``context[node_id]`` and stays
there for the entire run AND gets persisted to ``instance.context_json``
on every node completion via ``_save_checkpoint``. ReAct nodes commonly
return giant ``iterations`` lists (10–50 kB each); a 28-node V10-shape
workflow can produce a ``context_json`` of several hundred kB. LangChain's
*State of Agent Engineering 2026* puts state-management bloat at the #1
production-failure category in agent systems.

This migration adds the durable side-channel for overflow output.

Shape
-----

  * ``node_output_artifacts`` — one row per node whose output exceeded
    the per-node budget (default 64 kB, configurable via
    ``data.config.contextOutputBudget``). Stores the FULL output JSON
    so the copilot can fetch it via ``inspect_node_artifact`` when the
    user asks for details. The in-context replacement is a small stub:
    ``{"_overflow": True, "summary": "...", "_artifact_id": "<uuid>",
    "size_bytes": N, "preview": {...}}``.

  * RLS tenant-scoped: same policy shape as every other engine-touched
    table. Cascade-deletes when the parent WorkflowInstance is deleted
    (artifacts only outlive the run as long as the run does).

  * Two indexes: ``(instance_id, node_id)`` for the inspect_node_artifact
    point lookup, and ``(tenant_id, created_at)`` for retention
    sweeps + per-tenant audit queries.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "node_output_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column(
            "instance_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_id", sa.String(128), nullable=False),
        sa.Column(
            "output_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        # Carry the budget at the time of overflow so audit can show
        # "node X exceeded its 64 kB budget by 12 kB on instance Y".
        sa.Column("budget_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # Point lookup for inspect_node_artifact.
    op.create_index(
        "ix_artifact_instance_node",
        "node_output_artifacts",
        ["instance_id", "node_id"],
    )
    # Retention sweep + per-tenant audit queries.
    op.create_index(
        "ix_artifact_tenant_created",
        "node_output_artifacts",
        ["tenant_id", "created_at"],
    )

    op.execute("ALTER TABLE node_output_artifacts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE node_output_artifacts FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_node_output_artifacts "
        "ON node_output_artifacts "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_node_output_artifacts "
        "ON node_output_artifacts"
    )
    op.drop_index("ix_artifact_tenant_created", table_name="node_output_artifacts")
    op.drop_index("ix_artifact_instance_node", table_name="node_output_artifacts")
    op.drop_table("node_output_artifacts")
