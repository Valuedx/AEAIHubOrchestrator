# Model Registry

Single source of truth for every LLM and embedding model the orchestrator knows about. Lives at `backend/app/engine/model_registry.py` and is consumed by the copilot, every AI node, the embeddings subsystem, the KB pipeline, and the `/api/v1/models` endpoint (landing in MODEL-01.e).

> **One-line summary.** Pick a model anywhere in the app — copilot session, LLMAgent node, KB creation — and it resolves through one registry. Tier-based defaults (`fast` / `balanced` / `powerful` / `copilot`) let templates and nodes depend on a *tier* instead of pinning a specific model that may rotate.

---

## §1. Why a registry

Before MODEL-01 every model string was hardcoded in ~12 places:

* `backend/app/copilot/agent.py::DEFAULT_MODEL_BY_PROVIDER`
* `shared/node_registry.json` enums on every LLM node (6 nodes × 2 fields)
* `backend/app/engine/node_handlers.py` (LLMAgent fallback = `gemini-2.5-flash`)
* `backend/app/engine/react_loop.py`, `reflection_handler.py`, `intent_classifier.py`, `entity_extractor.py`, `memory_service.py` — all fell back to `gemini-2.5-flash`
* `backend/app/engine/embedding_provider.py::EMBEDDING_REGISTRY` — six pairs
* `frontend/src/lib/templates/index.ts` — 16 hardcoded `gemini-2.5-flash`
* `backend/alembic/versions/*_tenant_policies.py` + `backend/app/models/memory.py` — DB defaults

The copilot moved to Gemini 3.x in `a5ab31a`, but none of the engine nodes or templates moved with it. A Vertex tenant ran copilot on 3.x and workflows on 2.5 — silent drift. **MODEL-01 closes the drift and makes both 2.x and 3.x equally first-class**, with tier-based defaults so individual call sites don't pin.

---

## §2. What's in the registry

### 2.1 LLM catalogue (April 2026)

Every entry carries `provider`, `model_id`, `generation`, `tier`, `preview`, `context_window`, `supports_tools`, `supports_thinking`, `copilot_ok`, `modalities`, `deprecated`, `display_name`, `notes`. Listed by generation:

**Gemini 3.x (all preview, all fully multimodal: text / image / video / audio / PDF)**

| `provider` | `model_id` | `tier` | Copilot-ok? | Notes |
|---|---|---|---|---|
| `google` / `vertex` | `gemini-3.1-pro-preview-customtools` | `custom` | ✅ | Agentic-tool-calling-optimised variant. Used by the copilot runner. |
| `google` / `vertex` | `gemini-3.1-pro-preview` | `pro` | ✅ | Flagship reasoning for 3.x. |
| `google` / `vertex` | `gemini-3-flash-preview` | `flash` | ✅ | Cheap/fast frontier-class. |
| `google` / `vertex` | `gemini-3.1-flash-lite-preview` | `lite` | ❌ | Lowest cost / latency. Not copilot-ok (no thinking). |

**Gemini 2.5 (GA, fully multimodal)**

| `provider` | `model_id` | `tier` | Copilot-ok? |
|---|---|---|---|
| `google` / `vertex` | `gemini-2.5-pro` | `pro` | ✅ |
| `google` / `vertex` | `gemini-2.5-flash` | `flash` | ✅ |
| `google` / `vertex` | `gemini-2.5-flash-lite` | `lite` | ❌ |

**Gemini 2.0 (legacy, deprecated but still selectable)**

| `provider` | `model_id` | `tier` | Modalities |
|---|---|---|---|
| `google` / `vertex` | `gemini-2.0-flash` | `flash` | text + image + audio |
| `google` | `gemini-2.0-flash-lite` | `lite` | text + image |

**Anthropic Claude (GA, text + image + PDF)**

| `model_id` | `tier` | Notes |
|---|---|---|
| `claude-sonnet-4-6` | `custom` | Snapshot alias used by copilot default. |
| `claude-sonnet-4-20250514` | `pro` | Latest Sonnet 4. |
| `claude-3-5-haiku-20241022` | `flash` | text + image only. |

**OpenAI (GA, text + image + audio on full GPT-4o)**

| `model_id` | `tier` | Notes |
|---|---|---|
| `gpt-4o` | `pro` | Copilot adapter pending (COPILOT-01b.iv). |
| `gpt-4o-mini` | `flash` | text + image. |

### 2.2 Embedding catalogue

Every entry carries `provider`, `model_id`, `dim`, `preview`, `modalities`, `deprecated`, `display_name`, `notes`. **`gemini-embedding-2` is Google's first natively multimodal embedding model — it embeds text, images, video, and audio into the same 3072-dim vector space (Matryoshka — reducible via `output_dimensionality`).**

