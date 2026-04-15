import uuid
from types import SimpleNamespace

from app.engine.memory_service import (
    EffectiveMemoryPolicy,
    assemble_agent_messages,
    assemble_history_text,
    build_conversation_idempotency_key,
    build_history_block,
    build_memory_record_dedupe_key,
    memory_debug_to_node_config,
)


class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter_by(self, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)


class _FakeDB:
    def __init__(self, conversation_rows):
        self._conversation_rows = list(conversation_rows)

    def query(self, model):
        return _FakeQuery(self._conversation_rows)


def _message(turn_index: int, role: str, content: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        session_ref_id=uuid.uuid4(),
        tenant_id="tenant-a",
        session_id="sess-1",
        turn_index=turn_index,
        role=role,
        content=content,
    )


def test_build_history_block_keeps_summary_separate_from_recent_turns():
    session = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id="tenant-a",
        session_id="sess-1",
        summary_text="Customer already shared environment details.",
        summary_through_turn=2,
        message_count=4,
    )
    all_messages = [
        _message(1, "user", "Old question"),
        _message(2, "assistant", "Old answer"),
        _message(3, "user", "Recent clarification"),
        _message(4, "assistant", "Recent follow-up"),
    ]

    summary_block, recent_messages = build_history_block(
        session=session,
        all_messages=all_messages,
        policy=EffectiveMemoryPolicy(recent_token_budget=1000),
    )

    assert "Customer already shared environment details." in summary_block
    assert "Recent clarification" not in summary_block
    assert [msg.turn_index for msg in recent_messages] == [3, 4]


def test_build_history_block_respects_recent_token_budget():
    session = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id="tenant-a",
        session_id="sess-1",
        summary_text="",
        summary_through_turn=0,
        message_count=2,
    )
    all_messages = [
        _message(1, "user", "Earlier context that should be dropped"),
        _message(2, "assistant", "latest " * 200),
    ]

    _, recent_messages = build_history_block(
        session=session,
        all_messages=all_messages,
        policy=EffectiveMemoryPolicy(recent_token_budget=20),
    )

    assert [msg.turn_index for msg in recent_messages] == [2]


def test_assemble_history_text_formats_summary_and_recent_turns(monkeypatch):
    session = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id="tenant-a",
        session_id="sess-1",
        summary_text="Summarized earlier discussion.",
        summary_through_turn=2,
        message_count=4,
    )
    rows = [
        _message(1, "user", "Old question"),
        _message(2, "assistant", "Old answer"),
        _message(3, "user", "Recent clarification"),
        _message(4, "assistant", "Recent follow-up"),
    ]
    fake_db = _FakeDB(rows)

    monkeypatch.setattr(
        "app.engine.memory_service.resolve_memory_policy",
        lambda *args, **kwargs: EffectiveMemoryPolicy(
            history_node_id="node_history",
            recent_token_budget=1000,
        ),
    )
    monkeypatch.setattr(
        "app.engine.memory_service.get_or_create_session",
        lambda *args, **kwargs: session,
    )

    history_text, debug = assemble_history_text(
        fake_db,
        tenant_id="tenant-a",
        workflow_def_id="",
        context={"node_history": {"session_id": "sess-1", "messages": []}},
        node_config={"historyNodeId": "node_history"},
    )

    assert "Earlier conversation summary:" in history_text
    assert "USER: Recent clarification" in history_text
    assert debug["summary_used"] is True
    assert debug["recent_turn_count"] == 2


def test_assemble_agent_messages_uses_turns_without_duplication(monkeypatch):
    session = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id="tenant-a",
        session_id="sess-1",
        summary_text="Prior summary",
        summary_through_turn=2,
        message_count=4,
    )
    rows = [
        _message(1, "user", "Old question"),
        _message(2, "assistant", "Old answer"),
        _message(3, "user", "Recent clarification"),
        _message(4, "assistant", "Recent follow-up"),
    ]
    fake_db = _FakeDB(rows)

    monkeypatch.setattr(
        "app.engine.memory_service.resolve_memory_policy",
        lambda *args, **kwargs: EffectiveMemoryPolicy(
            history_node_id="node_history",
            instructions=["Tenant memory policy"],
            recent_token_budget=1000,
            max_semantic_hits=0,
        ),
    )
    monkeypatch.setattr(
        "app.engine.memory_service.get_or_create_session",
        lambda *args, **kwargs: session,
    )
    monkeypatch.setattr(
        "app.engine.memory_service.retrieve_memory_records",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "app.engine.memory_service._active_entity_facts",
        lambda *args, **kwargs: [],
    )

    messages, debug = assemble_agent_messages(
        fake_db,
        tenant_id="tenant-a",
        workflow_def_id="",
        context={
            "trigger": {"message": "Current question"},
            "node_history": {"session_id": "sess-1", "messages": []},
            "node_result": {"value": "abc"},
        },
        node_config={"historyNodeId": "node_history"},
        rendered_system_prompt="Solve the user request.",
    )

    assert [msg["role"] for msg in messages] == ["system", "assistant", "user", "assistant", "user"]
    assert messages[0]["content"].startswith("Tenant memory policy")
    assert "Prior summary" in messages[1]["content"]
    assert messages[1]["content"].count("Recent clarification") == 0
    assert messages[2]["content"] == "Recent clarification"
    assert "Latest user message:\nCurrent question" in messages[-1]["content"]
    assert debug["recent_turn_count"] == 2


