"""ADMIN-02 — extend tenant_policies with rate-limit overrides.

Revision ID: 0021
Revises: 0020
Create Date: 2026-04-21

Adds two nullable columns mirroring ADMIN-01's precedence pattern:

  * ``rate_limit_requests_per_window`` — max API requests per window
  * ``rate_limit_window_seconds``      — window duration in seconds

Null = fall through to ``ORCHESTRATOR_RATE_LIMIT_REQUESTS`` and the
new ``ORCHESTRATOR_RATE_LIMIT_WINDOW_SECONDS`` env defaults.

Note: the old ``ORCHESTRATOR_RATE_LIMIT_WINDOW`` string setting ("1
minute") is deprecated — it was only consumed by slowapi's Limiter
which was never actually wired into a middleware, so it had no
runtime effect. The new int-seconds setting supersedes it cleanly.
"""

import sqlalchemy as sa
from alembic import op


revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_policies",
        sa.Column("rate_limit_requests_per_window", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tenant_policies",
        sa.Column("rate_limit_window_seconds", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tenant_policies", "rate_limit_window_seconds")
    op.drop_column("tenant_policies", "rate_limit_requests_per_window")
