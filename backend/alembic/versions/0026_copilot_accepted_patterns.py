"""SMART-02 — per-tenant accepted-patterns library + flag on tenant_policies.

Revision ID: 0026
Revises: 0025
Create Date: 2026-04-22

Two additive changes:

  1. New ``copilot_accepted_patterns`` table. Every successful
     ``/promote`` saves one row: the accepted ``graph_json``, the
     originating NL intent (first user turn of the draft's most
     recent session), the node types + tag set used, and a small
     amount of denormalised metadata so retrieval doesn't have to
     re-walk the graph.

  2. Third SMART-XX flag on ``tenant_policies``:
     ``smart_02_pattern_library_enabled``. Default TRUE because
     save + retrieve are pure DB I/O (no LLM, no embeddings) —
     cheap enough that opt-out is for operators who genuinely
     don't want the learning loop, not for cost control.

Table keeps its own RLS policy, mirroring every other tenant-
scoped table in the repo. Indexes key on ``(tenant_id,
created_at desc)`` so the retrieval path (top-N most-recent
candidates per tenant) is O(log n) + in-memory overlap scoring.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # copilot_accepted_patterns
    # ------------------------------------------------------------------
    op.create_table(
        "copilot_accepted_patterns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        # Source draft is informational only — the draft is deleted as
        # part of promote, so this FK is nullable and ON DELETE
        # SET NULL. What we actually preserve is the promoted graph.
        sa.Column(
            "source_draft_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "source_workflow_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_definitions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("nl_intent", sa.Text(), nullable=True),
        sa.Column(
            "graph_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "node_types",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("node_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("edge_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_by",
            sa.String(128),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_accepted_pattern_tenant_created",
        "copilot_accepted_patterns",
        ["tenant_id", "created_at"],
    )

    op.execute(
        "ALTER TABLE copilot_accepted_patterns ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE copilot_accepted_patterns FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_copilot_accepted_patterns "
        "ON copilot_accepted_patterns "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )

    # ------------------------------------------------------------------
    # SMART-02 opt-out flag
    # ------------------------------------------------------------------
    op.add_column(
        "tenant_policies",
        sa.Column(
            "smart_02_pattern_library_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column(
        "tenant_policies", "smart_02_pattern_library_enabled",
    )
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_copilot_accepted_patterns "
        "ON copilot_accepted_patterns"
    )
    op.drop_table("copilot_accepted_patterns")
