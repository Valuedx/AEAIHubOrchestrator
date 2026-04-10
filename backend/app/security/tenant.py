"""Tenant authentication — delegates to jwt_auth.get_tenant_id.

This module exists for backward compatibility; all endpoints import
`get_tenant_id` from here.
"""

from app.security.jwt_auth import get_tenant_id  # noqa: F401
