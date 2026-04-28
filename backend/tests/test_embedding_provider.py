"""MODEL-01.d — embedding provider catalogue is registry-backed.

These tests pin the contract between ``embedding_provider`` and the
central ``model_registry`` so a new registry entry automatically shows
up in the KB-create dialog and ``/api/v1/knowledge-bases/embedding-options``.
"""

from __future__ import annotations

import pytest

from app.engine import embedding_provider as ep
from app.engine import model_registry as mr


def test_registry_is_back_compat_dict_mirror() -> None:
    """EMBEDDING_REGISTRY stays a dict[(provider, model)] -> dim."""
    for pair, dim in mr.EMBEDDING_DIMENSIONS.items():
        assert ep.EMBEDDING_REGISTRY[pair] == dim


def test_list_embedding_options_includes_gemini_embedding_2() -> None:
    options = ep.list_embedding_options()
    vertex_gemini_2 = next(
        (o for o in options if o["provider"] == "vertex" and o["model"] == "gemini-embedding-2"),
        None,
    )
    assert vertex_gemini_2 is not None, "gemini-embedding-2 missing from vertex options"
    assert vertex_gemini_2["dimension"] == 3072
    # Multimodal across all four Gemini-supported modalities.
    assert set(vertex_gemini_2["modalities"]) >= {"text", "image", "video", "audio"}
    assert vertex_gemini_2["preview"] is False
    assert vertex_gemini_2["display_name"]


def test_list_embedding_options_text_only_entry_has_single_modality() -> None:
    options = ep.list_embedding_options()
    openai_small = next(
        (o for o in options if o["provider"] == "openai" and o["model"] == "text-embedding-3-small"),
        None,
    )
    assert openai_small is not None
    assert openai_small["modalities"] == ["text"]


def test_list_embedding_options_excludes_deprecated() -> None:
    """Deprecated entries should never reach the picker."""
    options = ep.list_embedding_options()
    for o in options:
        entry = mr.find_embedding_model(o["provider"], o["model"])
        assert entry is not None
        assert not entry.deprecated


def test_list_embedding_options_all_fields_populated() -> None:
    """Every option carries the KB-dialog-required fields."""
    for o in ep.list_embedding_options():
        for key in ("provider", "model", "dimension", "modalities", "preview", "display_name"):
            assert key in o, f"missing {key!r} in {o}"


def test_get_embedding_dimension_for_gemini_embedding_2() -> None:
    assert ep.get_embedding_dimension("vertex", "gemini-embedding-2") == 3072
    assert ep.get_embedding_dimension("google", "gemini-embedding-2") == 3072


def test_get_embedding_dimension_raises_for_unknown() -> None:
    with pytest.raises(ValueError):
        ep.get_embedding_dimension("openai", "text-embedding-9000")
