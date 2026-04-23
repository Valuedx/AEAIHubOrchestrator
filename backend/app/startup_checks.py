"""STARTUP-01 — preflight / readiness checks.

Every failure mode that used to silently express itself as "my workflow
sat at queued forever" or "secrets aren't decrypting for reasons I
can't see" should be caught here with a specific, remedied log line.
Operators shouldn't have to spelunk docs to discover that their
Celery worker isn't running.

Design
------

* Each check is a plain sync function returning a ``CheckResult``.
  Blocking I/O is fine — six checks in series stay under ~5 seconds
  even when Celery is unreachable.
* Every check catches its own exceptions and maps them to a ``fail``
  result. A bug IN the check function shouldn't break the endpoint
  or the lifespan.
* Status levels are deliberately kept to three values:

    - ``pass``  — this dependency is healthy for its role
    - ``warn``  — degraded but still serve traffic (e.g. RLS bypassed
      on a dev role — not a fatal issue, but security is weakened
      and operators need to know)
    - ``fail``  — readiness probe should return 503

* Lifespan runs every check once at boot and logs the outcomes. The
  ``/health/ready`` endpoint re-runs them live so k8s readiness
  probes reflect current state, not boot-time state.
* Set ``ORCHESTRATOR_SKIP_STARTUP_CHECKS=true`` to silence the
  lifespan pass — useful in tests that don't want startup IO.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Literal

logger = logging.getLogger(__name__)

Status = Literal["pass", "warn", "fail"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    message: str
    # Remediation hint — operators should be able to read this and
    # know the exact next step without grepping docs.
    remediation: str = ""
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Tier 1 — always run, always logged
# ---------------------------------------------------------------------------


def check_database() -> CheckResult:
    """Verify the app DB is reachable AND on the latest alembic head.

    "Schema is behind head" is the #1 cause of cryptic ``column does
    not exist`` errors after a deploy where migrations weren't run.
    Flagging it at startup saves that entire class of support ticket.
    """
    try:
        from app.database import SessionLocal
        from sqlalchemy import text as _text
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name="database",
            status="fail",
            message=f"Cannot import database module: {exc}",
            remediation="Check ORCHESTRATOR_DATABASE_URL and the app's import graph.",
        )

    # 1. Connectivity
    try:
        db = SessionLocal()
        try:
            db.execute(_text("SELECT 1"))
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name="database",
            status="fail",
            message=f"SELECT 1 failed: {exc}",
            remediation="Confirm Postgres is running and ORCHESTRATOR_DATABASE_URL is reachable with valid credentials.",
        )

    # 2. Alembic head match
    try:
        import os
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from alembic.runtime.migration import MigrationContext

        # Resolve alembic config relative to the running process.
        config_path = os.path.join(os.getcwd(), "alembic.ini")
        if not os.path.exists(config_path):
            # Running under a non-standard cwd — try backend/ explicitly.
            alt = os.path.join(os.getcwd(), "backend", "alembic.ini")
            if os.path.exists(alt):
                config_path = alt

        cfg = Config(config_path)
        script = ScriptDirectory.from_config(cfg)
        head_rev = script.get_current_head()

        from app.database import engine as db_engine

        with db_engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            current_rev = ctx.get_current_revision()
    except Exception as exc:  # noqa: BLE001
        # Don't fail-startup on alembic introspection errors — the DB
        # itself is reachable (step 1 passed). Warn so operators see it.
        return CheckResult(
            name="database",
            status="warn",
            message=f"Connected, but could not verify alembic head: {exc}",
            remediation="Ensure alembic.ini is accessible from the working directory and `alembic current` works.",
        )

    if current_rev != head_rev:
        return CheckResult(
            name="database",
            status="warn",
            message=f"Schema behind head: current={current_rev} head={head_rev}",
            remediation="Run `alembic upgrade head` from the backend directory.",
            details={"current": current_rev, "head": head_rev},
        )
    return CheckResult(
        name="database",
        status="pass",
        message=f"Connected and on head revision {head_rev}.",
        details={"revision": head_rev},
    )


def check_redis() -> CheckResult:
    """Ping Redis. Fatal if USE_CELERY=true (broker is required);
    warn-only otherwise (only used for SSE token streaming + OIDC
    PKCE state)."""
    from app.config import settings

    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        client.ping()
        client.close()
    except Exception as exc:  # noqa: BLE001
        severity: Status = "fail" if settings.use_celery else "warn"
        return CheckResult(
            name="redis",
            status=severity,
            message=f"Redis PING failed: {exc}",
            remediation=(
                "Start Redis and verify ORCHESTRATOR_REDIS_URL."
                if settings.use_celery
                else "Redis is optional without Celery; SSE streaming + OIDC state will degrade."
            ),
        )
    return CheckResult(
        name="redis",
        status="pass",
        message="Redis PING ok.",
    )


def check_celery_workers() -> CheckResult:
    """Confirm at least one Celery worker is connected when the app
    is configured to dispatch via Celery. Captures the exact scenario
    where a tester sees "queued" forever because no worker is running.
    """
    from app.config import settings

    if not settings.use_celery:
        return CheckResult(
            name="celery_workers",
            status="pass",
            message="USE_CELERY=false — tasks run in-process, no worker required.",
        )

    try:
        from app.workers.celery_app import celery_app

        reply = celery_app.control.inspect(timeout=2).ping()
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name="celery_workers",
            status="warn",
            message=f"Celery inspect raised: {exc}",
            remediation="Ensure Celery broker (Redis) is reachable and a worker is running.",
        )

    if not reply:
        return CheckResult(
            name="celery_workers",
            status="warn",
            message="No Celery workers responded to ping within 2s.",
            remediation=(
                "Start a worker: `celery -A app.workers.celery_app worker --loglevel=info`. "
                "Workflows dispatched via Celery will queue indefinitely until one is running."
            ),
        )
    return CheckResult(
        name="celery_workers",
        status="pass",
        message=f"{len(reply)} Celery worker(s) responded.",
        details={"workers": list(reply.keys())},
    )


def check_rls_posture() -> CheckResult:
    """Warn if the app DB role is a Postgres superuser.

    Superusers BYPASS every RLS policy silently — cross-tenant reads
    appear to work, but tenant isolation is not actually enforced.
    Documented in SETUP_GUIDE §5.2a, which is easy to miss.
    """
    try:
        from app.database import SessionLocal
        from sqlalchemy import text as _text
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name="rls_posture",
            status="fail",
            message=f"Cannot import database module: {exc}",
        )

    try:
        db = SessionLocal()
        try:
            is_super = db.execute(
                _text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            ).scalar()
            current_user = db.execute(_text("SELECT current_user")).scalar()
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name="rls_posture",
            status="warn",
            message=f"Could not introspect current_user: {exc}",
            remediation="Verify the app can SELECT from pg_roles (most roles can by default).",
        )

    if is_super:
        return CheckResult(
            name="rls_posture",
            status="warn",
            message=(
                f"App connected as superuser {current_user!r} — RLS policies are silently bypassed."
            ),
            remediation=(
                "Create a non-superuser role per SETUP_GUIDE §5.2a and point "
                "ORCHESTRATOR_DATABASE_URL at it. Tenant isolation is not enforced today."
            ),
            details={"current_user": current_user, "is_superuser": True},
        )
    return CheckResult(
        name="rls_posture",
        status="pass",
        message=f"Connected as non-superuser {current_user!r}; RLS is enforced.",
        details={"current_user": current_user, "is_superuser": False},
    )


def check_auth_mode() -> CheckResult:
    """Sanity-check auth-related env:

    * jwt mode with the placeholder SECRET_KEY
    * oidc mode with any required OIDC_* field blank
    """
    from app.config import settings

    mode = (settings.auth_mode or "dev").lower()

    if mode == "jwt":
        if settings.secret_key in ("", "change-me-in-production"):
            return CheckResult(
                name="auth_mode",
                status="warn",
                message="AUTH_MODE=jwt but SECRET_KEY is the placeholder.",
                remediation="Set ORCHESTRATOR_SECRET_KEY to a long random string before production use.",
            )
        return CheckResult(
            name="auth_mode",
            status="pass",
            message="AUTH_MODE=jwt with a non-default SECRET_KEY.",
        )

    if mode == "oidc" or settings.oidc_enabled:
        missing = [
            name
            for name, value in (
                ("OIDC_ISSUER", settings.oidc_issuer),
                ("OIDC_CLIENT_ID", settings.oidc_client_id),
                ("OIDC_CLIENT_SECRET", settings.oidc_client_secret),
                ("OIDC_REDIRECT_URI", settings.oidc_redirect_uri),
            )
            if not value
        ]
        if missing:
            return CheckResult(
                name="auth_mode",
                status="fail",
                message=f"OIDC enabled but missing: {', '.join(missing)}.",
                remediation="Populate every ORCHESTRATOR_OIDC_* field or set OIDC_ENABLED=false.",
                details={"missing": missing},
            )
        return CheckResult(
            name="auth_mode",
            status="pass",
            message="OIDC configured with all required fields.",
        )

    # dev mode — fine for local; warn when it looks production-y.
    return CheckResult(
        name="auth_mode",
        status="pass",
        message="AUTH_MODE=dev (header-based tenant id). Do not ship this to production untouched.",
    )


def check_vault_key() -> CheckResult:
    """VAULT_KEY must be set whenever tenant_secrets has rows — else
    every read will blow up at Fernet.decrypt time."""
    from app.config import settings

    if settings.vault_key:
        return CheckResult(
            name="vault_key",
            status="pass",
            message="VAULT_KEY is set.",
        )

    # Vault key is blank — is that a problem?
    try:
        from app.database import SessionLocal
        from sqlalchemy import text as _text
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name="vault_key",
            status="warn",
            message=f"VAULT_KEY blank; couldn't introspect tenant_secrets: {exc}",
            remediation="Generate with `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` and set ORCHESTRATOR_VAULT_KEY.",
        )

    try:
        db = SessionLocal()
        try:
            row_count = db.execute(_text("SELECT COUNT(*) FROM tenant_secrets")).scalar()
        finally:
            db.close()
    except Exception:
        # Table not there yet (pre-migration) — that's fine; warn lightly.
        return CheckResult(
            name="vault_key",
            status="warn",
            message="VAULT_KEY is blank. Encrypting/decrypting tenant_secrets will fail at first use.",
            remediation="Generate a Fernet key and set ORCHESTRATOR_VAULT_KEY.",
        )

    if row_count and row_count > 0:
        return CheckResult(
            name="vault_key",
            status="fail",
            message=f"VAULT_KEY is blank but tenant_secrets has {row_count} row(s) — decryption will fail at runtime.",
            remediation="Set ORCHESTRATOR_VAULT_KEY to the Fernet key that encrypted those rows.",
        )
    return CheckResult(
        name="vault_key",
        status="warn",
        message="VAULT_KEY is blank; tenant_secrets is empty so nothing breaks yet.",
        remediation="Generate a Fernet key before creating the first tenant secret.",
    )


# ---------------------------------------------------------------------------
# Tier 2 — optional; slower / more fragile
# ---------------------------------------------------------------------------


def check_mcp_default_server() -> CheckResult:
    """Reach out to the env-fallback MCP server (pre-MCP-02 mode).

    Skipped when any tenant has a ``tenant_mcp_servers`` row — that
    case is covered by MCP-02's resolver and per-server configuration.
    Operators who've fully migrated to the registry don't need this
    check probing their legacy env URL every readiness poll.
    """
    from app.config import settings

    if not settings.mcp_server_url:
        return CheckResult(
            name="mcp_default_server",
            status="pass",
            message="ORCHESTRATOR_MCP_SERVER_URL is blank — no default MCP server to probe.",
        )

    # Any tenant registered? If so, env fallback is already deemphasised.
    try:
        from app.database import SessionLocal
        from sqlalchemy import text as _text

        db = SessionLocal()
        try:
            n_rows = db.execute(
                _text("SELECT COUNT(*) FROM tenant_mcp_servers")
            ).scalar()
        finally:
            db.close()
        if n_rows and n_rows > 0:
            return CheckResult(
                name="mcp_default_server",
                status="pass",
                message=(
                    f"{n_rows} tenant MCP server(s) registered — env fallback "
                    "skipped from readiness probe."
                ),
            )
    except Exception:
        # pre-migration or DB issue — fall through; we'll probe the URL.
        pass

    # Cheap reachability probe — TCP connect, not a full MCP handshake.
    # Full handshake adds another 1-2s per readiness poll with little
    # extra signal vs. this.
    try:
        import socket
        from urllib.parse import urlparse

        parsed = urlparse(settings.mcp_server_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=2):
            pass
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name="mcp_default_server",
            status="warn",
            message=f"Cannot reach {settings.mcp_server_url}: {exc}",
            remediation=(
                "Either start the MCP server, update ORCHESTRATOR_MCP_SERVER_URL, "
                "or migrate tenants to the per-tenant registry (MCP-02)."
            ),
        )
    return CheckResult(
        name="mcp_default_server",
        status="pass",
        message=f"{settings.mcp_server_url} is reachable (TCP connect).",
    )


# ---------------------------------------------------------------------------
# Registry + runner
# ---------------------------------------------------------------------------


def check_model_registry_drift() -> CheckResult:
    """Catch `shared/node_registry.json` drifting from the central
    model registry. A drift means users see a model in the Node
    Inspector dropdown that the backend doesn't know how to route —
    or vice versa, a new registry model hasn't been surfaced to
    users yet. MODEL-01.c wired this check.
    """
    try:
        from app.engine.model_registry import node_registry_drift

        drifts = node_registry_drift()
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name="model_registry_drift",
            status="fail",
            message=f"drift check itself raised: {exc}",
            remediation=(
                "File a bug — the drift check should never raise. "
                "In the meantime, inspect `backend/app/engine/model_registry.py`."
            ),
        )
    if not drifts:
        return CheckResult(
            name="model_registry_drift",
            status="pass",
            message="shared/node_registry.json matches the central model registry.",
        )
    return CheckResult(
        name="model_registry_drift",
        status="warn",
        message="; ".join(drifts),
        remediation=(
            "Update `shared/node_registry.json` model enums to match "
            "`backend/app/engine/model_registry.py::expected_node_enum()`, "
            "or add the new model to the registry."
        ),
    )


_REGISTRY: tuple[Callable[[], CheckResult], ...] = (
    check_database,
    check_redis,
    check_celery_workers,
    check_rls_posture,
    check_auth_mode,
    check_vault_key,
    check_mcp_default_server,
    check_model_registry_drift,
)


def run_all_checks() -> list[CheckResult]:
    """Run every check sequentially, catching per-check exceptions so
    one broken check can't mask the others."""
    results: list[CheckResult] = []
    for fn in _REGISTRY:
        try:
            results.append(fn())
        except Exception as exc:  # noqa: BLE001
            results.append(
                CheckResult(
                    name=fn.__name__,
                    status="fail",
                    message=f"Check itself raised: {exc}",
                    remediation="File a bug — a startup check must not raise uncaught.",
                )
            )
    return results


def log_results(results: list[CheckResult]) -> None:
    """One line per check at the appropriate log level."""
    for r in results:
        if r.status == "fail":
            logger.error(
                "startup check %s FAIL: %s  remediation=%s",
                r.name, r.message, r.remediation or "(none)",
            )
        elif r.status == "warn":
            logger.warning(
                "startup check %s WARN: %s  remediation=%s",
                r.name, r.message, r.remediation or "(none)",
            )
        else:
            logger.info("startup check %s pass: %s", r.name, r.message)


def overall_status(results: list[CheckResult]) -> Status:
    """Aggregate: any ``fail`` → fail; any ``warn`` → warn; else pass."""
    if any(r.status == "fail" for r in results):
        return "fail"
    if any(r.status == "warn" for r in results):
        return "warn"
    return "pass"


def results_as_dict(results: list[CheckResult]) -> dict[str, Any]:
    """Serialisable shape for the /health/ready endpoint."""
    return {
        "status": overall_status(results),
        "checks": [asdict(r) for r in results],
    }
