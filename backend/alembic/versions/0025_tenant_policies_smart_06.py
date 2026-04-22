"""SMART-06 — MCP tool discovery flag on tenant_policies.

Revision ID: 0025
Revises: 0024
Create Date: 2026-04-22

Second flag in the SMART-XX series (after 0024's SMART-04 lints).
Same template: one named boolean column, non-null with a design-
default matching the feature's expected cost. Default TRUE because
MCP tool discovery is a single cached `list_tools` call per
session — zero LLM cost, negligible latency.
"""

import sqlalchemy as sa
from alembic import op


revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_policies",
        sa.Column(
            "smart_06_mcp_discovery_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_policies", "smart_06_mcp_discovery_enabled")
