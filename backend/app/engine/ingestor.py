"""Document ingestion pipeline — parse, chunk, embed, store."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.engine.chunker import chunk_text
from app.engine.embedding_provider import get_embeddings_batch_sync
from app.engine.vector_store import ChunkData, get_vector_store

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Document parsing
# ---------------------------------------------------------------------------

def parse_document(file_bytes: bytes, content_type: str) -> str:
    """Extract plain text from uploaded file bytes based on MIME type."""
    ct = content_type.lower()

    if ct == "application/pdf":
        return _parse_pdf(file_bytes)

    if ct in (
        "text/plain",
        "text/markdown",
        "text/x-markdown",
        "text/csv",
        "text/html",
    ):
        return file_bytes.decode("utf-8", errors="replace")

    # Fallback: try UTF-8 decode
    try:
        return file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning("Cannot decode content_type=%s as UTF-8, using latin-1", ct)
        return file_bytes.decode("latin-1", errors="replace")


def _parse_pdf(file_bytes: bytes) -> str:
    import pymupdf  # type: ignore[import-untyped]

    pages: list[str] = []
    with pymupdf.open(stream=file_bytes, filetype="pdf") as doc:
        for page in doc:
            text = page.get_text()
            if text.strip():
                pages.append(text)
    return "\n\n".join(pages)


# ---------------------------------------------------------------------------
# Full ingestion pipeline
# ---------------------------------------------------------------------------

def ingest_document(
    db: Session,
    document_id: uuid.UUID,
    file_bytes: bytes,
    content_type: str,
    kb: Any,
) -> int:
    """Parse, chunk, embed, and store a document. Returns the chunk count.

    Parameters
    ----------
    db : Session
        Active SQLAlchemy session.
    document_id : UUID
        The ``kb_documents.id`` for the document being ingested.
    file_bytes : bytes
        Raw file content.
    content_type : str
        MIME type of the uploaded file.
    kb : KnowledgeBase
        The parent knowledge base (provides embedding and chunking config).
    """

    # 1. Parse
    raw_text = parse_document(file_bytes, content_type)
    if not raw_text.strip():
        logger.warning("Document %s produced empty text after parsing", document_id)
        return 0

    logger.info(
        "Ingestor: parsed doc=%s  chars=%d  strategy=%s",
        document_id,
        len(raw_text),
        kb.chunking_strategy,
    )

    # 2. Chunk — build embed_fn for semantic strategy
    extra_kwargs: dict[str, Any] = {}
    if kb.chunking_strategy == "semantic":
        extra_kwargs["embed_fn"] = lambda texts: get_embeddings_batch_sync(
            texts, kb.embedding_provider, kb.embedding_model
        )
        extra_kwargs["semantic_threshold"] = kb.semantic_threshold or 0.5

    chunks = chunk_text(
        raw_text,
        strategy=kb.chunking_strategy,
        chunk_size=kb.chunk_size,
        chunk_overlap=kb.chunk_overlap,
        **extra_kwargs,
    )

    if not chunks:
        logger.warning("Document %s produced 0 chunks", document_id)
        return 0

    logger.info("Ingestor: doc=%s  chunks=%d", document_id, len(chunks))

    # 3. Embed
    texts = [c.content for c in chunks]
    embeddings = get_embeddings_batch_sync(
        texts, kb.embedding_provider, kb.embedding_model
    )

    # 4. Build ChunkData payloads
    chunk_data_list: list[ChunkData] = []
    for i, (c, emb) in enumerate(zip(chunks, embeddings)):
        chunk_data_list.append(
            ChunkData(
                chunk_id=uuid.uuid4(),
                document_id=document_id,
                content=c.content,
                chunk_index=i,
                embedding=emb,
                metadata=c.metadata,
            )
        )

    # 5. Store via the vector backend
    store = get_vector_store(kb.vector_store, db=db)
    stored = store.add_embeddings(kb.id, kb.tenant_id, chunk_data_list)

    logger.info("Ingestor: stored %d chunks for doc=%s via %s", stored, document_id, kb.vector_store)
    return stored
