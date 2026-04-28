"""Unit tests for the central model registry.

Covers: catalogue completeness, Google/Vertex parity, tier-default
resolution, lookup + allowlist helpers, embedding-dimension back-compat.
"""

from __future__ import annotations

import pytest

from app.engine import model_registry as mr


# ---------------------------------------------------------------------------
# Catalogue coverage / parity
# ---------------------------------------------------------------------------


def test_gemini_3x_variants_present_on_both_providers() -> None:
    """Every Gemini 3.x variant must be listed for BOTH google and vertex."""
    gemini_3x_ids = {
        m.model_id for m in mr.LLM_MODELS
        if m.generation == "3.x" and m.provider == "google"
    }
    assert gemini_3x_ids, "no Gemini 3.x models registered for google"
    for mid in gemini_3x_ids:
        assert mr.find_llm_model("vertex", mid) is not None, (
            f"Gemini 3.x model {mid} missing vertex parity entry"
        )


def test_gemini_25_variants_present_on_both_providers() -> None:
    gemini_25_ids = {
        m.model_id for m in mr.LLM_MODELS
        if m.generation == "2.5" and m.provider == "google"
    }
    assert gemini_25_ids
    for mid in gemini_25_ids:
        assert mr.find_llm_model("vertex", mid) is not None, (
            f"Gemini 2.5 model {mid} missing vertex parity"
        )


def test_customtools_variant_is_copilot_only_tier() -> None:
    m = mr.find_llm_model("vertex", "gemini-3.1-pro-preview-customtools")
    assert m is not None
    assert m.tier == "custom"
    assert m.copilot_ok is True


def test_lite_tier_models_excluded_from_copilot() -> None:
    for m in mr.LLM_MODELS:
        if m.tier == "lite":
            assert m.copilot_ok is False, (
                f"{m.provider}:{m.model_id} is lite tier but marked copilot_ok"
            )


def test_no_duplicate_provider_model_pairs() -> None:
    seen: set[tuple[str, str]] = set()
    for m in mr.LLM_MODELS:
        pair = (m.provider, m.model_id)
        assert pair not in seen, f"duplicate LLM entry: {pair}"
        seen.add(pair)
    seen_e: set[tuple[str, str]] = set()
    for e in mr.EMBEDDING_MODELS:
        pair = (e.provider, e.model_id)
        assert pair not in seen_e, f"duplicate embedding entry: {pair}"
        seen_e.add(pair)


# ---------------------------------------------------------------------------
# list_llm_models filters
# ---------------------------------------------------------------------------


def test_list_llm_models_all() -> None:
    # Default excludes deprecated; include it explicitly to match the raw catalogue.
    all_models = mr.list_llm_models(include_deprecated=True)
    assert len(all_models) == len(mr.LLM_MODELS)


def test_list_llm_models_filter_by_provider() -> None:
    vertex_models = mr.list_llm_models(provider="vertex")
    assert vertex_models, "no vertex models"
    assert all(m.provider == "vertex" for m in vertex_models)


def test_list_llm_models_exclude_preview() -> None:
    ga_models = mr.list_llm_models(include_preview=False)
    assert all(not m.preview for m in ga_models)
    # 2.5-pro should be present as GA.
    assert any(m.model_id == "gemini-2.5-pro" for m in ga_models)
    # 3.x Pro should NOT be present (it's preview).
    assert not any(m.model_id == "gemini-3.1-pro-preview" for m in ga_models)


def test_list_llm_models_exclude_deprecated() -> None:
    live = mr.list_llm_models(include_deprecated=False)
    assert all(not m.deprecated for m in live)
    assert not any(m.model_id == "gemini-2.0-flash" for m in live)


def test_list_llm_models_copilot_only() -> None:
    copilot = mr.list_llm_models(copilot_only=True)
    assert copilot
    assert all(m.copilot_ok for m in copilot)
    # Lite variants filtered out.
    assert not any(m.tier == "lite" for m in copilot)


# ---------------------------------------------------------------------------
# default_llm_for tier resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("provider", "role", "expected"),
    [
        ("google", "fast", "gemini-2.5-flash"),
        ("vertex", "fast", "gemini-2.5-flash"),
        ("google", "balanced", "gemini-2.5-pro"),
        ("vertex", "balanced", "gemini-2.5-pro"),
        ("google", "powerful", "gemini-3.1-pro-preview"),
        ("vertex", "powerful", "gemini-3.1-pro-preview"),
        ("google", "copilot", "gemini-3.1-pro-preview-customtools"),
        ("vertex", "copilot", "gemini-3.1-pro-preview-customtools"),
        ("anthropic", "fast", "claude-3-5-haiku-20241022"),
        ("anthropic", "copilot", "claude-sonnet-4-6"),
        ("openai", "fast", "gpt-4o-mini"),
        ("openai", "balanced", "gpt-4o"),
    ],
)
def test_default_llm_for_tier(provider: str, role: str, expected: str) -> None:
    assert mr.default_llm_for(provider, role) == expected  # type: ignore[arg-type]


