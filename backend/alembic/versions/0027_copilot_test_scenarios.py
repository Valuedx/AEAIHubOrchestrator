"""COPILOT-03.a — persisted test scenarios for the copilot debug loop.

Revision ID: 0027
Revises: 0026
Create Date: 2026-04-23

Adds ``copilot_test_scenarios`` — reusable "given this trigger
payload, the draft should produce output containing X" regression
cases the copilot can save during a chat and re-run before promote
(03.e). One row per saved scenario, tenant-scoped RLS.

Design notes
------------

* The scenario is bound to a draft via ``draft_id`` for v1. When a
  draft promotes (see 03.e), scenarios migrate onto the resulting
  ``workflow_id`` instead; that migration is a DML step inside the
  promote transaction, not a schema change, so it doesn't need a
  separate table today.
* ``pins_json`` is reserved for a future follow-up — scenarios with
  pinned upstream outputs require the execute path to support
  pinned-context injection. 03.a keeps scenarios to trigger-only
  (``payload_json``) runs; ``pins_json`` is declared here so
  adding pin support later is purely backend-side.
* ``expected_output_contains_json`` is the partial-match assertion:
  actual output must contain every key/value in the expected dict
  (recursive). Absent = scenario records but makes no assertion;
  run_scenario returns the raw output instead of pass/fail.
* ``name`` is unique per draft — the copilot won't silently
  overwrite a scenario the user already named.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "copilot_test_scenarios",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column(
            "draft_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_drafts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        # Promote moves scenarios from draft_id to workflow_id.
        sa.Column(
            "workflow_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_definitions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column(
            "payload_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "pins_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "expected_output_contains_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # One of draft_id / workflow_id must be set — never both, never
    # neither. Enforced at the DB so a bad DML can't leak through the
    # API layer.
    op.create_check_constraint(
        "ck_scenario_draft_xor_workflow",
        "copilot_test_scenarios",
        "(draft_id IS NOT NULL)::int + (workflow_id IS NOT NULL)::int = 1",
    )

    # Name uniqueness is per-owner: a draft can't have two scenarios
    # named "empty payload"; neither can a workflow.
    op.create_index(
        "ix_scenario_draft_name",
        "copilot_test_scenarios",
        ["draft_id", "name"],
        unique=True,
        postgresql_where=sa.text("draft_id IS NOT NULL"),
    )
    op.create_index(
        "ix_scenario_workflow_name",
        "copilot_test_scenarios",
        ["workflow_id", "name"],
        unique=True,
        postgresql_where=sa.text("workflow_id IS NOT NULL"),
    )
    op.create_index(
        "ix_scenario_tenant_created",
        "copilot_test_scenarios",
        ["tenant_id", "created_at"],
    )

    op.execute("ALTER TABLE copilot_test_scenarios ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE copilot_test_scenarios FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_copilot_test_scenarios "
        "ON copilot_test_scenarios "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_copilot_test_scenarios "
        "ON copilot_test_scenarios"
    )
    op.drop_index("ix_scenario_tenant_created", table_name="copilot_test_scenarios")
    op.drop_index("ix_scenario_workflow_name", table_name="copilot_test_scenarios")
    op.drop_index("ix_scenario_draft_name", table_name="copilot_test_scenarios")
    op.drop_constraint(
        "ck_scenario_draft_xor_workflow",
        "copilot_test_scenarios",
        type_="check",
    )
    op.drop_table("copilot_test_scenarios")
