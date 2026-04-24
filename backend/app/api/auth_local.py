"""LOCAL-AUTH-01 — local username/password login router.

Active when ``ORCHESTRATOR_AUTH_MODE=local``. Exposes:

  POST /auth/local/login   — exchange credentials for an internal JWT
  GET  /auth/me            — return the caller's user row (Bearer required)

The JWT issued here is the same shape as the one issued by OIDC / the
dev ``/auth/token`` helper, so every downstream endpoint that already
resolves tenant via ``get_tenant_id`` keeps working unchanged. We embed
``user_id`` / ``username`` / ``is_admin`` as extra claims so the admin
user-CRUD endpoints can enforce authorization without a second DB round
trip.

Active Directory / LDAP binding is explicitly deferred — a future
``POST /auth/local/login`` request with an AD-backed user will go
through the same endpoint, routed to an ``authenticate_external``
path inside ``security/local_auth.py``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from jose import JWTError, jwt
from pydantic import BaseModel, Field

from app.config import settings
from app.database import SessionLocal, get_tenant_db, set_tenant_context
from app.security import local_auth
from app.security.jwt_auth import ALGORITHM, create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    tenant_id: str = Field(..., min_length=1, max_length=64)
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1)


class UserOut(BaseModel):
    id: str
    tenant_id: str
    username: str
    email: str | None
    is_admin: bool
    disabled: bool


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_out(user) -> UserOut:
    return UserOut(
        id=str(user.id),
        tenant_id=user.tenant_id,
        username=user.username,
        email=user.email,
        is_admin=user.is_admin,
        disabled=user.disabled,
    )


def _extract_bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header with Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return auth[7:].strip()


def _decode_jwt(token: str) -> dict:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            status_code=401,
            detail=f"Invalid or expired token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/local/login", response_model=LoginResponse)
def local_login(body: LoginRequest):
    """Exchange tenant + username + password for an internal JWT.

    The tenant_id is supplied in the body because at this point the
    caller has no token yet, so the usual ``get_tenant_id`` dependency
    isn't available. We bind the GUC to the submitted tenant_id so the
    RLS policy on ``users`` scopes the lookup.

    Returns a generic 401 on any credential failure — no account
    enumeration. Per-cause details are logged at INFO level by
    ``local_auth.authenticate``.
    """
    if settings.auth_mode != "local":
        raise HTTPException(404, "Local auth mode not enabled")

    db = SessionLocal()
    try:
        set_tenant_context(db, body.tenant_id)
        user = local_auth.authenticate(
            db, body.tenant_id, body.username, body.password
        )
    finally:
        db.close()

    if user is None:
        raise HTTPException(401, "Invalid credentials")

    token = create_access_token(
        tenant_id=user.tenant_id,
        subject=str(user.id),
        extra_claims={
            "username": user.username,
            "is_admin": user.is_admin,
        },
    )
    return LoginResponse(access_token=token, user=_user_out(user))


@router.get("/me", response_model=UserOut)
def me(request: Request, db=Depends(get_tenant_db)):
    """Return the caller's current user row.

    Useful for the frontend after a page reload — the token is in
    sessionStorage but the server-side user state (is_admin changed,
    account disabled) may have drifted. Works in any ``auth_mode`` that
    uses JWTs; returns 404 when the token's ``sub`` doesn't match a
    row in ``users`` (e.g. OIDC-minted tokens where no local user
    exists).
    """
    token = _extract_bearer(request)
    payload = _decode_jwt(token)
    user_id = payload.get("sub")
    if not user_id or user_id == "api":
        raise HTTPException(
            404,
            "Token is not bound to a local user (sub missing or 'api')",
        )

    from app.models.user import User

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(404, "User not found")
    return _user_out(user)