def test_default_llm_for_falls_back_when_role_missing() -> None:
    # openai has no "copilot" role — falls back to another role.
    resolved = mr.default_llm_for("openai", role="copilot")
    assert resolved in {"gpt-4o", "gpt-4o-mini"}


def test_default_llm_for_unknown_provider_raises() -> None:
    with pytest.raises(mr.UnknownModelError):
        mr.default_llm_for("nonexistent", role="fast")


def test_every_tier_default_exists_in_catalogue() -> None:
    """Guards against tier defaults drifting away from the catalogue."""
    for role, mapping in mr.LLM_TIER_DEFAULTS.items():
        for provider, model_id in mapping.items():
            assert mr.find_llm_model(provider, model_id) is not None, (
                f"tier default {role}/{provider}={model_id!r} missing from LLM_MODELS"
            )


# ---------------------------------------------------------------------------
# is_allowed_llm
# ---------------------------------------------------------------------------


def test_is_allowed_llm_accepts_known_model() -> None:
    assert mr.is_allowed_llm("vertex", "gemini-2.5-flash")


def test_is_allowed_llm_rejects_unknown_model() -> None:
    assert not mr.is_allowed_llm("vertex", "gemini-999-ultra")


def test_is_allowed_llm_can_block_preview() -> None:
    assert mr.is_allowed_llm("vertex", "gemini-3.1-pro-preview", allow_preview=True)
    assert not mr.is_allowed_llm(
        "vertex", "gemini-3.1-pro-preview", allow_preview=False
    )


def test_is_allowed_llm_can_block_deprecated() -> None:
    assert not mr.is_allowed_llm(
        "vertex", "gemini-2.0-flash", allow_deprecated=False
    )


def test_is_allowed_llm_enforces_family_allowlist() -> None:
    # Tenant allows only 2.5-generation models.
    assert mr.is_allowed_llm(
        "vertex", "gemini-2.5-flash", allowed_families={"2.5"}
    )
    assert not mr.is_allowed_llm(
        "vertex", "gemini-3.1-pro-preview", allowed_families={"2.5"}
    )
    # Multi-family allowlist.
    assert mr.is_allowed_llm(
        "vertex", "gemini-3.1-pro-preview", allowed_families={"2.5", "3.x"}
    )


# ---------------------------------------------------------------------------
# Embedding registry
# ---------------------------------------------------------------------------


def test_gemini_embedding_2_present_on_both_providers() -> None:
    assert mr.find_embedding_model("google", "gemini-embedding-2") is not None
    assert mr.find_embedding_model("vertex", "gemini-embedding-2") is not None


def test_gemini_embedding_2_is_multimodal() -> None:
    m = mr.find_embedding_model("vertex", "gemini-embedding-2")
    assert m is not None
    assert set(m.modalities) >= {"text", "image", "video", "audio"}


def test_default_embedding_for_vertex() -> None:
    assert mr.default_embedding_for("vertex") == ("vertex", "gemini-embedding-2")


def test_default_embedding_global() -> None:
    assert mr.default_embedding_for() == ("openai", "text-embedding-3-small")


def test_default_embedding_for_unknown_provider_raises() -> None:
    with pytest.raises(mr.UnknownModelError):
        mr.default_embedding_for("nonexistent")


def test_is_allowed_embedding() -> None:
    assert mr.is_allowed_embedding("openai", "text-embedding-3-small")
    assert not mr.is_allowed_embedding("openai", "unknown-emb")


def test_embedding_dimensions_backcompat() -> None:
    # Every embedding in the catalogue is in the back-compat dict.
    for m in mr.EMBEDDING_MODELS:
        assert mr.EMBEDDING_DIMENSIONS[(m.provider, m.model_id)] == m.dim


# ---------------------------------------------------------------------------
# to_dict serialisation (for /api/v1/models)
# ---------------------------------------------------------------------------


def test_llm_model_to_dict_shape() -> None:
    m = mr.find_llm_model("vertex", "gemini-3.1-pro-preview")
    assert m is not None
    d = m.to_dict()
    for key in (
        "provider", "model_id", "generation", "tier", "preview",
        "context_window", "supports_tools", "supports_thinking",
        "copilot_ok", "modalities", "deprecated", "display_name", "notes",
    ):
        assert key in d, f"missing {key!r} in LlmModel.to_dict"
    assert isinstance(d["modalities"], list)


