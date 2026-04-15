"""Advanced memory hard cutover: normalized conversation rows and memory tables.

Revision ID: 0012
Revises: 0011
Create Date: 2026-04-15
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def _parse_ts(raw: object) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.add_column(
        "conversation_sessions",
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "conversation_sessions",
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversation_sessions",
        sa.Column("summary_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "conversation_sessions",
        sa.Column("summary_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversation_sessions",
        sa.Column("summary_through_turn", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "conversation_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_ref_id",
            UUID(as_uuid=True),
            sa.ForeignKey("conversation_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(256), nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workflow_def_id", UUID(as_uuid=True), nullable=True),
        sa.Column("instance_id", UUID(as_uuid=True), nullable=True),
        sa.Column("node_id", sa.String(128), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
    )
    op.create_index("ix_conv_msg_session_ref_id", "conversation_messages", ["session_ref_id"])
    op.create_index("ix_conv_msg_tenant_id", "conversation_messages", ["tenant_id"])
    op.create_index("ix_conv_msg_session_id", "conversation_messages", ["session_id"])
    op.create_index(
        "ix_conv_msg_session_turn",
        "conversation_messages",
        ["session_ref_id", "turn_index"],
        unique=True,
    )
    op.create_index(
        "ix_conv_msg_session_idem_role",
        "conversation_messages",
        ["session_ref_id", "idempotency_key", "role"],
        unique=True,
    )

    op.create_table(
        "memory_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column(
            "workflow_def_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workflow_definitions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("instructions_text", sa.Text(), nullable=True),
        sa.Column("enabled_scopes", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("max_recent_tokens", sa.Integer(), nullable=False, server_default="1200"),
        sa.Column("max_semantic_hits", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("include_entity_memory", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("summary_trigger_messages", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("summary_recent_turns", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("summary_max_tokens", sa.Integer(), nullable=False, server_default="400"),
        sa.Column("history_order", sa.String(32), nullable=False, server_default="summary_first"),
        sa.Column("semantic_score_threshold", sa.Float(), nullable=False, server_default="0"),
        sa.Column("embedding_provider", sa.String(32), nullable=False, server_default="openai"),
        sa.Column("embedding_model", sa.String(128), nullable=False, server_default="text-embedding-3-small"),
        sa.Column("vector_store", sa.String(32), nullable=False, server_default="pgvector"),
        sa.Column("entity_mappings_json", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
    )
    op.create_index("ix_mem_profile_tenant_name", "memory_profiles", ["tenant_id", "name"])
    op.create_index(
        "ix_mem_profile_tenant_wf_default",
        "memory_profiles",
        ["tenant_id", "workflow_def_id", "is_default"],
    )
    op.create_index(
        "ux_mem_profile_tenant_default",
        "memory_profiles",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("workflow_def_id IS NULL AND is_default = true"),
    )
    op.create_index(
        "ux_mem_profile_workflow_default",
        "memory_profiles",
        ["tenant_id", "workflow_def_id"],
        unique=True,
        postgresql_where=sa.text("workflow_def_id IS NOT NULL AND is_default = true"),
    )

    op.create_table(
        "memory_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("scope_key", sa.String(256), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "session_ref_id",
            UUID(as_uuid=True),
            sa.ForeignKey("conversation_sessions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("workflow_def_id", UUID(as_uuid=True), nullable=True),
        sa.Column("entity_type", sa.String(128), nullable=True),
        sa.Column("entity_key", sa.String(256), nullable=True),
        sa.Column("source_instance_id", UUID(as_uuid=True), nullable=True),
        sa.Column("source_node_id", sa.String(128), nullable=True),
        sa.Column("embedding_provider", sa.String(32), nullable=False, server_default="openai"),
        sa.Column("embedding_model", sa.String(128), nullable=False, server_default="text-embedding-3-small"),
        sa.Column("vector_store", sa.String(32), nullable=False, server_default="pgvector"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
    )
    op.create_index("ix_mem_record_scope_lookup", "memory_records", ["tenant_id", "scope", "scope_key"])
    op.create_index(
        "ix_mem_record_entity_lookup",
        "memory_records",
        ["tenant_id", "entity_type", "entity_key"],
    )
    op.execute("ALTER TABLE memory_records ADD COLUMN embedding vector")
    op.execute(
        "CREATE INDEX ix_memory_records_embedding ON memory_records "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "entity_facts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(128), nullable=False),
        sa.Column("entity_key", sa.String(256), nullable=False),
        sa.Column("fact_name", sa.String(128), nullable=False),
        sa.Column("fact_value", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "session_ref_id",
            UUID(as_uuid=True),
            sa.ForeignKey("conversation_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("workflow_def_id", UUID(as_uuid=True), nullable=True),
        sa.Column("source_instance_id", UUID(as_uuid=True), nullable=True),
        sa.Column("source_node_id", sa.String(128), nullable=True),
        sa.Column("metadata_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_entity_fact_active_lookup",
        "entity_facts",
        ["tenant_id", "entity_type", "entity_key", "fact_name", "valid_to"],
    )
    op.create_index(
        "ux_entity_fact_active_unique",
        "entity_facts",
        ["tenant_id", "entity_type", "entity_key", "fact_name"],
        unique=True,
        postgresql_where=sa.text("valid_to IS NULL"),
    )

    bind = op.get_bind()
    sessions = bind.execute(
        sa.text(
            "SELECT id, session_id, tenant_id, messages, created_at, updated_at "
            "FROM conversation_sessions"
        )
    ).mappings().all()

    conv_msg = sa.table(
        "conversation_messages",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("session_ref_id", UUID(as_uuid=True)),
        sa.column("tenant_id", sa.String()),
        sa.column("session_id", sa.String()),
        sa.column("turn_index", sa.Integer()),
        sa.column("role", sa.String()),
        sa.column("content", sa.Text()),
        sa.column("message_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    for session in sessions:
        raw_messages = session.get("messages") or []
        message_rows = []
        last_message_at = None
        for idx, msg in enumerate(raw_messages, start=1):
            if not isinstance(msg, dict):
                continue
            message_at = _parse_ts(msg.get("timestamp")) or session.get("updated_at") or session.get("created_at")
            last_message_at = message_at or last_message_at
            message_rows.append(
                {
                    "id": uuid.uuid4(),
                    "session_ref_id": session["id"],
                    "tenant_id": session["tenant_id"],
                    "session_id": session["session_id"],
                    "turn_index": idx,
                    "role": str(msg.get("role", "user") or "user"),
                    "content": str(msg.get("content", "") or ""),
                    "message_at": message_at,
                    "created_at": message_at,
                }
            )
        if message_rows:
            bind.execute(sa.insert(conv_msg), message_rows)
        bind.execute(
            sa.text(
                "UPDATE conversation_sessions "
                "SET message_count = :count, "
                "    last_message_at = :last_message_at, "
                "    summary_text = NULL, "
                "    summary_updated_at = NULL, "
                "    summary_through_turn = 0 "
                "WHERE id = :id"
            ),
            {
                "count": len(message_rows),
                "last_message_at": last_message_at,
                "id": session["id"],
            },
        )

    op.drop_column("conversation_sessions", "messages")


def downgrade() -> None:
    op.add_column(
        "conversation_sessions",
        sa.Column(
            "messages",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    bind = op.get_bind()
    sessions = bind.execute(
        sa.text("SELECT id FROM conversation_sessions")
    ).mappings().all()
    for session in sessions:
        rows = bind.execute(
            sa.text(
                "SELECT role, content, message_at "
                "FROM conversation_messages "
                "WHERE session_ref_id = :session_ref_id "
                "ORDER BY turn_index"
            ),
            {"session_ref_id": session["id"]},
        ).mappings().all()
        payload = [
            {
                "role": row["role"],
                "content": row["content"],
                "timestamp": row["message_at"].isoformat() if row["message_at"] else None,
            }
            for row in rows
        ]
        bind.execute(
            sa.text(
                "UPDATE conversation_sessions "
                "SET messages = CAST(:messages AS jsonb) "
                "WHERE id = :id"
            ),
            {"messages": json.dumps(payload), "id": session["id"]},
        )

    op.drop_table("entity_facts")
    op.execute("DROP INDEX IF EXISTS ix_memory_records_embedding")
    op.drop_table("memory_records")
    op.drop_index("ux_mem_profile_workflow_default", table_name="memory_profiles")
    op.drop_index("ux_mem_profile_tenant_default", table_name="memory_profiles")
    op.drop_table("memory_profiles")
    op.drop_table("conversation_messages")
    op.drop_column("conversation_sessions", "summary_through_turn")
    op.drop_column("conversation_sessions", "summary_updated_at")
    op.drop_column("conversation_sessions", "summary_text")
    op.drop_column("conversation_sessions", "last_message_at")
    op.drop_column("conversation_sessions", "message_count")
