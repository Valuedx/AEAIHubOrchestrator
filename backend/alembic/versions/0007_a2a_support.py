"""Add A2A protocol support: is_published flag and a2a_api_keys table.

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-07
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    # ── 1. Publish flag on workflow_definitions ──────────────────────────────
    # Workflows with is_published=True appear in the tenant's A2A agent card.
    op.add_column(
        "workflow_definitions",
        sa.Column(
            "is_published",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )

    # ── 2. Inbound A2A API key table ─────────────────────────────────────────
    # Stores SHA-256 hashes of keys issued to external agents.
    # The raw key is shown once at creation time and never persisted.
    op.create_table(
        "a2a_api_keys",
        sa.Column("id",         postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id",  sa.String(64),  nullable=False),
        sa.Column("label",      sa.String(128), nullable=False),
        sa.Column("key_hash",   sa.String(64),  nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "label", name="uq_a2a_key_tenant_label"),
    )
    op.create_index("ix_a2a_key_tenant_id", "a2a_api_keys", ["tenant_id"])
    op.create_index("ix_a2a_key_hash",      "a2a_api_keys", ["key_hash"], unique=True)


def downgrade():
    op.drop_index("ix_a2a_key_hash",      table_name="a2a_api_keys")
    op.drop_index("ix_a2a_key_tenant_id", table_name="a2a_api_keys")
    op.drop_table("a2a_api_keys")
    op.drop_column("workflow_definitions", "is_published")
