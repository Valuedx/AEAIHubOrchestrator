"""Backend-agnostic retrieval — embed query, search vector store, return ranked chunks."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.engine.embedding_provider import get_embedding_sync
from app.engine.vector_store import get_vector_store, ChunkResult

logger = logging.getLogger(__name__)


def retrieve_chunks(
    db: Session,
    kb_ids: list[uuid.UUID],
    query: str,
    tenant_id: str,
    top_k: int = 5,
    score_threshold: float = 0.0,
) -> list[dict[str, Any]]:
    """Retrieve relevant chunks across one or more knowledge bases.

    All KBs must share the same embedding provider + model (validated by the
    caller).  The function groups KBs by vector-store backend so it can issue
    a single embed call and fan-out searches.

    Returns a list of dicts sorted by descending score, capped at *top_k*.
    """
    if not kb_ids or not query.strip():
        return []

    from app.models.knowledge import KnowledgeBase

    kbs = (
        db.query(KnowledgeBase)
        .filter(KnowledgeBase.id.in_(kb_ids), KnowledgeBase.tenant_id == tenant_id)
        .all()
    )
    if not kbs:
        logger.warning("retrieve_chunks: no KBs found for ids=%s tenant=%s", kb_ids, tenant_id)
        return []

    # Validate: all KBs must use the same embedding config
    provider = kbs[0].embedding_provider
    model = kbs[0].embedding_model
    mismatched = [
        kb for kb in kbs[1:]
        if kb.embedding_provider != provider or kb.embedding_model != model
    ]
    if mismatched:
        names = [kb.name for kb in mismatched]
        logger.warning(
            "retrieve_chunks: KBs have mixed embedding models (%s/%s vs %s); "
            "using first KB's model for the query — results may be inaccurate",
            provider, model, names,
        )

    # Embed the query once
    query_embedding = get_embedding_sync(query, provider, model, task_type="RETRIEVAL_QUERY")

    # Group by vector store backend
    by_backend: dict[str, list[KnowledgeBase]] = {}
    for kb in kbs:
        by_backend.setdefault(kb.vector_store, []).append(kb)

    all_results: list[ChunkResult] = []

    for backend, backend_kbs in by_backend.items():
        store = get_vector_store(backend, db=db)
        for kb in backend_kbs:
            results = store.search(
                kb_id=kb.id,
                tenant_id=tenant_id,
                query_embedding=query_embedding,
                top_k=top_k,
                score_threshold=score_threshold,
            )
            all_results.extend(results)

    # Sort by score descending, take top_k
    all_results.sort(key=lambda r: r.score, reverse=True)
    top = all_results[:top_k]

    # Look up document filenames for context
    doc_ids = {r.document_id for r in top}
    doc_names: dict[uuid.UUID, str] = {}
    if doc_ids:
        from app.models.knowledge import KBDocument

        docs = db.query(KBDocument.id, KBDocument.filename).filter(KBDocument.id.in_(doc_ids)).all()
        doc_names = {d.id: d.filename for d in docs}

    return [
        {
            "content": r.content,
            "score": round(r.score, 4),
            "chunk_index": r.chunk_index,
            "document_id": str(r.document_id),
            "document_filename": doc_names.get(r.document_id, ""),
            "metadata": r.metadata,
        }
        for r in top
    ]
