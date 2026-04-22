"""ADMIN-01 — per-tenant policy registry CRUD.

Unlike ``tenant_integrations`` (multiple rows per tenant, keyed by
label) or ``tenant_mcp_servers``, a tenant has exactly one policy
row. So the API shape is a singleton:

  GET   /api/v1/tenant-policy   — returns effective values + source
  PATCH /api/v1/tenant-policy   — UPSERT partial override

PATCH semantics: passing an explicit ``null`` for a field clears that
override so the value falls through to the env default. Omitting the
field leaves the current override alone. Passing an integer sets (or
overwrites) the override.

RLS handles tenant isolation at the DB layer. Platform-admin "see
every tenant's policy" is deliberately not supported here — that
would need a BYPASSRLS role and a separate auth mode.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_tenant_db
from app.security.tenant import get_tenant_id
from app.models.workflow import TenantPolicy
from app.engine.tenant_policy_resolver import get_effective_policy

logger = logging.getLogger(__name__)

router = APIRouter()


# Sentinel-ish: PATCH body uses an Optional[int] that Pydantic keeps
# distinct between "field absent" (leave alone) and "field present as
# null" (clear override). Python doesn't give us a clean way to tell
# those apart without inspecting the raw payload, so we read
# ``model_fields_set`` on the parsed model.


class TenantPolicyUpdate(BaseModel):
    execution_quota_per_hour: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Per-hour workflow execution quota. Null clears the "
            "override so this tenant inherits ORCHESTRATOR_EXECUTION_"
            "QUOTA_PER_HOUR."
        ),
    )
    max_snapshots: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Max workflow_snapshots rows retained per workflow for "
            "this tenant. 0 = unlimited. Null clears the override."
        ),
    )
    mcp_pool_size: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Warm MCP client sessions per (tenant, server) pool. "
            "Null clears the override."
        ),
    )
    rate_limit_requests_per_window: int | None = Field(
        default=None,
        ge=1,
        description=(
            "ADMIN-02 — max API requests per window for this tenant. "
            "Null clears the override so this tenant inherits "
            "ORCHESTRATOR_RATE_LIMIT_REQUESTS."
        ),
    )
    rate_limit_window_seconds: int | None = Field(
        default=None,
        ge=1,
        description=(
            "ADMIN-02 — rate-limit window duration in seconds. "
            "60 = 1 minute, 3600 = 1 hour. Null clears the override."
        ),
    )
    smart_04_lints_enabled: bool | None = Field(
        default=None,
        description=(
            "SMART-04 — toggle the copilot's proactive authoring "
            "lints. True = run lints after every mutation. False = "
            "skip (cost-conscious tenants). Null clears the override "
            "so this tenant inherits ORCHESTRATOR_SMART_04_LINTS_ENABLED."
        ),
    )


class TenantPolicyOut(BaseModel):
    tenant_id: str
    # ``values`` carries integer knobs (quotas, limits, pool sizes).
    # SMART-XX feature flags land in ``flags`` so the frontend can
    # render typed toggles without switching on schema per-key.
    values: dict[str, int]
    flags: dict[str, bool]
    source: dict[str, str]
    updated_at: str | None


@router.get("", response_model=TenantPolicyOut)
def get_policy(
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    """Return the tenant's effective policy (overrides merged with env)."""
    policy = get_effective_policy(tenant_id)
    row = db.query(TenantPolicy).filter_by(tenant_id=tenant_id).first()
    return TenantPolicyOut(
        tenant_id=tenant_id,
        values={
            "execution_quota_per_hour": policy.execution_quota_per_hour,
            "max_snapshots": policy.max_snapshots,
            "mcp_pool_size": policy.mcp_pool_size,
            "rate_limit_requests_per_window": policy.rate_limit_requests_per_window,
            "rate_limit_window_seconds": policy.rate_limit_window_seconds,
        },
        flags={
            "smart_04_lints_enabled": policy.smart_04_lints_enabled,
        },
        source=dict(policy.source),
        updated_at=row.updated_at.isoformat() if row and row.updated_at else None,
    )


@router.patch("", response_model=TenantPolicyOut)
def update_policy(
    body: TenantPolicyUpdate,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    """UPSERT the tenant's policy row.

    Fields *present* in the request body — including explicit null —
    are written. Omitted fields are left alone.
    """
    row = db.query(TenantPolicy).filter_by(tenant_id=tenant_id).first()
    if row is None:
        row = TenantPolicy(tenant_id=tenant_id)
        db.add(row)

    # model_fields_set distinguishes "field was sent" (including null)
    # from "field was omitted". Null → clear override; int → set it.
    sent = body.model_fields_set
    if "execution_quota_per_hour" in sent:
        row.execution_quota_per_hour = body.execution_quota_per_hour
    if "max_snapshots" in sent:
        row.max_snapshots = body.max_snapshots
    if "mcp_pool_size" in sent:
        row.mcp_pool_size = body.mcp_pool_size
    if "rate_limit_requests_per_window" in sent:
        row.rate_limit_requests_per_window = body.rate_limit_requests_per_window
    if "rate_limit_window_seconds" in sent:
        row.rate_limit_window_seconds = body.rate_limit_window_seconds
    if "smart_04_lints_enabled" in sent:
        row.smart_04_lints_enabled = body.smart_04_lints_enabled

    db.commit()
    db.refresh(row)

    # Re-resolve so the response reflects post-commit state, including
    # fresh source labels.
    policy = get_effective_policy(tenant_id)
    return TenantPolicyOut(
        tenant_id=tenant_id,
        values={
            "execution_quota_per_hour": policy.execution_quota_per_hour,
            "max_snapshots": policy.max_snapshots,
            "mcp_pool_size": policy.mcp_pool_size,
            "rate_limit_requests_per_window": policy.rate_limit_requests_per_window,
            "rate_limit_window_seconds": policy.rate_limit_window_seconds,
        },
        flags={
            "smart_04_lints_enabled": policy.smart_04_lints_enabled,
        },
        source=dict(policy.source),
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )
