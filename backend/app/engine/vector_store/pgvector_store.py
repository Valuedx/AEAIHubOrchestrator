"""pgvector-backed vector store — stores embeddings in the ``kb_chunks`` table."""

from __future__ import annotations

import json as _json
import logging
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.engine.vector_store import ChunkData, ChunkResult, VectorStore

logger = logging.getLogger(__name__)

_BATCH_INSERT = text(
    """
    INSERT INTO kb_chunks
        (id, document_id, kb_id, tenant_id, content, chunk_index, embedding, metadata_json, created_at)
    VALUES
        (:id, :document_id, :kb_id, :tenant_id, :content, :chunk_index,
         :embedding::vector, :metadata_json::jsonb, NOW())
    """
)

_SEARCH = text(
    """
    SELECT id, document_id, content, chunk_index, metadata_json,
           1 - (embedding <=> :query::vector) AS score
    FROM kb_chunks
    WHERE kb_id = :kb_id
      AND tenant_id = :tenant_id
      AND 1 - (embedding <=> :query::vector) >= :threshold
    ORDER BY embedding <=> :query::vector
    LIMIT :top_k
    """
)


class PgVectorStore(VectorStore):
    def __init__(self, db: Session) -> None:
        self._db = db

    # ------------------------------------------------------------------
    def add_embeddings(
        self,
        kb_id: uuid.UUID,
        tenant_id: str,
        chunks: list[ChunkData],
    ) -> int:
        if not chunks:
            return 0

        params: list[dict[str, Any]] = []
        for c in chunks:
            params.append(
                {
                    "id": str(c.chunk_id),
                    "document_id": str(c.document_id),
                    "kb_id": str(kb_id),
                    "tenant_id": tenant_id,
                    "content": c.content,
                    "chunk_index": c.chunk_index,
                    "embedding": str(c.embedding),
                    "metadata_json": _json.dumps(c.metadata),
                }
            )

        for p in params:
            self._db.execute(_BATCH_INSERT, p)

        self._db.flush()
        logger.info("pgvector: inserted %d chunks for kb=%s", len(params), kb_id)
        return len(params)

    # ------------------------------------------------------------------
    def search(
        self,
        kb_id: uuid.UUID,
        tenant_id: str,
        query_embedding: list[float],
        top_k: int = 5,
        score_threshold: float = 0.0,
    ) -> list[ChunkResult]:
        rows = self._db.execute(
            _SEARCH,
            {
                "query": str(query_embedding),
                "kb_id": str(kb_id),
                "tenant_id": tenant_id,
                "threshold": score_threshold,
                "top_k": top_k,
            },
        ).fetchall()

        results: list[ChunkResult] = []
        for r in rows:
            meta = r.metadata_json
            if isinstance(meta, str):
                meta = _json.loads(meta)
            results.append(
                ChunkResult(
                    chunk_id=uuid.UUID(str(r.id)),
                    document_id=uuid.UUID(str(r.document_id)),
                    content=r.content,
                    chunk_index=r.chunk_index,
                    score=float(r.score),
                    metadata=meta or {},
                )
            )
        return results

    # ------------------------------------------------------------------
    def delete_by_document(
        self, kb_id: uuid.UUID, document_id: uuid.UUID
    ) -> int:
        result = self._db.execute(
            text(
                "DELETE FROM kb_chunks WHERE kb_id = :kb_id AND document_id = :doc_id"
            ),
            {"kb_id": str(kb_id), "doc_id": str(document_id)},
        )
        self._db.flush()
        return result.rowcount  # type: ignore[return-value]

    # ------------------------------------------------------------------
    def delete_by_kb(self, kb_id: uuid.UUID) -> None:
        self._db.execute(
            text("DELETE FROM kb_chunks WHERE kb_id = :kb_id"),
            {"kb_id": str(kb_id)},
        )
        self._db.flush()
