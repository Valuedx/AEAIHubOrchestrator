"""CTX-MGMT.H — instance_context_trace table + tenant policy flag.

Revision ID: 0036
Revises: 0035
Create Date: 2026-05-01

Background
----------

When a Jinja interpolation produces an empty string at runtime, the
only debugging tool today is "pull instance.context_json and eyeball
it." With multi-node graphs (V10 has 28 nodes, growing) and reducer
support (CTX-MGMT.L), the question "where did node_4r come from?"
needs a structured answer.

This migration adds:

  * ``instance_context_trace`` — append-only event log of context
    writes per instance. v1 tracks WRITES only (low volume = O(nodes)
    per run, high signal). Reads + misses are deferred to v2 — the
    volume is template-dependent and the missing-key story is better
    served by a static lint (CTX-MGMT.B).
  * ``tenant_policies.context_trace_enabled`` — opt-in flag for
    production. Ephemeral (copilot-initiated) instances always trace
    so the copilot's debug tools have the data they need.

Per-instance row cap is 500 events — handled by the helper module
(`app/engine/context_trace.py`), not the schema. The helper deletes
oldest rows on insert when the cap is hit.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instance_context_trace",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column(
            "instance_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Which node performed the operation (the writer for "write"
        # events; the reader for future "read"/"miss" events).
        sa.Column("node_id", sa.String(128), nullable=False),
        # "write" today; "read" / "miss" reserved for v2.
        sa.Column("op", sa.String(16), nullable=False),
        # The context key being written/read. For writes this is the
        # node_id; for reads it'll be the JSONPath-ish dotted path.
        sa.Column("key", sa.String(256), nullable=False),
        # Output size in bytes (for write events; null for reads).
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        # The reducer that was applied (write events only). Helps the
        # copilot answer "did this slot get appended to or overwritten?"
        sa.Column("reducer", sa.String(32), nullable=True),
        # Whether the write produced an overflow stub (CTX-MGMT.A).
        sa.Column("overflowed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # Point lookup for the inspect_context_flow tool.
    op.create_index(
        "ix_ctxtrace_instance_key",
        "instance_context_trace",
        ["instance_id", "key", "ts"],
    )
    # Per-tenant audit / retention sweep.
    op.create_index(
        "ix_ctxtrace_tenant_ts",
        "instance_context_trace",
        ["tenant_id", "ts"],
    )

    op.execute("ALTER TABLE instance_context_trace ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE instance_context_trace FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_instance_context_trace "
        "ON instance_context_trace "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )

    # Tenant policy flag — opt-in for production. Ephemeral instances
    # always trace (regardless of this flag) so copilot debug tools
    # have the data without needing tenant-policy intervention.
    op.add_column(
        "tenant_policies",
        sa.Column(
            "context_trace_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tenant_policies", "context_trace_enabled")
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_instance_context_trace "
        "ON instance_context_trace"
    )
    op.drop_index("ix_ctxtrace_tenant_ts", table_name="instance_context_trace")
    op.drop_index("ix_ctxtrace_instance_key", table_name="instance_context_trace")
    op.drop_table("instance_context_trace")
