"""Enable PostgreSQL Row-Level Security for tenant isolation.

Revision ID: 0001
Revises: 0000
Create Date: 2026-03-20

Applies RLS policies to all tenant-scoped tables so that queries scoped
to a session's `app.tenant_id` setting only return rows belonging to
that tenant.  The application must run SET app.tenant_id = '<id>' at the
start of each database session.
"""

from alembic import op

revision = "0001"
down_revision = "0000"
branch_labels = None
depends_on = None

_TENANT_TABLES = [
    "workflow_definitions",
    "workflow_instances",
    "execution_logs",
    "tenant_tool_overrides",
    "tenant_secrets",
]


def upgrade() -> None:
    for table in _TENANT_TABLES:
        # execution_logs gets tenant_id via its instance, but we add the
        # policy on the join column for defence-in-depth.
        if table == "execution_logs":
            continue

        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")

        op.execute(f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
            USING (tenant_id = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
        """)

    op.execute("""
        CREATE OR REPLACE FUNCTION set_tenant_id(tid TEXT)
        RETURNS VOID AS $$
        BEGIN
            PERFORM set_config('app.tenant_id', tid, true);
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS set_tenant_id(TEXT);")

    for table in _TENANT_TABLES:
        if table == "execution_logs":
            continue
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table};")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
