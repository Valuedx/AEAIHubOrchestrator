"""AE AI Hub -- Orchestrator API Gateway."""

import atexit
import logging

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
from app.security.rate_limiter import limiter
from app.security.tenant_rate_limit import TenantRateLimitMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="AE AI Hub - Orchestrator",
    description="Agentic workflow orchestration engine with visual DAG builder",
    version="0.9.2",
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

if settings.oidc_enabled:
    from app.api.auth import router as oidc_router
    app.include_router(oidc_router)
    logging.getLogger(__name__).info("OIDC federation enabled (issuer: %s)", settings.oidc_issuer)

from app.observability import shutdown as _shutdown_langfuse
atexit.register(_shutdown_langfuse)


@app.get("/health")
def health():
    return {"status": "ok", "service": "ae-ai-hub-orchestrator"}


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
