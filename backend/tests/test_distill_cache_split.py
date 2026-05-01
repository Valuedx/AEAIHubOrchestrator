"""CTX-MGMT.J v2 — distillBlocks land on the per-turn user message,
not the system prompt.

Pre-v2: render_distill_blocks was appended to the system prompt
*before* assemble_agent_messages was called. The system prompt
therefore changed every turn (worknotes/findings drift continuously),
which busted the provider's prefix cache on every turn.

v2: render_distill_blocks runs in the handler, but the result is
passed through to assemble_agent_messages as a separate
``distill_text`` argument, where it lands on the per-turn dynamic
user message. The system prompt stays cache-stable across turns.

These tests verify the ride-along contract:
    - The system message contains ONLY the static rendered prompt
    - The distill text shows up on the final user message
    - In both memory-enabled and memory-disabled paths
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.engine.memory_service import (
    EffectiveMemoryPolicy,
    assemble_agent_messages,
)


# ---------------------------------------------------------------------------
# Test scaffolding (mirrors test_memory_service.py shape)
# ---------------------------------------------------------------------------


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
    def __init__(self, conversation_rows=None):
        self._conversation_rows = list(conversation_rows or [])

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


# ---------------------------------------------------------------------------
# Memory-disabled path — the simplest case
# ---------------------------------------------------------------------------


class TestMemoryDisabledPath:
    """When the memory profile is disabled, assemble_agent_messages
    builds a tiny [system, user] pair. Distill must land on the
    user message — never the system message."""

    def test_distill_appears_on_user_message_not_system(self, monkeypatch):
        monkeypatch.setattr(
            "app.engine.memory_service.resolve_memory_policy",
            lambda *args, **kwargs: EffectiveMemoryPolicy(enabled=False),
        )
        messages, debug = assemble_agent_messages(
            _FakeDB(),
            tenant_id="tenant-a",
            workflow_def_id="",
            context={"node_x": {"foo": "bar"}},
            node_config={},
            rendered_system_prompt="You are a helpful agent.",
            distill_text="=== RECENT FINDINGS ===\n- alpha\n- beta",
        )

        # Two messages: system, user.
        roles = [m["role"] for m in messages]
        assert roles == ["system", "user"]
        # System content is ONLY the static prompt (no distill leak).
        assert messages[0]["content"] == "You are a helpful agent."
        assert "RECENT FINDINGS" not in messages[0]["content"]
        # Distill is on the user message.
        assert "RECENT FINDINGS" in messages[1]["content"]
        assert "- alpha" in messages[1]["content"]
        assert debug["enabled"] is False

    def test_empty_distill_omitted_cleanly(self, monkeypatch):
        # No distill_text → behaves identically to pre-v2 disabled path
        # (just a structured workflow context block on the user side).
        monkeypatch.setattr(
            "app.engine.memory_service.resolve_memory_policy",
            lambda *args, **kwargs: EffectiveMemoryPolicy(enabled=False),
        )
        messages, _ = assemble_agent_messages(
            _FakeDB(),
            tenant_id="tenant-a",
            workflow_def_id="",
            context={"node_x": {"foo": "bar"}},
            node_config={},
            rendered_system_prompt="System",
            distill_text="",
        )
        assert messages[0]["content"] == "System"
        # User message exists; whatever it says, it doesn't contain
        # the distill marker.
        assert "RECENT FINDINGS" not in messages[1]["content"]

    def test_default_distill_text_arg_is_empty(self, monkeypatch):
        # Backward-compat: callers that don't pass distill_text still
        # work. Old call sites stay valid even before they're updated.
        monkeypatch.setattr(
            "app.engine.memory_service.resolve_memory_policy",
            lambda *args, **kwargs: EffectiveMemoryPolicy(enabled=False),
        )
        messages, _ = assemble_agent_messages(
            _FakeDB(),
            tenant_id="tenant-a",
            workflow_def_id="",
            context={"node_x": {"foo": "bar"}},
            node_config={},
            rendered_system_prompt="System",
        )
        # No crash, no leak.
        assert messages[0]["content"] == "System"


# ---------------------------------------------------------------------------
# Memory-enabled path — distill rides on final_sections
# ---------------------------------------------------------------------------


class TestMemoryEnabledPath:
    """In the memory-enabled flow, the per-turn user message bundles
    facts, semantic hits, latest user message, workflow context, and
    (with v2) distill. System message is the cached prefix; user
    message is the per-turn dynamic surface."""

    def _patch_memory_calls(self, monkeypatch, *, session=None):
        monkeypatch.setattr(
            "app.engine.memory_service.resolve_memory_policy",
            lambda *args, **kwargs: EffectiveMemoryPolicy(
                history_node_id="node_history",
                instructions=["You are a helpful agent."],
                recent_token_budget=1000,
                max_semantic_hits=0,
            ),
        )
        monkeypatch.setattr(
            "app.engine.memory_service.get_or_create_session",
            lambda *args, **kwargs: session,
        )
        monkeypatch.setattr(
            "app.engine.memory_service.get_active_episode",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            "app.engine.memory_service.retrieve_memory_records",
            lambda *args, **kwargs: [],
        )
        monkeypatch.setattr(
            "app.engine.memory_service._active_entity_facts",
            lambda *args, **kwargs: [],
        )

    def test_distill_lands_on_final_user_message(self, monkeypatch):
        session = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id="tenant-a",
            session_id="sess-1",
            summary_text="",
            summary_through_turn=0,
            message_count=0,
            active_episode_id=None,
        )
        self._patch_memory_calls(monkeypatch, session=session)

        messages, _ = assemble_agent_messages(
            _FakeDB(),
            tenant_id="tenant-a",
            workflow_def_id="",
            context={
                "trigger": {"message": "User asked X"},
                "node_history": {"session_id": "sess-1", "messages": []},
            },
            node_config={"historyNodeId": "node_history"},
            rendered_system_prompt="Solve the user request.",
            distill_text="=== RECENT FINDINGS ===\n- alpha\n- beta",
        )

        # System message has only the policy instructions + static
        # rendered prompt. Distill marker MUST NOT appear.
        assert messages[0]["role"] == "system"
        assert "RECENT FINDINGS" not in messages[0]["content"]
        assert "Solve the user request." in messages[0]["content"]

        # Final user message carries the distill block alongside other
        # per-turn dynamic sections.
        final = messages[-1]
        assert final["role"] == "user"
        assert "RECENT FINDINGS" in final["content"]
        assert "- alpha" in final["content"]
        # Latest user message also lives on this same message.
        assert "Latest user message:\nUser asked X" in final["content"]

    def test_no_distill_means_no_distill_section(self, monkeypatch):
        session = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id="tenant-a",
            session_id="sess-1",
            summary_text="",
            summary_through_turn=0,
            message_count=0,
            active_episode_id=None,
        )
        self._patch_memory_calls(monkeypatch, session=session)

        messages, _ = assemble_agent_messages(
            _FakeDB(),
            tenant_id="tenant-a",
            workflow_def_id="",
            context={
                "trigger": {"message": "User asked X"},
                "node_history": {"session_id": "sess-1", "messages": []},
            },
            node_config={"historyNodeId": "node_history"},
            rendered_system_prompt="Solve the user request.",
            distill_text="",
        )
        # Final user message exists but no distill section header.
        assert "RECENT FINDINGS" not in messages[-1]["content"]
        # Latest user message still present.
        assert "Latest user message:\nUser asked X" in messages[-1]["content"]


# ---------------------------------------------------------------------------
# Cache-stability proof — same system prompt across turns regardless
# of distill content drift
# ---------------------------------------------------------------------------


class TestCacheStability:
    """The whole point of v2 is that the system prompt no longer
    drifts when distill content drifts. Two assemblies with different
    distill payloads but the same rendered_system_prompt must produce
    byte-identical system messages."""

    def _patch_memory_calls(self, monkeypatch, session):
        monkeypatch.setattr(
            "app.engine.memory_service.resolve_memory_policy",
            lambda *args, **kwargs: EffectiveMemoryPolicy(
                history_node_id="node_history",
                instructions=["Cached instructions"],
                recent_token_budget=1000,
                max_semantic_hits=0,
            ),
        )
        monkeypatch.setattr(
            "app.engine.memory_service.get_or_create_session",
            lambda *args, **kwargs: session,
        )
        monkeypatch.setattr(
            "app.engine.memory_service.get_active_episode",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            "app.engine.memory_service.retrieve_memory_records",
            lambda *args, **kwargs: [],
        )
        monkeypatch.setattr(
            "app.engine.memory_service._active_entity_facts",
            lambda *args, **kwargs: [],
        )

    def test_same_system_prompt_across_changing_distill(self, monkeypatch):
        session = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id="tenant-a",
            session_id="sess-1",
            summary_text="",
            summary_through_turn=0,
            message_count=0,
            active_episode_id=None,
        )
        self._patch_memory_calls(monkeypatch, session)

        # Turn 1 — three findings.
        msgs_t1, _ = assemble_agent_messages(
            _FakeDB(),
            tenant_id="tenant-a",
            workflow_def_id="",
            context={
                "trigger": {"message": "Q"},
                "node_history": {"session_id": "sess-1", "messages": []},
            },
            node_config={"historyNodeId": "node_history"},
            rendered_system_prompt="Static system text.",
            distill_text="=== FIND ===\n- a\n- b\n- c",
        )

        # Turn 2 — five findings, content totally different.
        msgs_t2, _ = assemble_agent_messages(
            _FakeDB(),
            tenant_id="tenant-a",
            workflow_def_id="",
            context={
                "trigger": {"message": "Q"},
                "node_history": {"session_id": "sess-1", "messages": []},
            },
            node_config={"historyNodeId": "node_history"},
            rendered_system_prompt="Static system text.",
            distill_text="=== FIND ===\n- d\n- e\n- f\n- g\n- h",
        )

        # The system message must be byte-identical between the two
        # turns — that's what makes the prefix cache hit.
        assert msgs_t1[0]["role"] == "system"
        assert msgs_t2[0]["role"] == "system"
        assert msgs_t1[0]["content"] == msgs_t2[0]["content"]

        # And the distill content must show up only on the user side
        # (and must differ between turns, proving distill is wired up).
        assert "- a" in msgs_t1[-1]["content"]
        assert "- a" not in msgs_t2[-1]["content"]
        assert "- h" in msgs_t2[-1]["content"]


# ---------------------------------------------------------------------------
# Handler wiring — confirm the call sites pass distill_text through
# ---------------------------------------------------------------------------


class TestHandlerWiring:
    """Source-inspection guard so a future refactor can't silently
    move distill rendering back into the system prompt. The two
    handlers (LLM agent + ReAct) must both call
    ``render_distill_blocks`` and pass the result via the
    ``distill_text`` kwarg of ``assemble_agent_messages``."""

    def test_llm_agent_passes_distill_text_kwarg(self):
        from pathlib import Path

        src = Path(__file__).parent.parent / "app" / "engine" / "node_handlers.py"
        source = src.read_text(encoding="utf-8")
        # Distill is rendered.
        assert "render_distill_blocks(context, config.get(\"distillBlocks\"))" in source
        # Result is passed via the kwarg, NOT appended to system_prompt.
        assert "distill_text=_distill_text" in source
        # The pre-v2 append pattern must be gone.
        assert "system_prompt = system_prompt + \"\\n\\n\" + _distill_text" not in source

    def test_react_loop_passes_distill_text_kwarg(self):
        from pathlib import Path

        src = Path(__file__).parent.parent / "app" / "engine" / "react_loop.py"
        source = src.read_text(encoding="utf-8")
        assert "render_distill_blocks(context, config.get(\"distillBlocks\"))" in source
        assert "distill_text=_distill_text" in source
        # Pre-v2 append-to-system-prompt pattern removed.
        assert "system_prompt = system_prompt + \"\\n\\n\" + _distill_text" not in source
