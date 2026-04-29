"""ae_ops_support_cases

AE Ops Support — mock-ticketing case table for the dual-role support workflow.

Stores a single support "case" per (tenant, session). The workflow uses this
as its durable state machine for the Planner → Approval → Executor flow:
both BUSINESS and TECH webhook paths converge on the same row, the planner
reads/writes ``state``, the approval node writes the ``plan_json`` the tech
user sees, and ``/handoff`` flips ``state = 'HANDED_OFF'`` to lock the bot
out of further mutations.

No external ticketing — this is intentionally local to the orchestrator for
demo simplicity. ``case_id`` doubles as the user-facing ticket id.

Revision ID: 48f869152a93
Revises: 384daed57459
Create Date: 2026-04-29 02:11:59.318858
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = '48f869152a93'
down_revision: Union[str, None] = '384daed57459'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "support_cases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(128), nullable=False),
        sa.Column("requester_id", sa.String(128), nullable=True),
        # State machine values map to the prompt's case lifecycle:
        # NEW | PLANNING | NEED_INFO | WAITING_APPROVAL | READY_TO_EXECUTE
        # | EXECUTING | WAITING_ON_TEAM | RESOLVED_PENDING_CONFIRMATION
        # | HANDED_OFF | CLOSED | FAILED
        sa.Column("state", sa.String(48), nullable=False, server_default=sa.text("'NEW'")),
        sa.Column("title", sa.String(256), nullable=True),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("priority", sa.String(16), nullable=True),
        sa.Column("assigned_team", sa.String(64), nullable=True),
        # Plan + evidence JSONB: produced by the planner ReAct agent, surfaced
        # to the tech approver, consumed by the executor on approve.
        sa.Column("plan_json", JSONB, nullable=True),
        sa.Column("evidence", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("worknotes", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        # Free-form snapshot of last-known AE context (workflow_id, request_id,
        # agent_id, etc.) so subsequent turns reuse it without re-asking.
        sa.Column("resolved_context", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_support_cases_tenant_session", "support_cases", ["tenant_id", "session_id"], unique=False,
    )
    op.create_index(
        "ix_support_cases_tenant_state", "support_cases", ["tenant_id", "state"], unique=False,
    )

    # RLS — same pattern as every other tenant-scoped table (migration 0001 / 0014).
    op.execute("ALTER TABLE support_cases ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE support_cases FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_support_cases ON support_cases
            USING ((tenant_id)::text = current_setting('app.tenant_id', true))
            WITH CHECK ((tenant_id)::text = current_setting('app.tenant_id', true))
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_support_cases ON support_cases")
    op.drop_index("ix_support_cases_tenant_state", table_name="support_cases")
    op.drop_index("ix_support_cases_tenant_session", table_name="support_cases")
    op.drop_table("support_cases")
