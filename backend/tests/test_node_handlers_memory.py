import uuid
from types import SimpleNamespace

from app.engine.memory_service import EffectiveMemoryPolicy
from app.engine.node_handlers import (
    _handle_archive_conversation_episode,
    _handle_bridge_user_reply,
    _handle_save_conversation_state,
)


class _FakeDB:
    def __init__(self):
        self.committed = False
        self.closed = False

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True

    def execute(self, *_args, **_kwargs):
        # set_tenant_context is invoked on every SessionLocal() in node
        # handlers. Tests don't need the GUC; accept and ignore.
        return None


def test_bridge_user_reply_preserves_upstream_memory_debug():
    memory_debug = {"enabled": True, "profile_id": str(uuid.uuid4())}

    out = _handle_bridge_user_reply(
        {"config": {"responseNodeId": "node_agent"}},
        {"node_agent": {"response": "Hello", "memory_debug": memory_debug}},
        "tenant-a",
    )

    assert out["text"] == "Hello"
    assert out["memory_debug"] == memory_debug


def test_save_conversation_uses_response_memory_debug_policy(monkeypatch):
    fake_db = _FakeDB()
    session = SimpleNamespace(id=uuid.uuid4(), message_count=2, active_episode_id=None)
    profile_id = str(uuid.uuid4())
    captured: dict[str, object] = {}

    monkeypatch.setattr("app.database.SessionLocal", lambda: fake_db)
    monkeypatch.setattr(
        "app.engine.memory_service.append_conversation_turns",
        lambda *args, **kwargs: (session, []),
    )
    monkeypatch.setattr(
        "app.engine.memory_service.get_active_episode",
        lambda *args, **kwargs: None,
    )

    def _fake_resolve_memory_policy(db, **kwargs):
        captured["node_config"] = kwargs["node_config"]
        return EffectiveMemoryPolicy(
            enabled=True,
            scopes=["session", "entity"],
            include_entity_memory=False,
            history_order="recent_first",
        )

    monkeypatch.setattr(
        "app.engine.memory_service.resolve_memory_policy",
        _fake_resolve_memory_policy,
    )
    monkeypatch.setattr(
        "app.engine.memory_service.refresh_rolling_summary",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        "app.engine.memory_service.promote_entity_facts",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "app.engine.memory_service.promote_memory_records",
        lambda *args, **kwargs: [],
    )

    result = _handle_save_conversation_state(
        {"config": {"responseNodeId": "node_agent"}},
        {
            "trigger": {"session_id": "sess-1", "message": "Hi"},
            "_workflow_def_id": str(uuid.uuid4()),
            "_instance_id": str(uuid.uuid4()),
            "_current_node_id": "node_save",
            "node_agent": {
                "response": "Hello",
                "memory_debug": {
                    "enabled": True,
                    "profile_id": profile_id,
                    "scopes": ["session", "entity"],
                    "include_entity_memory": False,
                    "history_order": "recent_first",
                },
            },
        },
        "tenant-a",
    )

    assert result["saved"] is True
    assert captured["node_config"] == {
        "memoryEnabled": True,
        "memoryProfileId": profile_id,
        "memoryScopes": ["session", "entity"],
        "includeEntityMemory": False,
        "historyOrder": "recent_first",
    }
    assert fake_db.committed is True
    assert fake_db.closed is True


def test_save_conversation_skips_advanced_memory_when_response_memory_disabled(monkeypatch):
    fake_db = _FakeDB()
    session = SimpleNamespace(id=uuid.uuid4(), message_count=2, active_episode_id=None)
    calls = {"summary": 0, "facts": 0, "records": 0}

    monkeypatch.setattr("app.database.SessionLocal", lambda: fake_db)
    monkeypatch.setattr(
        "app.engine.memory_service.append_conversation_turns",
        lambda *args, **kwargs: (session, []),
    )
    monkeypatch.setattr(
        "app.engine.memory_service.get_active_episode",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.engine.memory_service.resolve_memory_policy",
        lambda *args, **kwargs: EffectiveMemoryPolicy(enabled=False),
    )
    monkeypatch.setattr(
        "app.engine.memory_service.refresh_rolling_summary",
        lambda *args, **kwargs: calls.__setitem__("summary", calls["summary"] + 1),
    )
    monkeypatch.setattr(
        "app.engine.memory_service.promote_entity_facts",
        lambda *args, **kwargs: calls.__setitem__("facts", calls["facts"] + 1),
    )
    monkeypatch.setattr(
        "app.engine.memory_service.promote_memory_records",
        lambda *args, **kwargs: calls.__setitem__("records", calls["records"] + 1),
    )

    result = _handle_save_conversation_state(
        {"config": {"responseNodeId": "node_agent"}},
        {
            "trigger": {"session_id": "sess-1", "message": "Hi"},
            "_workflow_def_id": str(uuid.uuid4()),
            "_instance_id": str(uuid.uuid4()),
            "_current_node_id": "node_save",
            "node_agent": {
                "response": "Hello",
                "memory_debug": {"enabled": False},
            },
        },
        "tenant-a",
    )

    assert result["saved"] is True
    assert result["summary_updated"] is False
    assert result["promoted_memory_records"] == 0
    assert result["promoted_entity_facts"] == 0
    assert calls == {"summary": 0, "facts": 0, "records": 0}


