"""Per-tenant rate limiting using slowapi + Redis-atomic execution quota.

Two independent layers:
  1. API request rate — slowapi (configurable requests/window per tenant).
  2. Execution quota  — atomic INCR+EXPIRE per (tenant, hour-bucket) in Redis.
     Falls back to a DB count if Redis is unreachable; the fallback is
     subject to TOCTOU but is better than failing open.

The previous implementation counted rows in ``workflow_instances`` and
then compared to the quota — two concurrent requests could both read
just-under-quota, both pass the check, then both insert rows, silently
exceeding the quota. The Redis path closes that window.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Callable

from fastapi import Request, HTTPException
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.config import settings
from app.models.workflow import WorkflowInstance

logger = logging.getLogger(__name__)


def _get_tenant_key(request: Request) -> str:
    """Extract tenant identifier for rate limiting.

    Tries the X-Tenant-Id header first, then falls back to client IP.
    JWT-based tenant extraction happens after the rate limiter runs, so
    we use the header here as a lightweight proxy.
    """
    return request.headers.get("x-tenant-id") or get_remote_address(request)


limiter = Limiter(
    key_func=_get_tenant_key,
    default_limits=[f"{settings.rate_limit_requests}/{settings.rate_limit_window}"],
    storage_uri=settings.redis_url,
)


# ---------------------------------------------------------------------------
# Execution quota
# ---------------------------------------------------------------------------

_redis_client = None


def _get_redis_client():
    """Lazy + cached Redis client for quota INCR."""
    global _redis_client
    if _redis_client is None:
        import redis  # local import so the module loads without redis

        _redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def _hour_bucket(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d-%H")


_QUOTA_BUCKET_TTL_SECONDS = 3700  # one hour plus a small buffer to avoid off-by-one


def _redis_quota_key(tenant_id: str, bucket: str | None = None) -> str:
    return f"orch:quota:{tenant_id}:{bucket or _hour_bucket()}"


def _check_via_redis(
    tenant_id: str,
    *,
    client_factory: Callable[[], object] = _get_redis_client,
) -> int:
    """Atomically increment the tenant's hour-bucket counter and raise 429
    if it exceeds the quota. Returns the counter value on success.

    A pipeline makes INCR + EXPIRE a single round-trip — even with many
    concurrent callers, exactly one per quota slot is allowed to "win"
    the last token.
    """
    client = client_factory()
    key = _redis_quota_key(tenant_id)
    pipe = client.pipeline()
    pipe.incr(key)
    pipe.expire(key, _QUOTA_BUCKET_TTL_SECONDS)
    result = pipe.execute()
    count = int(result[0])
    if count > settings.execution_quota_per_hour:
        logger.warning(
            "Tenant %s exceeded execution quota (%d/%d per hour, Redis)",
            tenant_id, count, settings.execution_quota_per_hour,
        )
        raise HTTPException(
            status_code=429,
            detail=f"Execution quota exceeded: {count}/{settings.execution_quota_per_hour} per hour",
        )
    return count


def _check_via_db(db: Session, tenant_id: str) -> int:
    """Fallback path when Redis is unavailable. Counts recent instances —
    not atomic, but correct under single-writer load."""
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    recent_count = (
        db.query(WorkflowInstance)
        .filter(
            WorkflowInstance.tenant_id == tenant_id,
            WorkflowInstance.created_at >= one_hour_ago,
        )
        .count()
    )
    if recent_count >= settings.execution_quota_per_hour:
        logger.warning(
            "Tenant %s exceeded execution quota (%d/%d per hour, DB fallback)",
            tenant_id, recent_count, settings.execution_quota_per_hour,
        )
        raise HTTPException(
            status_code=429,
            detail=f"Execution quota exceeded: {recent_count}/{settings.execution_quota_per_hour} per hour",
        )
    return recent_count


def check_execution_quota(db: Session, tenant_id: str) -> None:
    """Raise 429 if the tenant has exceeded their hourly execution quota.

    Prefers the atomic Redis path; falls back to a DB count on any Redis
    error so a transient infra problem doesn't fail the request outright.
    """
    try:
        _check_via_redis(tenant_id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — any redis library error
        logger.warning(
            "Redis quota check failed for tenant %s, falling back to DB: %s",
            tenant_id, exc,
        )
        _check_via_db(db, tenant_id)
