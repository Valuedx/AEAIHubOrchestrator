"""Add generic embedding cache table for Intent Classifier and future nodes.

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "embedding_cache",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_emb_cache_lookup",
        "embedding_cache",
        ["tenant_id", "provider", "model", "text_hash"],
        unique=True,
    )

    op.execute("ALTER TABLE embedding_cache ADD COLUMN embedding vector")

    op.execute(
        "CREATE INDEX ix_emb_cache_embedding ON embedding_cache "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    op.execute("ALTER TABLE embedding_cache ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE embedding_cache FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_embedding_cache ON embedding_cache "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_embedding_cache ON embedding_cache")
    op.execute("ALTER TABLE embedding_cache DISABLE ROW LEVEL SECURITY")
    op.drop_table("embedding_cache")
