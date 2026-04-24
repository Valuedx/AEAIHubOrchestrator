"""Multi-provider embedding abstraction.

Supports OpenAI, Google GenAI, and Google Vertex AI.  Each provider function
returns a list of float vectors.  The central model registry
(:mod:`app.engine.model_registry`) owns the catalogue of valid
``(provider, model)`` pairs + per-entry metadata (dimension, modalities,
preview flag); this module mirrors the dimension dict for back-compat and
adds the provider-specific call paths.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.config import settings
from app.engine.model_registry import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODELS,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider / model -> dimension registry — sourced from the central model
# registry so adding a new embedding (e.g. ``gemini-embedding-2``) is a
# one-line edit there; callers that import ``EMBEDDING_REGISTRY`` keep
# working unchanged.
# ---------------------------------------------------------------------------

EMBEDDING_REGISTRY: dict[tuple[str, str], int] = dict(EMBEDDING_DIMENSIONS)


def get_embedding_dimension(provider: str, model: str) -> int:
    dim = EMBEDDING_REGISTRY.get((provider, model))
    if dim is None:
        raise ValueError(
            f"Unknown embedding model: provider={provider!r}, model={model!r}. "
            f"Valid options: {list(EMBEDDING_REGISTRY.keys())}"
        )
    return dim


def list_embedding_options() -> list[dict[str, Any]]:
    """Return the full catalogue of supported embedding provider/model combos.

    Each entry carries modality + preview metadata drawn from the central
    registry. Consumers (KB-create dialog, `/api/v1/models?kind=embedding`)
    use this shape to render modality chips + dim + preview badges.
    """
    out: list[dict[str, Any]] = []
    for m in EMBEDDING_MODELS:
        if m.deprecated:
            continue
        out.append(
            {
                "provider": m.provider,
                "model": m.model_id,
                "dimension": m.dim,
                "modalities": list(m.modalities),
                "preview": m.preview,
                "display_name": m.display_name or m.model_id,
                "notes": m.notes,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Async embedding functions
# ---------------------------------------------------------------------------

async def get_embedding(
    text: str, provider: str, model: str, *, task_type: str = "RETRIEVAL_QUERY"
) -> list[float]:
    batch = await get_embeddings_batch([text], provider, model, task_type=task_type)
    return batch[0]


async def get_embeddings_batch(
    texts: list[str],
    provider: str,
    model: str,
    *,
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> list[list[float]]:
    """Embed *texts* in sub-batches respecting ``settings.embedding_batch_size``."""
    if not texts:
        return []

    batch_size = settings.embedding_batch_size
    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), batch_size):
        chunk = texts[i : i + batch_size]
        if provider == "openai":
            embs = await _embed_openai(chunk, model)
        elif provider == "google":
            embs = await _embed_google(chunk, model)
        elif provider == "vertex":
            embs = await _embed_vertex(chunk, model, task_type)
        else:
            raise ValueError(f"Unknown embedding provider: {provider!r}")
        all_embeddings.extend(embs)

    return all_embeddings


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------

async def _embed_openai(texts: list[str], model: str) -> list[list[float]]:
    if not settings.openai_api_key:
        raise ValueError("ORCHESTRATOR_OPENAI_API_KEY is not configured")

    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
    response = client.embeddings.create(model=model, input=texts)
    return [item.embedding for item in response.data]


async def _embed_google(texts: list[str], model: str) -> list[list[float]]:
    if not settings.google_api_key:
        raise ValueError("ORCHESTRATOR_GOOGLE_API_KEY is not configured")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.google_api_key)
    result = client.models.embed_content(
        model=model,
        contents=texts,
        config=types.EmbedContentConfig(output_dimensionality=get_embedding_dimension("google", model)),
    )
    return [e.values for e in result.embeddings]


async def _embed_vertex(
    texts: list[str], model: str, task_type: str
) -> list[list[float]]:
    from app.engine.llm_providers import _google_client
    # Pass None for tenant_id to use env defaults in the sync/batch path
    client = _google_client(backend="vertex", tenant_id=None)
    
    result = await client.models.embed_content(
        model=model,
        contents=texts,
    )
    return [e.values for e in result.embeddings]


# ---------------------------------------------------------------------------
# Sync wrappers (for Celery / worker threads)
# ---------------------------------------------------------------------------

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=2)


def _get_or_create_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            t = threading.Thread(target=_loop.run_forever, daemon=True)
            t.start()
        return _loop


def get_embedding_sync(
    text: str, provider: str, model: str, *, task_type: str = "RETRIEVAL_QUERY"
) -> list[float]:
    loop = _get_or_create_loop()
    future = asyncio.run_coroutine_threadsafe(
        get_embedding(text, provider, model, task_type=task_type), loop
    )
    return future.result(timeout=120)


def get_embeddings_batch_sync(
    texts: list[str],
    provider: str,
    model: str,
    *,
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> list[list[float]]:
    loop = _get_or_create_loop()
    future = asyncio.run_coroutine_threadsafe(
        get_embeddings_batch(texts, provider, model, task_type=task_type), loop
    )
    return future.result(timeout=300)