| `provider` | `model_id` | `dim` | Modalities |
|---|---|---|---|
| `google` / `vertex` | `gemini-embedding-2` | 3072 | text + image + video + audio |
| `google` / `vertex` | `gemini-embedding-001` | 3072 | text |
| `vertex` | `text-embedding-005` | 768 | text |
| `vertex` | `text-multilingual-embedding-002` | 768 | text |
| `google` | `text-embedding-004` | 768 | text |
| `openai` | `text-embedding-3-small` | 1536 | text |
| `openai` | `text-embedding-3-large` | 3072 | text |

---

## §3. Tier-based defaults

The registry maps four user-facing roles to a concrete model ID per provider. Templates, engine nodes, and the copilot pick a *role*; the registry resolves it. Swapping a model family becomes a one-line edit.

| Role | Intent | `google` / `vertex` | `anthropic` | `openai` |
|---|---|---|---|---|
| `fast` | cheapest / lowest latency | `gemini-2.5-flash` | `claude-3-5-haiku-20241022` | `gpt-4o-mini` |
| `balanced` | prod default | `gemini-2.5-pro` | `claude-sonnet-4-20250514` | `gpt-4o` |
| `powerful` | flagship / complex reasoning | `gemini-3.1-pro-preview` | `claude-sonnet-4-20250514` | `gpt-4o` |
| `copilot` | agentic tools loop | `gemini-3.1-pro-preview-customtools` | `claude-sonnet-4-6` | *(pending)* |

**Default policy.** GA variants are preferred for `fast` and `balanced` so workflows don't silently run on preview models. `powerful` and `copilot` roles opt into Gemini 3.x preview explicitly — any tenant that disables preview via the allowlist falls back to 2.5-pro automatically.

`default_llm_for(provider, role)` resolves the role. Missing roles fall back in this order: `copilot → powerful → balanced → fast`, so adding a new provider needs at most one role populated.

