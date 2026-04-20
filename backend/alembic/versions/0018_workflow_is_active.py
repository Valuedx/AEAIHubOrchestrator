"""DV-07 — add is_active flag to workflow_definitions.

Revision ID: 0018
Revises: 0017
Create Date: 2026-04-21

Workflows now carry an ``is_active`` boolean. Only active workflows
fire on Schedule Triggers; every other code path (manual Run, PATCH
updates, duplicate) stays unaffected. Default ``TRUE`` preserves the
pre-DV-07 behaviour for existing rows.
"""

import sqlalchemy as sa
from alembic import op


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_definitions",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
    )


def downgrade() -> None:
    op.drop_column("workflow_definitions", "is_active")
