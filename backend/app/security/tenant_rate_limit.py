"""ADMIN-02 — per-tenant API rate-limit middleware.

Fixed-window rate limiting via Redis INCR+EXPIRE, keyed by
``(tenant, time-bucket)``. Per-tenant overrides come from
``engine/tenant_policy_resolver``; when a tenant has no row, the
env defaults (``ORCHESTRATOR_RATE_LIMIT_REQUESTS`` and
``ORCHESTRATOR_RATE_LIMIT_WINDOW_SECONDS``) apply.

Why this replaces slowapi: the previous ``slowapi.Limiter`` was
instantiated but never wired into a middleware, so it had no
runtime effect — we'd been shipping a dead dependency. This module
takes the ``check_execution_quota`` design (which works fine) and
applies it to the API-rate layer. One less dead-code path; one
real enforcement mechanism.

Design choices:

* **Fail-open on Redis errors.** A broken Redis is already painful
  for execution quotas; hard-failing every HTTP request on top of
  that would make the outage much worse. Log a warning and let the
  request through.
* **Key by tenant, fall back to IP.** Same approach the old slowapi
  config used via ``_get_tenant_key``. Unauthenticated requests that
  pre-date tenant resolution get IP-based limiting at env defaults.
* **Bucket math in the key.** ``floor(now / window_seconds)`` gives
  a clean rollover without any race. The TTL is just the window
  length plus a small buffer so an idle bucket evicts itself.
* **Skip list for non-rate-limited paths.** ``/health`` and the
  OpenAPI / docs endpoints are exempt — they're infra and shouldn't
  count against a tenant's budget.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

# Paths that never count against the rate limit. Match by prefix so
# subpaths under /docs (the OpenAPI schema, static assets) are also
# exempt.
_EXEMPT_PATH_PREFIXES: tuple[str, ...] = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
)


def _tenant_key(request: Request) -> str:
    """Extract the rate-limit bucket key for this request.

    Matches the old ``_get_tenant_key`` contract — tenant header wins,
    client IP falls back. JWT parsing happens in a downstream dependency
    so we can't read it here; the header is a lightweight proxy that's
    good enough for the rate-limit layer.
    """
    from slowapi.util import get_remote_address

    return request.headers.get("x-tenant-id") or get_remote_address(request)


def _redis_bucket_key(tenant_key: str, window_seconds: int, now: float | None = None) -> str:
    """Fixed-window bucket key: ``orch:ratelimit:<tenant>:<bucket_index>``."""
    n = int(now if now is not None else time.time())
    bucket = n // window_seconds
    return f"orch:ratelimit:{tenant_key}:{bucket}"


def check_api_rate_limit(
    tenant_key: str,
    *,
    requests_per_window: int,
    window_seconds: int,
    client_factory: Callable[[], object] | None = None,
    now: float | None = None,
) -> int:
    """Atomic INCR+EXPIRE against the tenant's current-window bucket.

    Returns the post-increment count. Raises ``_RateLimitExceeded``
    when the count exceeds ``requests_per_window`` — caller converts
    that to an HTTP response.

    ``client_factory`` is injected for tests; production callers let
    it default to the module-local lazy Redis client in
    ``security/rate_limiter.py``.
    """
    if client_factory is None:
        from app.security.rate_limiter import _get_redis_client

        client_factory = _get_redis_client

    client = client_factory()
    key = _redis_bucket_key(tenant_key, window_seconds, now=now)
    pipe = client.pipeline()
    pipe.incr(key)
    # +5s buffer so the bucket evicts a few ticks after it's no longer
    # the "current" one, keeping Redis clean without bumping the
    # enforcement semantics.
    pipe.expire(key, window_seconds + 5)
    result = pipe.execute()
    count = int(result[0])
    if count > requests_per_window:
        raise _RateLimitExceeded(
            limit=requests_per_window,
            window_seconds=window_seconds,
            count=count,
        )
    return count


class _RateLimitExceeded(Exception):
    def __init__(self, *, limit: int, window_seconds: int, count: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.count = count
        super().__init__(f"rate limit exceeded: {count}/{limit} in {window_seconds}s")


class TenantRateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that enforces the per-tenant API rate limit.

    Runs before route handlers; returns **429** when a tenant exceeds
    their window budget. Resolves the per-tenant policy via
    ``engine/tenant_policy_resolver`` so each tenant can have its
    own cap.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        # Skip infra endpoints — they aren't tenant work.
        if any(request.url.path.startswith(p) for p in _EXEMPT_PATH_PREFIXES):
            return await call_next(request)

        from app.engine.tenant_policy_resolver import get_effective_policy

        key = _tenant_key(request)
        # Resolver reads tenant_policies; if the X-Tenant-Id header is
        # a real tenant id, that tenant's override (if any) applies.
        # If the key is an IP (unauthenticated), the resolver hits
        # ``get_effective_policy(tenant_id=<ip>)`` which won't find a
        # row and cleanly returns env defaults — that's the right
        # answer for an unauthenticated caller.
        policy = get_effective_policy(key)

        try:
            check_api_rate_limit(
                key,
                requests_per_window=policy.rate_limit_requests_per_window,
                window_seconds=policy.rate_limit_window_seconds,
            )
        except _RateLimitExceeded as exc:
            logger.info(
                "rate limit exceeded for %r: %d/%d in %ds",
                key, exc.count, exc.limit, exc.window_seconds,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "detail": (
                        f"API rate limit exceeded: {exc.count}/{exc.limit} "
                        f"per {exc.window_seconds}s window"
                    ),
                },
                headers={"Retry-After": str(exc.window_seconds)},
            )
        except Exception as exc:  # noqa: BLE001 — Redis / whatever
            # Fail-open on Redis / resolver errors. A broken rate-limit
            # layer should not cascade into 500s on every endpoint; we
            # log and continue. The execution-quota layer has the same
            # fail-open stance for the same reason.
            logger.warning(
                "rate limit check failed for %r, allowing request: %s",
                key, exc,
            )

        return await call_next(request)
