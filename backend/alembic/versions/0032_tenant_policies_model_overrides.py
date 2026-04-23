"""MODEL-01.e — per-tenant model defaults + family allowlist.

Revision ID: 0032
Revises: 0031
Create Date: 2026-04-23

Adds five nullable columns to ``tenant_policies``:

* ``default_llm_provider`` / ``default_llm_model`` — optional pin. Null
  = tier-based resolution through :func:`app.engine.model_registry.default_llm_for`.
* ``default_embedding_provider`` / ``default_embedding_model`` — same
  idea for embeddings.
* ``allowed_model_families`` — JSONB array of generation strings (e.g.
  ``["2.5", "3.x"]``). Null/empty = no family restriction. Enforced by
  ``is_allowed_llm(...)`` at copilot session-create and node-config
  validation time.

All columns nullable so a tenant that never opens the Tenant Policy
dialog's model row keeps the registry's defaults — zero behavioural
change on upgrade.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_policies",
        sa.Column("default_llm_provider", sa.String(32), nullable=True),
    )
    op.add_column(
        "tenant_policies",
        sa.Column("default_llm_model", sa.String(128), nullable=True),
    )
    op.add_column(
        "tenant_policies",
        sa.Column("default_embedding_provider", sa.String(32), nullable=True),
    )
    op.add_column(
        "tenant_policies",
        sa.Column("default_embedding_model", sa.String(128), nullable=True),
    )
    op.add_column(
        "tenant_policies",
        sa.Column("allowed_model_families", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_policies", "allowed_model_families")
    op.drop_column("tenant_policies", "default_embedding_model")
    op.drop_column("tenant_policies", "default_embedding_provider")
    op.drop_column("tenant_policies", "default_llm_model")
    op.drop_column("tenant_policies", "default_llm_provider")
