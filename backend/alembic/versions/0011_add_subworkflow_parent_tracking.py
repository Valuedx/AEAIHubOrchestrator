"""Add parent tracking columns to workflow_instances for sub-workflow support.

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_instances",
        sa.Column(
            "parent_instance_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workflow_instances.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "workflow_instances",
        sa.Column("parent_node_id", sa.String(128), nullable=True),
    )
    op.create_index(
        "ix_wf_inst_parent",
        "workflow_instances",
        ["parent_instance_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_wf_inst_parent", table_name="workflow_instances")
    op.drop_column("workflow_instances", "parent_node_id")
    op.drop_column("workflow_instances", "parent_instance_id")
