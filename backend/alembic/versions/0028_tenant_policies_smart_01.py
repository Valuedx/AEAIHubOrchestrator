"""SMART-01 — scenario memory + strict promote-gate flags.

Revision ID: 0028
Revises: 0027
Create Date: 2026-04-23

Two boolean columns on ``tenant_policies``. Both default **FALSE** —
SMART-01 is opt-in per tenant (unlike SMART-02/04/06 which default
true) because both behaviours spend real engine tokens:

* ``smart_01_scenario_memory_enabled`` — when true, every successful
  ``execute_draft`` run auto-saves a scenario (deduped by a stable
  payload hash so re-running the same payload doesn't create
  duplicate rows). Cost: one extra ``copilot_test_scenarios`` INSERT
  per run.

* ``smart_01_strict_promote_gate_enabled`` — when true, the promote
  endpoint runs every saved scenario BEFORE landing the draft and
  refuses with HTTP 400 on any non-``pass`` result (no "promote
  anyway" override). Cost: one full draft execution per saved
  scenario at every promote. Off = the promote flow stays soft (the
  PromoteDialog's existing "promote anyway" checkbox applies).
"""

import sqlalchemy as sa
from alembic import op


revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_policies",
        sa.Column(
            "smart_01_scenario_memory_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "tenant_policies",
        sa.Column(
            "smart_01_strict_promote_gate_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_policies", "smart_01_strict_promote_gate_enabled")
    op.drop_column("tenant_policies", "smart_01_scenario_memory_enabled")
