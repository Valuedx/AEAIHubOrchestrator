"""Add workflow_snapshots table for version history.

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workflow_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_def_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("graph_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workflow_def_id"],
            ["workflow_definitions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_snapshot_workflow_def_id",
        "workflow_snapshots",
        ["workflow_def_id"],
    )
    op.create_index(
        "ix_snapshot_tenant_id",
        "workflow_snapshots",
        ["tenant_id"],
    )
    op.create_index(
        "ix_snapshot_def_version",
        "workflow_snapshots",
        ["workflow_def_id", "version"],
        unique=True,
    )


def downgrade():
    op.drop_index("ix_snapshot_def_version", table_name="workflow_snapshots")
    op.drop_index("ix_snapshot_tenant_id", table_name="workflow_snapshots")
    op.drop_index("ix_snapshot_workflow_def_id", table_name="workflow_snapshots")
    op.drop_table("workflow_snapshots")
