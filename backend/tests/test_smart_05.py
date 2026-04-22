"""SMART-05 — vector-backed docs search.

Mocks the embedding provider so tests stay hermetic. Pins:

* ``search_docs(use_vector=False)`` behaviour is unchanged (01b.iii
  regression).
* ``search_docs(use_vector=True)`` with a working provider returns
  results ranked by cosine similarity and stamps
  ``backend: "vector"``.
* Embedding-provider failure in the corpus-embed step falls back to
  word-overlap and stamps ``vector_fallback`` on the result.
* Embedding-provider failure in the query-embed step does the same.
* The vector index is rebuilt when the (provider, model) pair
  changes — operators who swap embedding providers don't get stale
  dim-mismatched vectors.
* Normalisation is L2 so cosine reduces to dot product; we assert
  that against a hand-built 2-chunk toy corpus where the ranking is
  obvious.
* Runner-tool dispatch wires the ``smart_05_vector_docs_enabled``
  flag through to ``docs_index.search_docs`` / ``get_node_examples``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


TENANT = "tenant-smart05"


# ---------------------------------------------------------------------------
# Test corpus + helpers
# ---------------------------------------------------------------------------


def _toy_chunks():
    from app.copilot.docs_index import DocChunk

    return [
        DocChunk(
            source_path="codewiki/classifier.md",
            title="Intent Classifier",
            anchor="intent-classifier",
            kind="codewiki",
            node_type=None,
            text="Classify incoming messages by intent and route downstream.",
            tokens=set(),  # irrelevant for vector path
        ),
        DocChunk(
            source_path="codewiki/scheduler.md",
            title="Cron Scheduler",
            anchor="cron-scheduler",
            kind="codewiki",
            node_type=None,
            text="Run workflows on a cron schedule. Not related to classification.",
            tokens=set(),
        ),
    ]


def _fake_embeddings_for(text: str, *, dim: int = 4) -> list[float]:
    """Deterministic fake embedding — L2-normalised toy vectors whose
    inner product encodes the semantic we want for the test. We
    hand-pick values so that a "classify" query leans toward chunk
    #0 (Intent Classifier)."""
    t = text.lower()
    if "classif" in t or "route" in t or "intent" in t:
        raw = [1.0, 0.2, 0.1, 0.1]
    elif "cron" in t or "schedule" in t:
        raw = [0.1, 1.0, 0.1, 0.1]
    else:
        raw = [0.1, 0.1, 1.0, 0.1]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


# ---------------------------------------------------------------------------
# Word-overlap path unchanged
# ---------------------------------------------------------------------------


def test_search_docs_default_is_word_overlap_backend():
    """No flag = word-overlap, same as 01b.iii."""
    from app.copilot import docs_index

    result = docs_index.search_docs("classify")
    assert result["backend"] == "word_overlap"


# ---------------------------------------------------------------------------
# Vector path happy path
# ---------------------------------------------------------------------------


def test_vector_search_ranks_by_cosine_and_stamps_backend():
    from app.copilot import docs_index

    chunks = _toy_chunks()
    docs_index.reset_vector_cache()

    def fake_batch(texts, provider, model, task_type="RETRIEVAL_DOCUMENT"):
        return [_fake_embeddings_for(t) for t in texts]

    def fake_single(text, provider, model, task_type="RETRIEVAL_QUERY"):
        return _fake_embeddings_for(text)

    with patch(
        "app.copilot.docs_index._get_index", return_value=chunks,
    ), patch(
        "app.engine.embedding_provider.get_embeddings_batch_sync",
        side_effect=fake_batch,
    ), patch(
        "app.engine.embedding_provider.get_embedding_sync",
        side_effect=fake_single,
    ):
        result = docs_index.search_docs(
            "classify user intent", top_k=2, use_vector=True,
        )

    assert result["backend"] == "vector"
    # Intent Classifier should rank #1; Cron Scheduler #2.
    titles = [r["title"] for r in result["results"]]
    assert titles[0] == "Intent Classifier"


# ---------------------------------------------------------------------------
# Fallback paths
# ---------------------------------------------------------------------------


def test_vector_search_falls_back_to_word_overlap_on_build_failure():
    """Embedding provider unreachable → word-overlap backend + a
    ``vector_fallback`` hint on the envelope. Agent can narrate the
    degraded path from that field."""
    from app.copilot import docs_index

    docs_index.reset_vector_cache()

    chunks = _toy_chunks()
    with patch(
        "app.copilot.docs_index._get_index", return_value=chunks,
    ), patch(
        "app.engine.embedding_provider.get_embeddings_batch_sync",
        side_effect=RuntimeError("provider down"),
    ):
        result = docs_index.search_docs("classify", use_vector=True)

    assert result["backend"] == "word_overlap"
    assert "vector_fallback" in result
    assert "embedding provider unavailable" in result["vector_fallback"]


