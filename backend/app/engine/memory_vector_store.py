"""Dedicated vector storage for semantic/episodic memory records."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class MemoryVectorData:
    record_id: uuid.UUID
    embedding: list[float]


@dataclass
class MemoryVectorResult:
    record_id: uuid.UUID
    score: float


class MemoryVectorStore(ABC):
    @abstractmethod
    def add_embeddings(
        self,
        *,
        tenant_id: str,
        provider: str,
        model: str,
        records: list[MemoryVectorData],
    ) -> int:
        ...

    @abstractmethod
    def search(
        self,
        *,
        tenant_id: str,
        provider: str,
        model: str,
        record_ids: list[uuid.UUID],
        query_embedding: list[float],
        top_k: int,
    ) -> list[MemoryVectorResult]:
        ...


_PGVECTOR_UPDATE = text(
    """
    UPDATE memory_records
    SET embedding = :embedding::vector
    WHERE id = :record_id
    """
)

_PGVECTOR_SEARCH = text(
    """
    SELECT id, 1 - (embedding <=> CAST(:query AS vector)) AS score
    FROM memory_records
    WHERE tenant_id = :tenant_id
      AND id IN :record_ids
      AND embedding IS NOT NULL
    ORDER BY embedding <=> CAST(:query AS vector)
    LIMIT :top_k
    """
).bindparams(bindparam("record_ids", expanding=True))


class PgVectorMemoryStore(MemoryVectorStore):
    def __init__(self, db: Session) -> None:
        self._db = db

    def add_embeddings(
        self,
        *,
        tenant_id: str,
        provider: str,
        model: str,
        records: list[MemoryVectorData],
    ) -> int:
        if not records:
            return 0
        for record in records:
            self._db.execute(
                _PGVECTOR_UPDATE,
                {
                    "record_id": str(record.record_id),
                    "embedding": str(record.embedding),
                },
            )
        self._db.flush()
        return len(records)

    def search(
        self,
        *,
        tenant_id: str,
        provider: str,
        model: str,
        record_ids: list[uuid.UUID],
        query_embedding: list[float],
        top_k: int,
    ) -> list[MemoryVectorResult]:
        if not record_ids:
            return []
        rows = self._db.execute(
            _PGVECTOR_SEARCH,
            {
                "tenant_id": tenant_id,
                "record_ids": [str(rid) for rid in record_ids],
                "query": str(query_embedding),
                "top_k": top_k,
            },
        ).fetchall()
        return [
            MemoryVectorResult(record_id=uuid.UUID(str(row.id)), score=float(row.score))
            for row in rows
        ]


_memory_locks: dict[str, threading.Lock] = {}
_memory_lock_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _memory_lock_guard:
        if key not in _memory_locks:
            _memory_locks[key] = threading.Lock()
        return _memory_locks[key]


def _memory_dir() -> Path:
    root = Path(settings.faiss_index_dir) / "memory"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _index_key(*, tenant_id: str, provider: str, model: str) -> str:
    return f"{_safe_part(tenant_id)}__{_safe_part(provider)}__{_safe_part(model)}"


def _index_path(key: str) -> Path:
    return _memory_dir() / f"{key}.index"


def _meta_path(key: str) -> Path:
    return _memory_dir() / f"{key}.meta.json"


def _load_meta(key: str) -> list[dict[str, Any]]:
    path = _meta_path(key)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def _save_meta(key: str, meta: list[dict[str, Any]]) -> None:
    _meta_path(key).write_text(json.dumps(meta), encoding="utf-8")


class FAISSMemoryStore(MemoryVectorStore):
    def add_embeddings(
        self,
        *,
        tenant_id: str,
        provider: str,
        model: str,
        records: list[MemoryVectorData],
    ) -> int:
        if not records:
            return 0

        import faiss  # type: ignore[import-untyped]

        key = _index_key(tenant_id=tenant_id, provider=provider, model=model)
        lock = _lock_for(key)
        with lock:
            index_path = _index_path(key)
            meta = _load_meta(key)
            seen_ids = {m["record_id"] for m in meta}
            fresh = [r for r in records if str(r.record_id) not in seen_ids]
            if not fresh:
                return 0

            dim = len(fresh[0].embedding)
            vectors = np.array([r.embedding for r in fresh], dtype=np.float32)
            faiss.normalize_L2(vectors)

            if index_path.exists():
                index = faiss.read_index(str(index_path))
            else:
                index = faiss.IndexFlatIP(dim)

            index.add(vectors)
            for record in fresh:
                meta.append({"record_id": str(record.record_id), "tenant_id": tenant_id})

            faiss.write_index(index, str(index_path))
            _save_meta(key, meta)

        return len(fresh)

    def search(
        self,
        *,
        tenant_id: str,
        provider: str,
        model: str,
        record_ids: list[uuid.UUID],
        query_embedding: list[float],
        top_k: int,
    ) -> list[MemoryVectorResult]:
        if not record_ids:
            return []

        import faiss  # type: ignore[import-untyped]

        key = _index_key(tenant_id=tenant_id, provider=provider, model=model)
        lock = _lock_for(key)
        with lock:
            index_path = _index_path(key)
            if not index_path.exists():
                return []
            index = faiss.read_index(str(index_path))
            meta = _load_meta(key)

        if index.ntotal == 0 or not meta:
            return []

        allowed = {str(rid) for rid in record_ids}
        query = np.array([query_embedding], dtype=np.float32)
        faiss.normalize_L2(query)
        distances, indices = index.search(query, index.ntotal)

        out: list[MemoryVectorResult] = []
        for score_val, idx in zip(distances[0], indices[0]):
            if idx < 0 or idx >= len(meta):
                continue
            record_id = meta[idx]["record_id"]
            if record_id not in allowed:
                continue
            out.append(
                MemoryVectorResult(
                    record_id=uuid.UUID(record_id),
                    score=float(score_val),
                )
            )
            if len(out) >= top_k:
                break
        return out


def get_memory_vector_store(backend: str, *, db: Session | None = None) -> MemoryVectorStore:
    if backend == "pgvector":
        if db is None:
            raise ValueError("db is required for pgvector memory storage")
        return PgVectorMemoryStore(db)
    if backend == "faiss":
        return FAISSMemoryStore()
    raise ValueError(f"Unknown memory vector store backend: {backend!r}")
