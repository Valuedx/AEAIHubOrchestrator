"""Knowledge Base API — CRUD, document upload, search."""

from __future__ import annotations

import base64
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.security.tenant import get_tenant_id
from app.engine.embedding_provider import (
    get_embedding_dimension,
    list_embedding_options,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class KBCreate(BaseModel):
    name: str
    description: str | None = None
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    vector_store: str = "pgvector"
    chunking_strategy: str = "recursive"
    chunk_size: int = 1000
    chunk_overlap: int = 200
    semantic_threshold: float | None = None


class KBUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class KBOut(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: str | None
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    vector_store: str
    chunking_strategy: str
    chunk_size: int
    chunk_overlap: int
    semantic_threshold: float | None
    document_count: int
    status: str
    created_at: str
    updated_at: str


class DocumentOut(BaseModel):
    id: str
    kb_id: str
    filename: str
    content_type: str
    file_size: int
    chunk_count: int
    status: str
    error: str | None
    created_at: str


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=50)
    score_threshold: float = Field(default=0.0, ge=0, le=1)


class ChunkOut(BaseModel):
    content: str
    score: float
    chunk_index: int
    document_id: str
    document_filename: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class EmbeddingOptionOut(BaseModel):
    provider: str
    model: str
    dimension: int


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _kb_to_out(kb: Any) -> KBOut:
    return KBOut(
        id=str(kb.id),
        tenant_id=kb.tenant_id,
        name=kb.name,
        description=kb.description,
        embedding_provider=kb.embedding_provider,
        embedding_model=kb.embedding_model,
        embedding_dimension=kb.embedding_dimension,
        vector_store=kb.vector_store,
        chunking_strategy=kb.chunking_strategy,
        chunk_size=kb.chunk_size,
        chunk_overlap=kb.chunk_overlap,
        semantic_threshold=kb.semantic_threshold,
        document_count=kb.document_count,
        status=kb.status,
        created_at=kb.created_at.isoformat() if kb.created_at else "",
        updated_at=kb.updated_at.isoformat() if kb.updated_at else "",
    )


def _doc_to_out(doc: Any) -> DocumentOut:
    return DocumentOut(
        id=str(doc.id),
        kb_id=str(doc.kb_id),
        filename=doc.filename,
        content_type=doc.content_type,
        file_size=doc.file_size,
        chunk_count=doc.chunk_count,
        status=doc.status,
        error=doc.error,
        created_at=doc.created_at.isoformat() if doc.created_at else "",
    )


# ---------------------------------------------------------------------------
# Embedding options (drives frontend dropdowns)
# ---------------------------------------------------------------------------

@router.get("/embedding-options", response_model=list[EmbeddingOptionOut])
def get_embedding_options():
    return list_embedding_options()


@router.get("/chunking-strategies")
def get_chunking_strategies():
    return [
        {"id": "recursive", "label": "Recursive", "description": "Recursive character splitting on paragraph/sentence boundaries (recommended default)"},
        {"id": "token", "label": "Token", "description": "Split by token count using tiktoken — precise token budgets for LLMs"},
        {"id": "markdown", "label": "Markdown", "description": "Structure-aware splitting respecting headings, code fences, and lists"},
        {"id": "semantic", "label": "Semantic", "description": "Embedding-based splitting at topic-shift boundaries (more expensive)"},
    ]


@router.get("/vector-stores")
def get_vector_stores():
    return [
        {"id": "pgvector", "label": "pgvector", "description": "PostgreSQL vector extension — best for production / multi-tenant"},
        {"id": "faiss", "label": "FAISS", "description": "Local FAISS index — best for development / single-tenant"},
    ]


# ---------------------------------------------------------------------------
# Knowledge Base CRUD
# ---------------------------------------------------------------------------

@router.post("", response_model=KBOut, status_code=201)
def create_knowledge_base(
    body: KBCreate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
):
    from app.models.knowledge import KnowledgeBase

    # Validate embedding model
    try:
        dim = get_embedding_dimension(body.embedding_provider, body.embedding_model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    kb = KnowledgeBase(
        tenant_id=tenant_id,
        name=body.name,
        description=body.description,
        embedding_provider=body.embedding_provider,
        embedding_model=body.embedding_model,
        embedding_dimension=dim,
        vector_store=body.vector_store,
        chunking_strategy=body.chunking_strategy,
        chunk_size=body.chunk_size,
        chunk_overlap=body.chunk_overlap,
        semantic_threshold=body.semantic_threshold,
    )
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return _kb_to_out(kb)


@router.get("", response_model=list[KBOut])
def list_knowledge_bases(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
):
    from app.models.knowledge import KnowledgeBase

    kbs = (
        db.query(KnowledgeBase)
        .filter_by(tenant_id=tenant_id)
        .order_by(KnowledgeBase.created_at.desc())
        .all()
    )
    return [_kb_to_out(kb) for kb in kbs]


@router.get("/{kb_id}", response_model=KBOut)
def get_knowledge_base(
    kb_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
):
    from app.models.knowledge import KnowledgeBase

    kb = db.query(KnowledgeBase).filter_by(id=kb_id, tenant_id=tenant_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    return _kb_to_out(kb)


@router.put("/{kb_id}", response_model=KBOut)
def update_knowledge_base(
    kb_id: str,
    body: KBUpdate,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
):
    from app.models.knowledge import KnowledgeBase

    kb = db.query(KnowledgeBase).filter_by(id=kb_id, tenant_id=tenant_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    if body.name is not None:
        kb.name = body.name
    if body.description is not None:
        kb.description = body.description

    db.commit()
    db.refresh(kb)
    return _kb_to_out(kb)


@router.delete("/{kb_id}", status_code=204)
def delete_knowledge_base(
    kb_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
):
    from app.models.knowledge import KnowledgeBase
    from app.engine.vector_store import get_vector_store

    kb = db.query(KnowledgeBase).filter_by(id=kb_id, tenant_id=tenant_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    # Clean up vector store data
    try:
        store = get_vector_store(kb.vector_store, db=db)
        store.delete_by_kb(kb.id)
    except Exception as exc:
        logger.warning("Failed to clean up vector store for kb=%s: %s", kb_id, exc)

    db.delete(kb)
    db.commit()


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

@router.post("/{kb_id}/documents", response_model=DocumentOut, status_code=202)
async def upload_document(
    kb_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
):
    from app.models.knowledge import KnowledgeBase, KBDocument

    kb = db.query(KnowledgeBase).filter_by(id=kb_id, tenant_id=tenant_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    file_bytes = await file.read()
    file_size = len(file_bytes)

    max_bytes = settings.kb_max_file_size_mb * 1024 * 1024
    if file_size > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {settings.kb_max_file_size_mb} MB",
        )

    doc = KBDocument(
        kb_id=kb.id,
        tenant_id=tenant_id,
        filename=file.filename or "unknown",
        content_type=file.content_type or "application/octet-stream",
        file_size=file_size,
        status="pending",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # Dispatch ingestion
    from app.workers.tasks import ingest_document_task

    file_b64 = base64.b64encode(file_bytes).decode("ascii")
    ingest_document_task.delay(str(doc.id), file_b64)

    return _doc_to_out(doc)


@router.get("/{kb_id}/documents", response_model=list[DocumentOut])
def list_documents(
    kb_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
):
    from app.models.knowledge import KBDocument

    docs = (
        db.query(KBDocument)
        .filter_by(kb_id=kb_id, tenant_id=tenant_id)
        .order_by(KBDocument.created_at.desc())
        .all()
    )
    return [_doc_to_out(d) for d in docs]


@router.delete("/{kb_id}/documents/{doc_id}", status_code=204)
def delete_document(
    kb_id: str,
    doc_id: str,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
):
    from app.models.knowledge import KnowledgeBase, KBDocument
    from app.engine.vector_store import get_vector_store

    kb = db.query(KnowledgeBase).filter_by(id=kb_id, tenant_id=tenant_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    doc = db.query(KBDocument).filter_by(id=doc_id, kb_id=kb_id, tenant_id=tenant_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Remove vectors
    try:
        store = get_vector_store(kb.vector_store, db=db)
        store.delete_by_document(kb.id, doc.id)
    except Exception as exc:
        logger.warning("Failed to remove vectors for doc=%s: %s", doc_id, exc)

    was_ready = doc.status == "ready"
    db.delete(doc)
    if was_ready:
        kb.document_count = max(0, kb.document_count - 1)
    db.commit()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@router.post("/{kb_id}/search", response_model=list[ChunkOut])
def search_knowledge_base(
    kb_id: str,
    body: SearchRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(get_tenant_id),
):
    from app.engine.retriever import retrieve_chunks

    try:
        parsed_id = uuid.UUID(kb_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid knowledge base ID")

    results = retrieve_chunks(
        db=db,
        kb_ids=[parsed_id],
        query=body.query,
        tenant_id=tenant_id,
        top_k=body.top_k,
        score_threshold=body.score_threshold,
    )
    return results
