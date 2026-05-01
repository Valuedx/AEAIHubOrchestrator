"""CTX-MGMT.F — tenant policy flag for write-time secret scrubbing.

Revision ID: 0038
Revises: 0037
Create Date: 2026-05-01

Adds ``tenant_policies.context_secret_scrub_enabled``. **Default
TRUE** — close-leak-at-write-time is the safe default. Tenants who
need un-scrubbed in-memory context (e.g. for debugging a workflow
where a token value is part of legitimate flow) can opt out.

The scrubber itself is the existing key-based redactor in
``app/engine/scrubber.py`` — already used on log writes since 0001.
This flag controls whether it ALSO runs at context-write time
inside ``_execute_single_node`` (and the parallel-branch path).
"""

import sqlalchemy as sa
from alembic import op


revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tenant_policies",
        sa.Column(
            "context_secret_scrub_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_policies", "context_secret_scrub_enabled")
