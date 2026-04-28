"""fix_memory_embedding_dim_768

Revision ID: 384daed57459
Revises: 0033
Create Date: 2026-04-28 22:20:34.573909

⚠️  BREAKING for tenants on 1536-dim (OpenAI text-embedding-3-small) embeddings  ⚠️

This migration repins three pgvector columns to 768 dimensions to align with
Vertex ``text-embedding-005`` and Google ``gemini-embedding-2`` (the new
default per ``settings.embedding_default_provider`` / ``embedding_default_model``):

  - ``kb_chunks.embedding``        (RAG knowledge-base chunks)
  - ``embedding_cache.embedding``  (NLP node embedding cache)
  - ``memory_records.embedding``   (advanced-memory v1 records)

**pgvector cannot cast between dimensions** — `vector(1536)::vector(768)`
raises `ERROR: expected 1536 dimensions, not 768` on any non-empty row. This
migration therefore refuses to upgrade if it finds existing rows whose
dimension is not already 768. Operators must explicitly clear stale
embeddings (or re-embed via the running app) before running ``alembic upgrade
head``.

Operator runbook to migrate from 1536 → 768:

1. Backup the affected tables:
       ``pg_dump -t kb_chunks -t embedding_cache -t memory_records ... > backup.sql``
2. Null out the old vectors (the app will recompute on next read/write):
       ``UPDATE kb_chunks SET embedding = NULL;``
       ``UPDATE embedding_cache SET embedding = NULL;``
       ``UPDATE memory_records SET embedding = NULL;``
3. Now run ``alembic upgrade head``.
4. Trigger re-embedding by either re-uploading affected KB documents or
   waiting for the next conversation turn / cache miss to repopulate.

Tenants who never had 1536-dim data (fresh installs, or already on Vertex)
pass the pre-flight check trivially and the migration is a no-op for data —
only the column type and HNSW index are recreated.

Downgrade reverses to 1536; the same null-out runbook applies in reverse.
"""
from typing import Sequence, Union

from alembic import op


revision: str = '384daed57459'
down_revision: Union[str, None] = '0033'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_EMBEDDED_TABLES = [
    ("kb_chunks", "ix_kb_chunks_embedding"),
    ("embedding_cache", "ix_emb_cache_embedding"),
    ("memory_records", "ix_memory_records_embedding"),
]


def _assert_no_rows_with_dim_other_than(target_dim: int) -> None:
    """Refuse to upgrade if any embedded table has rows whose vector
    dimension is not ``target_dim``. pgvector can't cast between
    dimensions — without this guard, the ALTER COLUMN below would raise
    a confusing dimension-mismatch error mid-transaction and roll the
    whole migration back. Better to fail fast with a remediation message
    that tells the operator exactly which table to clear.
    """
    bind = op.get_bind()
    bad: list[tuple[str, int, int]] = []
    for table, _ in _EMBEDDED_TABLES:
        # vector_dims() returns NULL for NULL embeddings, so a non-embedded
        # row never trips the guard.
        row = bind.execute(
            f"""
            SELECT vector_dims(embedding) AS dim, COUNT(*) AS n
            FROM {table}
            WHERE embedding IS NOT NULL
            GROUP BY vector_dims(embedding)
            ORDER BY n DESC
            """  # noqa: S608 — table name is from a fixed module-level constant
        ).fetchall()
        for dim, n in row:
            if dim != target_dim:
                bad.append((table, int(dim), int(n)))

    if bad:
        details = "\n".join(
            f"  - {table}: {n} row(s) at dimension {dim} (expected {target_dim})"
            for table, dim, n in bad
        )
        raise RuntimeError(
            "Refusing to repin embedding columns: existing rows have a different "
            f"dimension than {target_dim}. pgvector cannot cast between dimensions, "
            "so this migration would fail mid-transaction.\n\n"
            f"{details}\n\n"
            "Remediation (see migration docstring for the full runbook):\n"
            "  1. Back up the affected tables.\n"
            "  2. UPDATE <table> SET embedding = NULL on the rows above.\n"
            "  3. Re-run `alembic upgrade head`.\n"
            "  4. Re-embed via the running app (KB re-ingest or conversation replay)."
        )


def upgrade() -> None:
    _assert_no_rows_with_dim_other_than(768)

    # 1. Drop existing indexes — their operator class is tied to the dimension.
    for _, index_name in _EMBEDDED_TABLES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")

    # 2. Pin the dimension to 768 (Vertex text-embedding-005 / Google
    #    gemini-embedding-2). The USING clause is a no-op for NULL rows
    #    and identity-preserving for already-768 rows; the pre-flight
    #    check above guarantees nothing else exists.
    for table, _ in _EMBEDDED_TABLES:
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN embedding TYPE vector(768) "
            f"USING embedding::vector(768)"
        )

    # 3. Recreate the HNSW indexes at the new dimension.
    for table, index_name in _EMBEDDED_TABLES:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} "
            f"USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    _assert_no_rows_with_dim_other_than(1536)

    for _, index_name in _EMBEDDED_TABLES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")

    # Revert to 1536 (OpenAI text-embedding-3-small).
    for table, _ in _EMBEDDED_TABLES:
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN embedding TYPE vector(1536) "
            f"USING embedding::vector(1536)"
        )

    # Recreate the HNSW indexes at 1536-dim.
    for table, index_name in _EMBEDDED_TABLES:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} "
            f"USING hnsw (embedding vector_cosine_ops)"
        )