def test_assemble_history_text_supports_recent_first_order(monkeypatch):
    session = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id="tenant-a",
        session_id="sess-1",
        summary_text="Earlier summary block.",
        summary_through_turn=2,
        message_count=4,
    )
    rows = [
        _message(1, "user", "Old question"),
        _message(2, "assistant", "Old answer"),
        _message(3, "user", "Recent clarification"),
        _message(4, "assistant", "Recent follow-up"),
    ]
    fake_db = _FakeDB(rows)

    monkeypatch.setattr(
        "app.engine.memory_service.resolve_memory_policy",
        lambda *args, **kwargs: EffectiveMemoryPolicy(
            history_node_id="node_history",
            recent_token_budget=1000,
            history_order="recent_first",
        ),
    )
    monkeypatch.setattr(
        "app.engine.memory_service.get_or_create_session",
        lambda *args, **kwargs: session,
    )

    history_text, debug = assemble_history_text(
        fake_db,
        tenant_id="tenant-a",
        workflow_def_id="",
        context={"node_history": {"session_id": "sess-1", "messages": []}},
        node_config={"historyNodeId": "node_history"},
    )

    assert history_text.index("Recent turns:") < history_text.index("Earlier conversation summary:")
    assert debug["history_order"] == "recent_first"


def test_conversation_idempotency_key_changes_with_loop_iteration_and_content():
    base = build_conversation_idempotency_key(
        session_id="sess-1",
        instance_id="inst-1",
        node_id="node-save",
        loop_iteration=1,
        user_message="hello",
        assistant_response="world",
    )
    same = build_conversation_idempotency_key(
        session_id="sess-1",
        instance_id="inst-1",
        node_id="node-save",
        loop_iteration=1,
        user_message="hello",
        assistant_response="world",
    )
    different_loop = build_conversation_idempotency_key(
        session_id="sess-1",
        instance_id="inst-1",
        node_id="node-save",
        loop_iteration=2,
        user_message="hello",
        assistant_response="world",
    )
    different_content = build_conversation_idempotency_key(
        session_id="sess-1",
        instance_id="inst-1",
        node_id="node-save",
        loop_iteration=1,
        user_message="hello again",
        assistant_response="world",
    )

    assert base == same
    assert base != different_loop
    assert base != different_content


def test_memory_record_dedupe_key_changes_with_scope_and_content():
    base = build_memory_record_dedupe_key(
        tenant_id="tenant-a",
        scope="session",
        scope_key="sess-1",
        kind="episode",
        conversation_idempotency_key="conv-idem-1",
    )
    same = build_memory_record_dedupe_key(
        tenant_id="tenant-a",
        scope="session",
        scope_key="sess-1",
        kind="episode",
        conversation_idempotency_key="conv-idem-1",
    )
    different_scope = build_memory_record_dedupe_key(
        tenant_id="tenant-a",
        scope="tenant",
        scope_key="tenant-a",
        kind="episode",
        conversation_idempotency_key="conv-idem-1",
    )
    different_content = build_memory_record_dedupe_key(
        tenant_id="tenant-a",
        scope="session",
        scope_key="sess-1",
        kind="episode",
        conversation_idempotency_key="conv-idem-2",
    )

    assert base == same
    assert base != different_scope
    assert base != different_content


def test_memory_debug_to_node_config_extracts_save_time_overrides():
    node_config = memory_debug_to_node_config(
        {
            "enabled": False,
            "profile_id": str(uuid.uuid4()),
            "scopes": ["session", "entity", "bogus"],
            "include_entity_memory": False,
            "history_order": "recent_first",
        }
    )

    assert node_config == {
        "memoryEnabled": False,
        "memoryProfileId": node_config["memoryProfileId"],
        "memoryScopes": ["session", "entity"],
        "includeEntityMemory": False,
        "historyOrder": "recent_first",
    }
