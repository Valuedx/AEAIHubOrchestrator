"""LOCAL-AUTH-01 — unit tests for the pure-logic helpers in
``app.security.local_auth``.

Covers password hashing, verification, strength policy, and the
"authenticate()" failure branches using a fully-faked DB session. No
PostgreSQL dependency — these tests only exercise Python code paths.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.security import local_auth


# ---------------------------------------------------------------------------
# validate_password_strength + hash_password
# ---------------------------------------------------------------------------


def test_validate_password_strength_rejects_short_password():
    with pytest.raises(local_auth.PasswordTooWeak):
        local_auth.validate_password_strength("short")


def test_validate_password_strength_accepts_min_length():
    # Default min is 8 — this is on the boundary.
    local_auth.validate_password_strength("12345678")


def test_hash_password_rejects_weak():
    with pytest.raises(local_auth.PasswordTooWeak):
        local_auth.hash_password("weak")


def test_hash_password_returns_argon2_id_string():
    h = local_auth.hash_password("correct-horse-battery-staple")
    # argon2-cffi always prefixes argon2id hashes with "$argon2id$".
    assert h.startswith("$argon2id$")


# ---------------------------------------------------------------------------
# verify_password
# ---------------------------------------------------------------------------


def test_verify_password_success():
    h = local_auth.hash_password("correct-horse-battery-staple")
    assert local_auth.verify_password("correct-horse-battery-staple", h) is True


def test_verify_password_failure():
    h = local_auth.hash_password("correct-horse-battery-staple")
    assert local_auth.verify_password("wrong", h) is False


def test_verify_password_returns_false_on_garbage_hash():
    # argon2-cffi raises InvalidHash on non-argon2 strings; verify_password
    # must catch it and return False rather than propagating.
    assert local_auth.verify_password("anything", "not-a-real-hash") is False


# ---------------------------------------------------------------------------
# authenticate — exercises all three failure paths
# ---------------------------------------------------------------------------


def _fake_user(password_hash: str, *, disabled: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id="user-1",
        tenant_id="tenant-a",
        username="alice",
        password_hash=password_hash,
        disabled=disabled,
        last_login_at=None,
    )


class _FakeDb:
    """Minimal stand-in for a SQLAlchemy Session.

    ``authenticate`` uses ``db.commit()`` / ``db.refresh(user)`` on
    success; it doesn't need ``.query(...)`` because the unit test
    patches ``get_user_by_username`` directly.
    """

    def __init__(self):
        self.committed = False

    def commit(self):
        self.committed = True

    def refresh(self, _obj):
        pass


def test_authenticate_returns_none_when_user_missing():
    with patch.object(local_auth, "get_user_by_username", return_value=None):
        result = local_auth.authenticate(_FakeDb(), "tenant-a", "ghost", "irrelevant")
    assert result is None


def test_authenticate_returns_none_when_user_disabled():
    hash_ = local_auth.hash_password("correct-horse-battery-staple")
    user = _fake_user(hash_, disabled=True)
    with patch.object(local_auth, "get_user_by_username", return_value=user):
        result = local_auth.authenticate(
            _FakeDb(), "tenant-a", "alice", "correct-horse-battery-staple"
        )
    assert result is None


def test_authenticate_returns_none_on_bad_password():
    hash_ = local_auth.hash_password("correct-horse-battery-staple")
    user = _fake_user(hash_)
    with patch.object(local_auth, "get_user_by_username", return_value=user):
        result = local_auth.authenticate(_FakeDb(), "tenant-a", "alice", "wrong")
    assert result is None


def test_authenticate_success_returns_user_and_stamps_last_login():
    hash_ = local_auth.hash_password("correct-horse-battery-staple")
    user = _fake_user(hash_)
    db = _FakeDb()
    with patch.object(local_auth, "get_user_by_username", return_value=user):
        result = local_auth.authenticate(
            db, "tenant-a", "alice", "correct-horse-battery-staple"
        )
    assert result is user
    assert db.committed is True
    assert isinstance(user.last_login_at, datetime)
    # Timezone-aware UTC — last_login_at must never be naive.
    assert user.last_login_at.tzinfo is not None
    assert user.last_login_at.tzinfo.utcoffset(user.last_login_at) == timezone.utc.utcoffset(
        user.last_login_at
    )
