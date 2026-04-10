"""Create core workflow and tenant tables (before RLS).

Revision ID: 0000
Revises:
Create Date: 2026-03-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0000"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("graph_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workflow_definitions_tenant_id",
        "workflow_definitions",
        ["tenant_id"],
    )
    op.create_index(
        "ix_wf_def_tenant_name",
        "workflow_definitions",
        ["tenant_id", "name"],
    )

    op.create_table(
        "workflow_instances",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("workflow_def_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("trigger_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "context_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("current_node_id", sa.String(128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workflow_def_id"],
            ["workflow_definitions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workflow_instances_tenant_id",
        "workflow_instances",
        ["tenant_id"],
    )
    op.create_index(
        "ix_workflow_instances_status",
        "workflow_instances",
        ["status"],
    )
    op.create_index(
        "ix_wf_inst_tenant_status",
        "workflow_instances",
        ["tenant_id", "status"],
    )

    op.create_table(
        "execution_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", sa.String(128), nullable=False),
        sa.Column("node_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("input_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("output_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workflow_instances.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_execution_logs_instance_id",
        "execution_logs",
        ["instance_id"],
    )

    op.create_table(
        "tenant_tool_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("tool_name", sa.String(256), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("config_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tenant_tool_overrides_tenant_id",
        "tenant_tool_overrides",
        ["tenant_id"],
    )
    op.create_index(
        "ix_tool_override_tenant_tool",
        "tenant_tool_overrides",
        ["tenant_id", "tool_name"],
        unique=True,
    )

    op.create_table(
        "tenant_secrets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("key_name", sa.String(256), nullable=False),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tenant_secrets_tenant_id",
        "tenant_secrets",
        ["tenant_id"],
    )
    op.create_index(
        "ix_tenant_secret_tenant_key",
        "tenant_secrets",
        ["tenant_id", "key_name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_tenant_secret_tenant_key", table_name="tenant_secrets")
    op.drop_index("ix_tenant_secrets_tenant_id", table_name="tenant_secrets")
    op.drop_table("tenant_secrets")

    op.drop_index("ix_tool_override_tenant_tool", table_name="tenant_tool_overrides")
    op.drop_index("ix_tenant_tool_overrides_tenant_id", table_name="tenant_tool_overrides")
    op.drop_table("tenant_tool_overrides")

    op.drop_index("ix_execution_logs_instance_id", table_name="execution_logs")
    op.drop_table("execution_logs")

    op.drop_index("ix_wf_inst_tenant_status", table_name="workflow_instances")
    op.drop_index("ix_workflow_instances_status", table_name="workflow_instances")
    op.drop_index("ix_workflow_instances_tenant_id", table_name="workflow_instances")
    op.drop_table("workflow_instances")

    op.drop_index("ix_wf_def_tenant_name", table_name="workflow_definitions")
    op.drop_index("ix_workflow_definitions_tenant_id", table_name="workflow_definitions")
    op.drop_table("workflow_definitions")
