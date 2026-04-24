"""LOCAL-AUTH-01 — tenant admin user CRUD.

Every endpoint requires a Bearer token whose ``is_admin`` claim is
true. Non-admins who try to call any route here get 403 even when the
token is otherwise valid.

Scope is deliberately tight: create, list, reset-password, toggle
disabled, delete. Usernames are immutable once set (changing username
would complicate audit trails on downstream tables that persist user
ids but surface usernames in the UI); operators who need a rename
should delete + recreate.
"""

from __future__ import annotations

import logging
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_tenant_db
from app.models.user import User
from app.security import local_auth
from app.security.jwt_auth import ALGORITHM
from app.security.tenant import get_tenant_id

logger = logging.getLogger(__name__)

router = APIRouter()

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class UserCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1)
    email: EmailStr | None = None
    is_admin: bool = False


class PasswordReset(BaseModel):
    password: str = Field(..., min_length=1)


class DisableToggle(BaseModel):
    disabled: bool


class UserOut(BaseModel):
    id: str
    tenant_id: str
    username: str
    email: str | None
    is_admin: bool
    disabled: bool
    created_at: str
    updated_at: str
    last_login_at: str | None


# ---------------------------------------------------------------------------
# Admin auth dependency
# ---------------------------------------------------------------------------


def require_admin(request: Request) -> dict:
    """Parse the Bearer JWT and enforce ``is_admin=true``.

    Returns the decoded claims so the handler can log the acting user.
    Works independently of ``get_tenant_id`` so the error body
    distinguishes "not authenticated" (401) from "authenticated but not
    admin" (403).
    """
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(
            401,
            "Missing Authorization header with Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = auth[7:].strip()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            401,
            f"Invalid or expired token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not payload.get("is_admin"):
        raise HTTPException(403, "Admin privilege required")
    return payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_out(u: User) -> UserOut:
    return UserOut(
        id=str(u.id),
        tenant_id=u.tenant_id,
        username=u.username,
        email=u.email,
        is_admin=u.is_admin,
        disabled=u.disabled,
        created_at=u.created_at.isoformat() if u.created_at else "",
        updated_at=u.updated_at.isoformat() if u.updated_at else "",
        last_login_at=u.last_login_at.isoformat() if u.last_login_at else None,
    )


def _get_or_404(db: Session, tenant_id: str, user_id: str) -> User:
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(422, "Invalid user_id")
    row = db.query(User).filter(User.id == uid, User.tenant_id == tenant_id).first()
    if row is None:
        raise HTTPException(404, "User not found")
    return row


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=UserOut, status_code=201)
def create_user(
    body: UserCreate,
    _claims: dict = Depends(require_admin),
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    if not _USERNAME_RE.match(body.username):
        raise HTTPException(
            400,
            "username must contain only letters, digits, '_', '.', and '-'",
        )
    try:
        pw_hash = local_auth.hash_password(body.password)
    except local_auth.PasswordTooWeak as exc:
        raise HTTPException(400, str(exc))

    existing = local_auth.get_user_by_username(db, tenant_id, body.username)
    if existing is not None:
        raise HTTPException(409, f"User '{body.username}' already exists in this tenant")

    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        username=body.username,
        email=body.email,
        password_hash=pw_hash,
        is_admin=body.is_admin,
        disabled=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info(
        "local-auth user created: user_id=%s tenant=%s by=%s",
        user.id,
        tenant_id,
        _claims.get("sub"),
    )
    return _to_out(user)


@router.get("", response_model=list[UserOut])
def list_users(
    _claims: dict = Depends(require_admin),
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    rows = (
        db.query(User)
        .filter(User.tenant_id == tenant_id)
        .order_by(func.lower(User.username))
        .all()
    )
    return [_to_out(u) for u in rows]


@router.get("/{user_id}", response_model=UserOut)
def get_user(
    user_id: str,
    _claims: dict = Depends(require_admin),
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    return _to_out(_get_or_404(db, tenant_id, user_id))


@router.put("/{user_id}/password", response_model=UserOut)
def reset_password(
    user_id: str,
    body: PasswordReset,
    _claims: dict = Depends(require_admin),
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    user = _get_or_404(db, tenant_id, user_id)
    try:
        user.password_hash = local_auth.hash_password(body.password)
    except local_auth.PasswordTooWeak as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    db.refresh(user)
    logger.info(
        "local-auth password reset: user_id=%s by=%s",
        user.id,
        _claims.get("sub"),
    )
    return _to_out(user)


@router.put("/{user_id}/disabled", response_model=UserOut)
def set_disabled(
    user_id: str,
    body: DisableToggle,
    _claims: dict = Depends(require_admin),
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    user = _get_or_404(db, tenant_id, user_id)
    # Guard against an admin disabling their own account and locking
    # everyone out — the only path back is a DB-level fix.
    if str(user.id) == _claims.get("sub") and body.disabled:
        raise HTTPException(400, "You cannot disable your own account")
    user.disabled = body.disabled
    db.commit()
    db.refresh(user)
    logger.info(
        "local-auth user %s: user_id=%s by=%s",
        "disabled" if body.disabled else "enabled",
        user.id,
        _claims.get("sub"),
    )
    return _to_out(user)


@router.delete("/{user_id}", status_code=204)
def delete_user(
    user_id: str,
    _claims: dict = Depends(require_admin),
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    user = _get_or_404(db, tenant_id, user_id)
    if str(user.id) == _claims.get("sub"):
        raise HTTPException(400, "You cannot delete your own account")
    db.delete(user)
    db.commit()
    logger.info(
        "local-auth user deleted: user_id=%s by=%s",
        user.id,
        _claims.get("sub"),
    )
