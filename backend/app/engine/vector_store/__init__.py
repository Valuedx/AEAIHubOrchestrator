"""Pluggable vector store abstraction for knowledge-base embeddings.

Two backends ship out of the box:
  - **pgvector** (default) — stores embeddings in PostgreSQL via the pgvector extension.
  - **faiss**   — stores embeddings in a local FAISS index with a JSON metadata sidecar.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChunkData:
    """Payload passed to ``VectorStore.add_embeddings``."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    content: str
    chunk_index: int
    embedding: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChunkResult:
    """Single result returned by ``VectorStore.search``."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    content: str
    chunk_index: int
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class VectorStore(ABC):
    """Backend-agnostic interface that every store must implement."""

    @abstractmethod
    def add_embeddings(
        self,
        kb_id: uuid.UUID,
        tenant_id: str,
        chunks: list[ChunkData],
    ) -> int:
        """Insert *chunks* and return the number of vectors stored."""
        ...

    @abstractmethod
    def search(
        self,
        kb_id: uuid.UUID,
        tenant_id: str,
        query_embedding: list[float],
        top_k: int = 5,
        score_threshold: float = 0.0,
    ) -> list[ChunkResult]:
        """Return up to *top_k* results ranked by descending similarity."""
        ...

    @abstractmethod
    def delete_by_document(
        self, kb_id: uuid.UUID, document_id: uuid.UUID
    ) -> int:
        """Remove all vectors belonging to *document_id* and return the count deleted."""
        ...

    @abstractmethod
    def delete_by_kb(self, kb_id: uuid.UUID) -> None:
        """Remove **all** vectors for the given knowledge base."""
        ...


def get_vector_store(backend: str, **kwargs: Any) -> VectorStore:
    """Factory that returns the concrete store for *backend* (``"pgvector"`` | ``"faiss"``)."""

    if backend == "pgvector":
        from app.engine.vector_store.pgvector_store import PgVectorStore

        return PgVectorStore(db=kwargs["db"])

    if backend == "faiss":
        from app.engine.vector_store.faiss_store import FAISSVectorStore

        return FAISSVectorStore()

    raise ValueError(f"Unknown vector store backend: {backend!r}")
