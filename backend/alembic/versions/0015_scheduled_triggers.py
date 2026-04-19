"""Add scheduled_triggers table for atomic Beat schedule-fire dedupe.

Revision ID: 0015
Revises: 0014
Create Date: 2026-04-19

Replaces the fragile 55-second wall-clock guard in
check_scheduled_workflows with a DB-enforced dedupe: Beat INSERTs a
(workflow_def_id, scheduled_for) row at minute precision; the UNIQUE
constraint makes the insert either win or raise IntegrityError so at
most one fire per workflow per minute can ever commit.

Rows are append-only from Beat's perspective. A short retention
window (one day) is enough to serve the dedupe check; an index on
created_at supports an operator-run prune.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduled_triggers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workflow_def_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "instance_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_instances.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workflow_def_id",
            "scheduled_for",
            name="uq_scheduled_trigger_wf_minute",
        ),
    )
    op.create_index(
        "ix_scheduled_trigger_created_at",
        "scheduled_triggers",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_scheduled_trigger_created_at", table_name="scheduled_triggers")
    op.drop_table("scheduled_triggers")
