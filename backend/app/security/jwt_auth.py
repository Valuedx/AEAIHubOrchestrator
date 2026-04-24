"""JWT-based authentication with tenant claim extraction.

In production, every request must carry a valid Bearer token whose payload
includes a `tenant_id` claim.  In development mode (ORCHESTRATOR_AUTH_MODE=dev),
the middleware falls back to the X-Tenant-Id header for convenience.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from fastapi import Depends, Header, HTTPException, Query, Request
from jose import JWTError, jwt

from app.config import settings

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24


def create_access_token(
    tenant_id: str,
    subject: str = "api",
    extra_claims: dict | None = None,
) -> str:
    """Issue a signed JWT with tenant_id embedded as a claim."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "tenant_id": tenant_id,
        "iat": now,
        "exp": now + timedelta(hours=TOKEN_EXPIRE_HOURS),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def _extract_bearer_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


async def get_tenant_id(
    request: Request,
    x_tenant_id_header: str | None = Header(default=None, alias="X-Tenant-Id"),
    x_tenant_id_query: str | None = Query(default=None, alias="x_tenant_id"),
) -> str:
    """Extract tenant_id from JWT bearer token or fall back to header/query in dev mode."""
    if settings.auth_mode in ("jwt", "local"):
        token = _extract_bearer_token(request)
        if not token:
            raise HTTPException(
                status_code=401,
                detail="Missing Authorization header with Bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        except JWTError as exc:
            raise HTTPException(
                status_code=401,
                detail=f"Invalid or expired token: {exc}",
                headers={"WWW-Authenticate": "Bearer"},
            )

        tenant_id = payload.get("tenant_id")
        if not tenant_id:
            raise HTTPException(status_code=401, detail="Token missing tenant_id claim")
        return tenant_id

    # In dev mode, check header then query parameter
    tid = x_tenant_id_header or x_tenant_id_query
    if not tid:
        raise HTTPException(status_code=401, detail="Missing X-Tenant-Id header or query parameter")
    return tid
