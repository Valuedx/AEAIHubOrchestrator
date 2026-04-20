"""MCP-02 — Tests for ``mcp_server_resolver.resolve_mcp_server``.

Covers the precedence chain (explicit label → is_default → env-var
fallback), auth-mode dispatch (none / static_headers / oauth_2_1), and
the ``{{ env.KEY }}`` secret resolution. The vault + SessionLocal are
patched so tests don't touch a real DB.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


TENANT = "tenant-a"


def _mock_row(
    *,
    label: str = "default-mcp",
    url: str = "https://mcp.example.com/mcp",
    auth_mode: str = "none",
    config: dict | None = None,
    is_default: bool = False,
):
    row = MagicMock()
    row.id = "row-id-123"
    row.tenant_id = TENANT
    row.label = label
    row.url = url
    row.auth_mode = auth_mode
    row.config_json = config or {}
    row.is_default = is_default
    return row


def _patch_session_returning(row):
    """Context-managed patches so resolver's SessionLocal returns a
    MagicMock session whose query chain yields ``row`` (or None)."""
    session = MagicMock()
    query = session.query.return_value
    filter_by = query.filter_by.return_value

    def _filter_by_chain(**kwargs):
        # Two call shapes:
        #   .filter_by(tenant_id=…)              → returns the sub-query
        #   .filter_by(label=…)  / is_default=…  → terminal .first() lookup
        inner = MagicMock()
        inner.first.return_value = row
        inner.filter_by.return_value.first.return_value = row
        return inner

    filter_by.filter_by.side_effect = lambda **kw: _filter_by_chain(**kw).filter_by.return_value
    filter_by.first.return_value = row
    filter_by.filter_by.return_value.first.return_value = row

    return session


class TestEnvFallback:
    def test_no_tenant_returns_env_url(self):
        from app.engine.mcp_server_resolver import resolve_mcp_server
        from app.config import settings

        target = resolve_mcp_server(tenant_id=None)
        assert target.url == settings.mcp_server_url
        assert target.headers == {}
        assert target.pool_key == "__env_fallback__"
        assert target.label is None


class TestRegistryHit:
    def test_explicit_label_returns_matching_row(self):
        from app.engine.mcp_server_resolver import resolve_mcp_server
        row = _mock_row(label="github", url="https://gh/mcp")
        session = _patch_session_returning(row)

        with patch("app.database.SessionLocal", return_value=session), \
             patch("app.database.set_tenant_context"):
            target = resolve_mcp_server(TENANT, "github")

        assert target.url == "https://gh/mcp"
        assert target.pool_key == "row-id-123"
        assert target.label == "github"

    def test_missing_label_raises(self):
        from app.engine.mcp_server_resolver import (
            McpServerResolutionError,
            resolve_mcp_server,
        )
        session = _patch_session_returning(None)

        with patch("app.database.SessionLocal", return_value=session), \
             patch("app.database.set_tenant_context"), \
             pytest.raises(McpServerResolutionError, match="No MCP server named 'ghost'"):
            resolve_mcp_server(TENANT, "ghost")

    def test_blank_label_falls_back_to_default(self):
        from app.engine.mcp_server_resolver import resolve_mcp_server
        row = _mock_row(label="the-default", is_default=True)
        session = _patch_session_returning(row)

        with patch("app.database.SessionLocal", return_value=session), \
             patch("app.database.set_tenant_context"):
            target = resolve_mcp_server(TENANT, None)

        assert target.label == "the-default"

    def test_blank_label_no_default_falls_back_to_env(self):
        from app.engine.mcp_server_resolver import resolve_mcp_server
        from app.config import settings
        session = _patch_session_returning(None)

        with patch("app.database.SessionLocal", return_value=session), \
             patch("app.database.set_tenant_context"):
            target = resolve_mcp_server(TENANT, None)

        assert target.url == settings.mcp_server_url
        assert target.pool_key == "__env_fallback__"


class TestAuthModeStaticHeaders:
    def test_resolves_env_placeholders_from_vault(self):
        from app.engine.mcp_server_resolver import resolve_mcp_server
        row = _mock_row(
            auth_mode="static_headers",
            config={"headers": {"Authorization": "Bearer {{ env.GH_TOKEN }}",
                                "X-Plain": "static-value"}},
        )
        session = _patch_session_returning(row)

        def _vault(tenant_id, key):
            assert tenant_id == TENANT
            return {"GH_TOKEN": "super-secret"}[key]

        with patch("app.database.SessionLocal", return_value=session), \
             patch("app.database.set_tenant_context"), \
             patch("app.security.vault.get_tenant_secret", side_effect=_vault):
            target = resolve_mcp_server(TENANT, "default-mcp")

        assert target.headers["Authorization"] == "Bearer super-secret"
        # Non-placeholder values pass through verbatim.
        assert target.headers["X-Plain"] == "static-value"

    def test_missing_secret_raises(self):
        from app.engine.mcp_server_resolver import (
            McpServerResolutionError,
            resolve_mcp_server,
        )
        row = _mock_row(
            auth_mode="static_headers",
            config={"headers": {"Authorization": "Bearer {{ env.MISSING }}"}},
        )
        session = _patch_session_returning(row)

        with patch("app.database.SessionLocal", return_value=session), \
             patch("app.database.set_tenant_context"), \
             patch("app.security.vault.get_tenant_secret", return_value=None), \
             pytest.raises(McpServerResolutionError, match="references secret 'MISSING'"):
            resolve_mcp_server(TENANT, "default-mcp")


class TestAuthModeOauth:
    def test_oauth_2_1_is_not_yet_implemented(self):
        from app.engine.mcp_server_resolver import (
            McpServerResolutionError,
            resolve_mcp_server,
        )
        row = _mock_row(auth_mode="oauth_2_1")
        session = _patch_session_returning(row)

        with patch("app.database.SessionLocal", return_value=session), \
             patch("app.database.set_tenant_context"), \
             pytest.raises(McpServerResolutionError, match="not yet implemented"):
            resolve_mcp_server(TENANT, "default-mcp")
