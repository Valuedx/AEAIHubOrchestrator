"""Extend PostgreSQL Row-Level Security to the memory, conversation, A2A,
and workflow-snapshot tables.

Revision ID: 0014
Revises: 0013
Create Date: 2026-04-19

Migration 0001 enabled RLS on workflow_definitions / workflow_instances /
tenant_tool_overrides / tenant_secrets. Migrations 0009 and 0010 covered
the knowledge-base and embedding-cache tables inline. The memory and
conversation tables (added in 0003 and 0012 / 0013) and the A2A key table
(added in 0007) were never wired up, which left a cross-tenant read path
open if the application ever forgot to apply a WHERE tenant_id=... filter.

This migration closes that gap by enabling and forcing RLS on every
remaining tenant-scoped table, using the same policy template as 0001:

    USING      (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true))

The application already calls ``set_tenant_id(<tenant>)`` at the start of
each request-scoped session via the JWT/tenant dependency; this migration
is a defence-in-depth layer beneath that filter.

Note: ``instance_checkpoints`` and ``execution_logs`` are intentionally
excluded — they have no ``tenant_id`` column and rely on cascade-delete
from ``workflow_instances``. Giving them their own column (and backfilling
via JOIN) is tracked separately.
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


# Every table listed here must already have a ``tenant_id`` column.
_TENANT_TABLES = [
    "conversation_sessions",
    "conversation_messages",
    "conversation_episodes",
    "memory_records",
    "memory_profiles",
    "entity_facts",
    "a2a_api_keys",
    "workflow_snapshots",
]


def upgrade() -> None:
    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_{table} ON {table}
            USING (tenant_id = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
            """
        )


def downgrade() -> None:
    for table in _TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON {table};")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
