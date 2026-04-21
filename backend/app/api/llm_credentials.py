"""ADMIN-03 — read-only status endpoint for the LLM Credentials dialog.

The dialog needs to know, per provider, whether the tenant has their
own key set, or the call would fall through to the env default, or
neither side has a key (fatal at call time). It does NOT need the
values themselves — those live in the Fernet-encrypted ``tenant_secrets``
vault and are never returned through any API.

Writes still go through the existing ``/api/v1/secrets`` endpoints
— the dialog POSTs / PUTs / DELETEs secrets under the four well-known
key names. Only the *reads* need this specialised surface because
the raw secrets API doesn't return values (by design) and the dialog
needs the per-provider source labels anyway.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.security.tenant import get_tenant_id

logger = logging.getLogger(__name__)

router = APIRouter()


class LlmCredentialStatus(BaseModel):
    # Mirrors engine/llm_credentials_resolver.Source.
    # "tenant_secret" — this tenant's own vault row is set.
    # "env_default"   — using ORCHESTRATOR_*_API_KEY fallback.
    # "missing"       — neither source has a value; call will raise.
    source: str
    secret_name: str


class LlmCredentialsOut(BaseModel):
    tenant_id: str
    # Per-provider status. Keys: google, openai, openai_base_url, anthropic.
    providers: dict[str, LlmCredentialStatus]


@router.get("", response_model=LlmCredentialsOut)
def get_credentials(tenant_id: str = Depends(get_tenant_id)):
    """Report which providers have a tenant-scoped key vs. inherit env.

    Never returns the secret values — this is a metadata-only surface
    so the admin UI can render "tenant override" / "env default" /
    "not configured" badges.
    """
    from app.engine.llm_credentials_resolver import get_credentials_status

    raw = get_credentials_status(tenant_id)
    return LlmCredentialsOut(
        tenant_id=tenant_id,
        providers={k: LlmCredentialStatus(**v) for k, v in raw.items()},
    )
