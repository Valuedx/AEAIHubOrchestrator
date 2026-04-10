"""MCP Tool bridge endpoint.

Lists available tools from the MCP server via Streamable HTTP transport
and exposes them as a JSON API for the frontend node palette.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.security.tenant import get_tenant_id
from app.models.tenant import TenantToolOverride
from app.api.schemas import ToolOut

router = APIRouter(prefix="/api/v1/tools", tags=["tools"])
logger = logging.getLogger(__name__)


def _load_tool_specs() -> list[dict[str, Any]]:
    """Load tool specs from MCP server via Streamable HTTP transport."""
    from app.engine.mcp_client import list_tools

    raw = list_tools()
    tools = []
    for t in raw:
        tools.append({
            "name": t["name"],
            "title": t.get("title", t["name"]),
            "description": t.get("description", ""),
            "category": t.get("category", "misc"),
            "safety_tier": t.get("safety_tier", "safe_read"),
            "tags": t.get("tags", []),
        })
    return tools


@router.post("/invalidate-cache", status_code=204)
def invalidate_cache(tenant_id: str = Depends(get_tenant_id)):
    """Invalidate the MCP tool list cache, forcing a re-fetch on next request."""
    from app.engine.mcp_client import invalidate_tool_cache
    invalidate_tool_cache()


@router.get("", response_model=list[ToolOut])
def list_tools(
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_db),
):
    """Return MCP tools filtered by tenant overrides.

    If a TenantToolOverride exists for a tool with enabled=False, that
    tool is excluded from the response for this tenant.
    """
    all_tools = _load_tool_specs()

    overrides = (
        db.query(TenantToolOverride)
        .filter_by(tenant_id=tenant_id)
        .all()
    )

    if not overrides:
        return all_tools

    override_map = {o.tool_name: o for o in overrides}
    filtered = []
    for tool in all_tools:
        override = override_map.get(tool["name"])
        if override and not override.enabled:
            continue
        filtered.append(tool)
    return filtered
