"""Resolve an AE (or other external-system) connection config by
overlaying per-node settings onto tenant_integrations defaults.

Precedence (highest first):
  1. Explicit per-node config fields (node_config["baseUrl"], etc.)
  2. The tenant_integrations row named by ``integrationLabel`` if set
  3. The ``is_default=true`` tenant_integration row for the system
  4. Raise if nothing resolves the required fields

This keeps one-AE-per-tenant and multi-AE-per-tenant setups both
simple — the operator can either configure a default integration and
leave the node config blank, or override any field on the node when a
specific workflow needs a different target.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.workflow import TenantIntegration


class IntegrationConfigError(ValueError):
    """Required integration field missing from both the node and the
    tenant_integrations default."""


def resolve_integration_config(
    db: Session,
    *,
    tenant_id: str,
    system: str,
    node_config: dict[str, Any],
    required_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Merge node config over the matching tenant_integration row.

    Returns a dict with the resolved fields. Missing required fields
    raise ``IntegrationConfigError`` with a message that names the
    field and where (node / default) was checked.

    The caller is responsible for selecting the right
    ``required_fields`` tuple for its system (e.g. for
    AutomationEdge: ``("baseUrl", "orgCode")``).
    """
    integration: TenantIntegration | None = None
    label = (node_config or {}).get("integrationLabel")
    query = db.query(TenantIntegration).filter_by(
        tenant_id=tenant_id, system=system,
    )
    if label:
        integration = query.filter_by(label=label).first()
        if integration is None:
            raise IntegrationConfigError(
                f"No {system} integration named '{label}' for tenant {tenant_id}"
            )
    else:
        integration = query.filter_by(is_default=True).first()

    base: dict[str, Any] = dict(integration.config_json or {}) if integration else {}

    # Overlay non-empty per-node values on top of the integration defaults.
    for key, value in (node_config or {}).items():
        if key == "integrationLabel":
            continue
        if value in (None, "", [], {}):
            continue
        base[key] = value

    missing = [f for f in required_fields if base.get(f) in (None, "", [], {})]
    if missing:
        raise IntegrationConfigError(
            f"{system} integration is missing required field(s) {missing}. "
            f"Set them on the node config or on a tenant_integrations row."
        )
    return base
