"""AE AI Hub -- Orchestrator API Gateway."""

import atexit
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.api.workflows import router as workflows_router
from app.api.tools import router as tools_router
from app.api.sse import router as sse_router
from app.api.conversations import router as conversations_router
from app.api.a2a import router as a2a_router
from app.api.knowledge import router as knowledge_router
from app.api.memory import router as memory_router
from app.api.secrets import router as secrets_router
from app.api.async_jobs import router as async_jobs_router
from app.api.tenant_integrations import router as tenant_integrations_router
from app.api.tenant_mcp_servers import router as tenant_mcp_servers_router
from app.api.tenant_policies import router as tenant_policies_router
from app.api.models import router as models_router
from app.api.llm_credentials import router as llm_credentials_router
from app.api.copilot_drafts import router as copilot_drafts_router
from app.api.copilot_sessions import router as copilot_sessions_router
from app.security.rate_limiter import limiter
from app.security.tenant_rate_limit import TenantRateLimitMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """STARTUP-01 — run preflight checks once on boot.

    Each check catches its own exceptions so a misbehaving check can't
    abort startup. Results are logged at INFO/WARNING/ERROR levels so
    operators see failures in the regular uvicorn stream. The
    ``/health/ready`` endpoint re-runs the same checks live for
    readiness probes.

    Disabled when ``ORCHESTRATOR_SKIP_STARTUP_CHECKS=true`` — tests
    that spin up TestClient(app) without real IO set that to keep
    per-test logs quiet.
    """
    if not settings.skip_startup_checks:
        from app.startup_checks import run_all_checks, log_results, overall_status

        results = run_all_checks()
        log_results(results)
        agg = overall_status(results)
        if agg == "fail":
            logging.getLogger(__name__).error(
                "Startup checks report FAIL — /health/ready will 503 until remedied.",
            )
        elif agg == "warn":
            logging.getLogger(__name__).warning(
                "Startup checks report WARN — app will serve traffic; see remediation messages above.",
            )
        else:
            logging.getLogger(__name__).info("All startup checks passed.")

    # LOCAL-AUTH-01 — idempotent admin seed. No-op unless auth_mode=local
    # AND ORCHESTRATOR_LOCAL_ADMIN_USERNAME/PASSWORD are set. Runs after
    # startup checks so a DB outage surfaces via the normal health
    # signal rather than an opaque seed failure.
    try:
        from app.security.local_auth import ensure_admin_seeded
        ensure_admin_seeded()
    except Exception as exc:  # pragma: no cover — seed is best-effort
        logging.getLogger(__name__).warning(
            "local-auth admin seed skipped: %s", exc,
        )
    yield


app = FastAPI(
    title="AE AI Hub - Orchestrator",
    description="Agentic workflow orchestration engine with visual DAG builder",
    version="0.9.2",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# ADMIN-02: per-tenant API rate limit. Added AFTER CORS so preflight
# OPTIONS requests don't count against the tenant's budget (Starlette
# processes middlewares outside-in; CORS short-circuits OPTIONS before
# our middleware runs).
app.add_middleware(TenantRateLimitMiddleware)

app.include_router(workflows_router)
app.include_router(tools_router)
app.include_router(sse_router)
app.include_router(conversations_router)
app.include_router(a2a_router)
app.include_router(knowledge_router, prefix="/api/v1/knowledge-bases", tags=["knowledge"])
app.include_router(memory_router)
app.include_router(secrets_router, prefix="/api/v1/secrets", tags=["secrets"])
app.include_router(async_jobs_router)
app.include_router(
    tenant_integrations_router,
    prefix="/api/v1/tenant-integrations",
    tags=["tenant-integrations"],
)
app.include_router(
    tenant_mcp_servers_router,
    prefix="/api/v1/tenant-mcp-servers",
    tags=["tenant-mcp-servers"],
)
app.include_router(
    tenant_policies_router,
    prefix="/api/v1/tenant-policy",
    tags=["tenant-policy"],
)
app.include_router(
    models_router,
    prefix="/api/v1/models",
    tags=["models"],
)
app.include_router(
    llm_credentials_router,
    prefix="/api/v1/llm-credentials",
    tags=["llm-credentials"],
)
app.include_router(copilot_drafts_router)
app.include_router(copilot_sessions_router)

if settings.oidc_enabled:
    from app.api.auth import router as oidc_router
    app.include_router(oidc_router)
    logging.getLogger(__name__).info("OIDC federation enabled (issuer: %s)", settings.oidc_issuer)

# LOCAL-AUTH-01 — local username/password auth. The login + /auth/me
# routes are only mounted when auth_mode=local so dev/jwt/oidc
# deployments don't expose a surface they can't service. Admin user
# CRUD is on the same gate.
if settings.auth_mode == "local":
    from app.api.auth_local import router as auth_local_router
    from app.api.users import router as users_router
    app.include_router(auth_local_router)
    app.include_router(users_router, prefix="/api/v1/users", tags=["users"])
    logging.getLogger(__name__).info("Local password auth enabled")

from app.observability import shutdown as _shutdown_langfuse
atexit.register(_shutdown_langfuse)


@app.get("/health")
def health():
    """Liveness probe — unconditional 200 as long as the process is up.

    Does NOT verify any external dependency. Use ``/health/ready`` for
    readiness (DB, Redis, Celery workers, RLS posture, etc.).
    """
    return {"status": "ok", "service": "ae-ai-hub-orchestrator"}


@app.get("/health/ready")
def health_ready():
    """Readiness probe (STARTUP-01).

    Runs every preflight check live and returns a structured report.
    HTTP 503 when any check is ``fail``; HTTP 200 otherwise (warns
    included). k8s readiness probes should pair with this; liveness
    probes should keep using ``/health``.
    """
    from app.startup_checks import overall_status, results_as_dict, run_all_checks

    results = run_all_checks()
    body = results_as_dict(results)
    status_code = 503 if overall_status(results) == "fail" else 200
    return JSONResponse(status_code=status_code, content=body)


@app.get("/auth/token")
def dev_token(tenant_id: str = "default"):
    """Development-only endpoint to generate a JWT for testing.

    In production, tokens are issued by the organization's identity provider.
    """
    if settings.auth_mode != "dev":
        return JSONResponse(
            status_code=403,
            content={"detail": "Token generation disabled in production mode"},
        )
    from app.security.jwt_auth import create_access_token
    token = create_access_token(tenant_id=tenant_id)
    return {"access_token": token, "token_type": "bearer", "tenant_id": tenant_id}
