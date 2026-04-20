"""MCP-02 — per-tenant MCP server registry.

Revision ID: 0019
Revises: 0018
Create Date: 2026-04-21

Two tables:

* ``tenant_mcp_servers`` — the registry itself. One row per MCP server
  a tenant wants to use. ``auth_mode`` discriminates how config_json is
  interpreted:

    * ``none`` — bare URL, no auth headers
    * ``static_headers`` — config_json.headers is a map of HTTP headers
      applied on every request. Values may reference secrets via
      ``{{ env.KEY_NAME }}`` placeholders that are resolved at runtime
      through the Fernet-encrypted tenant_secrets vault — raw tokens
      never live in this table.
    * ``oauth_2_1`` — reserved for MCP-03. The column accepts this value
      so the registry doesn't require another migration later, but the
      runtime currently raises NotImplementedError if it resolves to an
      oauth_2_1 server.

  A partial unique index enforces at most one ``is_default=true`` row
  per tenant, mirroring the pattern used by tenant_integrations.

* ``tenant_mcp_server_tool_fingerprints`` — forward-declared empty
  side table for MCP-06 (tool-poisoning / rug-pull detection). Stores a
  SHA-256 of (name, description, inputSchema) per tool per server so
  drift between fetches can be detected. No code writes to it today.

Both tables enable RLS with the standard
``tenant_id = current_setting('app.tenant_id')`` policy.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. tenant_mcp_servers --------------------------------------------
    op.create_table(
        "tenant_mcp_servers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("url", sa.String(1024), nullable=False),
        sa.Column(
            "auth_mode",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'none'"),
        ),
        sa.Column(
            "config_json",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "is_default",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id", "label",
            name="uq_tenant_mcp_server_label",
        ),
        sa.CheckConstraint(
            "auth_mode IN ('none', 'static_headers', 'oauth_2_1')",
            name="ck_tenant_mcp_server_auth_mode",
        ),
    )
    # Only one default per tenant (mirrors tenant_integrations pattern).
    op.execute(
        "CREATE UNIQUE INDEX ux_tenant_mcp_server_default "
        "ON tenant_mcp_servers (tenant_id) "
        "WHERE is_default = true"
    )
    op.execute("ALTER TABLE tenant_mcp_servers ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_mcp_servers FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_tenant_mcp_servers ON tenant_mcp_servers "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )

    # 2. tenant_mcp_server_tool_fingerprints (forward for MCP-06) -------
    op.create_table(
        "tenant_mcp_server_tool_fingerprints",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "server_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenant_mcp_servers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_name", sa.String(256), nullable=False),
        sa.Column("fingerprint_sha256", sa.String(64), nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "server_id", "tool_name",
            name="uq_mcp_tool_fingerprint",
        ),
    )


def downgrade() -> None:
    op.drop_table("tenant_mcp_server_tool_fingerprints")
    op.execute("DROP INDEX IF EXISTS ux_tenant_mcp_server_default")
    op.drop_table("tenant_mcp_servers")