# ---------------------------------------------------------------------------
# Multimodal coverage
# ---------------------------------------------------------------------------


def test_gemini_25_and_3x_are_fully_multimodal() -> None:
    """Gemini 2.5 + 3.x must carry the full (text+image+video+audio+pdf) set."""
    expected = {"text", "image", "video", "audio", "pdf"}
    for m in mr.LLM_MODELS:
        if m.generation in {"2.5", "3.x"}:
            assert set(m.modalities) >= expected, (
                f"{m.provider}:{m.model_id} missing multimodal coverage: {m.modalities}"
            )


def test_gemini_20_carries_some_multimodal() -> None:
    # 2.0 is deprecated but at minimum should accept text+image.
    for m in mr.LLM_MODELS:
        if m.generation == "2.0":
            assert "text" in m.modalities and "image" in m.modalities


def test_gpt4o_accepts_image() -> None:
    for m in mr.LLM_MODELS:
        if m.provider == "openai" and "gpt-4o" in m.model_id:
            assert "image" in m.modalities


def test_claude_accepts_image() -> None:
    for m in mr.LLM_MODELS:
        if m.provider == "anthropic":
            assert "image" in m.modalities, (
                f"{m.model_id} should accept image input"
            )


def test_every_llm_model_declares_text() -> None:
    # Sanity: no LLM should be image-only.
    for m in mr.LLM_MODELS:
        assert "text" in m.modalities, (
            f"{m.provider}:{m.model_id} must list text as a modality"
        )


# ---------------------------------------------------------------------------
# node_registry.json drift (MODEL-01.c)
# ---------------------------------------------------------------------------


def test_expected_node_enum_excludes_copilot_only_and_deprecated() -> None:
    enum_ids = set(mr.expected_node_enum())
    # Copilot custom-tools variant isn't surfaced to user nodes.
    assert "gemini-3.1-pro-preview-customtools" not in enum_ids
    # 2.0 is deprecated — excluded.
    assert "gemini-2.0-flash" not in enum_ids
    assert "gemini-2.0-flash-lite" not in enum_ids
    # Claude alias snapshot hidden from user-facing enum.
    assert "claude-sonnet-4-6" not in enum_ids
    # But the user-facing Gemini variants are present.
    assert "gemini-3.1-pro-preview" in enum_ids
    assert "gemini-3-flash-preview" in enum_ids
    assert "gemini-3.1-flash-lite-preview" in enum_ids
    assert "gemini-2.5-pro" in enum_ids
    assert "gemini-2.5-flash" in enum_ids
    assert "gemini-2.5-flash-lite" in enum_ids


def test_no_drift_with_shipped_node_registry_json() -> None:
    """Pin the shipped `shared/node_registry.json` against the registry.

    This is the regression guard: if someone adds a Gemini 3.2 entry to
    the registry but forgets to refresh the JSON, this test fails.
    """
    drifts = mr.node_registry_drift()
    assert drifts == [], f"node_registry.json drift: {drifts}"


def test_node_registry_drift_detects_missing(tmp_path) -> None:
    """Simulated drift: custom JSON missing a registry model."""
    import json as _json

    fake = {
        "nodes": [
            {
                "type": "llm_agent",
                "config_schema": {
                    "model": {
                        "type": "string",
                        "enum": ["gemini-2.5-flash"],  # intentionally small
                    }
                },
            }
        ]
    }
    p = tmp_path / "node_registry.json"
    p.write_text(_json.dumps(fake), encoding="utf-8")
    drifts = mr.node_registry_drift(str(p))
    assert drifts
    assert any("missing" in d for d in drifts)


def test_node_registry_drift_detects_extra(tmp_path) -> None:
    """Simulated drift: custom JSON with a model not in the registry."""
    import json as _json

    fake_enum = list(mr.expected_node_enum()) + ["gemini-9000-ultra"]
    fake = {
        "nodes": [
            {
                "type": "llm_agent",
                "config_schema": {"model": {"type": "string", "enum": fake_enum}},
            }
        ]
    }
    p = tmp_path / "node_registry.json"
    p.write_text(_json.dumps(fake), encoding="utf-8")
    drifts = mr.node_registry_drift(str(p))
    assert drifts
    assert any("not in the registry" in d for d in drifts)


def test_embedding_model_to_dict_shape() -> None:
    e = mr.find_embedding_model("vertex", "gemini-embedding-2")
    assert e is not None
    d = e.to_dict()
    for key in (
        "provider", "model_id", "dim", "preview",
        "modalities", "deprecated", "display_name", "notes",
    ):
        assert key in d
    assert isinstance(d["modalities"], list)
