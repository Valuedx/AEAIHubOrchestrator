"""CTX-MGMT.M — checkpoint retention policy.

Revision ID: 0039
Revises: 0038
Create Date: 2026-05-01

Adds ``tenant_policies.checkpoint_retention_days``. Nullable —
``NULL`` means use the system default (30 days). Tenants who need
shorter retention for cost reasons (or longer for compliance) can
override.

Pairs with the new ``prune_aged_checkpoints`` operator utility in
``app/engine/forgetting.py`` — same shape as
``cleanup_ephemeral_workflows`` but for `InstanceCheckpoint` rows.
"""

import sqlalchemy as sa
from alembic import op


revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_policies",
        sa.Column(
            "checkpoint_retention_days",
            sa.Integer(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_policies", "checkpoint_retention_days")
