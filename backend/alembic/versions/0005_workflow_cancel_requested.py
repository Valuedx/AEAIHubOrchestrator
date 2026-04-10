"""Add cancel_requested flag for cooperative workflow cancellation.

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-22
"""

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_instances",
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("workflow_instances", "cancel_requested")
