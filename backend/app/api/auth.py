"""OIDC Authorization Code flow router.

Handles:
  GET  /auth/oidc/login     — redirect browser to OIDC provider
  GET  /auth/oidc/callback  — exchange code, issue internal JWT

PKCE state/nonce stored in Redis with a 5-minute TTL.
Requires ORCHESTRATOR_OIDC_ENABLED=true plus provider settings.
"""

from __future__ import annotations

import json
import logging
import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse

from app.config import settings

router = APIRouter(prefix="/auth/oidc", tags=["auth"])
logger = logging.getLogger(__name__)

_PKCE_TTL = 300  # seconds


# ---------------------------------------------------------------------------
# Redis helpers (reuse Celery's redis URL)
# ---------------------------------------------------------------------------

def _redis():
    import redis as _redis_lib
    return _redis_lib.from_url(settings.redis_url, decode_responses=True)


# ---------------------------------------------------------------------------
# Discovery document cache
# ---------------------------------------------------------------------------

_discovery: dict | None = None


def _get_discovery() -> dict:
    global _discovery
    if _discovery:
        return _discovery
    import httpx
    url = settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
    resp = httpx.get(url, timeout=10)
    resp.raise_for_status()
    _discovery = resp.json()
    return _discovery


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------

def _generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using S256 method."""
    import base64
    import hashlib
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/login")
def oidc_login():
    """Redirect browser to OIDC provider authorization endpoint."""
    if not settings.oidc_enabled:
        raise HTTPException(404, "OIDC not enabled")

    try:
        disc = _get_discovery()
    except Exception as exc:
        logger.error("OIDC discovery fetch failed: %s", exc)
        raise HTTPException(502, "Cannot reach OIDC provider")

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier, challenge = _generate_pkce()

    r = _redis()
    r.setex(
        f"oidc:state:{state}",
        _PKCE_TTL,
        json.dumps({"code_verifier": verifier, "nonce": nonce}),
    )

    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_uri,
        "scope": settings.oidc_scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = disc["authorization_endpoint"] + "?" + urlencode(params)
    return RedirectResponse(auth_url)


@router.get("/callback")
def oidc_callback(code: str, state: str):
    """Exchange authorization code for tokens, extract tenant claim, issue JWT."""
    if not settings.oidc_enabled:
        raise HTTPException(404, "OIDC not enabled")

    r = _redis()
    raw = r.get(f"oidc:state:{state}")
    if not raw:
        raise HTTPException(400, "Invalid or expired state parameter")
    r.delete(f"oidc:state:{state}")
    pkce_data = json.loads(raw)

    try:
        disc = _get_discovery()
    except Exception as exc:
        logger.error("OIDC discovery fetch failed: %s", exc)
        raise HTTPException(502, "Cannot reach OIDC provider")

    # Exchange code for tokens
    import httpx
    token_resp = httpx.post(
        disc["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.oidc_redirect_uri,
            "client_id": settings.oidc_client_id,
            "client_secret": settings.oidc_client_secret,
            "code_verifier": pkce_data["code_verifier"],
        },
        timeout=15,
    )
    if not token_resp.is_success:
        logger.error("OIDC token exchange failed: %s", token_resp.text)
        raise HTTPException(502, "Token exchange failed")

    tokens = token_resp.json()
    id_token_raw = tokens.get("id_token")
    if not id_token_raw:
        raise HTTPException(502, "No id_token in provider response")

    # Validate and decode ID token using authlib
    from authlib.jose import jwt as authlib_jwt
    from authlib.jose.errors import JoseError
    import httpx as _httpx

    jwks_resp = _httpx.get(disc["jwks_uri"], timeout=10)
    jwks_resp.raise_for_status()
    jwks = jwks_resp.json()

    try:
        claims = authlib_jwt.decode(id_token_raw, jwks)
        claims.validate()
    except JoseError as exc:
        logger.warning("OIDC ID token validation failed: %s", exc)
        raise HTTPException(401, "ID token validation failed")

    # Extract tenant_id from configured claim
    tenant_id = claims.get(settings.oidc_tenant_claim)
    if not tenant_id:
        raise HTTPException(401, f"ID token missing claim: {settings.oidc_tenant_claim}")

    # Issue internal JWT
    from app.security.jwt_auth import create_access_token
    internal_token = create_access_token(tenant_id=str(tenant_id))

    logger.info("OIDC login success: tenant=%s", tenant_id)
    return JSONResponse({
        "access_token": internal_token,
        "token_type": "bearer",
        "tenant_id": str(tenant_id),
    })