def test_save_conversation_skips_memory_record_promotion_for_error_output(monkeypatch):
    fake_db = _FakeDB()
    session = SimpleNamespace(id=uuid.uuid4(), message_count=2, active_episode_id=None)
    calls = {"records": 0}

    monkeypatch.setattr("app.database.SessionLocal", lambda: fake_db)
    monkeypatch.setattr(
        "app.engine.memory_service.append_conversation_turns",
        lambda *args, **kwargs: (session, []),
    )
    monkeypatch.setattr(
        "app.engine.memory_service.get_active_episode",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.engine.memory_service.resolve_memory_policy",
        lambda *args, **kwargs: EffectiveMemoryPolicy(enabled=True),
    )
    monkeypatch.setattr(
        "app.engine.memory_service.refresh_rolling_summary",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        "app.engine.memory_service.promote_entity_facts",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "app.engine.memory_service.promote_memory_records",
        lambda *args, **kwargs: calls.__setitem__("records", calls["records"] + 1),
    )

    result = _handle_save_conversation_state(
        {"config": {"responseNodeId": "node_agent"}},
        {
            "trigger": {"session_id": "sess-1", "message": "Hi"},
            "_workflow_def_id": str(uuid.uuid4()),
            "_instance_id": str(uuid.uuid4()),
            "_current_node_id": "node_save",
            "node_agent": {
                "response": "Partial output",
                "error": "upstream failure",
                "memory_debug": {"enabled": True},
            },
        },
        "tenant-a",
    )

    assert result["saved"] is True
    assert result["promoted_memory_records"] == 0
    assert calls["records"] == 0


def test_archive_conversation_episode_resolves_expressions_and_returns_archive_info(monkeypatch):
    fake_db = _FakeDB()
    session = SimpleNamespace(id=uuid.uuid4(), session_id="sess-1")
    episode = SimpleNamespace(
        id=uuid.uuid4(),
        status="archived",
        title="VPN Incident",
        archive_reason="resolved",
        archived_at=None,
        checkpoint_summary_text="Issue closed after resetting the VPN profile.",
    )
    memory_row = SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr("app.database.SessionLocal", lambda: fake_db)
    monkeypatch.setattr(
        "app.engine.memory_service.get_or_create_session",
        lambda *args, **kwargs: session,
    )
    monkeypatch.setattr(
        "app.engine.memory_service.archive_active_episode",
        lambda *args, **kwargs: (episode, [memory_row]),
    )

    result = _handle_archive_conversation_episode(
        {
            "config": {
                "sessionIdExpression": "trigger.session_id",
                "summaryExpression": "node_reflection.final_summary",
                "titleExpression": "node_reflection.final_title",
                "reason": "resolved",
            }
        },
        {
            "trigger": {"session_id": "sess-1"},
            "_workflow_def_id": str(uuid.uuid4()),
            "_instance_id": str(uuid.uuid4()),
            "_current_node_id": "node_archive",
            "node_reflection": {
                "final_summary": "Issue closed after resetting the VPN profile.",
                "final_title": "VPN Incident",
            },
        },
        "tenant-a",
    )

    assert result["archived"] is True
    assert result["episode_id"] == str(episode.id)
    assert result["title"] == "VPN Incident"
    assert result["memory_records_created"] == 1
    assert result["summary_text"] == "Issue closed after resetting the VPN profile."
