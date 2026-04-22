"""Tenant Integrations API — CRUD for external-system connection defaults.

Lets an operator configure a tenant-wide default AutomationEdge (or
future Jenkins/Temporal/...) connection once, and then reference it
from any ``AutomationEdge`` node by label — or leave the node blank to
use the default.

The ``config_json`` payload is system-specific and intentionally
untyped here; the system-specific handler (e.g. ``_handle_automation_
edge``) validates required fields at run time via
``resolve_integration_config(required_fields=...)``. That keeps the
CRUD flexible for future systems without schema churn.

Secrets are never stored in ``config_json`` — only the
``credentialsSecretPrefix`` string that points at a vault entry. The
vault itself is still the sole keeper of usernames/passwords/tokens.

The partial unique index on ``(tenant_id, system) WHERE is_default``
enforces at most one default per system at the DB level. The create /
update endpoints clear any prior default in the same transaction
before flipping a new row, so operators can swap the default without
hitting IntegrityError.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_tenant_db
from app.security.tenant import get_tenant_id
from app.models.workflow import TenantIntegration

logger = logging.getLogger(__name__)

router = APIRouter()


# Systems we currently support. The API accepts new entries without code
# changes, but keep this list for validation + the UI dropdown.
#
# ``automationedge`` — RPA job submission + async resume (Pattern A/C).
# ``vertex`` — per-tenant Google Cloud Vertex AI project routing (VERTEX-02).
#   config_json shape: ``{"project": "<gcp-project-id>", "location": "<region>"}``.
#   Raw credentials are NOT stored — ADC (GOOGLE_APPLICATION_CREDENTIALS or
#   workload identity) is still process-global, so a single service-account
#   identity needs aiplatform.user across every target project.
_SUPPORTED_SYSTEMS = {"automationedge", "vertex"}


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class TenantIntegrationBase(BaseModel):
    label: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Human-readable name, e.g. 'prod-ae' or 'dev-ae'.",
    )
    config_json: dict[str, Any] = Field(
        ...,
        description=(
            "System-specific connection config. For AutomationEdge: "
            "{baseUrl, orgCode, credentialsSecretPrefix, authMode, "
            "source, userId}."
        ),
    )
    is_default: bool = Field(
        default=False,
        description=(
            "Exactly one integration per (tenant, system) may be the "
            "default. Nodes with a blank integrationLabel pick this one."
        ),
    )


class TenantIntegrationCreate(TenantIntegrationBase):
    system: str = Field(
        ...,
        min_length=1,
        max_length=32,
        description="e.g. 'automationedge'.",
    )


class TenantIntegrationUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=128)
    config_json: dict[str, Any] | None = None
    is_default: bool | None = None


class TenantIntegrationOut(BaseModel):
    id: str
    tenant_id: str
    system: str
    label: str
    config_json: dict[str, Any]
    is_default: bool
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", response_model=TenantIntegrationOut, status_code=201)
def create_integration(
    body: TenantIntegrationCreate,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    if body.system not in _SUPPORTED_SYSTEMS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown integration system '{body.system}'. "
                   f"Supported: {sorted(_SUPPORTED_SYSTEMS)}",
        )

    existing = (
        db.query(TenantIntegration)
        .filter_by(tenant_id=tenant_id, system=body.system, label=body.label)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Integration '{body.label}' already exists for {body.system}",
        )

    if body.is_default:
        _clear_default(db, tenant_id=tenant_id, system=body.system)

    row = TenantIntegration(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        system=body.system,
        label=body.label,
        config_json=body.config_json,
        is_default=body.is_default,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.get("", response_model=list[TenantIntegrationOut])
def list_integrations(
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
    system: str | None = Query(
        default=None,
        description="Filter by integration system (e.g. 'automationedge').",
    ),
):
    query = db.query(TenantIntegration).filter_by(tenant_id=tenant_id)
    if system:
        query = query.filter_by(system=system)
    rows = query.order_by(TenantIntegration.system, TenantIntegration.label).all()
    return [_to_out(r) for r in rows]


@router.get("/{integration_id}", response_model=TenantIntegrationOut)
def get_integration(
    integration_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    return _to_out(_get_or_404(db, tenant_id, integration_id))


@router.patch("/{integration_id}", response_model=TenantIntegrationOut)
def update_integration(
    integration_id: str,
    body: TenantIntegrationUpdate,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    row = _get_or_404(db, tenant_id, integration_id)

    # Label rename is allowed but must not collide with an existing label
    # for the same (tenant, system).
    if body.label is not None and body.label != row.label:
        clash = (
            db.query(TenantIntegration)
            .filter_by(tenant_id=tenant_id, system=row.system, label=body.label)
            .first()
        )
        if clash is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Integration '{body.label}' already exists for {row.system}",
            )
        row.label = body.label

    if body.config_json is not None:
        row.config_json = body.config_json

    if body.is_default is not None and body.is_default != row.is_default:
        if body.is_default:
            _clear_default(db, tenant_id=tenant_id, system=row.system, exclude_id=row.id)
        row.is_default = body.is_default

    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.delete("/{integration_id}", status_code=204)
def delete_integration(
    integration_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    row = _get_or_404(db, tenant_id, integration_id)
    db.delete(row)
    db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_or_404(db: Session, tenant_id: str, integration_id: str) -> TenantIntegration:
    try:
        uid = uuid.UUID(integration_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid integration_id")

    row = (
        db.query(TenantIntegration)
        .filter_by(id=uid, tenant_id=tenant_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Integration not found")
    return row


def _clear_default(
    db: Session,
    *,
    tenant_id: str,
    system: str,
    exclude_id: uuid.UUID | None = None,
) -> None:
    """Flip the current default for (tenant, system) to is_default=false.

    Called before flipping a different row to default so the partial
    unique index ``ux_tenant_integration_default`` doesn't fire. Runs
    inside the caller's transaction — commit is the caller's job.
    """
    query = db.query(TenantIntegration).filter_by(
        tenant_id=tenant_id, system=system, is_default=True,
    )
    if exclude_id is not None:
        query = query.filter(TenantIntegration.id != exclude_id)
    for row in query.all():
        row.is_default = False


def _to_out(row: TenantIntegration) -> TenantIntegrationOut:
    return TenantIntegrationOut(
        id=str(row.id),
        tenant_id=row.tenant_id,
        system=row.system,
        label=row.label,
        config_json=row.config_json or {},
        is_default=row.is_default,
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )
