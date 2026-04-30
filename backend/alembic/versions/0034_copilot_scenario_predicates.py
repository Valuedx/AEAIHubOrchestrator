"""COPILOT-V2 — predicate-based assertions for test scenarios.

Revision ID: 0034
Revises: 48f869152a93
Create Date: 2026-05-01

Adds ``expected_predicates_json`` to ``copilot_test_scenarios``.

Why
---

The original ``expected_output_contains_json`` is a partial-match dict
assertion: every key/value in the expected dict must appear in the
actual output. Useful for shape assertions, useless for behavior.

Real-world rubrics from V8/V9/V10 evals look like:

  - "reply ends with a question"
  - "reply contains one of [recon, report]"
  - "reply does NOT contain 'system prompt'"
  - "intent in [ops, output_missing]"
  - "no tool from [ae.workflow, ae.request] called"
  - "reply does not say 'Maximum iterations reached'"

These are predicates over the run output, not partial-match assertions.
This column stores a list of ``{type, args}`` predicates evaluated by
``app.copilot.predicates.evaluate_predicates`` after run_scenario.

Backwards compatible: nullable, defaults to NULL. Existing scenarios
keep working unchanged.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0034"
down_revision = "48f869152a93"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "copilot_test_scenarios",
        sa.Column(
            "expected_predicates_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("copilot_test_scenarios", "expected_predicates_json")
