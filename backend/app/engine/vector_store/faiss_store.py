"""FAISS-backed vector store — file-persisted indexes with a JSON metadata sidecar."""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from app.config import settings
from app.engine.vector_store import ChunkData, ChunkResult, VectorStore

logger = logging.getLogger(__name__)

_kb_locks: dict[str, threading.Lock] = {}
_global_lock = threading.Lock()


def _lock_for(kb_id: uuid.UUID) -> threading.Lock:
    key = str(kb_id)
    with _global_lock:
        if key not in _kb_locks:
            _kb_locks[key] = threading.Lock()
        return _kb_locks[key]


def _index_dir() -> Path:
    d = Path(settings.faiss_index_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path(kb_id: uuid.UUID) -> Path:
    return _index_dir() / f"{kb_id}.index"


def _meta_path(kb_id: uuid.UUID) -> Path:
    return _index_dir() / f"{kb_id}.meta.json"


def _load_meta(kb_id: uuid.UUID) -> list[dict[str, Any]]:
    p = _meta_path(kb_id)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return []


def _save_meta(kb_id: uuid.UUID, meta: list[dict[str, Any]]) -> None:
    _meta_path(kb_id).write_text(json.dumps(meta, default=str), encoding="utf-8")


class FAISSVectorStore(VectorStore):

    # ------------------------------------------------------------------
    def add_embeddings(
        self,
        kb_id: uuid.UUID,
        tenant_id: str,
        chunks: list[ChunkData],
    ) -> int:
        if not chunks:
            return 0

        import faiss  # type: ignore[import-untyped]

        dim = len(chunks[0].embedding)
        vectors = np.array(
            [c.embedding for c in chunks], dtype=np.float32
        )
        faiss.normalize_L2(vectors)

        lock = _lock_for(kb_id)
        with lock:
            idx_path = _index_path(kb_id)
            meta = _load_meta(kb_id)

            if idx_path.exists():
                index = faiss.read_index(str(idx_path))
            else:
                index = faiss.IndexFlatIP(dim)

            index.add(vectors)

            for c in chunks:
                meta.append(
                    {
                        "chunk_id": str(c.chunk_id),
                        "document_id": str(c.document_id),
                        "tenant_id": tenant_id,
                        "content": c.content,
                        "chunk_index": c.chunk_index,
                        "metadata": c.metadata,
                    }
                )

            faiss.write_index(index, str(idx_path))
            _save_meta(kb_id, meta)

        logger.info("faiss: added %d vectors for kb=%s (dim=%d)", len(chunks), kb_id, dim)
        return len(chunks)

    # ------------------------------------------------------------------
    def search(
        self,
        kb_id: uuid.UUID,
        tenant_id: str,
        query_embedding: list[float],
        top_k: int = 5,
        score_threshold: float = 0.0,
    ) -> list[ChunkResult]:
        import faiss  # type: ignore[import-untyped]

        lock = _lock_for(kb_id)
        with lock:
            idx_path = _index_path(kb_id)
            if not idx_path.exists():
                return []

            index = faiss.read_index(str(idx_path))
            meta = _load_meta(kb_id)

        query = np.array([query_embedding], dtype=np.float32)
        faiss.normalize_L2(query)

        k = min(top_k, index.ntotal)
        if k == 0:
            return []

        distances, indices = index.search(query, k)

        results: list[ChunkResult] = []
        for score_val, idx in zip(distances[0], indices[0]):
            if idx < 0 or idx >= len(meta):
                continue
            score = float(score_val)
            if score < score_threshold:
                continue
            m = meta[idx]
            if m.get("tenant_id") != tenant_id:
                continue
            results.append(
                ChunkResult(
                    chunk_id=uuid.UUID(m["chunk_id"]),
                    document_id=uuid.UUID(m["document_id"]),
                    content=m["content"],
                    chunk_index=m["chunk_index"],
                    score=score,
                    metadata=m.get("metadata", {}),
                )
            )
        return results

    # ------------------------------------------------------------------
    def delete_by_document(
        self, kb_id: uuid.UUID, document_id: uuid.UUID
    ) -> int:
        import faiss  # type: ignore[import-untyped]

        lock = _lock_for(kb_id)
        with lock:
            idx_path = _index_path(kb_id)
            meta = _load_meta(kb_id)
            if not idx_path.exists() or not meta:
                return 0

            index = faiss.read_index(str(idx_path))

            doc_str = str(document_id)
            keep_indices = [
                i for i, m in enumerate(meta) if m["document_id"] != doc_str
            ]
            removed = len(meta) - len(keep_indices)

            if removed == 0:
                return 0

            if not keep_indices:
                os.remove(str(idx_path))
                _save_meta(kb_id, [])
                return removed

            dim = index.d
            all_vectors = np.zeros((index.ntotal, dim), dtype=np.float32)
            for i in range(index.ntotal):
                all_vectors[i] = faiss.rev_swig_ptr(
                    index.get_xb().at(i * dim), dim
                ) if hasattr(index, "get_xb") else _reconstruct_vector(index, i, dim)

            kept_vectors = all_vectors[keep_indices]
            new_index = faiss.IndexFlatIP(dim)
            new_index.add(kept_vectors)
            new_meta = [meta[i] for i in keep_indices]

            faiss.write_index(new_index, str(idx_path))
            _save_meta(kb_id, new_meta)

        return removed

    # ------------------------------------------------------------------
    def delete_by_kb(self, kb_id: uuid.UUID) -> None:
        lock = _lock_for(kb_id)
        with lock:
            for p in (_index_path(kb_id), _meta_path(kb_id)):
                if p.exists():
                    os.remove(str(p))
        logger.info("faiss: deleted index for kb=%s", kb_id)


def _reconstruct_vector(index: Any, i: int, dim: int) -> np.ndarray:
    """Reconstruct a single vector from a FAISS index."""
    vec = np.zeros(dim, dtype=np.float32)
    index.reconstruct(i, vec)
    return vec
