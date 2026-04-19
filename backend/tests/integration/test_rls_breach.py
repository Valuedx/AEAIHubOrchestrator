"""End-to-end RLS enforcement test.

Proves that a query made under tenant A's context cannot see tenant B's
rows — the core promise of migration 0001 + 0014. Runs under the
non-superuser role so the policies are actually in force (a superuser
would silently bypass them, which is the exact failure mode SETUP_GUIDE
§5.2a warns about).
"""

from __future__ import annotations

import uuid

from sqlalchemy import text


def _insert_memory_record(session, tenant_id: str) -> uuid.UUID:
    record_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO memory_records
                (id, tenant_id, scope, scope_key, kind, content,
                 metadata_json, embedding_provider, embedding_model, vector_store)
            VALUES
                (:id, :tenant_id, 'session', 'sess-1', 'fact', 'private to tenant',
                 '{}', 'openai', 'text-embedding-3-small', 'pgvector')
            """
        ),
        {"id": record_id, "tenant_id": tenant_id},
    )
    return record_id


def test_tenant_cannot_read_another_tenants_memory_records(app_session):
    # Insert two rows under different tenants — each under its own
    # app.tenant_id GUC so the INSERT WITH CHECK clause passes.
    app_session.execute(text("SELECT set_tenant_id('tenant-a')"))
    rec_a = _insert_memory_record(app_session, "tenant-a")
    app_session.commit()

    app_session.execute(text("SELECT set_tenant_id('tenant-b')"))
    rec_b = _insert_memory_record(app_session, "tenant-b")
    app_session.commit()

    # As tenant A, SELECTing the row we know exists under tenant B must
    # return zero rows — RLS hides it completely.
    app_session.execute(text("SELECT set_tenant_id('tenant-a')"))
    rows = app_session.execute(
        text("SELECT id, tenant_id FROM memory_records WHERE id = :id"),
        {"id": rec_b},
    ).fetchall()
    assert rows == [], f"tenant-a should not see tenant-b row {rec_b}, got {rows}"

    # Sanity: tenant A can still see its own row.
    rows = app_session.execute(
        text("SELECT id FROM memory_records WHERE id = :id"),
        {"id": rec_a},
    ).fetchall()
    assert len(rows) == 1


def test_insert_under_wrong_tenant_is_rejected_by_with_check(app_session):
    app_session.execute(text("SELECT set_tenant_id('tenant-a')"))
    # Attempt to insert a row tagged as tenant-b while GUC says tenant-a —
    # the WITH CHECK clause must reject it.
    from sqlalchemy.exc import DBAPIError

    import pytest

    with pytest.raises(DBAPIError):
        _insert_memory_record(app_session, "tenant-b")
        app_session.commit()
    app_session.rollback()


def test_conversation_messages_are_rls_protected(app_session):
    """Smoke check that migration 0014's policy covers conversation_messages
    too — not just memory_records."""
    app_session.execute(text("SELECT set_tenant_id('tenant-a')"))
    session_ref_id = uuid.uuid4()
    # conversation_sessions is the parent — insert it first.
    app_session.execute(
        text(
            """
            INSERT INTO conversation_sessions
                (id, tenant_id, session_id, message_count, summary_through_turn)
            VALUES (:id, 'tenant-a', 'sess-1', 0, 0)
            """
        ),
        {"id": session_ref_id},
    )
    msg_id = uuid.uuid4()
    app_session.execute(
        text(
            """
            INSERT INTO conversation_messages
                (id, session_ref_id, tenant_id, session_id, turn_index, role,
                 content, message_at)
            VALUES
                (:id, :sess, 'tenant-a', 'sess-1', 0, 'user', 'hi', now())
            """
        ),
        {"id": msg_id, "sess": session_ref_id},
    )
    app_session.commit()

    # Switch tenant — message must be invisible.
    app_session.execute(text("SELECT set_tenant_id('tenant-b')"))
    rows = app_session.execute(
        text("SELECT id FROM conversation_messages WHERE id = :id"),
        {"id": msg_id},
    ).fetchall()
    assert rows == []
