"""Knowledge Base models for RAG — knowledge bases, documents, and chunks."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Float,
    Integer,
    String,
    DateTime,
    ForeignKey,
    Text,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class KnowledgeBase(Base):
    """Tenant-scoped knowledge base with embedding and chunking configuration."""

    __tablename__ = "knowledge_bases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    name = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)

    embedding_provider = Column(String(32), nullable=False, default="openai")
    embedding_model = Column(String(128), nullable=False, default="text-embedding-3-small")
    embedding_dimension = Column(Integer, nullable=False, default=1536)

    vector_store = Column(String(32), nullable=False, default="pgvector")
    chunking_strategy = Column(String(32), nullable=False, default="recursive")
    chunk_size = Column(Integer, nullable=False, default=1000)
    chunk_overlap = Column(Integer, nullable=False, default=200)
    semantic_threshold = Column(Float, nullable=True)

    document_count = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="ready")

    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    documents = relationship("KBDocument", back_populates="knowledge_base", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_kb_tenant_name", "tenant_id", "name"),
    )


class KBDocument(Base):
    """A single uploaded document within a knowledge base."""

    __tablename__ = "kb_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kb_id = Column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id = Column(String(64), nullable=False, index=True)
    filename = Column(String(512), nullable=False)
    content_type = Column(String(128), nullable=False)
    file_size = Column(Integer, nullable=False, default=0)
    chunk_count = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="pending")
    error = Column(Text, nullable=True)
    metadata_json = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    knowledge_base = relationship("KnowledgeBase", back_populates="documents")

    __table_args__ = (
        Index("ix_kb_doc_tenant_kb", "tenant_id", "kb_id"),
    )


class KBChunk(Base):
    """Embedded text chunk (pgvector backend only — FAISS stores in files)."""

    __tablename__ = "kb_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(
        UUID(as_uuid=True),
        ForeignKey("kb_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kb_id = Column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id = Column(String(64), nullable=False, index=True)
    content = Column(Text, nullable=False)
    chunk_index = Column(Integer, nullable=False, default=0)
    # The embedding column is created via raw SQL in the migration (pgvector VECTOR type).
    # SQLAlchemy does not have a native Vector column type — we use raw SQL for queries.
    metadata_json = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("ix_kb_chunk_kb_tenant", "kb_id", "tenant_id"),
    )
