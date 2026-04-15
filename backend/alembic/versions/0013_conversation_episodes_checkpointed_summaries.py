"""Add conversation episodes and checkpointed summary profile settings.

Revision ID: 0013
Revises: 0012
Create Date: 2026-04-15
"""

from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memory_profiles",
        sa.Column("summary_provider", sa.String(length=32), nullable=False, server_default="google"),
    )
    op.add_column(
        "memory_profiles",
        sa.Column("summary_model", sa.String(length=128), nullable=False, server_default="gemini-2.5-flash"),
    )
    op.add_column(
        "memory_profiles",
        sa.Column("episode_archive_provider", sa.String(length=32), nullable=False, server_default="google"),
    )
    op.add_column(
        "memory_profiles",
        sa.Column("episode_archive_model", sa.String(length=128), nullable=False, server_default="gemini-2.5-flash"),
    )
    op.add_column(
        "memory_profiles",
        sa.Column("episode_inactivity_minutes", sa.Integer(), nullable=False, server_default="10080"),
    )
    op.add_column(
        "memory_profiles",
        sa.Column("episode_min_turns", sa.Integer(), nullable=False, server_default="2"),
    )
    op.add_column(
        "memory_profiles",
        sa.Column("auto_archive_on_resolved", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "memory_profiles",
        sa.Column("promote_interactions", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )

    op.create_table(
        "conversation_episodes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column(
            "session_ref_id",
            UUID(as_uuid=True),
            sa.ForeignKey("conversation_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workflow_def_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "memory_profile_id",
            UUID(as_uuid=True),
            sa.ForeignKey("memory_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("start_turn", sa.Integer(), nullable=False),
        sa.Column("end_turn", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=256), nullable=True),
        sa.Column("checkpoint_summary_text", sa.Text(), nullable=True),
        sa.Column("summary_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary_through_turn", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("archive_reason", sa.String(length=32), nullable=True),
        sa.Column("archive_metadata_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "archived_memory_record_id",
            UUID(as_uuid=True),
            sa.ForeignKey("memory_records.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
    )
    op.create_index("ix_conversation_episodes_tenant_id", "conversation_episodes", ["tenant_id"])
    op.create_index("ix_conversation_episodes_session_ref_id", "conversation_episodes", ["session_ref_id"])
    op.create_index("ix_conversation_episodes_workflow_def_id", "conversation_episodes", ["workflow_def_id"])
    op.create_index("ix_conversation_episodes_memory_profile_id", "conversation_episodes", ["memory_profile_id"])
    op.create_index("ix_conversation_episodes_status", "conversation_episodes", ["status"])
    op.create_index("ix_conversation_episodes_archived_memory_record_id", "conversation_episodes", ["archived_memory_record_id"])
    op.create_index(
        "ix_conv_episode_session_status",
        "conversation_episodes",
        ["session_ref_id", "status"],
    )
    op.create_index(
        "ux_conv_episode_active_session",
        "conversation_episodes",
        ["session_ref_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.add_column(
        "conversation_sessions",
        sa.Column(
            "active_episode_id",
            UUID(as_uuid=True),
            sa.ForeignKey("conversation_episodes.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_conversation_sessions_active_episode_id",
        "conversation_sessions",
        ["active_episode_id"],
    )

    bind = op.get_bind()

    bind.execute(
        sa.text(
            "UPDATE memory_records SET kind = 'interaction' WHERE kind = 'episode'"
        )
    )

    sessions = bind.execute(
        sa.text(
            "SELECT id, tenant_id, session_id, message_count, last_message_at, "
            "summary_text, summary_updated_at, summary_through_turn, created_at, updated_at "
            "FROM conversation_sessions"
        )
    ).mappings().all()

    episode_table = sa.table(
        "conversation_episodes",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("tenant_id", sa.String()),
        sa.column("session_ref_id", UUID(as_uuid=True)),
        sa.column("status", sa.String()),
        sa.column("start_turn", sa.Integer()),
        sa.column("end_turn", sa.Integer()),
        sa.column("checkpoint_summary_text", sa.Text()),
        sa.column("summary_updated_at", sa.DateTime(timezone=True)),
        sa.column("summary_through_turn", sa.Integer()),
        sa.column("last_activity_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    for session in sessions:
        message_count = int(session.get("message_count") or 0)
        if message_count <= 0 and not (session.get("summary_text") or "").strip():
            continue

        episode_id = uuid.uuid4()
        last_activity_at = session.get("last_message_at") or session.get("updated_at") or session.get("created_at")
        bind.execute(
            sa.insert(episode_table).values(
                id=episode_id,
                tenant_id=session["tenant_id"],
                session_ref_id=session["id"],
                status="active",
                start_turn=1,
                end_turn=None,
                checkpoint_summary_text=session.get("summary_text"),
                summary_updated_at=session.get("summary_updated_at"),
                summary_through_turn=int(session.get("summary_through_turn") or 0),
                last_activity_at=last_activity_at,
                created_at=session.get("created_at"),
                updated_at=session.get("updated_at"),
            )
        )
        bind.execute(
            sa.text(
                "UPDATE conversation_sessions SET active_episode_id = :episode_id WHERE id = :session_id"
            ),
            {"episode_id": episode_id, "session_id": session["id"]},
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE memory_records SET kind = 'episode' WHERE kind = 'interaction'"
        )
    )

    op.drop_index("ix_conversation_sessions_active_episode_id", table_name="conversation_sessions")
    op.drop_column("conversation_sessions", "active_episode_id")

    op.drop_index("ux_conv_episode_active_session", table_name="conversation_episodes")
    op.drop_index("ix_conv_episode_session_status", table_name="conversation_episodes")
    op.drop_index("ix_conversation_episodes_archived_memory_record_id", table_name="conversation_episodes")
    op.drop_index("ix_conversation_episodes_status", table_name="conversation_episodes")
    op.drop_index("ix_conversation_episodes_memory_profile_id", table_name="conversation_episodes")
    op.drop_index("ix_conversation_episodes_workflow_def_id", table_name="conversation_episodes")
    op.drop_index("ix_conversation_episodes_session_ref_id", table_name="conversation_episodes")
    op.drop_index("ix_conversation_episodes_tenant_id", table_name="conversation_episodes")
    op.drop_table("conversation_episodes")

    op.drop_column("memory_profiles", "promote_interactions")
    op.drop_column("memory_profiles", "auto_archive_on_resolved")
    op.drop_column("memory_profiles", "episode_min_turns")
    op.drop_column("memory_profiles", "episode_inactivity_minutes")
    op.drop_column("memory_profiles", "episode_archive_model")
    op.drop_column("memory_profiles", "episode_archive_provider")
    op.drop_column("memory_profiles", "summary_model")
    op.drop_column("memory_profiles", "summary_provider")
