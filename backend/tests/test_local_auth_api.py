"""LOCAL-AUTH-01 — router-level tests for /auth/local/login and the
tenant admin user CRUD at /api/v1/users.

These tests stub out SQLAlchemy with dependency overrides + a small
in-memory user store so the argon2 hashing path runs for real but the
PostgreSQL-specific bits (UUID column, RLS GUC) don't. That keeps the
tests runnable in CI without a Postgres instance.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


TENANT = "tenant-a"


# ---------------------------------------------------------------------------
# In-memory fake user store
# ---------------------------------------------------------------------------


class _FakeUserStore:
    """Dict-backed substitute for the ``users`` table.

    Implements just enough to satisfy the router code paths:
    ``get_user_by_username``, direct id-filtered query, and
    ``tenant_id``-scoped ordered list.
    """

    def __init__(self) -> None:
        self.rows: dict[str, Any] = {}

    # --- API mirror of local_auth.get_user_by_username -------------------

    def get_by_username(self, tenant_id: str, username: str):
        for row in self.rows.values():
            if row.tenant_id == tenant_id and row.username.lower() == username.lower():
                return row
        return None

    def add(self, row) -> None:
        self.rows[str(row.id)] = row


def _make_user(
    *,
    username: str,
    password_hash: str,
    is_admin: bool = False,
    disabled: bool = False,
    tenant_id: str = TENANT,
):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        username=username,
        email=None,
        password_hash=password_hash,
        is_admin=is_admin,
        disabled=disabled,
        created_at=now,
        updated_at=now,
        last_login_at=None,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store() -> _FakeUserStore:
    return _FakeUserStore()


@pytest.fixture
def login_client(store: _FakeUserStore):
    """TestClient wired to /auth/local/login with auth_mode=local."""
    from app.api.auth_local import router as auth_local_router
    from app.config import settings
    from app.database import SessionLocal

    with patch.object(settings, "auth_mode", "local"):
        app = FastAPI()
        app.include_router(auth_local_router)

        # Replace SessionLocal() + set_tenant_context with a no-op MagicMock
        # session so the router doesn't try to hit Postgres. The real
        # authentication path is exercised via a patched
        # local_auth.get_user_by_username below.
        session = MagicMock()
        with patch("app.api.auth_local.SessionLocal", return_value=session), \
             patch("app.api.auth_local.set_tenant_context"), \
             patch(
                 "app.api.auth_local.local_auth.get_user_by_username",
                 side_effect=lambda db, tid, uname: store.get_by_username(tid, uname),
             ):
            yield TestClient(app)

        # SessionLocal isn't actually used once patched away — quiet the
        # linter that Note: SessionLocal import is only to prove the
        # symbol exists at the expected path.
        assert SessionLocal is not None


@pytest.fixture
def admin_client(store: _FakeUserStore):
    """TestClient wired to the admin user CRUD router with every DB call
    routed through ``store``."""
    from app.api.users import router as users_router
    from app.database import get_tenant_db
    from app.security.tenant import get_tenant_id

    # Fake Session that proxies query(User) into the store.
    class _FakeQuery:
        def __init__(self, store_: _FakeUserStore):
            self._store = store_
            self._tenant: str | None = None
            self._user_id: uuid.UUID | None = None

        def filter(self, *conditions):
            # The router uses two query shapes:
            #   .filter(User.id == uid, User.tenant_id == t).first()
            #   .filter(User.tenant_id == t)...all()
            # We only need to remember tenant / user_id across successive
            # .filter() + .order_by() + terminal calls. The comparisons
            # arrive as BinaryExpression objects whose .right is a
            # BindParameter; poke those out.
            for expr in conditions:
                try:
                    name = expr.left.key
                    value = expr.right.value
                except AttributeError:
                    continue
                if name == "id":
                    self._user_id = value
                elif name == "tenant_id":
                    self._tenant = value
            return self

        def order_by(self, *_):
            return self

        def first(self):
            for row in self._store.rows.values():
                if self._tenant and row.tenant_id != self._tenant:
                    continue
                if self._user_id and row.id != self._user_id:
                    continue
                return row
            return None

        def all(self):
            out = []
            for row in self._store.rows.values():
                if self._tenant and row.tenant_id != self._tenant:
                    continue
                out.append(row)
            return out

    class _FakeSession:
        def __init__(self, store_: _FakeUserStore):
            self._store = store_

        def query(self, _model):
            return _FakeQuery(self._store)

        def add(self, row):
            self._store.add(row)

        def delete(self, row):
            self._store.rows.pop(str(row.id), None)

        def commit(self):
            pass

        def refresh(self, _row):
            pass

    app = FastAPI()
    app.include_router(users_router, prefix="/api/v1/users")

    def _fake_db():
        yield _FakeSession(store)

    app.dependency_overrides[get_tenant_id] = lambda: TENANT
    app.dependency_overrides[get_tenant_db] = _fake_db

    return TestClient(app)


@pytest.fixture
def admin_token():
    """A valid admin JWT for use with the users router."""
    from app.security.jwt_auth import create_access_token

    return create_access_token(
        tenant_id=TENANT,
        subject=str(uuid.uuid4()),
        extra_claims={"username": "admin", "is_admin": True},
    )


@pytest.fixture
def non_admin_token():
    from app.security.jwt_auth import create_access_token

    return create_access_token(
        tenant_id=TENANT,
        subject=str(uuid.uuid4()),
        extra_claims={"username": "bob", "is_admin": False},
    )


# ---------------------------------------------------------------------------
# /auth/local/login
# ---------------------------------------------------------------------------


def test_login_success_returns_jwt_and_user(login_client, store):
    from app.security import local_auth

    pw_hash = local_auth.hash_password("correct-horse-battery-staple")
    store.add(_make_user(username="alice", password_hash=pw_hash))

    resp = login_client.post(
        "/auth/local/login",
        json={"tenant_id": TENANT, "username": "alice", "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["username"] == "alice"
    assert body["user"]["tenant_id"] == TENANT
    # The token must carry the user_id as sub and is_admin=False.
    from jose import jwt

    from app.config import settings
    from app.security.jwt_auth import ALGORITHM

    claims = jwt.decode(body["access_token"], settings.secret_key, algorithms=[ALGORITHM])
    assert claims["tenant_id"] == TENANT
    assert claims["username"] == "alice"
    assert claims["is_admin"] is False


def test_login_case_insensitive_username(login_client, store):
    from app.security import local_auth

    store.add(
        _make_user(
            username="Alice",
            password_hash=local_auth.hash_password("correct-horse-battery-staple"),
        )
    )
    resp = login_client.post(
        "/auth/local/login",
        json={"tenant_id": TENANT, "username": "ALICE", "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 200


def test_login_bad_password_returns_401(login_client, store):
    from app.security import local_auth

    store.add(
        _make_user(
            username="alice",
            password_hash=local_auth.hash_password("correct-horse-battery-staple"),
        )
    )
    resp = login_client.post(
        "/auth/local/login",
        json={"tenant_id": TENANT, "username": "alice", "password": "wrong"},
    )
    assert resp.status_code == 401
    # Generic body — no enumeration hint.
    assert "credentials" in resp.json()["detail"].lower()


def test_login_disabled_user_returns_401(login_client, store):
    from app.security import local_auth

    store.add(
        _make_user(
            username="alice",
            password_hash=local_auth.hash_password("correct-horse-battery-staple"),
            disabled=True,
        )
    )
    resp = login_client.post(
        "/auth/local/login",
        json={"tenant_id": TENANT, "username": "alice", "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 401


def test_login_unknown_user_returns_401(login_client):
    resp = login_client.post(
        "/auth/local/login",
        json={"tenant_id": TENANT, "username": "ghost", "password": "irrelevant-but-long"},
    )
    assert resp.status_code == 401


def test_login_returns_404_when_auth_mode_not_local():
    """The login endpoint is only active under auth_mode=local. In any
    other mode it must respond 404 even if routed (the include_router
    call is gated in main.py, but a defence-in-depth 404 inside the
    handler keeps that from being the only guard)."""
    from app.api.auth_local import router as auth_local_router
    from app.config import settings

    with patch.object(settings, "auth_mode", "jwt"):
        app = FastAPI()
        app.include_router(auth_local_router)
        client = TestClient(app)
        resp = client.post(
            "/auth/local/login",
            json={"tenant_id": TENANT, "username": "x", "password": "yyyyyyyy"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /api/v1/users admin CRUD
# ---------------------------------------------------------------------------


def test_create_user_requires_admin(admin_client, non_admin_token):
    resp = admin_client.post(
        "/api/v1/users",
        headers={"Authorization": f"Bearer {non_admin_token}"},
        json={"username": "bob", "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 403


def test_create_user_rejects_weak_password(admin_client, admin_token):
    resp = admin_client.post(
        "/api/v1/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"username": "bob", "password": "short"},
    )
    assert resp.status_code == 400
    assert "characters" in resp.json()["detail"].lower()


def test_create_user_happy_path_and_conflict(admin_client, admin_token, store):
    resp = admin_client.post(
        "/api/v1/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"username": "bob", "password": "correct-horse-battery-staple"},
    )
    assert resp.status_code == 201, resp.text
    assert len(store.rows) == 1
    created = next(iter(store.rows.values()))
    assert created.username == "bob"
    assert created.is_admin is False

    # Duplicate (case-insensitive match) → 409.
    dup = admin_client.post(
        "/api/v1/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"username": "BOB", "password": "correct-horse-battery-staple"},
    )
    assert dup.status_code == 409


def test_list_users_admin_only(admin_client, admin_token, store):
    from app.security import local_auth

    store.add(
        _make_user(
            username="alice",
            password_hash=local_auth.hash_password("correct-horse-battery-staple"),
        )
    )
    resp = admin_client.get(
        "/api/v1/users",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["username"] == "alice"


def test_reset_password_changes_hash(admin_client, admin_token, store):
    from app.security import local_auth

    original_hash = local_auth.hash_password("correct-horse-battery-staple")
    user = _make_user(username="alice", password_hash=original_hash)
    store.add(user)

    resp = admin_client.put(
        f"/api/v1/users/{user.id}/password",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"password": "a-totally-new-passphrase"},
    )
    assert resp.status_code == 200
    assert user.password_hash != original_hash
    assert local_auth.verify_password("a-totally-new-passphrase", user.password_hash)


def test_self_disable_blocked(admin_client, store):
    """An admin cannot disable themselves — the only path back from a
    fully-locked tenant is a DB-level fix, so the API refuses."""
    from app.security import local_auth
    from app.security.jwt_auth import create_access_token

    user = _make_user(
        username="admin",
        password_hash=local_auth.hash_password("correct-horse-battery-staple"),
        is_admin=True,
    )
    store.add(user)
    self_token = create_access_token(
        tenant_id=TENANT,
        subject=str(user.id),
        extra_claims={"username": "admin", "is_admin": True},
    )
    resp = admin_client.put(
        f"/api/v1/users/{user.id}/disabled",
        headers={"Authorization": f"Bearer {self_token}"},
        json={"disabled": True},
    )
    assert resp.status_code == 400
