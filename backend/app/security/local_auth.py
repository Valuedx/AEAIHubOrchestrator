"""LOCAL-AUTH-01 — local password authentication service.

Pure helpers: no FastAPI/HTTP concerns (those live in
``app.api.auth_local``). This module owns the rules for

  * password strength (min length)
  * argon2 hashing + verification
  * tenant-scoped user lookup (case-insensitive username)
  * authentication (lookup + verify + disabled check)
  * the idempotent admin-seed used at startup

Active Directory / LDAP binding is explicitly out of scope for this
revision. A future ``authenticate_external(...)`` path will live in a
sibling ``ldap_auth.py`` and will be wired into the same router so a
single tenant can mix local users with AD-backed users.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHash
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, set_tenant_context
from app.models.user import User

logger = logging.getLogger(__name__)


# argon2id defaults from argon2-cffi are RFC 9106 "second recommended"
# (64 MiB, 3 passes). Fine for interactive login on a single orchestrator
# process. Operators with CPU-bound tenants can bump ``time_cost`` via
# a settings extension later.
_hasher = PasswordHasher()


class PasswordTooWeak(ValueError):
    """Raised by ``hash_password`` when the plaintext fails policy."""


def validate_password_strength(password: str) -> None:
    """Enforce the minimum password policy.

    Policy is deliberately minimal for v1 (length only). Complexity
    rules (mixed case, digits, symbols) add marginal real-world security
    at the cost of pushing users toward predictable substitutions, so we
    skip them and lean on the min-length + bcrypt-grade hashing instead.
    """
    min_len = settings.password_min_length
    if not isinstance(password, str) or len(password) < min_len:
        raise PasswordTooWeak(
            f"Password must be at least {min_len} characters long."
        )


def hash_password(password: str) -> str:
    validate_password_strength(password)
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
        return True
    except (VerifyMismatchError, InvalidHash):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when argon2's tunable parameters have moved past the hash's
    recorded cost. Used to opportunistically upgrade hashes on login.
    """
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHash:
        return False


def get_user_by_username(
    db: Session, tenant_id: str, username: str
) -> User | None:
    """Case-insensitive username lookup within a tenant.

    The caller is responsible for having called ``set_tenant_context``
    on ``db`` so the RLS policy on ``users`` doesn't filter out the row
    we're trying to find.
    """
    if not username:
        return None
    return (
        db.query(User)
        .filter(
            User.tenant_id == tenant_id,
            func.lower(User.username) == username.lower(),
        )
        .first()
    )


def authenticate(
    db: Session, tenant_id: str, username: str, password: str
) -> User | None:
    """Return the User on success, None on any failure.

    A None result deliberately does NOT distinguish "no such user",
    "bad password", or "disabled" — a verbose error helps attackers
    enumerate accounts. The caller logs which of those it was at INFO
    level for operators; the HTTP response stays generic.
    """
    user = get_user_by_username(db, tenant_id, username)
    if user is None:
        logger.info("local-auth login failed: no such user (tenant=%s)", tenant_id)
        return None
    if user.disabled:
        logger.info("local-auth login failed: user disabled (user_id=%s)", user.id)
        return None
    if not verify_password(password, user.password_hash):
        logger.info("local-auth login failed: bad password (user_id=%s)", user.id)
        return None

    # Opportunistic rehash on successful login so a tunables bump in
    # code gradually upgrades stored hashes without a bulk migration.
    if needs_rehash(user.password_hash):
        user.password_hash = _hasher.hash(password)

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


def ensure_admin_seeded() -> None:
    """Create the bootstrap admin if ``ORCHESTRATOR_LOCAL_ADMIN_*`` env
    vars are set and no admin yet exists for the target tenant.

    Idempotent: no-op on subsequent boots once the admin row exists.
    Only runs when ``auth_mode == "local"`` — in dev/jwt/oidc modes the
    ``users`` table may not even be needed.
    """
    if settings.auth_mode != "local":
        return
    username = settings.local_admin_username.strip()
    password = settings.local_admin_password
    tenant_id = settings.local_admin_tenant_id.strip() or "default"
    if not username or not password:
        logger.info(
            "local-auth: no admin seed configured "
            "(set ORCHESTRATOR_LOCAL_ADMIN_USERNAME/PASSWORD to enable)",
        )
        return

    db = SessionLocal()
    try:
        set_tenant_context(db, tenant_id)
        existing = get_user_by_username(db, tenant_id, username)
        if existing is not None:
            return
        try:
            pw_hash = hash_password(password)
        except PasswordTooWeak as exc:
            logger.error("local-auth admin seed rejected: %s", exc)
            return
        user = User(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            username=username,
            email=None,
            password_hash=pw_hash,
            is_admin=True,
            disabled=False,
        )
        db.add(user)
        db.commit()
        logger.info(
            "local-auth: seeded admin user (tenant=%s, username=%s)",
            tenant_id,
            username,
        )
    finally:
        db.close()
