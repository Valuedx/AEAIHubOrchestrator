"""SMART-05 — vector-backed docs search flag.

Revision ID: 0029
Revises: 0028
Create Date: 2026-04-23

Single boolean column on ``tenant_policies``:
``smart_05_vector_docs_enabled``. Default **FALSE** (opt-in) because
the upgrade spends real embedding tokens — one batch at first
``search_docs`` call per process restart to build the corpus index,
plus one embedding per copilot query. The file-backed word-overlap
path shipped in 01b.iii remains the fallback for every tenant that
leaves this flag off, and the vector path automatically degrades to
word-overlap if the embedding provider is unreachable (no
credentials, network error), so turning the flag on is strictly
additive — it never returns *fewer* results.
"""

import sqlalchemy as sa
from alembic import op


revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_policies",
        sa.Column(
            "smart_05_vector_docs_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_policies", "smart_05_vector_docs_enabled")
