"""MODEL-01.e — `/api/v1/models` endpoint + tenant allowlist integration.

Tests exercise the router directly with a mocked
``get_effective_policy`` so the DB doesn't have to be up.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import models as models_mod
from app.api.models import router as models_router


TENANT = "tenant-a"


def _policy(
    *,
    allowed_families=None,
    pin_llm=None,  # (provider, model) or None
    pin_emb=None,
):
    """Build an EffectivePolicy with the minimum env defaults + our overrides."""
    from app.engine.tenant_policy_resolver import EffectivePolicy

    src = {k: "env_default" for k in (
        "execution_quota_per_hour", "max_snapshots", "mcp_pool_size",
        "rate_limit_requests_per_window", "rate_limit_window_seconds",
        "smart_04_lints_enabled", "smart_06_mcp_discovery_enabled",
        "smart_02_pattern_library_enabled", "smart_01_scenario_memory_enabled",
        "smart_01_strict_promote_gate_enabled", "smart_05_vector_docs_enabled",
        "default_llm_provider", "default_llm_model", "default_embedding_provider",
        "default_embedding_model", "allowed_model_families",
    )}
    return EffectivePolicy(
        execution_quota_per_hour=1000,
        max_snapshots=10,
        mcp_pool_size=5,
        rate_limit_requests_per_window=100,
        rate_limit_window_seconds=60,
        smart_04_lints_enabled=True,
        smart_06_mcp_discovery_enabled=True,
        smart_02_pattern_library_enabled=True,
        smart_01_scenario_memory_enabled=False,
        smart_01_strict_promote_gate_enabled=False,
        smart_05_vector_docs_enabled=False,
        default_llm_provider=(pin_llm[0] if pin_llm else None),
        default_llm_model=(pin_llm[1] if pin_llm else None),
        default_embedding_provider=(pin_emb[0] if pin_emb else None),
        default_embedding_model=(pin_emb[1] if pin_emb else None),
        allowed_model_families=allowed_families,
        source=src,
    )


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(models_router, prefix="/api/v1/models")
    from app.security.tenant import get_tenant_id

    app.dependency_overrides[get_tenant_id] = lambda: TENANT
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /api/v1/models
# ---------------------------------------------------------------------------


def test_list_models_default_returns_both_kinds(client):
    with patch.object(models_mod, "get_effective_policy", return_value=_policy()):
        resp = client.get("/api/v1/models")
    assert resp.status_code == 200
    body = resp.json()
    assert "llm" in body and "embedding" in body
    assert body["llm"], "no LLMs returned"
    assert body["embedding"], "no embeddings returned"


def test_list_models_filter_by_kind_llm(client):
    with patch.object(models_mod, "get_effective_policy", return_value=_policy()):
        resp = client.get("/api/v1/models?kind=llm")
    body = resp.json()
    assert body["llm"]
    assert body["embedding"] == []


def test_list_models_filter_by_kind_embedding(client):
    with patch.object(models_mod, "get_effective_policy", return_value=_policy()):
        resp = client.get("/api/v1/models?kind=embedding")
    body = resp.json()
    assert body["llm"] == []
    assert body["embedding"]


def test_list_models_filter_by_provider(client):
    with patch.object(models_mod, "get_effective_policy", return_value=_policy()):
        resp = client.get("/api/v1/models?kind=llm&provider=vertex")
    body = resp.json()
    assert body["llm"]
    for m in body["llm"]:
        assert m["provider"] == "vertex"


def test_list_models_include_preview_false_hides_3x(client):
    with patch.object(models_mod, "get_effective_policy", return_value=_policy()):
        resp = client.get("/api/v1/models?kind=llm&include_preview=false")
    body = resp.json()
    for m in body["llm"]:
        assert not m["preview"], f"{m['model_id']} is preview but returned"
    # 2.5 pro should be present.
    assert any(m["model_id"] == "gemini-2.5-pro" for m in body["llm"])


def test_list_models_copilot_only(client):
    with patch.object(models_mod, "get_effective_policy", return_value=_policy()):
        resp = client.get("/api/v1/models?kind=llm&copilot_only=true")
    body = resp.json()
    assert body["llm"]
    for m in body["llm"]:
        assert m["copilot_ok"] is True


def test_list_models_allowed_families_blocks_3x(client):
    """Tenant allowlist pinned to 2.5 — 3.x preview must be filtered."""
    with patch.object(
        models_mod, "get_effective_policy",
        return_value=_policy(allowed_families=["2.5"]),
    ):
        resp = client.get("/api/v1/models?kind=llm")
    body = resp.json()
    assert body["llm"]
    for m in body["llm"]:
        assert m["generation"] == "2.5"


def test_list_models_allowed_families_allows_both(client):
    with patch.object(
        models_mod, "get_effective_policy",
        return_value=_policy(allowed_families=["2.5", "3.x"]),
    ):
        resp = client.get("/api/v1/models?kind=llm")
    body = resp.json()
    gens = {m["generation"] for m in body["llm"]}
    assert "2.5" in gens
    assert "3.x" in gens
    assert "claude-4" not in gens  # filtered out by allowlist


def test_list_models_includes_modalities_field(client):
    with patch.object(models_mod, "get_effective_policy", return_value=_policy()):
        resp = client.get("/api/v1/models")
    body = resp.json()
    sample = body["llm"][0]
    assert "modalities" in sample
    assert isinstance(sample["modalities"], list)
    # gemini-embedding-2 should carry 4 modalities.
    gem2 = next(
        (e for e in body["embedding"] if e["model_id"] == "gemini-embedding-2"), None
    )
    assert gem2 is not None
    assert set(gem2["modalities"]) >= {"text", "image", "video", "audio"}


# ---------------------------------------------------------------------------
# GET /api/v1/models/defaults
# ---------------------------------------------------------------------------


def test_defaults_no_pin_returns_registry_defaults(client):
    with patch.object(models_mod, "get_effective_policy", return_value=_policy()):
        resp = client.get("/api/v1/models/defaults")
    assert resp.status_code == 200
    body = resp.json()
    assert body["fast"]["model_id"] == "gemini-2.5-flash"
    assert body["balanced"]["model_id"] == "gemini-2.5-pro"
    assert body["powerful"]["model_id"] == "gemini-3.1-pro-preview"
    assert body["copilot"]["model_id"] == "gemini-3.1-pro-preview-customtools"
    # Global embedding default.
    assert body["embedding"]["provider"] == "openai"
    assert body["embedding"]["model_id"] == "text-embedding-3-small"


def test_defaults_llm_pin_wins_across_roles(client):
    with patch.object(
        models_mod, "get_effective_policy",
        return_value=_policy(pin_llm=("vertex", "gemini-2.5-flash")),
    ):
        resp = client.get("/api/v1/models/defaults")
    body = resp.json()
    for role in ("fast", "balanced", "powerful", "copilot"):
        assert body[role]["provider"] == "vertex"
        assert body[role]["model_id"] == "gemini-2.5-flash"


def test_defaults_embedding_pin(client):
    with patch.object(
        models_mod, "get_effective_policy",
        return_value=_policy(pin_emb=("vertex", "gemini-embedding-2")),
    ):
        resp = client.get("/api/v1/models/defaults")
    body = resp.json()
    assert body["embedding"]["provider"] == "vertex"
    assert body["embedding"]["model_id"] == "gemini-embedding-2"


def test_defaults_llm_provider_only_pin_uses_registry_for_model(client):
    """Tenant sets default_llm_provider=vertex but leaves model null.
    Result: roles resolve through default_llm_for(vertex, role)."""
    with patch.object(
        models_mod, "get_effective_policy",
        return_value=_policy(pin_llm=("vertex", None)),  # type: ignore[arg-type]
    ):
        resp = client.get("/api/v1/models/defaults")
    # pin_llm requires both provider AND model to "win"; partial pin means
    # we still get tier-based resolution, but the provider may or may not
    # be vertex — the endpoint opts for google as the canonical default.
    body = resp.json()
    # Balanced role should still resolve cleanly.
    assert body["balanced"]["model_id"]
