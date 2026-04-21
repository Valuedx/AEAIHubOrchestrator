"""ADMIN-01 — per-tenant policy overrides for operational knobs.

Revision ID: 0020
Revises: 0019
Create Date: 2026-04-21

One row per tenant. Every override column is nullable — a null means
"use the env-default value" via ``tenant_policy_resolver``. Keeping it
keyed on ``tenant_id`` (not a UUID) mirrors the "exactly one row per
tenant" rule; the DB is the sole source of truth.

RLS policy follows the standard ``app.tenant_id`` GUC pattern so each
tenant can read / write only their own row. A platform-admin cross-
tenant view would need a separate BYPASSRLS role — deliberately out
of scope here.

Scope covered: ``execution_quota_per_hour``, ``max_snapshots``,
``mcp_pool_size``. Rate-limit / rate-window are carved out as
**ADMIN-02** because slowapi reads its limit string at module import.
Per-tenant LLM provider keys are **ADMIN-03** — larger touch surface.
"""

import sqlalchemy as sa
from alembic import op


revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_policies",
        sa.Column("tenant_id", sa.String(64), primary_key=True),
        sa.Column("execution_quota_per_hour", sa.Integer(), nullable=True),
        sa.Column("max_snapshots", sa.Integer(), nullable=True),
        sa.Column("mcp_pool_size", sa.Integer(), nullable=True),
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
    )

    op.execute("ALTER TABLE tenant_policies ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_policies FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_tenant_policies ON tenant_policies "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.drop_table("tenant_policies")
