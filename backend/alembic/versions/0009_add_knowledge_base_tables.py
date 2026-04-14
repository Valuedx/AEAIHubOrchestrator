"""Add knowledge base, document, and chunk tables for RAG.

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-14
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # -- knowledge_bases --
    op.create_table(
        "knowledge_bases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("embedding_provider", sa.String(32), nullable=False, server_default="openai"),
        sa.Column("embedding_model", sa.String(128), nullable=False, server_default="text-embedding-3-small"),
        sa.Column("embedding_dimension", sa.Integer, nullable=False, server_default="1536"),
        sa.Column("vector_store", sa.String(32), nullable=False, server_default="pgvector"),
        sa.Column("chunking_strategy", sa.String(32), nullable=False, server_default="recursive"),
        sa.Column("chunk_size", sa.Integer, nullable=False, server_default="1000"),
        sa.Column("chunk_overlap", sa.Integer, nullable=False, server_default="200"),
        sa.Column("semantic_threshold", sa.Float, nullable=True),
        sa.Column("document_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="ready"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_kb_tenant_name", "knowledge_bases", ["tenant_id", "name"])

    # -- kb_documents --
    op.create_table(
        "kb_documents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("kb_id", UUID(as_uuid=True), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(128), nullable=False),
        sa.Column("file_size", sa.Integer, nullable=False, server_default="0"),
        sa.Column("chunk_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("metadata_json", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_kb_doc_tenant_kb", "kb_documents", ["tenant_id", "kb_id"])

    # -- kb_chunks (with pgvector embedding column) --
    op.create_table(
        "kb_chunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("kb_id", UUID(as_uuid=True), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False, server_default="0"),
        sa.Column("metadata_json", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_kb_chunk_kb_tenant", "kb_chunks", ["kb_id", "tenant_id"])

    # Add the vector embedding column (variable dimension, so we use a generic vector type)
    op.execute("ALTER TABLE kb_chunks ADD COLUMN embedding vector")

    # HNSW index for fast cosine similarity
    op.execute(
        "CREATE INDEX ix_kb_chunks_embedding ON kb_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    # RLS for all tenant-scoped KB tables (follows 0001 pattern)
    for table in ("knowledge_bases", "kb_documents", "kb_chunks"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation_{table} ON {table} "
            f"USING (tenant_id = current_setting('app.tenant_id', true)) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
        )


def downgrade() -> None:
    for table in ("kb_chunks", "kb_documents", "knowledge_bases"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_table("kb_chunks")
    op.drop_table("kb_documents")
    op.drop_table("knowledge_bases")
