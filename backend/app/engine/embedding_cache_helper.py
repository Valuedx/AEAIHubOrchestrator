"""Embedding cache helpers — get_or_embed, transient batch, and save-time precompute."""

from __future__ import annotations

import hashlib
import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()


def get_or_embed(
    tenant_id: str,
    texts: list[str],
    provider: str,
    model: str,
    db: Session,
) -> list[list[float]]:
    """Return embedding vectors for *texts*, using the DB cache where possible.

    Texts already present in ``embedding_cache`` are read directly.  Missing
    texts are batch-embedded via the embedding provider, upserted into the
    cache, and then returned.
    """
    if not texts:
        return []

    from sqlalchemy import text as sa_text

    hashes = [_text_hash(t) for t in texts]
    vectors: list[list[float] | None] = [None] * len(texts)

    unique_hashes = list(set(hashes))

    rows = db.execute(
        sa_text(
            "SELECT text_hash, embedding::text AS embedding_text "
            "FROM embedding_cache "
            "WHERE tenant_id = :tenant_id AND provider = :provider "
            "AND model = :model AND text_hash = ANY(:hashes) "
            "AND embedding IS NOT NULL"
        ),
        {
            "tenant_id": tenant_id,
            "provider": provider,
            "model": model,
            "hashes": unique_hashes,
        },
    ).fetchall()

    cached: dict[str, list[float]] = {}
    for row in rows:
        try:
            cached[row.text_hash] = [
                float(x) for x in row.embedding_text.strip("[]").split(",")
            ]
        except (ValueError, AttributeError):
            logger.warning("Failed to parse cached embedding for hash %s", row.text_hash)

    for i, h in enumerate(hashes):
        if h in cached:
            vectors[i] = cached[h]

    missing_indices = [i for i, v in enumerate(vectors) if v is None]
    if missing_indices:
        missing_texts = [texts[i] for i in missing_indices]
        from app.engine.embedding_provider import get_embeddings_batch_sync

        new_vecs = get_embeddings_batch_sync(missing_texts, provider, model)

        for idx, vec in zip(missing_indices, new_vecs):
            vectors[idx] = vec
            h = hashes[idx]
            vec_literal = "[" + ",".join(str(f) for f in vec) + "]"
            db.execute(
                sa_text(
                    "INSERT INTO embedding_cache "
                    "(id, tenant_id, text_hash, text, provider, model, embedding, created_at) "
                    "VALUES (gen_random_uuid(), :tenant_id, :text_hash, :text, :provider, :model, "
                    ":embedding ::vector, now()) "
                    "ON CONFLICT (tenant_id, provider, model, text_hash) DO NOTHING"
                ),
                {
                    "tenant_id": tenant_id,
                    "text_hash": h,
                    "text": texts[idx],
                    "provider": provider,
                    "model": model,
                    "embedding": vec_literal,
                },
            )
        db.commit()

    return [v if v is not None else [] for v in vectors]


def embed_batch_transient(
    texts: list[str],
    provider: str,
    model: str,
) -> list[list[float]]:
    """Compute embeddings on-the-fly without any DB interaction."""
    if not texts:
        return []
    from app.engine.embedding_provider import get_embeddings_batch_sync

    return get_embeddings_batch_sync(texts, provider, model)


def _intent_text(intent_cfg: dict) -> str:
    """Build the embedding text for a single intent config dict."""
    pieces: list[str] = []
    if intent_cfg.get("name"):
        pieces.append(str(intent_cfg["name"]))
    if intent_cfg.get("description"):
        pieces.append(str(intent_cfg["description"]))
    for ex in intent_cfg.get("examples", []):
        if ex:
            pieces.append(str(ex))
    return " ".join(p.strip() for p in pieces if p).strip() or str(
        intent_cfg.get("name", "")
    )


def precompute_node_embeddings(
    graph_json: dict,
    tenant_id: str,
    db: Session,
) -> list[str]:
    """Scan for Intent Classifier nodes with cacheEmbeddings=true and precompute.

    Returns a list of warning strings (empty = all OK, no-op if nothing to do).
    """
    warnings: list[str] = []

    for node in graph_json.get("nodes", []):
        data = node.get("data", {})
        if data.get("label") != "Intent Classifier":
            continue
        config = data.get("config", {})
        if not config.get("cacheEmbeddings", False):
            continue
        mode = config.get("mode", "hybrid")
        if mode == "llm_only":
            continue

        intents = config.get("intents", [])
        if not intents:
            warnings.append(
                f"Node {node.get('id', '?')}: Intent Classifier has cacheEmbeddings=true "
                f"but no intents configured"
            )
            continue

        provider = config.get("embeddingProvider", "openai")
        model = config.get("embeddingModel", "text-embedding-3-small")

        texts = [_intent_text(it) for it in intents]
        try:
            get_or_embed(tenant_id, texts, provider, model, db)
            logger.info(
                "Precomputed %d intent embeddings for node %s (provider=%s, model=%s)",
                len(texts),
                node.get("id", "?"),
                provider,
                model,
            )
        except Exception as exc:
            msg = (
                f"Node {node.get('id', '?')}: failed to precompute intent embeddings: {exc}"
            )
            logger.warning(msg)
            warnings.append(msg)

    return warnings
