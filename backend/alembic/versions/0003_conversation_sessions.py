"""Add conversation_sessions table for stateful re-trigger pattern.

Revision ID: 0003
Revises: 0002
Create Date: 2026-03-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "conversation_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.String(256), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column(
            "messages",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conv_session_tenant_id",
        "conversation_sessions",
        ["tenant_id"],
    )
    op.create_index(
        "ix_conv_session_tenant_session",
        "conversation_sessions",
        ["tenant_id", "session_id"],
        unique=True,
    )


def downgrade():
    op.drop_index("ix_conv_session_tenant_session", table_name="conversation_sessions")
    op.drop_index("ix_conv_session_tenant_id", table_name="conversation_sessions")
    op.drop_table("conversation_sessions")
