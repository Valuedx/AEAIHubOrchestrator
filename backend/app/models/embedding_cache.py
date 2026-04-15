"""Generic embedding cache — persists vectors for any tenant-scoped text."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, Text, Index
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class EmbeddingCache(Base):
    """Tenant-scoped embedding cache keyed by (tenant, provider, model, text_hash).

    The ``embedding`` column is added via raw SQL in the Alembic migration
    (pgvector ``VECTOR`` type) — same pattern as ``kb_chunks``.
    """

    __tablename__ = "embedding_cache"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False)
    text_hash = Column(String(64), nullable=False)
    text = Column(Text, nullable=False)
    provider = Column(String(32), nullable=False)
    model = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index(
            "ix_emb_cache_lookup",
            "tenant_id", "provider", "model", "text_hash",
            unique=True,
        ),
    )
