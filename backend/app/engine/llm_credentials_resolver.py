"""ADMIN-03 — resolve per-tenant LLM provider credentials.

Mirrors the shape of ``mcp_server_resolver`` and ``tenant_policy_resolver``
but targets a different surface: LLM provider API keys (Google AI
Studio, OpenAI, Anthropic) that otherwise lived as process-global
env vars and forced every tenant onto the same billing account.

Storage model
-------------

Keys live in the existing Fernet-encrypted ``tenant_secrets`` vault
under four well-known names:

  * ``LLM_GOOGLE_API_KEY``
  * ``LLM_OPENAI_API_KEY``
  * ``LLM_OPENAI_BASE_URL`` (non-secret but stored here for locality)
  * ``LLM_ANTHROPIC_API_KEY``

This beats a new ``tenant_llm_credentials`` table for three reasons:
encryption at rest is already handled, the existing Secrets CRUD API
covers management, and the ``{{ env.KEY }}`` templating pattern in
node configs can reference the same keys without extra plumbing.

Precedence
----------

1. Tenant secret under the well-known key, if present and non-empty.
2. ``settings.<knob>`` env default.
3. Missing from both → raise ``ValueError`` with a message that names
   *both* remediation paths so operators can pick whichever fits.

Graceful degrade
----------------

A broken tenant_secrets lookup (DB unreachable, RLS denial, vault
decrypt failure) logs a warning and falls back to env defaults. An
LLM call that would otherwise hard-fail on a flaky secrets table is
better off running against the shared env key than 500-ing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from app.config import settings

logger = logging.getLogger(__name__)


Source = Literal["tenant_secret", "env_default", "missing"]


@dataclass(frozen=True)
class ResolvedProviderValue:
    """A resolved provider value + where it came from.

    Callers typically unpack just ``value`` for the SDK client; the
    admin dialog reads ``source`` to render per-field badges.
    """

    value: str
    source: Source


# Well-known secret names — single source of truth. Referenced from
# both the resolver and the admin UI's payload.
LLM_GOOGLE_API_KEY_NAME = "LLM_GOOGLE_API_KEY"
LLM_OPENAI_API_KEY_NAME = "LLM_OPENAI_API_KEY"
LLM_OPENAI_BASE_URL_NAME = "LLM_OPENAI_BASE_URL"
LLM_ANTHROPIC_API_KEY_NAME = "LLM_ANTHROPIC_API_KEY"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_tenant_secret_safe(tenant_id: str | None, key_name: str) -> str | None:
    """Wrap ``get_tenant_secret`` with a broad except so resolver
    callers never see a raw vault exception.

    The vault can fail for mundane reasons (table missing in a fresh
    install, RLS context not set on a back-channel thread, Fernet key
    rotation mid-flight) that shouldn't fail the LLM call. Log once
    and let the caller fall through to the env default.
    """
    if not tenant_id:
        return None
    try:
        from app.security.vault import get_tenant_secret

        return get_tenant_secret(tenant_id, key_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "llm_credentials_resolver: failed to read %r for tenant %r, "
            "falling back to env default: %s",
            key_name, tenant_id, exc,
        )
        return None


def _resolve(
    tenant_id: str | None,
    secret_name: str,
    env_value: str,
) -> ResolvedProviderValue:
    tenant_value = _get_tenant_secret_safe(tenant_id, secret_name)
    if tenant_value:
        return ResolvedProviderValue(value=tenant_value, source="tenant_secret")
    if env_value:
        return ResolvedProviderValue(value=env_value, source="env_default")
    return ResolvedProviderValue(value="", source="missing")


def _raise_missing(
    provider_label: str,
    secret_name: str,
    env_name: str,
) -> str:
    """Format the remediation-bearing error used by every provider
    when neither source has a value."""
    raise ValueError(
        f"{provider_label} credentials are not configured for this tenant. "
        f"Set the '{secret_name}' secret via the LLM Credentials dialog "
        f"(or POST /api/v1/secrets), or set {env_name} as an "
        "orchestrator-wide env default."
    )


# ---------------------------------------------------------------------------
# Public per-provider accessors
# ---------------------------------------------------------------------------


def get_google_api_key(tenant_id: str | None) -> str:
    """Google AI Studio API key for this tenant.

    Not to be confused with Vertex AI (project-based, ADC auth) which
    has its own resolver and table. ``provider='google'`` nodes use
    this; ``provider='vertex'`` nodes use VERTEX-02.
    """
    resolved = _resolve(
        tenant_id,
        LLM_GOOGLE_API_KEY_NAME,
        settings.google_api_key,
    )
    if resolved.source == "missing":
        _raise_missing(
            "Google AI Studio",
            LLM_GOOGLE_API_KEY_NAME,
            "ORCHESTRATOR_GOOGLE_API_KEY",
        )
    return resolved.value


def get_openai_api_key(tenant_id: str | None) -> str:
    resolved = _resolve(
        tenant_id,
        LLM_OPENAI_API_KEY_NAME,
        settings.openai_api_key,
    )
    if resolved.source == "missing":
        _raise_missing(
            "OpenAI",
            LLM_OPENAI_API_KEY_NAME,
            "ORCHESTRATOR_OPENAI_API_KEY",
        )
    return resolved.value


def get_openai_base_url(tenant_id: str | None) -> str:
    """Base URL for the OpenAI-compatible endpoint this tenant uses.

    Unlike API keys, this is always returnable: there's always a
    fallback (``settings.openai_base_url`` defaults to
    ``https://api.openai.com/v1``). Custom proxies / LiteLLM endpoints
    use the tenant override; most tenants leave it unset.
    """
    resolved = _resolve(
        tenant_id,
        LLM_OPENAI_BASE_URL_NAME,
        settings.openai_base_url,
    )
    # An actually-missing base URL is impossible thanks to the env
    # default, but guard anyway.
    return resolved.value or "https://api.openai.com/v1"


def get_anthropic_api_key(tenant_id: str | None) -> str:
    resolved = _resolve(
        tenant_id,
        LLM_ANTHROPIC_API_KEY_NAME,
        settings.anthropic_api_key,
    )
    if resolved.source == "missing":
        _raise_missing(
            "Anthropic",
            LLM_ANTHROPIC_API_KEY_NAME,
            "ORCHESTRATOR_ANTHROPIC_API_KEY",
        )
    return resolved.value


# ---------------------------------------------------------------------------
# Admin/UI: per-provider source report
# ---------------------------------------------------------------------------


def get_credentials_status(tenant_id: str | None) -> dict[str, dict[str, str]]:
    """Report where each LLM provider's credential is coming from.

    Used by the LLM Credentials admin dialog to render per-field
    badges. Never returns the secret values themselves — this is a
    metadata-only surface.
    """
    providers: list[tuple[str, str, str]] = [
        ("google", LLM_GOOGLE_API_KEY_NAME, settings.google_api_key),
        ("openai", LLM_OPENAI_API_KEY_NAME, settings.openai_api_key),
        ("openai_base_url", LLM_OPENAI_BASE_URL_NAME, settings.openai_base_url),
        ("anthropic", LLM_ANTHROPIC_API_KEY_NAME, settings.anthropic_api_key),
    ]
    out: dict[str, dict[str, str]] = {}
    for key, secret_name, env_value in providers:
        resolved = _resolve(tenant_id, secret_name, env_value)
        out[key] = {
            "source": resolved.source,
            "secret_name": secret_name,
            # Never include the value itself — UI only needs to know
            # where it came from, not what it is.
        }
    return out