**Default embedding.** Global default is `openai` / `text-embedding-3-small` (GA, cheapest, widest compatibility). Vertex / Google tenants are offered `gemini-embedding-2` at picker time — see [§5](#5-kb-picker-ux).

---

## §4. API

```python
from app.engine.model_registry import (
    LLM_MODELS, EMBEDDING_MODELS,           # full catalogues
    list_llm_models, list_embedding_models,  # filtered views
    find_llm_model, find_embedding_model,    # (provider, model_id) lookup
    default_llm_for,                         # role → model_id
    default_embedding_for,                   # provider → (provider, model_id)
    is_allowed_llm, is_allowed_embedding,    # tenant allowlist + preview gate
    EMBEDDING_DIMENSIONS,                    # back-compat dim dict
    UnknownModelError,
)

# Resolve a default model for an LLMAgent node on Vertex tenants:
model_id = default_llm_for("vertex", role="balanced")  # -> "gemini-2.5-pro"

# Validate user-picked model against a conservative tenant allowlist:
ok = is_allowed_llm(
    "vertex", user_picked_model,
    allowed_families={"2.5"},   # tenant pinned to 2.5 generation
    allow_preview=False,         # no preview models
)

# Enumerate copilot-capable models only (hides lite + non-tool variants):
for m in list_llm_models(copilot_only=True):
    print(m.display_name, m.modalities)
```

### 4.1 Filters

`list_llm_models(provider=None, *, include_preview=True, include_deprecated=False, copilot_only=False)` and `list_embedding_models(provider=None, *, include_preview=True, include_deprecated=False)` both return stable-ordered lists. Default hides deprecated — pass `include_deprecated=True` for admin UIs.

### 4.2 Allowlist rules

`is_allowed_llm` checks:

1. The `(provider, model_id)` pair exists in the catalogue.
2. `allow_preview=False` blocks any entry with `preview=True`.
3. `allow_deprecated=False` blocks any entry with `deprecated=True`.
4. `allowed_families` (a set of `generation` strings) restricts to matching entries.

`allowed_families=None` means no family restriction. Tenant allowlist storage lands in MODEL-01.e — see [§8](#8-planned-mode-01e-tenant-overrides).

---

## §5. KB picker UX (embeddings)

Every KB-creation or editing dialog must expose the embedding picker grouped by provider, with:

* **dimension** displayed next to the model name so users understand storage cost.
* **modality chips** (text / image / video / audio) — `gemini-embedding-2` shows all four, letting users pick it intentionally for mixed-media KBs.
* **preview badge** where applicable (none ship with `preview=True` today; placeholder for when Google's next embedding preview drops).

If a tenant has set a `default_embedding_provider` / `default_embedding_model` (planned in 01.e), the picker pre-selects it. Otherwise the global default (`openai` / `text-embedding-3-small`) is pre-selected. Changing an embedding model on an existing KB requires a reindex — the dialog surfaces a warning and a "reindex now" button.

---

## §6. Multimodal guarantee

Multimodal support is tracked per-model. Node handlers must preserve native parts for multimodal-capable models instead of flattening to text. See [the multimodal memory note](../.claude/memory/feedback_multimodal_support.md) for the full rules; the short version:

* **Data layer:** every `LlmModel` and `EmbeddingModel` entry carries a `modalities` tuple. Never drop it.
* **API layer:** the `/api/v1/models` payload (01.e) returns modalities per entry so frontends can show badges.
* **UI layer:** node inspectors, KB picker, copilot session create all surface modality chips visibly.
* **Pipeline layer:** if a node routes image/audio/video/PDF attachments, it must route them as native parts to a multimodal-capable model, not pre-transcribe to text.

Tests (`backend/tests/test_model_registry.py`) pin modality invariants so regressions are caught at CI time: every Gemini 2.5 and 3.x entry must carry `{text, image, video, audio, pdf}`; every LLM entry must at minimum list `text`; every Anthropic and GPT-4o entry must list `image`.

---

## §7. Adding a new model

1. Append an `LlmModel(...)` or `EmbeddingModel(...)` to the catalogue. Populate every field — the dataclass is frozen to make typos loud.
2. For Gemini, add parity entries for **both** `google` and `vertex` providers (tests enforce this).
3. If it's a new tier (`lite`/`flash`/`pro`/`custom`), decide whether `LLM_TIER_DEFAULTS` changes. Bump the appropriate role if this is now the preferred default.
4. Update [§2](#2-whats-in-the-registry) on this page.
5. Run `python -m pytest tests/test_model_registry.py` — the tier-default sanity check will catch any mapping that points to a model not in the catalogue.

No frontend changes are needed — pickers driven by `/api/v1/models` pick up new entries automatically (01.e).

---

## §8. Planned (MODEL-01.e) — tenant overrides

MODEL-01.e extends `tenant_policies` with four columns + one JSON list:

| Column | Purpose |
|---|---|
| `default_llm_provider` | Overrides the process-wide default provider for this tenant |
| `default_llm_model` | Optional pin; null = tier-based default applies |
| `default_embedding_provider` | Same idea for embeddings |
| `default_embedding_model` | Same |
| `allowed_model_families` | JSON array of generation strings — empty = no restriction |

`is_allowed_llm(...)` will read tenant policy at resolve time. A conservative tenant can set `allowed_model_families = ["2.5"]` to block all preview and all 3.x usage — including the copilot, which falls back to `gemini-2.5-pro` under that policy.

The admin UI row for these fields ships in the existing Tenant Policy dialog; see [tenant-policies.md](tenant-policies.md) for the full policy surface.

---

## §9. Ticket series status

| Ticket | Status | What shipped |
|---|---|---|
| MODEL-01.a | ✅ Done | `backend/app/engine/model_registry.py` + 44 unit tests in `test_model_registry.py`. Tier defaults + multimodal metadata + helper fns. |
| MODEL-01.b | Planned | Copilot agent runner + suggest_fix wired to `default_llm_for` + allowlist validation on session create. |
| MODEL-01.c | Planned | Every engine AI node (`LLMAgent`, `ReAct`, `Reflection`, `Intent Classifier`, `Entity Extractor`, memory summariser) reads defaults from registry; `shared/node_registry.json` enums synced + startup drift check. |
| MODEL-01.d | Planned | `EMBEDDING_REGISTRY` extended with full metadata via registry; KB dialog gains full picker; `gemini-embedding-2` becomes the Vertex-tenant recommended default. |
| MODEL-01.e | Planned | `GET /api/v1/models` endpoint + `useModels()` frontend hook; Tenant Policy migration for `default_llm_*` / `default_embedding_*` / `allowed_model_families`. |
| MODEL-01.f | Planned | Frontend starter templates pick a tier (not a model); full docs sweep + backend+frontend test suites. |

See [feature-roadmap.md](feature-roadmap.md#sprint-2e--model-registry) for the full picture.

---

## §10. Related reading

* [Copilot](copilot.md) — agent runner's provider/model resolution
* [Vertex AI Integration](vertex.md) — Vertex-specific model availability + the Gemini 3.x lineup
* [RAG & Knowledge Base](rag-knowledge-base.md) — embedding model selection for KB ingestion
* [Node Types](node-types.md) — per-node `model` enum (generated from the registry in MODEL-01.c)
* [Tenant Policies](tenant-policies.md) — per-tenant allowlist + default overrides (MODEL-01.e)
* [API Reference](api-reference.md) — `/api/v1/models` endpoint (MODEL-01.e)
