"""MCP-02 — CRUD for the per-tenant MCP server registry.

Mirrors ``tenant_integrations`` in shape so operators have one mental
model for connection registries. Key differences:

* One row per MCP *server* (URL + auth), not per *system*. The "system"
  dimension doesn't exist here — every row is an MCP server.
* ``auth_mode`` is a discriminator on ``config_json``. Today we accept
  ``none`` and ``static_headers``. ``oauth_2_1`` is accepted by the API
  but the runtime will NotImplementedError — it's stored so MCP-03
  can land without another migration.
* Raw credentials never live in ``config_json``. Header values embed
  ``{{ env.SECRET_NAME }}`` placeholders that are resolved at call time
  through the Fernet-encrypted ``tenant_secrets`` vault.

The partial unique index ``ux_tenant_mcp_server_default`` enforces the
"one default per tenant" rule at the DB layer; the create / update
endpoints clear any prior default in the same transaction before
flipping a new row so operators can swap the default cleanly without
hitting IntegrityError.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_tenant_db
from app.security.tenant import get_tenant_id
from app.models.workflow import TenantMcpServer

logger = logging.getLogger(__name__)

router = APIRouter()


AuthMode = Literal["none", "static_headers", "oauth_2_1"]


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class TenantMcpServerBase(BaseModel):
    label: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Human-readable name, e.g. 'github-mcp' or 'prod-tools'.",
    )
    url: str = Field(
        ...,
        min_length=1,
        max_length=1024,
        description="Streamable-HTTP MCP endpoint, e.g. https://mcp.example.com/mcp",
    )
    auth_mode: AuthMode = Field(
        default="none",
        description=(
            "How the runtime attaches credentials. "
            "'none' sends no auth headers. "
            "'static_headers' reads config_json.headers and resolves "
            "{{ env.KEY }} placeholders from the tenant_secrets vault. "
            "'oauth_2_1' is reserved for MCP-03 and not yet runnable."
        ),
    )
    config_json: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Auth-mode-specific config. For 'static_headers': "
            "{'headers': {'Authorization': 'Bearer {{ env.MY_TOKEN }}', ...}}. "
            "Empty for 'none'."
        ),
    )
    is_default: bool = Field(
        default=False,
        description=(
            "Exactly one server per tenant may be the default. Nodes "
            "with a blank mcpServerLabel pick this one. If none is set, "
            "the legacy settings.mcp_server_url env var is used."
        ),
    )


class TenantMcpServerCreate(TenantMcpServerBase):
    pass


class TenantMcpServerUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=128)
    url: str | None = Field(default=None, min_length=1, max_length=1024)
    auth_mode: AuthMode | None = None
    config_json: dict[str, Any] | None = None
    is_default: bool | None = None


class TenantMcpServerOut(BaseModel):
    id: str
    tenant_id: str
    label: str
    url: str
    auth_mode: str
    config_json: dict[str, Any]
    is_default: bool
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=TenantMcpServerOut, status_code=201)
def create_server(
    body: TenantMcpServerCreate,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    existing = (
        db.query(TenantMcpServer)
        .filter_by(tenant_id=tenant_id, label=body.label)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"MCP server '{body.label}' already exists for this tenant",
        )

    if body.is_default:
        _clear_default(db, tenant_id=tenant_id)

    row = TenantMcpServer(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        label=body.label,
        url=body.url,
        auth_mode=body.auth_mode,
        config_json=body.config_json,
        is_default=body.is_default,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.get("", response_model=list[TenantMcpServerOut])
def list_servers(
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    rows = (
        db.query(TenantMcpServer)
        .filter_by(tenant_id=tenant_id)
        .order_by(TenantMcpServer.label)
        .all()
    )
    return [_to_out(r) for r in rows]


@router.get("/{server_id}", response_model=TenantMcpServerOut)
def get_server(
    server_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    return _to_out(_get_or_404(db, tenant_id, server_id))


@router.patch("/{server_id}", response_model=TenantMcpServerOut)
def update_server(
    server_id: str,
    body: TenantMcpServerUpdate,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    row = _get_or_404(db, tenant_id, server_id)

    if body.label is not None and body.label != row.label:
        clash = (
            db.query(TenantMcpServer)
            .filter_by(tenant_id=tenant_id, label=body.label)
            .first()
        )
        if clash is not None:
            raise HTTPException(
                status_code=409,
                detail=f"MCP server '{body.label}' already exists for this tenant",
            )
        row.label = body.label

    if body.url is not None:
        row.url = body.url
    if body.auth_mode is not None:
        row.auth_mode = body.auth_mode
    if body.config_json is not None:
        row.config_json = body.config_json

    if body.is_default is not None and body.is_default != row.is_default:
        if body.is_default:
            _clear_default(db, tenant_id=tenant_id, exclude_id=row.id)
        row.is_default = body.is_default

    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.delete("/{server_id}", status_code=204)
def delete_server(
    server_id: str,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    row = _get_or_404(db, tenant_id, server_id)
    db.delete(row)
    db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_or_404(db: Session, tenant_id: str, server_id: str) -> TenantMcpServer:
    try:
        uid = uuid.UUID(server_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid server_id")

    row = (
        db.query(TenantMcpServer)
        .filter_by(id=uid, tenant_id=tenant_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return row


def _clear_default(
    db: Session,
    *,
    tenant_id: str,
    exclude_id: uuid.UUID | None = None,
) -> None:
    """Flip the current default for this tenant to False so the partial
    unique index doesn't fire when another row is promoted. Runs inside
    the caller's transaction — commit is the caller's job."""
    query = db.query(TenantMcpServer).filter_by(
        tenant_id=tenant_id, is_default=True,
    )
    if exclude_id is not None:
        query = query.filter(TenantMcpServer.id != exclude_id)
    for row in query.all():
        row.is_default = False


def _to_out(row: TenantMcpServer) -> TenantMcpServerOut:
    return TenantMcpServerOut(
        id=str(row.id),
        tenant_id=row.tenant_id,
        label=row.label,
        url=row.url,
        auth_mode=row.auth_mode,
        config_json=row.config_json or {},
        is_default=row.is_default,
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )
