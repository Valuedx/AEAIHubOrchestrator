"""MCP-02 — resolve a tenant + label combo to a concrete MCP server target.

Precedence (highest first):
  1. ``server_label`` argument matches a row in tenant_mcp_servers
  2. The tenant's ``is_default=True`` row
  3. ``settings.mcp_server_url`` — the pre-MCP-02 env-var fallback so
     existing tenants keep working without a registry row

The returned ``ResolvedMcpServer`` is opaque to callers aside from the
two fields they need: ``url`` and ``headers``. Auth-mode dispatch (and
the future OAuth resource-server flow from MCP-03) lives here so the
client doesn't need to know what mode a row is in.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


# ``{{ env.FOO }}`` with optional inner whitespace. Matches the
# templating convention the rest of the codebase uses for secrets.
_ENV_PLACEHOLDER = re.compile(r"\{\{\s*env\.([A-Za-z0-9_]+)\s*\}\}")


class McpServerResolutionError(RuntimeError):
    """Raised when a named label is not found for the tenant, or when
    a resolved row's auth mode is not yet implemented."""


@dataclass(frozen=True)
class ResolvedMcpServer:
    """Concrete target for a single MCP call."""

    url: str
    headers: dict[str, str] = field(default_factory=dict)
    # Opaque identifier used to key the session pool. For a registry
    # hit this is the row id; for the env-var fallback it's a sentinel
    # string so all fallback calls share one pool.
    pool_key: str = "__env_fallback__"
    label: str | None = None


def resolve_mcp_server(
    tenant_id: str | None,
    label: str | None = None,
) -> ResolvedMcpServer:
    """Return the MCP server target for this tenant + optional label.

    ``tenant_id=None`` always returns the env-var fallback — used by
    internal paths (e.g. schema priming) that don't have tenant context.
    """
    if tenant_id is None:
        return _env_fallback()

    # Registry lookup needs a short-lived session; keep it scoped here so
    # callers don't juggle DB handles.
    from app.database import SessionLocal, set_tenant_context
    from app.models.workflow import TenantMcpServer

    db = SessionLocal()
    try:
        set_tenant_context(db, tenant_id)
        query = db.query(TenantMcpServer).filter_by(tenant_id=tenant_id)
        if label:
            row = query.filter_by(label=label).first()
            if row is None:
                raise McpServerResolutionError(
                    f"No MCP server named '{label}' for tenant {tenant_id}"
                )
        else:
            row = query.filter_by(is_default=True).first()

        if row is None:
            # No registry row and no label requested — fall back to env.
            return _env_fallback()

        return _row_to_target(tenant_id, row)
    finally:
        db.close()


def _env_fallback() -> ResolvedMcpServer:
    return ResolvedMcpServer(
        url=settings.mcp_server_url,
        headers={},
        pool_key="__env_fallback__",
        label=None,
    )


def _row_to_target(tenant_id: str, row: Any) -> ResolvedMcpServer:
    """Translate a ``TenantMcpServer`` row into a target.

    Secrets are resolved here so the rest of the client doesn't touch
    the vault. A missing referenced secret raises — we would rather
    fail loudly at the call site than silently send an unauth'd request
    that a spec-compliant server would 401 anyway.
    """
    auth_mode = (row.auth_mode or "none").lower()
    if auth_mode == "none":
        return ResolvedMcpServer(
            url=row.url,
            headers={},
            pool_key=str(row.id),
            label=row.label,
        )
    if auth_mode == "static_headers":
        headers = _resolve_headers(
            tenant_id,
            (row.config_json or {}).get("headers") or {},
        )
        return ResolvedMcpServer(
            url=row.url,
            headers=headers,
            pool_key=str(row.id),
            label=row.label,
        )
    if auth_mode == "oauth_2_1":
        raise McpServerResolutionError(
            f"MCP server '{row.label}' uses auth_mode=oauth_2_1 which is "
            "not yet implemented. Tracked as MCP-03."
        )
    raise McpServerResolutionError(
        f"MCP server '{row.label}' has unknown auth_mode={auth_mode!r}"
    )


def _resolve_headers(
    tenant_id: str,
    raw_headers: dict[str, Any],
) -> dict[str, str]:
    """Substitute ``{{ env.KEY }}`` placeholders against the Fernet vault."""
    from app.security.vault import get_tenant_secret

    out: dict[str, str] = {}
    for name, template in raw_headers.items():
        if not isinstance(template, str):
            # Ignore non-string header values — the API layer should
            # have caught this, but defensive on the off-chance config
            # was written directly.
            continue

        def _sub(match: re.Match[str]) -> str:
            key = match.group(1)
            value = get_tenant_secret(tenant_id, key)
            if value is None:
                raise McpServerResolutionError(
                    f"Header {name!r} references secret {key!r} which is "
                    "not in the tenant vault. Add it via the Secrets dialog."
                )
            return value

        out[str(name)] = _ENV_PLACEHOLDER.sub(_sub, template)
    return out
