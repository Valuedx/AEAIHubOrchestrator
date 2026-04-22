"""Tenant Secrets API — CRUD for encrypted credential vault."""

from __future__ import annotations

import logging
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_tenant_db
from app.security.tenant import get_tenant_id
from app.security.vault import TenantSecret, encrypt_secret

logger = logging.getLogger(__name__)

router = APIRouter()

_KEY_NAME_RE = re.compile(r"^\w+$")


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class SecretCreate(BaseModel):
    key_name: str = Field(..., min_length=1, max_length=256)
    value: str = Field(..., min_length=1)


class SecretUpdate(BaseModel):
    value: str = Field(..., min_length=1)


class SecretOut(BaseModel):
    id: str
    key_name: str
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", response_model=SecretOut, status_code=201)
def create_secret(
    body: SecretCreate,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    if not _KEY_NAME_RE.match(body.key_name):
        raise HTTPException(
            status_code=400,
            detail="key_name must contain only letters, digits, and underscores",
        )

    existing = (
        db.query(TenantSecret)
        .filter_by(tenant_id=tenant_id, key_name=body.key_name)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"Secret '{body.key_name}' already exists")

    row = TenantSecret(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        key_name=body.key_name,
        encrypted_value=encrypt_secret(body.value),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.get("", response_model=list[SecretOut])
def list_secrets(
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    rows = (
        db.query(TenantSecret)
        .filter_by(tenant_id=tenant_id)
        .order_by(TenantSecret.key_name)
        .all()
    )
    return [_to_out(r) for r in rows]


@router.get("/{secret_id}", response_model=SecretOut)
def get_secret(
    secret_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    row = _get_or_404(db, tenant_id, secret_id)
    return _to_out(row)


@router.put("/{secret_id}", response_model=SecretOut)
def update_secret(
    secret_id: str,
    body: SecretUpdate,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    row = _get_or_404(db, tenant_id, secret_id)
    row.encrypted_value = encrypt_secret(body.value)
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.delete("/{secret_id}", status_code=204)
def delete_secret(
    secret_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    row = _get_or_404(db, tenant_id, secret_id)
    db.delete(row)
    db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_or_404(db: Session, tenant_id: str, secret_id: str) -> TenantSecret:
    try:
        uid = uuid.UUID(secret_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid secret_id")

    row = (
        db.query(TenantSecret)
        .filter_by(id=uid, tenant_id=tenant_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Secret not found")
    return row


def _to_out(row: TenantSecret) -> SecretOut:
    return SecretOut(
        id=str(row.id),
        key_name=row.key_name,
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )
