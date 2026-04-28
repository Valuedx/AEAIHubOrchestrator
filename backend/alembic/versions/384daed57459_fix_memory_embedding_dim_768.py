"""fix_memory_embedding_dim_768

Revision ID: 384daed57459
Revises: 0033
Create Date: 2026-04-28 22:20:34.573909
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '384daed57459'
down_revision: Union[str, None] = '0033'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_EMBEDDED_TABLES = [
    ("kb_chunks", "ix_kb_chunks_embedding"),
    ("embedding_cache", "ix_emb_cache_embedding"),
    ("memory_records", "ix_memory_records_embedding"),
]


def upgrade() -> None:
    # 1. Drop existing indexes because their operators are tied to the dimension
    for _, index_name in _EMBEDDED_TABLES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")

    # 2. Pin the dimension to 768 (Vertex)
    for table, _ in _EMBEDDED_TABLES:
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN embedding TYPE vector(768) "
            f"USING embedding::vector(768)"
        )

    # 3. Recreate the HNSW indexes
    for table, index_name in _EMBEDDED_TABLES:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} "
            f"USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    for _, index_name in _EMBEDDED_TABLES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")

    # Revert to 1536
    for table, _ in _EMBEDDED_TABLES:
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN embedding TYPE vector(1536) "
            f"USING embedding::vector(1536)"
        )

    # Recreate the HNSW indexes for 1536
    for table, index_name in _EMBEDDED_TABLES:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} "
            f"USING hnsw (embedding vector_cosine_ops)"
        )
