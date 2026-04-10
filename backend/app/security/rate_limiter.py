"""Per-tenant rate limiting using slowapi.

Limits are applied at two levels:
  1. API request rate   — configurable requests per time window per tenant.
  2. Execution quota    — max workflow executions per hour per tenant.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

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


def check_execution_quota(db: Session, tenant_id: str) -> None:
    """Raise 429 if the tenant has exceeded their hourly execution quota."""
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
            "Tenant %s exceeded execution quota (%d/%d per hour)",
            tenant_id, recent_count, settings.execution_quota_per_hour,
        )
        raise HTTPException(
            status_code=429,
            detail=f"Execution quota exceeded: {recent_count}/{settings.execution_quota_per_hour} per hour",
        )
