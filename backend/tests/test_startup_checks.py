"""STARTUP-01 — tests for app/startup_checks.py.

Each check function is unit-tested by mocking its one external
dependency (DB session, Redis client, Celery inspect, settings).
The goal isn't to re-test the underlying libraries; it's to prove
that:

  * a healthy dependency yields ``pass``
  * a broken dependency yields ``fail`` or ``warn`` with a specific
    remediation string
  * a bug INSIDE the check doesn't take the whole readiness endpoint
    down

Plus an integration test for ``run_all_checks`` + ``/health/ready``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# check_database
# ---------------------------------------------------------------------------


class TestCheckDatabase:
    def test_passes_when_select_1_and_alembic_head_match(self):
        from app import startup_checks

        fake_session = MagicMock()
        fake_session.execute.return_value = MagicMock()

        def _SessionLocal():
            return fake_session

        fake_ctx = MagicMock()
        fake_ctx.get_current_revision.return_value = "0021"
        fake_script = MagicMock()
        fake_script.get_current_head.return_value = "0021"

        with patch("app.database.SessionLocal", side_effect=_SessionLocal), \
             patch("alembic.script.ScriptDirectory.from_config", return_value=fake_script), \
             patch("alembic.runtime.migration.MigrationContext.configure", return_value=fake_ctx), \
             patch("app.database.engine") as mock_engine:
            mock_engine.connect.return_value.__enter__.return_value = MagicMock()
            result = startup_checks.check_database()

        assert result.status == "pass"
        assert "0021" in result.message

    def test_warns_when_schema_behind_head(self):
        from app import startup_checks

        fake_session = MagicMock()

        fake_ctx = MagicMock()
        fake_ctx.get_current_revision.return_value = "0019"
        fake_script = MagicMock()
        fake_script.get_current_head.return_value = "0021"

        with patch("app.database.SessionLocal", return_value=fake_session), \
             patch("alembic.script.ScriptDirectory.from_config", return_value=fake_script), \
             patch("alembic.runtime.migration.MigrationContext.configure", return_value=fake_ctx), \
             patch("app.database.engine") as mock_engine:
            mock_engine.connect.return_value.__enter__.return_value = MagicMock()
            result = startup_checks.check_database()

        assert result.status == "warn"
        assert "behind head" in result.message.lower()
        assert "alembic upgrade head" in result.remediation

    def test_fails_when_select_1_raises(self):
        from app import startup_checks

        def _boom():
            raise RuntimeError("connection refused")

        with patch("app.database.SessionLocal", side_effect=_boom):
            result = startup_checks.check_database()

        assert result.status == "fail"
        assert "connection refused" in result.message


# ---------------------------------------------------------------------------
# check_redis
# ---------------------------------------------------------------------------


class TestCheckRedis:
    def test_passes_when_ping_ok(self):
        from app import startup_checks

        fake_client = MagicMock()
        fake_client.ping.return_value = True

        with patch("redis.Redis.from_url", return_value=fake_client):
            result = startup_checks.check_redis()

        assert result.status == "pass"

    def test_fails_when_use_celery_and_ping_raises(self, monkeypatch):
        from app import startup_checks
        from app.config import settings

        monkeypatch.setattr(settings, "use_celery", True)

        def _boom(*_, **__):
            raise ConnectionError("no redis")

        with patch("redis.Redis.from_url", side_effect=_boom):
            result = startup_checks.check_redis()

        assert result.status == "fail"
        assert "PING failed" in result.message

    def test_warns_when_no_celery_and_ping_raises(self, monkeypatch):
        from app import startup_checks
        from app.config import settings

        monkeypatch.setattr(settings, "use_celery", False)

        with patch("redis.Redis.from_url", side_effect=ConnectionError("x")):
            result = startup_checks.check_redis()

        assert result.status == "warn"


# ---------------------------------------------------------------------------
# check_celery_workers
# ---------------------------------------------------------------------------


class TestCheckCeleryWorkers:
    def test_passes_when_use_celery_false(self, monkeypatch):
        from app import startup_checks
        from app.config import settings

        monkeypatch.setattr(settings, "use_celery", False)

        result = startup_checks.check_celery_workers()
        assert result.status == "pass"
        assert "in-process" in result.message

    def test_passes_when_workers_respond(self, monkeypatch):
        from app import startup_checks
        from app.config import settings

        monkeypatch.setattr(settings, "use_celery", True)

        fake_inspect = MagicMock()
        fake_inspect.ping.return_value = {"celery@worker-1": {"ok": "pong"}}
        fake_celery = MagicMock()
        fake_celery.control.inspect.return_value = fake_inspect

        with patch.dict("sys.modules", {"app.workers.celery_app": MagicMock(celery_app=fake_celery)}):
            result = startup_checks.check_celery_workers()

        assert result.status == "pass"
        assert "1 Celery worker" in result.message

    def test_warns_when_no_workers_responded(self, monkeypatch):
        from app import startup_checks
        from app.config import settings

        monkeypatch.setattr(settings, "use_celery", True)

        fake_inspect = MagicMock()
        fake_inspect.ping.return_value = None  # slowapi returns None when no workers
        fake_celery = MagicMock()
        fake_celery.control.inspect.return_value = fake_inspect

        with patch.dict("sys.modules", {"app.workers.celery_app": MagicMock(celery_app=fake_celery)}):
            result = startup_checks.check_celery_workers()

        assert result.status == "warn"
        assert "No Celery workers" in result.message
        assert "celery -A" in result.remediation


# ---------------------------------------------------------------------------
# check_rls_posture
# ---------------------------------------------------------------------------


class TestCheckRlsPosture:
    def _session_returning(self, *, is_super: bool, current_user: str = "ae_app"):
        session = MagicMock()
        calls = [is_super, current_user]

        def _scalar():
            return calls.pop(0)

        # Each db.execute(...).scalar() pops one response.
        session.execute.return_value.scalar.side_effect = _scalar
        return session

    def test_passes_for_non_superuser(self):
        from app import startup_checks

        session = self._session_returning(is_super=False, current_user="ae_app")
        with patch("app.database.SessionLocal", return_value=session):
            result = startup_checks.check_rls_posture()

        assert result.status == "pass"
        assert "non-superuser" in result.message

    def test_warns_when_superuser(self):
        from app import startup_checks

        session = self._session_returning(is_super=True, current_user="postgres")
        with patch("app.database.SessionLocal", return_value=session):
            result = startup_checks.check_rls_posture()

        assert result.status == "warn"
        assert "superuser" in result.message.lower()
        assert "SETUP_GUIDE §5.2a" in result.remediation


# ---------------------------------------------------------------------------
# check_auth_mode
# ---------------------------------------------------------------------------


class TestCheckAuthMode:
    def test_dev_mode_passes(self, monkeypatch):
        from app import startup_checks
        from app.config import settings

        monkeypatch.setattr(settings, "auth_mode", "dev")
        monkeypatch.setattr(settings, "oidc_enabled", False)

        result = startup_checks.check_auth_mode()
        assert result.status == "pass"

    def test_jwt_with_placeholder_secret_warns(self, monkeypatch):
        from app import startup_checks
        from app.config import settings

        monkeypatch.setattr(settings, "auth_mode", "jwt")
        monkeypatch.setattr(settings, "secret_key", "change-me-in-production")

        result = startup_checks.check_auth_mode()
        assert result.status == "warn"
        assert "SECRET_KEY" in result.message

    def test_jwt_with_real_secret_passes(self, monkeypatch):
        from app import startup_checks
        from app.config import settings

        monkeypatch.setattr(settings, "auth_mode", "jwt")
        monkeypatch.setattr(settings, "secret_key", "a-real-long-random-secret")

        result = startup_checks.check_auth_mode()
        assert result.status == "pass"

    def test_oidc_missing_fields_fails(self, monkeypatch):
        from app import startup_checks
        from app.config import settings

        monkeypatch.setattr(settings, "auth_mode", "oidc")
        monkeypatch.setattr(settings, "oidc_enabled", True)
        monkeypatch.setattr(settings, "oidc_issuer", "https://example.com")
        monkeypatch.setattr(settings, "oidc_client_id", "")
        monkeypatch.setattr(settings, "oidc_client_secret", "")
        monkeypatch.setattr(settings, "oidc_redirect_uri", "https://example.com/cb")

        result = startup_checks.check_auth_mode()
        assert result.status == "fail"
        assert "OIDC_CLIENT_ID" in result.message
        assert "OIDC_CLIENT_SECRET" in result.message


# ---------------------------------------------------------------------------
# check_vault_key
# ---------------------------------------------------------------------------


class TestCheckVaultKey:
    def test_passes_when_key_is_set(self, monkeypatch):
        from app import startup_checks
        from app.config import settings

        monkeypatch.setattr(settings, "vault_key", "a-fernet-key")

        result = startup_checks.check_vault_key()
        assert result.status == "pass"

    def test_fails_when_blank_and_secrets_exist(self, monkeypatch):
        from app import startup_checks
        from app.config import settings

        monkeypatch.setattr(settings, "vault_key", "")

        fake_session = MagicMock()
        fake_session.execute.return_value.scalar.return_value = 5
        with patch("app.database.SessionLocal", return_value=fake_session):
            result = startup_checks.check_vault_key()

        assert result.status == "fail"
        assert "5 row" in result.message

    def test_warns_when_blank_and_no_secrets(self, monkeypatch):
        from app import startup_checks
        from app.config import settings

        monkeypatch.setattr(settings, "vault_key", "")

        fake_session = MagicMock()
        fake_session.execute.return_value.scalar.return_value = 0
        with patch("app.database.SessionLocal", return_value=fake_session):
            result = startup_checks.check_vault_key()

        assert result.status == "warn"


# ---------------------------------------------------------------------------
# check_mcp_default_server
# ---------------------------------------------------------------------------


class TestCheckMcpDefaultServer:
    def test_passes_when_url_blank(self, monkeypatch):
        from app import startup_checks
        from app.config import settings

        monkeypatch.setattr(settings, "mcp_server_url", "")
        result = startup_checks.check_mcp_default_server()
        assert result.status == "pass"
        assert "blank" in result.message

    def test_passes_when_tenant_rows_exist_skips_probe(self, monkeypatch):
        from app import startup_checks
        from app.config import settings

        monkeypatch.setattr(settings, "mcp_server_url", "http://mcp.example.com")

        fake_session = MagicMock()
        fake_session.execute.return_value.scalar.return_value = 3
        with patch("app.database.SessionLocal", return_value=fake_session):
            result = startup_checks.check_mcp_default_server()

        assert result.status == "pass"
        assert "tenant MCP server" in result.message

    def test_warns_when_probe_fails(self, monkeypatch):
        from app import startup_checks
        from app.config import settings

        monkeypatch.setattr(settings, "mcp_server_url", "http://definitely-unreachable.invalid:9999")

        fake_session = MagicMock()
        fake_session.execute.return_value.scalar.return_value = 0
        with patch("app.database.SessionLocal", return_value=fake_session), \
             patch("socket.create_connection", side_effect=OSError("no route")):
            result = startup_checks.check_mcp_default_server()

        assert result.status == "warn"
        assert "Cannot reach" in result.message


# ---------------------------------------------------------------------------
# run_all_checks / aggregation / /health/ready
# ---------------------------------------------------------------------------


class TestRunAllChecks:
    def test_buggy_check_does_not_break_the_run(self):
        """If a check raises an uncaught exception, the runner turns
        that into a synthetic ``fail`` result instead of aborting."""
        from app import startup_checks

        def _exploding():
            raise RuntimeError("bug in check")

        _original = startup_checks._REGISTRY
        try:
            startup_checks._REGISTRY = (_exploding,)
            results = startup_checks.run_all_checks()
        finally:
            startup_checks._REGISTRY = _original

        assert len(results) == 1
        assert results[0].status == "fail"
        assert "bug in check" in results[0].message

    def test_overall_status_aggregates_correctly(self):
        from app.startup_checks import CheckResult, overall_status

        passes = [CheckResult("a", "pass", "ok"), CheckResult("b", "pass", "ok")]
        assert overall_status(passes) == "pass"

        warns = passes + [CheckResult("c", "warn", "meh")]
        assert overall_status(warns) == "warn"

        fails = warns + [CheckResult("d", "fail", "bad")]
        assert overall_status(fails) == "fail"


class TestHealthReadyEndpoint:
    def test_returns_200_when_all_pass(self):
        """Stub the registry with three passing checks; expect 200 + pass aggregate."""
        from fastapi.testclient import TestClient

        from app import startup_checks
        from app.startup_checks import CheckResult

        def _ok_1():
            return CheckResult("x1", "pass", "ok")

        def _ok_2():
            return CheckResult("x2", "pass", "ok")

        _original = startup_checks._REGISTRY
        startup_checks._REGISTRY = (_ok_1, _ok_2)
        try:
            from main import app
            with TestClient(app) as client:
                res = client.get("/health/ready")
        finally:
            startup_checks._REGISTRY = _original

        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "pass"
        assert len(body["checks"]) == 2

    def test_returns_503_when_any_check_fails(self):
        from fastapi.testclient import TestClient

        from app import startup_checks
        from app.startup_checks import CheckResult

        def _ok():
            return CheckResult("x", "pass", "ok")

        def _bad():
            return CheckResult("y", "fail", "down", remediation="restart y")

        _original = startup_checks._REGISTRY
        startup_checks._REGISTRY = (_ok, _bad)
        try:
            from main import app
            with TestClient(app) as client:
                res = client.get("/health/ready")
        finally:
            startup_checks._REGISTRY = _original

        assert res.status_code == 503
        body = res.json()
        assert body["status"] == "fail"
        # Remediation surfaces in the response so UIs / operators see it.
        assert body["checks"][1]["remediation"] == "restart y"

    def test_returns_200_with_warn_aggregate(self):
        """Warns are not fatal for readiness — a k8s probe shouldn't
        cycle a pod for an RLS warning."""
        from fastapi.testclient import TestClient

        from app import startup_checks
        from app.startup_checks import CheckResult

        def _warn():
            return CheckResult("z", "warn", "cosmetic", remediation="later")

        _original = startup_checks._REGISTRY
        startup_checks._REGISTRY = (_warn,)
        try:
            from main import app
            with TestClient(app) as client:
                res = client.get("/health/ready")
        finally:
            startup_checks._REGISTRY = _original

        assert res.status_code == 200
        assert res.json()["status"] == "warn"
