"""Declare a fixed pgvector dimension and rebuild the HNSW indexes.

Revision ID: 0016
Revises: 0015
Create Date: 2026-04-19

Completes the S1-14 follow-up left over from S1-12: migrations 0009,
0010, and 0012 created ``vector`` columns without a fixed dimension,
so the HNSW indexes they tried to build failed on any modern pgvector
(we worked around that in d1cf695 by swallowing the CREATE INDEX
error in an EXCEPTION block — which kept alembic happy but left
similarity queries doing a sequential scan).

This migration:

  1. ALTERs ``kb_chunks.embedding``, ``embedding_cache.embedding``,
     and ``memory_records.embedding`` to ``vector(1536)`` — the
     dimension produced by ``text-embedding-3-small``, which is
     ``settings.embedding_default_model`` in ``app/config.py``.
  2. (Re)creates the HNSW indexes — they succeed now that the column
     has a fixed dim.

Operators running a non-1536 embedding model (e.g. Google's
``text-embedding-004`` is 768) must either truncate the affected
tables before ``alembic upgrade head`` or write a custom migration
pinning the correct dimension. A ``USING`` cast lets the ALTER
succeed on empty tables and on 1536-dim data; mismatched data will
fail loudly, which is the correct failure mode.
"""

from alembic import op


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


_EMBEDDED_TABLES = [
    ("kb_chunks", "ix_kb_chunks_embedding"),
    ("embedding_cache", "ix_emb_cache_embedding"),
    ("memory_records", "ix_memory_records_embedding"),
]


def upgrade() -> None:
    # 1. Pin the dimension. The USING cast is a no-op on fresh tables
    #    and on rows that already hold a 1536-dim vector; it will error
    #    on mismatched data, which is the right failure mode.
    for table, _ in _EMBEDDED_TABLES:
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN embedding TYPE vector(1536) "
            f"USING embedding::vector(1536)"
        )

    # 2. Rebuild the HNSW indexes. Use IF NOT EXISTS because the older
    #    migrations may have silently succeeded on permissive pgvector
    #    builds and left the index in place.
    for table, index_name in _EMBEDDED_TABLES:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} "
            f"USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    for _, index_name in _EMBEDDED_TABLES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
    # Revert the dimension pin so future re-upgrades match the pre-0016
    # shape. The cast from a fixed-dim vector back to a dimension-less
    # one is allowed by pgvector.
    for table, _ in _EMBEDDED_TABLES:
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN embedding TYPE vector "
            f"USING embedding::vector"
        )