def test_vector_search_falls_back_on_query_embed_failure():
    """Corpus embeds OK but query embed fails → still degrades to
    word-overlap, not a hard 500."""
    from app.copilot import docs_index

    docs_index.reset_vector_cache()
    chunks = _toy_chunks()

    def fake_batch(texts, provider, model, task_type="RETRIEVAL_DOCUMENT"):
        return [_fake_embeddings_for(t) for t in texts]

    with patch(
        "app.copilot.docs_index._get_index", return_value=chunks,
    ), patch(
        "app.engine.embedding_provider.get_embeddings_batch_sync",
        side_effect=fake_batch,
    ), patch(
        "app.engine.embedding_provider.get_embedding_sync",
        side_effect=RuntimeError("rate limited"),
    ):
        result = docs_index.search_docs("classify", use_vector=True)

    assert result["backend"] == "word_overlap"
    assert "vector_fallback" in result


# ---------------------------------------------------------------------------
# Cache invalidation when embedding provider changes
# ---------------------------------------------------------------------------


def test_vector_cache_rebuilds_when_provider_model_pair_changes():
    from app.copilot import docs_index
    from app.config import settings

    docs_index.reset_vector_cache()
    chunks = _toy_chunks()

    call_count = {"n": 0}

    def counting_batch(texts, provider, model, task_type="RETRIEVAL_DOCUMENT"):
        call_count["n"] += 1
        return [_fake_embeddings_for(t) for t in texts]

    def fake_single(text, provider, model, task_type="RETRIEVAL_QUERY"):
        return _fake_embeddings_for(text)

    with patch(
        "app.copilot.docs_index._get_index", return_value=chunks,
    ), patch(
        "app.engine.embedding_provider.get_embeddings_batch_sync",
        side_effect=counting_batch,
    ), patch(
        "app.engine.embedding_provider.get_embedding_sync",
        side_effect=fake_single,
    ), patch.object(settings, "smart_05_embedding_provider", "openai"), \
         patch.object(settings, "smart_05_embedding_model", "text-embedding-3-small"):
        docs_index.search_docs("x", use_vector=True)
        docs_index.search_docs("y", use_vector=True)
        # Same provider + model → cache hit on second call.
        assert call_count["n"] == 1

    # Flip the provider → expect a fresh batch.
    with patch(
        "app.copilot.docs_index._get_index", return_value=chunks,
    ), patch(
        "app.engine.embedding_provider.get_embeddings_batch_sync",
        side_effect=counting_batch,
    ), patch(
        "app.engine.embedding_provider.get_embedding_sync",
        side_effect=fake_single,
    ), patch.object(settings, "smart_05_embedding_provider", "google"), \
         patch.object(settings, "smart_05_embedding_model", "text-embedding-004"):
        docs_index.search_docs("z", use_vector=True)
        assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# Runner-tool dispatch threads the tenant flag through
# ---------------------------------------------------------------------------


def _policy_with_vector(enabled: bool):
    from app.engine.tenant_policy_resolver import EffectivePolicy
    return EffectivePolicy(
        execution_quota_per_hour=50, max_snapshots=20, mcp_pool_size=4,
        rate_limit_requests_per_window=100, rate_limit_window_seconds=60,
        smart_04_lints_enabled=True,
        smart_06_mcp_discovery_enabled=True,
        smart_02_pattern_library_enabled=True,
        smart_01_scenario_memory_enabled=False,
        smart_01_strict_promote_gate_enabled=False,
        smart_05_vector_docs_enabled=enabled,
        source={},
    )


def test_runner_tool_search_docs_passes_flag_through_when_on():
    from app.copilot import runner_tools

    draft = MagicMock()
    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy_with_vector(True),
    ), patch(
        "app.copilot.docs_index.search_docs",
    ) as fake_search:
        fake_search.return_value = {"query": "x", "match_count": 0, "results": [], "backend": "vector"}
        runner_tools.dispatch(
            "search_docs",
            db=MagicMock(), tenant_id=TENANT, draft=draft,
            args={"query": "classify"},
        )
    _, kwargs = fake_search.call_args
    assert kwargs["use_vector"] is True


def test_runner_tool_search_docs_passes_flag_through_when_off():
    from app.copilot import runner_tools

    draft = MagicMock()
    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy_with_vector(False),
    ), patch(
        "app.copilot.docs_index.search_docs",
    ) as fake_search:
        fake_search.return_value = {"query": "x", "match_count": 0, "results": [], "backend": "word_overlap"}
        runner_tools.dispatch(
            "search_docs",
            db=MagicMock(), tenant_id=TENANT, draft=draft,
            args={"query": "classify"},
        )
    _, kwargs = fake_search.call_args
    assert kwargs["use_vector"] is False


def test_runner_tool_get_node_examples_threads_flag_through():
    from app.copilot import runner_tools

    draft = MagicMock()
    with patch(
        "app.engine.tenant_policy_resolver.get_effective_policy",
        return_value=_policy_with_vector(True),
    ), patch(
        "app.copilot.docs_index.get_node_examples",
    ) as fake_gne:
        fake_gne.return_value = {"node_type": "llm_agent", "registry_entry": None, "related_sections": []}
        runner_tools.dispatch(
            "get_node_examples",
            db=MagicMock(), tenant_id=TENANT, draft=draft,
            args={"node_type": "llm_agent"},
        )
    _, kwargs = fake_gne.call_args
    assert kwargs["use_vector"] is True
