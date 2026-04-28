"""COPILOT-01 — draft workspace + copilot chat-history tables.

Revision ID: 0022
Revises: 0021
Create Date: 2026-04-22

Three new tenant-scoped tables:

  * ``workflow_drafts``
      Ephemeral graph being edited by a copilot session (or a human
      editor). Promoted into ``workflow_definitions`` when the user
      accepts. Every mutation bumps ``version`` so a race between two
      concurrent tool calls surfaces as a 409 instead of silently
      last-writer-wins. ``base_version_at_fork`` is captured at draft
      creation so the promote step can refuse to clobber a base that
      diverged while the draft was open.

  * ``copilot_sessions``
      One chat session per draft (optionally many sequential sessions
      on the same draft). Holds the provider/model the agent runner
      should use (COPILOT-01b) so the frontend doesn't have to remember
      which model drafted each part of the graph.

  * ``copilot_turns``
      Ordered conversation history. ``role`` is one of ``user``,
      ``assistant``, or ``tool``. ``content_json`` is role-specific:
      text for user/assistant, ``{name, args, result}`` for tool turns.
      ``tenant_id`` is denormalised (not derived via join) so the RLS
      policy is a simple equality check that RLS-01's guarantees cover
      without any session pre-setup inside policy code.

RLS: each table gets ENABLE + FORCE + a ``tenant_id = current_setting
('app.tenant_id', true)`` policy mirroring every other tenant table
(see migration 0001 and the RLS-01 sweep).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # workflow_drafts
    # ------------------------------------------------------------------
    op.create_table(
        "workflow_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column(
            "base_workflow_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_definitions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # WorkflowDefinition.version at draft-creation time. The promote
        # step refuses to land if the base has moved on since.
        sa.Column("base_version_at_fork", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column(
            "graph_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{\"nodes\": [], \"edges\": []}'::jsonb"),
        ),
        # Optimistic-concurrency token. Every successful tool dispatch
        # increments this; a conflicting write with a stale version
        # returns 409 so the caller refetches before retrying.
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("created_by", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_draft_tenant_updated",
        "workflow_drafts",
        ["tenant_id", "updated_at"],
    )

    op.execute("ALTER TABLE workflow_drafts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workflow_drafts FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_workflow_drafts ON workflow_drafts "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )

    # ------------------------------------------------------------------
    # copilot_sessions
    # ------------------------------------------------------------------
    op.create_table(
        "copilot_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column(
            "draft_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_drafts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        # 'active' | 'completed' | 'abandoned'. An abandoned session
        # keeps its turns around so the user can see the history if they
        # later reopen the draft.
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_session_tenant_draft",
        "copilot_sessions",
        ["tenant_id", "draft_id"],
    )

    op.execute("ALTER TABLE copilot_sessions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE copilot_sessions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_copilot_sessions ON copilot_sessions "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )

    # ------------------------------------------------------------------
    # copilot_turns
    # ------------------------------------------------------------------
    op.create_table(
        "copilot_turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False, index=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("copilot_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column(
            "content_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "tool_calls_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "token_usage_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("session_id", "turn_index", name="uq_turn_session_idx"),
    )
    op.create_index(
        "ix_turn_tenant_session",
        "copilot_turns",
        ["tenant_id", "session_id"],
    )

    op.execute("ALTER TABLE copilot_turns ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE copilot_turns FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_copilot_turns ON copilot_turns "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_copilot_turns ON copilot_turns")
    op.drop_table("copilot_turns")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_copilot_sessions ON copilot_sessions")
    op.drop_table("copilot_sessions")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_workflow_drafts ON workflow_drafts")
    op.drop_table("workflow_drafts")
