"""Central LLM + embedding model registry.

Single source of truth for every (provider, model) pair the orchestrator
knows about. Consumed by:

* the copilot agent runner (default picks + allowlist validation)
* engine AI nodes (LLMAgent, ReAct, Reflection, Intent Classifier,
  Entity Extractor, memory summariser) — for default model resolution
* the embeddings subsystem (KB creation, SMART-05 docs index)
* the ``GET /api/v1/models`` endpoint (frontend pickers)
* the tenant-policy allowlist layer (per-tenant model gating)

Tier-based defaults map a coarse speed/quality choice (``fast`` /
``balanced`` / ``powerful`` / ``copilot``) to a concrete model ID per
provider, so templates and node handlers can depend on the *tier*
without pinning a specific model that may rotate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ModelKind = Literal["llm", "embedding"]
ModelTier = Literal["lite", "flash", "pro", "custom"]
"""The coarse speed/cost tier. ``custom`` is used for copilot-specific
agentic-tool variants (e.g. Gemini 3.1 Pro ``-customtools``) that aren't
intended as a user-facing tier choice."""

ModelRole = Literal["fast", "balanced", "powerful", "copilot"]
"""User-facing default roles. Templates + node handlers pick a role;
the registry resolves it to a concrete model ID for the active provider."""


Modality = Literal["text", "image", "video", "audio", "pdf"]


@dataclass(frozen=True)
class LlmModel:
    provider: str  # "google" | "vertex" | "anthropic" | "openai"
    model_id: str
    generation: str  # "2.0" | "2.5" | "3.x" | "claude-4" | "gpt-4o"
    tier: ModelTier
    preview: bool
    context_window: int | None
    supports_tools: bool
    supports_thinking: bool
    copilot_ok: bool
    modalities: tuple[str, ...] = ("text",)
    """Input modalities the model natively accepts. Node handlers must
    preserve native parts for multimodal models (don't flatten to text)."""
    deprecated: bool = False
    display_name: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "generation": self.generation,
            "tier": self.tier,
            "preview": self.preview,
            "context_window": self.context_window,
            "supports_tools": self.supports_tools,
            "supports_thinking": self.supports_thinking,
            "copilot_ok": self.copilot_ok,
            "modalities": list(self.modalities),
            "deprecated": self.deprecated,
            "display_name": self.display_name or self.model_id,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class EmbeddingModel:
    provider: str  # "openai" | "google" | "vertex"
    model_id: str
    dim: int
    preview: bool
    modalities: tuple[str, ...] = ("text",)
    deprecated: bool = False
    display_name: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "dim": self.dim,
            "preview": self.preview,
            "modalities": list(self.modalities),
            "deprecated": self.deprecated,
            "display_name": self.display_name or self.model_id,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# LLM catalogue — April 2026 lineup
# ---------------------------------------------------------------------------

_GEMINI_CTX = 1_000_000

# Convenience bundles so modality lists stay consistent across provider pairs.
_FULL_MM = ("text", "image", "video", "audio", "pdf")
"""Gemini 2.5 and 3.x natively accept text + image + video + audio + PDF."""
_LIMITED_MM = ("text", "image", "audio")
"""Gemini 2.0 accepts text + image + audio (no video)."""
_GPT4O_MM = ("text", "image", "audio")
_CLAUDE_MM = ("text", "image", "pdf")

LLM_MODELS: tuple[LlmModel, ...] = (
    # --- Gemini 3.x (preview) -----------------------------------------------
    # Agentic tools variant used by the copilot runner. Not a user tier.
    LlmModel(
        "google", "gemini-3.1-pro-preview-customtools", "3.x", "custom",
        preview=True, context_window=_GEMINI_CTX,
        supports_tools=True, supports_thinking=True, copilot_ok=True,
        modalities=_FULL_MM,
        display_name="Gemini 3.1 Pro (agentic tools)",
        notes="Preview. Agentic-tool-calling-optimised; the copilot default.",
    ),
    LlmModel(
        "vertex", "gemini-3.1-pro-preview-customtools", "3.x", "custom",
        preview=True, context_window=_GEMINI_CTX,
        supports_tools=True, supports_thinking=True, copilot_ok=True,
        modalities=_FULL_MM,
        display_name="Gemini 3.1 Pro (agentic tools)",
        notes="Preview. Agentic-tool-calling-optimised; the copilot default.",
    ),
    LlmModel(
        "google", "gemini-3.1-pro-preview", "3.x", "pro",
        preview=True, context_window=_GEMINI_CTX,
        supports_tools=True, supports_thinking=True, copilot_ok=True,
        modalities=_FULL_MM,
        display_name="Gemini 3.1 Pro",
        notes="Preview. Flagship reasoning for 3.x.",
    ),
    LlmModel(
        "vertex", "gemini-3.1-pro-preview", "3.x", "pro",
        preview=True, context_window=_GEMINI_CTX,
        supports_tools=True, supports_thinking=True, copilot_ok=True,
        modalities=_FULL_MM,
        display_name="Gemini 3.1 Pro",
        notes="Preview. Flagship reasoning for 3.x.",
    ),
    LlmModel(
        "google", "gemini-3-flash-preview", "3.x", "flash",
        preview=True, context_window=_GEMINI_CTX,
        supports_tools=True, supports_thinking=True, copilot_ok=True,
        modalities=_FULL_MM,
        display_name="Gemini 3 Flash",
        notes="Preview. Cheap/fast frontier-class.",
    ),
    LlmModel(
        "vertex", "gemini-3-flash-preview", "3.x", "flash",
        preview=True, context_window=_GEMINI_CTX,
        supports_tools=True, supports_thinking=True, copilot_ok=True,
        modalities=_FULL_MM,
        display_name="Gemini 3 Flash",
        notes="Preview. Cheap/fast frontier-class.",
    ),
    LlmModel(
        "google", "gemini-3.1-flash-lite-preview", "3.x", "lite",
        preview=True, context_window=_GEMINI_CTX,
        supports_tools=True, supports_thinking=False, copilot_ok=False,
        modalities=_FULL_MM,
        display_name="Gemini 3.1 Flash-Lite",
        notes="Preview. Lowest cost / latency.",
    ),
    LlmModel(
        "vertex", "gemini-3.1-flash-lite-preview", "3.x", "lite",
        preview=True, context_window=_GEMINI_CTX,
        supports_tools=True, supports_thinking=False, copilot_ok=False,
        modalities=_FULL_MM,
        display_name="Gemini 3.1 Flash-Lite",
        notes="Preview. Lowest cost / latency.",
    ),

    # --- Gemini 2.5 (GA) ----------------------------------------------------
    LlmModel(
        "google", "gemini-2.5-pro", "2.5", "pro",
        preview=False, context_window=_GEMINI_CTX,
        supports_tools=True, supports_thinking=True, copilot_ok=True,
        modalities=_FULL_MM,
        display_name="Gemini 2.5 Pro",
    ),
    LlmModel(
        "vertex", "gemini-2.5-pro", "2.5", "pro",
        preview=False, context_window=_GEMINI_CTX,
        supports_tools=True, supports_thinking=True, copilot_ok=True,
        modalities=_FULL_MM,
        display_name="Gemini 2.5 Pro",
    ),
    LlmModel(
        "google", "gemini-2.5-flash", "2.5", "flash",
        preview=False, context_window=_GEMINI_CTX,
        supports_tools=True, supports_thinking=True, copilot_ok=True,
        modalities=_FULL_MM,
        display_name="Gemini 2.5 Flash",
    ),
    LlmModel(
        "vertex", "gemini-2.5-flash", "2.5", "flash",
        preview=False, context_window=_GEMINI_CTX,
        supports_tools=True, supports_thinking=True, copilot_ok=True,
        modalities=_FULL_MM,
        display_name="Gemini 2.5 Flash",
    ),
    LlmModel(
        "google", "gemini-2.5-flash-lite", "2.5", "lite",
        preview=False, context_window=_GEMINI_CTX,
        supports_tools=True, supports_thinking=False, copilot_ok=False,
        modalities=_FULL_MM,
        display_name="Gemini 2.5 Flash-Lite",
    ),
    LlmModel(
        "vertex", "gemini-2.5-flash-lite", "2.5", "lite",
        preview=False, context_window=_GEMINI_CTX,
        supports_tools=True, supports_thinking=False, copilot_ok=False,
        modalities=_FULL_MM,
        display_name="Gemini 2.5 Flash-Lite",
    ),

    # --- Gemini 2.0 (legacy, deprecated but selectable) ---------------------
    LlmModel(
        "google", "gemini-2.0-flash", "2.0", "flash",
        preview=False, context_window=_GEMINI_CTX,
        supports_tools=True, supports_thinking=False, copilot_ok=False,
        modalities=_LIMITED_MM,
        deprecated=True, display_name="Gemini 2.0 Flash",
        notes="Legacy. Prefer 2.5 Flash or 3.x Flash.",
    ),
    LlmModel(
        "vertex", "gemini-2.0-flash", "2.0", "flash",
        preview=False, context_window=_GEMINI_CTX,
        supports_tools=True, supports_thinking=False, copilot_ok=False,
        modalities=_LIMITED_MM,
        deprecated=True, display_name="Gemini 2.0 Flash",
        notes="Legacy. Prefer 2.5 Flash or 3.x Flash.",
    ),
    LlmModel(
        "google", "gemini-2.0-flash-lite", "2.0", "lite",
        preview=False, context_window=_GEMINI_CTX,
        supports_tools=True, supports_thinking=False, copilot_ok=False,
        modalities=("text", "image"),
        deprecated=True, display_name="Gemini 2.0 Flash-Lite",
        notes="Legacy.",
    ),

    # --- Anthropic Claude ---------------------------------------------------
    LlmModel(
        "anthropic", "claude-sonnet-4-6", "claude-4", "custom",
        preview=False, context_window=200_000,
        supports_tools=True, supports_thinking=True, copilot_ok=True,
        modalities=_CLAUDE_MM,
        display_name="Claude Sonnet 4.6",
        notes="Alias snapshot used by copilot default.",
    ),
    LlmModel(
        "anthropic", "claude-sonnet-4-20250514", "claude-4", "pro",
        preview=False, context_window=200_000,
        supports_tools=True, supports_thinking=True, copilot_ok=True,
        modalities=_CLAUDE_MM,
        display_name="Claude Sonnet 4",
    ),
    LlmModel(
        "anthropic", "claude-3-5-haiku-20241022", "claude-3.5", "flash",
        preview=False, context_window=200_000,
        supports_tools=True, supports_thinking=False, copilot_ok=True,
        modalities=("text", "image"),
        display_name="Claude 3.5 Haiku",
    ),

    # --- OpenAI -------------------------------------------------------------
    LlmModel(
        "openai", "gpt-4o", "gpt-4o", "pro",
        preview=False, context_window=128_000,
        supports_tools=True, supports_thinking=False, copilot_ok=False,
        modalities=_GPT4O_MM,
        display_name="GPT-4o",
        notes="Copilot OpenAI adapter pending (COPILOT-01b.iv).",
    ),
    LlmModel(
        "openai", "gpt-4o-mini", "gpt-4o", "flash",
        preview=False, context_window=128_000,
        supports_tools=True, supports_thinking=False, copilot_ok=False,
        modalities=("text", "image"),
        display_name="GPT-4o mini",
    ),
)


# ---------------------------------------------------------------------------
# Embedding catalogue — April 2026 lineup
# ---------------------------------------------------------------------------

EMBEDDING_MODELS: tuple[EmbeddingModel, ...] = (
    # Gemini Embedding 2 — multimodal, GA on both Google AI API + Vertex.
    EmbeddingModel(
        "google", "gemini-embedding-2", dim=3072, preview=False,
        modalities=("text", "image", "video", "audio"),
        display_name="Gemini Embedding 2",
        notes="Multimodal. Matryoshka — reducible via output_dimensionality.",
    ),
    EmbeddingModel(
        "vertex", "gemini-embedding-2", dim=3072, preview=False,
        modalities=("text", "image", "video", "audio"),
        display_name="Gemini Embedding 2",
        notes="Multimodal. Matryoshka — reducible via output_dimensionality.",
    ),
    # Gemini Embedding 1 — text-only.
    EmbeddingModel(
        "google", "gemini-embedding-001", dim=3072, preview=False,
        display_name="Gemini Embedding 001",
    ),
    EmbeddingModel(
        "vertex", "gemini-embedding-001", dim=3072, preview=False,
        display_name="Gemini Embedding 001",
    ),
    # Vertex-specific text embeddings.
    EmbeddingModel(
        "vertex", "text-embedding-005", dim=768, preview=False,
        display_name="Vertex text-embedding-005",
    ),
    EmbeddingModel(
        "vertex", "text-multilingual-embedding-002", dim=768, preview=False,
        display_name="Vertex text-multilingual-embedding-002",
    ),
    # Google AI Studio.
    EmbeddingModel(
        "google", "text-embedding-004", dim=768, preview=False,
        display_name="Google text-embedding-004",
    ),
    # OpenAI.
    EmbeddingModel(
        "openai", "text-embedding-3-small", dim=1536, preview=False,
        display_name="OpenAI text-embedding-3-small",
    ),
    EmbeddingModel(
        "openai", "text-embedding-3-large", dim=3072, preview=False,
        display_name="OpenAI text-embedding-3-large",
    ),
)


# ---------------------------------------------------------------------------
# Tier-based defaults
# ---------------------------------------------------------------------------

# role -> provider -> model_id
LLM_TIER_DEFAULTS: dict[ModelRole, dict[str, str]] = {
    "fast": {
        "google": "gemini-3-flash-preview",
        "vertex": "gemini-3-flash-preview",
        "anthropic": "claude-3-5-haiku-20241022",
        "openai": "gpt-4o-mini",
    },
    "balanced": {
        "google": "gemini-2.5-pro",
        "vertex": "gemini-2.5-pro",
        "anthropic": "claude-sonnet-4-20250514",
        "openai": "gpt-4o",
    },
    "powerful": {
        "google": "gemini-3.1-pro-preview",
        "vertex": "gemini-3.1-pro-preview",
        "anthropic": "claude-sonnet-4-20250514",
        "openai": "gpt-4o",
    },
    "copilot": {
        "google": "gemini-3-flash-preview",
        "vertex": "gemini-3-flash-preview",
        "anthropic": "claude-sonnet-4-6",
    },
}

DEFAULT_EMBEDDING_BY_PROVIDER: dict[str, str] = {
    "openai": "text-embedding-3-small",
    "vertex": "gemini-embedding-2",
    "google": "gemini-embedding-2",
}

# Process-wide global default — used when no provider is specified.
GLOBAL_DEFAULT_EMBEDDING_PROVIDER = "openai"
GLOBAL_DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


class UnknownModelError(ValueError):
    """Raised when a (provider, model_id) pair isn't in the registry."""


def list_llm_models(
    provider: str | None = None,
    *,
    include_preview: bool = True,
    include_deprecated: bool = False,
    copilot_only: bool = False,
) -> list[LlmModel]:
    """Return LLM models matching the filters. Stable ordering."""
    out: list[LlmModel] = []
    for m in LLM_MODELS:
        if provider is not None and m.provider != provider:
            continue
        if not include_preview and m.preview:
            continue
        if not include_deprecated and m.deprecated:
            continue
        if copilot_only and not m.copilot_ok:
            continue
        out.append(m)
    return out


def list_embedding_models(
    provider: str | None = None,
    *,
    include_preview: bool = True,
    include_deprecated: bool = False,
) -> list[EmbeddingModel]:
    out: list[EmbeddingModel] = []
    for m in EMBEDDING_MODELS:
        if provider is not None and m.provider != provider:
            continue
        if not include_preview and m.preview:
            continue
        if not include_deprecated and m.deprecated:
            continue
        out.append(m)
    return out


def find_llm_model(provider: str, model_id: str) -> LlmModel | None:
    for m in LLM_MODELS:
        if m.provider == provider and m.model_id == model_id:
            return m
    return None


def find_embedding_model(provider: str, model_id: str) -> EmbeddingModel | None:
    for m in EMBEDDING_MODELS:
        if m.provider == provider and m.model_id == model_id:
            return m
    return None


def default_llm_for(provider: str, role: ModelRole = "balanced") -> str:
    """Resolve a (provider, role) pair to a concrete model ID.

    Falls back across roles if a specific role isn't wired for a provider:
    copilot → powerful → balanced → fast. Raises if no default exists at all.
    """
    fallback_order: list[ModelRole] = [role]
    for r in ("copilot", "powerful", "balanced", "fast"):
        if r != role and r not in fallback_order:
            fallback_order.append(r)  # type: ignore[arg-type]
    for r in fallback_order:
        mapping = LLM_TIER_DEFAULTS.get(r, {})
        if provider in mapping:
            return mapping[provider]
    raise UnknownModelError(
        f"No default model for provider={provider!r} (role={role!r})"
    )


def default_embedding_for(provider: str | None = None) -> tuple[str, str]:
    """Return (provider, model_id) for the default embedding.

    If ``provider`` is None, returns the global default.
    """
    if provider is None:
        return (GLOBAL_DEFAULT_EMBEDDING_PROVIDER, GLOBAL_DEFAULT_EMBEDDING_MODEL)
    model = DEFAULT_EMBEDDING_BY_PROVIDER.get(provider)
    if model is None:
        raise UnknownModelError(
            f"No default embedding model for provider={provider!r}"
        )
    return (provider, model)


def is_allowed_llm(
    provider: str,
    model_id: str,
    *,
    allowed_families: Iterable[str] | None = None,
    allow_preview: bool = True,
    allow_deprecated: bool = True,
) -> bool:
    """Check whether a model is usable under the given allowlist/flags.

    ``allowed_families`` (tenant allowlist) is matched against the model's
    ``generation`` field (e.g. ``"2.5"``, ``"3.x"``, ``"claude-4"``,
    ``"gpt-4o"``). ``None`` means no family restriction.
    """
    m = find_llm_model(provider, model_id)
    if m is None:
        return False
    if not allow_preview and m.preview:
        return False
    if not allow_deprecated and m.deprecated:
        return False
    if allowed_families is not None:
        families = set(allowed_families)
        if m.generation not in families:
            return False
    return True


def is_allowed_embedding(
    provider: str,
    model_id: str,
    *,
    allow_preview: bool = True,
    allow_deprecated: bool = True,
) -> bool:
    m = find_embedding_model(provider, model_id)
    if m is None:
        return False
    if not allow_preview and m.preview:
        return False
    if not allow_deprecated and m.deprecated:
        return False
    return True


# ---------------------------------------------------------------------------
# Back-compat: expose the embedding dimension dict that
# embedding_provider.py historically relied on. Kept so callers that
# import EMBEDDING_REGISTRY keep working while we migrate.
# ---------------------------------------------------------------------------

EMBEDDING_DIMENSIONS: dict[tuple[str, str], int] = {
    (m.provider, m.model_id): m.dim for m in EMBEDDING_MODELS
}


# ---------------------------------------------------------------------------
# Drift check — node_registry.json vs. this catalogue
# ---------------------------------------------------------------------------

# Nodes with a ``model`` enum that is supposed to stay in sync with the
# registry. Updated by MODEL-01.c — any new LLM node type should be
# added here so drift is caught at startup.
_NODE_REGISTRY_ENUM_NODES: tuple[str, ...] = (
    "llm_agent",
    "react_agent",
    "llm_router",
    "reflection",
    "intent_classifier",
    "entity_extractor",
)

# Model IDs that are *not* exposed on user-facing node enums even
# though they're in the catalogue. Copilot's agentic-tools variant
# and deprecated 2.0 models fit here.
_NODE_REGISTRY_EXCLUDED: frozenset[str] = frozenset(
    {
        "gemini-3.1-pro-preview-customtools",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "claude-sonnet-4-6",  # alias snapshot kept for copilot default only
    }
)


def expected_node_enum() -> list[str]:
    """The set of model IDs an LLM node's enum should expose.

    Stable ordering: 3.x first (newest), then 2.5 GA, then non-Gemini.
    Hidden entries: copilot-only, deprecated, and explicit aliases.
    """
    # De-dup provider pairs so a single model_id appears once per enum.
    seen: set[str] = set()
    ordered: list[str] = []
    for m in LLM_MODELS:
        if m.model_id in _NODE_REGISTRY_EXCLUDED:
            continue
        if m.deprecated:
            continue
        if m.model_id in seen:
            continue
        seen.add(m.model_id)
        ordered.append(m.model_id)
    return ordered


def node_registry_drift(node_registry_path: str | None = None) -> list[str]:
    """Return a list of drift messages between the registry and
    ``shared/node_registry.json``. Empty list = no drift.

    Called at startup by :func:`app.startup_checks.check_model_registry_drift`.
    Tests call this directly to pin the contract.
    """
    import json
    from pathlib import Path

    if node_registry_path is None:
        # shared/ is two levels above backend/
        repo_root = Path(__file__).resolve().parents[3]
        node_registry_path = str(repo_root / "shared" / "node_registry.json")

    path = Path(node_registry_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [f"node_registry.json not found at {path}"]
    except json.JSONDecodeError as exc:
        return [f"node_registry.json is invalid JSON: {exc}"]

    expected = set(expected_node_enum())
    drifts: list[str] = []
    node_types = {n["type"]: n for n in data.get("nodes", [])}
    for node_type in _NODE_REGISTRY_ENUM_NODES:
        node = node_types.get(node_type)
        if node is None:
            # Optional node — absence isn't drift.
            continue
        schema = (node.get("config_schema") or {}).get("model")
        if not schema or "enum" not in schema:
            continue
        actual = set(schema["enum"])
        missing = expected - actual
        extra = actual - expected
        if missing:
            drifts.append(
                f"node {node_type!r} is missing registry models: "
                f"{sorted(missing)}"
            )
        if extra:
            drifts.append(
                f"node {node_type!r} lists models not in the registry: "
                f"{sorted(extra)}"
            )
    return drifts


__all__ = [
    "LlmModel",
    "EmbeddingModel",
    "Modality",
    "ModelKind",
    "ModelTier",
    "ModelRole",
    "LLM_MODELS",
    "EMBEDDING_MODELS",
    "LLM_TIER_DEFAULTS",
    "DEFAULT_EMBEDDING_BY_PROVIDER",
    "GLOBAL_DEFAULT_EMBEDDING_PROVIDER",
    "GLOBAL_DEFAULT_EMBEDDING_MODEL",
    "EMBEDDING_DIMENSIONS",
    "UnknownModelError",
    "list_llm_models",
    "list_embedding_models",
    "find_llm_model",
    "find_embedding_model",
    "default_llm_for",
    "default_embedding_for",
    "is_allowed_llm",
    "is_allowed_embedding",
    "expected_node_enum",
    "node_registry_drift",
]
