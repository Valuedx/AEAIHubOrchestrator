"""CTX-MGMT.K — tenant policy flag for within-run context compaction.

Revision ID: 0037
Revises: 0036
Create Date: 2026-05-01

Single boolean column on ``tenant_policies``:
``context_compaction_enabled``. **Default TRUE** — compaction is a
cost saver (smaller context_json + smaller InstanceCheckpoint rows
+ smaller LLM-prompt-rendered structured-context blocks), so the
common case wants it on.

Tenants with strict audit-trail requirements (regulated industries
where a runtime replay must reproduce the exact in-memory state)
can opt out by setting this to FALSE. The full output remains
available via the existing ``node_output_artifacts`` table — only
the in-context stub-vs-full-payload distinction changes.

The default-TRUE shape is intentionally opposite to
``context_trace_enabled`` (added by 0036, default FALSE — opt-in
production observability). Trace adds a per-write DB write;
compaction reduces total storage. They optimize different cost axes.
"""

import sqlalchemy as sa
from alembic import op


revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_policies",
        sa.Column(
            "context_compaction_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_policies", "context_compaction_enabled")
