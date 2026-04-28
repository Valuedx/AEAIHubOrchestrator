"""COPILOT-01b.ii.b — add is_ephemeral flag to workflow_definitions.

Revision ID: 0023
Revises: 0022
Create Date: 2026-04-22

Introduces a boolean marker for WorkflowDefinition rows created as
transient execution containers by the copilot's ``execute_draft``
runner tool. These rows back a real ``WorkflowInstance`` (so the
engine can run against them and produce ``execution_logs`` rows) but
must not surface anywhere a human-authored workflow would:

  * excluded from ``list_workflows`` (user's "saved workflows" dialog)
  * excluded from the scheduler's active-workflow scan
  * excluded from the A2A agent card published-skills list

All filter updates live in this sprint; see ``codewiki/copilot.md``
for the full list. The engine itself (``dag_runner``, node handlers)
does NOT filter on ``is_ephemeral`` — it needs to be able to load
the ephemeral WorkflowDefinition to actually run it.

Existing rows default to ``False`` via the column's server_default,
so this is a non-breaking additive migration.
"""

import sqlalchemy as sa
from alembic import op


revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_definitions",
        sa.Column(
            "is_ephemeral",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # An index on is_ephemeral alone isn't worth it (very low cardinality:
    # ~95%+ of rows will be False). List queries already key on tenant_id
    # which is indexed; the is_ephemeral filter is cheap on top of that.


def downgrade() -> None:
    op.drop_column("workflow_definitions", "is_ephemeral")
