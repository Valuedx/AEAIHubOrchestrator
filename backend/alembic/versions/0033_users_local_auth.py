"""LOCAL-AUTH-01 — users table for local password authentication.

Revision ID: 0033
Revises: 0032
Create Date: 2026-04-25

Adds the ``users`` table used by the ``local`` auth mode. Each row is a
tenant-scoped username + argon2 password hash. RLS is enabled so a
compromised tenant session cannot read another tenant's credentials.

Active Directory / LDAP binding is out of scope for this revision — a
future migration will add optional ``external_auth_provider`` columns
to this same table when local AD is wired up.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID


revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("username", sa.String(128), nullable=False),
        sa.Column("email", sa.String(256), nullable=True),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("is_admin", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("disabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Case-insensitive unique username within a tenant. Two tenants can
    # share a username without colliding.
    op.execute(
        "CREATE UNIQUE INDEX ix_users_tenant_username ON users "
        "(tenant_id, lower(username));"
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])

    # RLS — same template as migration 0014.
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE users FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY tenant_isolation_users ON users
        USING (tenant_id = current_setting('app.tenant_id', true))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_users ON users;")
    op.execute("ALTER TABLE users DISABLE ROW LEVEL SECURITY;")
    op.drop_index("ix_users_tenant_id", table_name="users")
    op.execute("DROP INDEX IF EXISTS ix_users_tenant_username;")
    op.drop_table("users")
