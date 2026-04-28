"""SMART-04 — add copilot-intelligence feature flags to tenant_policies.

Revision ID: 0024
Revises: 0023
Create Date: 2026-04-22

Each flag in the SMART-01..06 roadmap series lands as one named
boolean on ``tenant_policies`` so a tenant can opt out of any
subset. Same shape as ADMIN-01/02 knobs: the column has a
non-null default (matching the feature's design-default) and is
read through ``engine/tenant_policy_resolver.get_effective_policy``
rather than the raw model so null values (e.g. on legacy rows that
predate the column) fall through to the matching
``settings.smart_XX_*`` env default.

This migration adds SMART-04 only:

  * ``smart_04_lints_enabled`` BOOLEAN NOT NULL DEFAULT TRUE
    — proactive authoring lints (graph-structure checks after
    every mutation). Zero LLM cost; default on.

Follow-up migrations add SMART-01/02/03/05/06 flags as those
tickets ship.
"""

import sqlalchemy as sa
from alembic import op


revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_policies",
        sa.Column(
            "smart_04_lints_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_policies", "smart_04_lints_enabled")
