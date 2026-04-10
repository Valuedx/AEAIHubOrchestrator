"""Add instance_checkpoints table for per-node execution snapshots.

Each row records the full execution context at the moment a specific node
completed successfully, enabling post-mortem debugging and the foundation
for checkpoint-aware Langfuse tracing (Item 5).

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "instance_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_id", sa.String(128), nullable=False),
        sa.Column(
            "context_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["workflow_instances.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_checkpoint_instance_id",
        "instance_checkpoints",
        ["instance_id"],
    )
    op.create_index(
        "ix_checkpoint_instance_node",
        "instance_checkpoints",
        ["instance_id", "node_id"],
    )


def downgrade():
    op.drop_index("ix_checkpoint_instance_node", table_name="instance_checkpoints")
    op.drop_index("ix_checkpoint_instance_id", table_name="instance_checkpoints")
    op.drop_table("instance_checkpoints")
