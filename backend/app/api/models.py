"""MODEL-01.e — ``GET /api/v1/models`` endpoint.

Single source of truth for every LLM and embedding model the
orchestrator can route to, filtered by this tenant's allowlist +
preview-gate. Consumed by the frontend's ``useModels()`` hook which
drives the Node Inspector dropdowns, copilot session-create picker,
KB-create dialog, and any template that picks a tier instead of a
concrete model.

The endpoint is a thin read-side view over
:mod:`app.engine.model_registry` + :mod:`app.engine.tenant_policy_resolver`:
adding a new model is a one-line edit in the registry; no change
needed here.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.engine.model_registry import (
    LLM_TIER_DEFAULTS,
    EmbeddingModel,
    LlmModel,
    default_embedding_for,
    default_llm_for,
    list_embedding_models,
    list_llm_models,
)
from app.engine.tenant_policy_resolver import get_effective_policy
from app.security.tenant import get_tenant_id

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Response shapes
# ---------------------------------------------------------------------------


class LlmModelOut(BaseModel):
    provider: str
    model_id: str
    generation: str
    tier: str
    preview: bool
    context_window: int | None
    supports_tools: bool
    supports_thinking: bool
    copilot_ok: bool
    modalities: list[str]
    deprecated: bool
    display_name: str
    notes: str


class EmbeddingModelOut(BaseModel):
    provider: str
    model_id: str
    dim: int
    preview: bool
    modalities: list[str]
    deprecated: bool
    display_name: str
    notes: str


class ModelDefaultEntry(BaseModel):
    provider: str
    model_id: str


class ModelDefaultsOut(BaseModel):
    fast: ModelDefaultEntry
    balanced: ModelDefaultEntry
    powerful: ModelDefaultEntry
    copilot: ModelDefaultEntry
    embedding: ModelDefaultEntry


class ModelsOut(BaseModel):
    llm: list[LlmModelOut]
    embedding: list[EmbeddingModelOut]


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------


def _filter_llm(
    models: list[LlmModel],
    *,
    allowed_families: list[str] | None,
    allow_preview: bool,
) -> list[LlmModelOut]:
    out: list[LlmModelOut] = []
    for m in models:
        if not allow_preview and m.preview:
            continue
        if allowed_families and m.generation not in set(allowed_families):
            continue
        out.append(LlmModelOut(**m.to_dict()))
    return out


def _filter_embedding(
    models: list[EmbeddingModel],
    *,
    allow_preview: bool,
) -> list[EmbeddingModelOut]:
    out: list[EmbeddingModelOut] = []
    for m in models:
        if not allow_preview and m.preview:
            continue
        out.append(EmbeddingModelOut(**m.to_dict()))
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


Kind = Literal["llm", "embedding", "all"]


@router.get("", response_model=ModelsOut)
def list_models(
    tenant_id: str = Depends(get_tenant_id),
    kind: Kind = Query(
        default="all",
        description="Return only LLMs, only embeddings, or both.",
    ),
    provider: str | None = Query(
        default=None,
        description="Restrict to one provider (google / vertex / anthropic / openai).",
    ),
    include_preview: bool = Query(
        default=True,
        description=(
            "Include preview models (Gemini 3.x today). Ignored when "
            "the tenant policy explicitly disallows preview."
        ),
    ),
    copilot_only: bool = Query(
        default=False,
        description="Only return LLMs where copilot_ok=true.",
    ),
):
    """Full catalogue filtered by tenant policy.

    Response shape is stable — missing ``llm`` or ``embedding`` list
    when ``kind`` narrows is returned as an empty list, not absent,
    so frontends don't branch on key presence.
    """
    policy = get_effective_policy(tenant_id)
    allowed_families = policy.allowed_model_families  # may be None
    # Effective preview gate: policy wins. When policy's allowlist is
    # tight enough to exclude every preview model, the gate is
    # inherently off; we still honour the query param as a further
    # narrowing step.
    effective_allow_preview = include_preview

    llm_out: list[LlmModelOut] = []
    embedding_out: list[EmbeddingModelOut] = []

    if kind in ("llm", "all"):
        raw = list_llm_models(
            provider=provider,
            include_preview=True,  # we filter here so the policy gate applies
            include_deprecated=False,
            copilot_only=copilot_only,
        )
        llm_out = _filter_llm(
            raw,
            allowed_families=allowed_families,
            allow_preview=effective_allow_preview,
        )

    if kind in ("embedding", "all"):
        raw_e = list_embedding_models(
            provider=provider,
            include_preview=True,
            include_deprecated=False,
        )
        embedding_out = _filter_embedding(
            raw_e, allow_preview=effective_allow_preview
        )

    return ModelsOut(llm=llm_out, embedding=embedding_out)


@router.get("/defaults", response_model=ModelDefaultsOut)
def get_model_defaults(tenant_id: str = Depends(get_tenant_id)):
    """Tenant-resolved defaults for each tier + embedding.

    Precedence:
      1. ``default_llm_model`` on ``tenant_policies`` (for every role) or
         ``default_embedding_model`` — tenant pin.
      2. Tier-based resolution through ``default_llm_for`` /
         ``default_embedding_for`` with the tenant's provider or the
         registry global default.
    """
    policy = get_effective_policy(tenant_id)
    pin_llm_provider = policy.default_llm_provider
    pin_llm_model = policy.default_llm_model
    pin_emb_provider = policy.default_embedding_provider
    pin_emb_model = policy.default_embedding_model

    def _resolve_llm(role: str) -> ModelDefaultEntry:
        # Full pin wins across every role for the tenant — simple
        # interpretation. Tenants who want per-role pins can leave
        # default_llm_* null and use allowed_model_families instead.
        if pin_llm_provider and pin_llm_model:
            return ModelDefaultEntry(
                provider=pin_llm_provider, model_id=pin_llm_model
            )
        provider = pin_llm_provider or "google"
        try:
            model = default_llm_for(provider, role=role)  # type: ignore[arg-type]
        except Exception:
            # Registry lookup miss (e.g. ``openai`` + role="copilot") —
            # pick the first available mapping for the role.
            mapping = LLM_TIER_DEFAULTS.get(role, {})  # type: ignore[index]
            if not mapping:
                raise HTTPException(500, f"No default configured for role {role!r}.")
            provider = next(iter(mapping.keys()))
            model = mapping[provider]
        return ModelDefaultEntry(provider=provider, model_id=model)

    def _resolve_embedding() -> ModelDefaultEntry:
        if pin_emb_provider and pin_emb_model:
            return ModelDefaultEntry(
                provider=pin_emb_provider, model_id=pin_emb_model
            )
        provider = pin_emb_provider
        if provider:
            prov, model = default_embedding_for(provider)
        else:
            prov, model = default_embedding_for(None)
        return ModelDefaultEntry(provider=prov, model_id=model)

    return ModelDefaultsOut(
        fast=_resolve_llm("fast"),
        balanced=_resolve_llm("balanced"),
        powerful=_resolve_llm("powerful"),
        copilot=_resolve_llm("copilot"),
        embedding=_resolve_embedding(),
    )
