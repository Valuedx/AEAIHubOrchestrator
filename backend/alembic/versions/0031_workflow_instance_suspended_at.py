"""HITL-01.b — track when an instance suspended, for age display + timeouts.

Revision ID: 0031
Revises: 0030
Create Date: 2026-04-23

Workflow instances already track ``started_at`` and ``completed_at``
but nothing explicitly recorded the timestamp of the most-recent
suspension. The pending-approvals dashboard needs this to show
"waiting 4h" next to each row, and HITL-01.c will use the same
column to compute timeout elapsed.

Adding the column now, in 01.b, costs nothing extra and lets 01.c
focus on the scheduler work without a split migration. v0 rows
(suspended before this migration ran) keep NULL — the dashboard
falls back to ``started_at`` for those; the timeout sweep will
skip NULL rows entirely so nothing gets auto-rejected based on a
phantom timestamp.
"""

import sqlalchemy as sa
from alembic import op


revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_instances",
        sa.Column(
            "suspended_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("workflow_instances", "suspended_at")
