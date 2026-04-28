"""COPILOT-01b.i — unit tests for the agent runner.

Mocks the Anthropic SDK so tests don't hit the network. Focuses on
the loop shape, turn persistence, tool-call dispatch, error
propagation, and iteration cap. The provider-specific marshalling
(Anthropic block shape) is exercised via fixture responses; OpenAI
/ Google equivalents will add parallel suites in 01b.iv.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.copilot import agent as agent_mod
from app.copilot.agent import (
    MAX_TOOL_ITERATIONS,
    AgentRunner,
    UnsupportedProviderError,
    default_model_for,
    supported_providers,
)


TENANT = "tenant-x"


# ---------------------------------------------------------------------------
# Fixtures — lightweight stand-ins for the ORM rows. Mirrors the field
# surface AgentRunner touches without pulling in the real models.
# ---------------------------------------------------------------------------


@dataclass
class _FakeDraft:
    id: uuid.UUID
    tenant_id: str
    graph_json: dict[str, Any]
    version: int


@dataclass
class _FakeSession:
    id: uuid.UUID
    tenant_id: str
    draft_id: uuid.UUID
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    status: str = "active"


class _FakeDb:
    """A minimum viable stand-in for SQLAlchemy Session.

    AgentRunner only uses .add/.flush, plus _persist_turn's count
    query. We record added objects and serve count() off the recorded
    list so turn_index is assigned correctly.
    """

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def flush(self) -> None:  # pragma: no cover — no-op for tests
        pass

    def query(self, model: Any):
        return _FakeQuery(self, model)


class _FakeQuery:
    def __init__(self, db: _FakeDb, model: Any) -> None:
        self._db = db
        self._model = model
        self._filters: dict[str, Any] = {}

    def filter_by(self, **kwargs: Any) -> "_FakeQuery":
        self._filters.update(kwargs)
        return self

    def count(self) -> int:
        from app.models.copilot import CopilotTurn

        if self._model is not CopilotTurn:
            return 0
        session_id = self._filters.get("session_id")
        return sum(
            1
            for obj in self._db.added
            if isinstance(obj, CopilotTurn) and obj.session_id == session_id
        )

    def order_by(self, *_a: Any) -> "_FakeQuery":
        return self

    def all(self) -> list[Any]:
        # Agent loads prior turns to rebuild Anthropic messages.
        # Filter recorded adds to match.
        from app.models.copilot import CopilotTurn

        if self._model is not CopilotTurn:
            return []
        session_id = self._filters.get("session_id")
        return sorted(
            [
                obj
                for obj in self._db.added
                if isinstance(obj, CopilotTurn) and obj.session_id == session_id
            ],
            key=lambda t: t.turn_index,
        )


@pytest.fixture
def draft():
    return _FakeDraft(
        id=uuid.uuid4(),
        tenant_id=TENANT,
        graph_json={"nodes": [], "edges": []},
        version=1,
    )


@pytest.fixture
def session(draft):
    return _FakeSession(
        id=uuid.uuid4(),
        tenant_id=TENANT,
        draft_id=draft.id,
    )


@pytest.fixture
def db():
    return _FakeDb()


# ---------------------------------------------------------------------------
# Fake Anthropic response shapes
# ---------------------------------------------------------------------------


def _text_block(text: str) -> SimpleNamespace:
    """Mirror the Anthropic SDK's .model_dump() via the shape our
    _block_to_dict() falls back on when model_dump isn't present."""
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(tool_id: str, name: str, input_: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=input_)


def _mk_response(
    *,
    blocks: list[SimpleNamespace],
    stop_reason: str = "end_turn",
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> SimpleNamespace:
    # Give SDK-blocks model_dump so _block_to_dict hits the fast path.
    for b in blocks:
        b.model_dump = lambda b=b: {
            k: v for k, v in b.__dict__.items() if k != "model_dump"
        }
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=input_tokens, output_tokens=output_tokens,
        ),
    )


@pytest.fixture
def mock_anthropic():
    """Patch the anthropic SDK + credential resolver so no network call."""
    with patch.object(agent_mod, "_call_anthropic") as mock_call:
        yield mock_call


# ---------------------------------------------------------------------------
# Provider metadata helpers
# ---------------------------------------------------------------------------


def test_supported_providers_lists_anthropic():
    assert "anthropic" in supported_providers()


def test_default_model_anthropic():
    assert default_model_for("anthropic").startswith("claude-")


def test_default_model_unknown_raises():
    with pytest.raises(UnsupportedProviderError):
        default_model_for("ollama")


# ---------------------------------------------------------------------------
# Agent runner — happy path without tool calls
# ---------------------------------------------------------------------------


def test_send_turn_text_only_persists_user_and_assistant(
    db, session, draft, mock_anthropic,
):
    mock_anthropic.return_value = {
        "text": "Hello. What kind of workflow?",
        "tool_uses": [],
        "raw_content": [{"type": "text", "text": "Hello. What kind of workflow?"}],
        "usage": {"input_tokens": 100, "output_tokens": 20},
        "stop_reason": "end_turn",
    }

    runner = AgentRunner(db, tenant_id=TENANT, session=session, draft=draft)
    events = list(runner.send_turn("I want to build a Slack summariser"))

    kinds = [e["type"] for e in events]
    assert kinds == ["assistant_text", "done"]
    assert events[0]["text"] == "Hello. What kind of workflow?"
    assert events[-1]["final_text"] == "Hello. What kind of workflow?"

    # Two turns added: user + assistant.
    from app.models.copilot import CopilotTurn

    turns = [t for t in db.added if isinstance(t, CopilotTurn)]
    assert [t.role for t in turns] == ["user", "assistant"]
    assert turns[0].content_json == {"text": "I want to build a Slack summariser"}
    assert turns[1].content_json["text"] == "Hello. What kind of workflow?"
    # turn_index is sequential starting at 0.
    assert turns[0].turn_index == 0
    assert turns[1].turn_index == 1


# ---------------------------------------------------------------------------
# Agent runner — tool-call loop
# ---------------------------------------------------------------------------


def test_send_turn_dispatches_mutation_tool_and_bumps_version(
    db, session, draft, mock_anthropic,
):
    # First response: one add_node tool_use.
    # Second response: final narration text, no tools → done.
    mock_anthropic.side_effect = [
        {
            "text": "",
            "tool_uses": [
                {"id": "toolu_1", "name": "add_node", "input": {"node_type": "llm_agent"}},
            ],
            "raw_content": [
                {"type": "tool_use", "id": "toolu_1", "name": "add_node",
                 "input": {"node_type": "llm_agent"}},
            ],
            "usage": {"input_tokens": 120, "output_tokens": 40},
            "stop_reason": "tool_use",
        },
        {
            "text": "Added the LLM Agent node.",
            "tool_uses": [],
            "raw_content": [{"type": "text", "text": "Added the LLM Agent node."}],
            "usage": {"input_tokens": 150, "output_tokens": 15},
            "stop_reason": "end_turn",
        },
    ]

    runner = AgentRunner(db, tenant_id=TENANT, session=session, draft=draft)
    events = list(runner.send_turn("add an LLM Agent"))

    kinds = [e["type"] for e in events]
    assert kinds == [
        "tool_call",
        "tool_result",
        "assistant_text",
        "done",
    ]

    # Tool call event carries args the LLM emitted.
    assert events[0]["name"] == "add_node"
    assert events[0]["args"] == {"node_type": "llm_agent"}

    # Tool result carries validation (mutation tool) + new draft version.
    assert events[1]["name"] == "add_node"
    assert events[1]["validation"] == {"errors": [], "warnings": []}
    assert events[1]["draft_version"] == 2
    assert events[1]["error"] is None

    # Draft actually mutated.
    assert len(draft.graph_json["nodes"]) == 1
    assert draft.graph_json["nodes"][0]["data"]["label"] == "LLM Agent"
    assert draft.version == 2

    # Three turns persisted: user, assistant (tool_use), tool, assistant (final).
    from app.models.copilot import CopilotTurn
    turns = [t for t in db.added if isinstance(t, CopilotTurn)]
    assert [t.role for t in turns] == ["user", "assistant", "tool", "assistant"]
    # The tool turn has tool_use_id pinned to what the LLM emitted.
    assert turns[2].content_json["tool_use_id"] == "toolu_1"


def test_send_turn_readonly_tool_does_not_bump_version(
    db, session, draft, mock_anthropic,
):
    mock_anthropic.side_effect = [
        {
            "text": "",
            "tool_uses": [
                {"id": "toolu_1", "name": "list_node_types", "input": {}},
            ],
            "raw_content": [
                {"type": "tool_use", "id": "toolu_1", "name": "list_node_types", "input": {}},
            ],
            "usage": {"input_tokens": 50, "output_tokens": 20},
            "stop_reason": "tool_use",
        },
        {
            "text": "Here are the available node types.",
            "tool_uses": [],
            "raw_content": [{"type": "text", "text": "Here are the available node types."}],
            "usage": {"input_tokens": 80, "output_tokens": 15},
            "stop_reason": "end_turn",
        },
    ]

    runner = AgentRunner(db, tenant_id=TENANT, session=session, draft=draft)
    events = list(runner.send_turn("what node types exist?"))

    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert tool_result["validation"] is None  # read-only
    assert tool_result["draft_version"] == 1  # unchanged
    assert draft.version == 1


def test_send_turn_bad_tool_args_surface_to_llm_not_500(
    db, session, draft, mock_anthropic,
):
    # LLM calls add_node with missing node_type → tool_layer raises
    # ToolLayerError → we should return the error to the LLM via
    # is_error, not crash the stream.
    mock_anthropic.side_effect = [
        {
            "text": "",
            "tool_uses": [
                {"id": "toolu_1", "name": "add_node", "input": {}},  # missing node_type
            ],
            "raw_content": [
                {"type": "tool_use", "id": "toolu_1", "name": "add_node", "input": {}},
            ],
            "usage": {"input_tokens": 50, "output_tokens": 20},
            "stop_reason": "tool_use",
        },
        {
            "text": "Sorry — I need to know which node type.",
            "tool_uses": [],
            "raw_content": [{"type": "text", "text": "Sorry — I need to know which node type."}],
            "usage": {"input_tokens": 80, "output_tokens": 15},
            "stop_reason": "end_turn",
        },
    ]

    runner = AgentRunner(db, tenant_id=TENANT, session=session, draft=draft)
    events = list(runner.send_turn("add a node"))

    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert tool_result["error"] is not None
    assert "node_type" in tool_result["error"].lower()

    # The turn after the tool call still produces assistant_text + done —
    # the agent keeps going and the user sees a graceful apology.
    assert events[-1]["type"] == "done"


def test_send_turn_unknown_tool_name_surfaces_as_error(
    db, session, draft, mock_anthropic,
):
    mock_anthropic.side_effect = [
        {
            "text": "",
            "tool_uses": [
                {"id": "toolu_1", "name": "made_up_tool", "input": {}},
            ],
            "raw_content": [
                {"type": "tool_use", "id": "toolu_1", "name": "made_up_tool", "input": {}},
            ],
            "usage": {"input_tokens": 50, "output_tokens": 20},
            "stop_reason": "tool_use",
        },
        {
            "text": "Let me try a real tool.",
            "tool_uses": [],
            "raw_content": [{"type": "text", "text": "Let me try a real tool."}],
            "usage": {"input_tokens": 60, "output_tokens": 10},
            "stop_reason": "end_turn",
        },
    ]

    runner = AgentRunner(db, tenant_id=TENANT, session=session, draft=draft)
    events = list(runner.send_turn("hi"))
    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert "unknown tool" in tool_result["error"].lower()


# ---------------------------------------------------------------------------
# Iteration cap
# ---------------------------------------------------------------------------


def test_send_turn_caps_at_max_iterations(db, session, draft, mock_anthropic):
    """Pathological loop: every LLM response is another tool call.
    We should bail after MAX_TOOL_ITERATIONS and emit a recoverable
    error, not spin forever."""
    def _always_tool_use(*_a, **_kw):
        return {
            "text": "",
            "tool_uses": [
                {"id": f"toolu_{_always_tool_use.calls}",
                 "name": "list_node_types", "input": {}},
            ],
            "raw_content": [
                {"type": "tool_use", "id": f"toolu_{_always_tool_use.calls}",
                 "name": "list_node_types", "input": {}},
            ],
            "usage": {"input_tokens": 50, "output_tokens": 20},
            "stop_reason": "tool_use",
        }
    _always_tool_use.calls = 0

    def _mock_call(*a, **kw):
        _always_tool_use.calls += 1
        return _always_tool_use(*a, **kw)

    mock_anthropic.side_effect = _mock_call

    runner = AgentRunner(db, tenant_id=TENANT, session=session, draft=draft)
    events = list(runner.send_turn("loop forever"))

    # Last event is an error with recoverable=True.
    final_error = [e for e in events if e["type"] == "error"]
    assert final_error
    assert final_error[-1]["recoverable"] is True
    # Capped at MAX_TOOL_ITERATIONS LLM calls.
    assert _always_tool_use.calls == MAX_TOOL_ITERATIONS


# ---------------------------------------------------------------------------
# Provider guard
# ---------------------------------------------------------------------------


def test_send_turn_dispatches_runner_tool_test_node(db, session, draft, mock_anthropic):
    """01b.ii.a — the runner-tool dispatch path. The LLM calls
    test_node; the agent routes to runner_tools, not the pure tool
    layer. Draft version does NOT bump (runner tools don't mutate the
    graph). Handler exceptions inside test_node surface as is_error
    so the LLM can self-correct."""
    from app.copilot import runner_tools

    # Seed the draft with a node so test_node can find it.
    draft.graph_json = {
        "nodes": [
            {
                "id": "node_1",
                "type": "agenticNode",
                "data": {"label": "LLM Agent", "config": {}},
            },
        ],
        "edges": [],
    }

    mock_anthropic.side_effect = [
        {
            "text": "",
            "tool_uses": [
                {
                    "id": "toolu_1",
                    "name": "test_node",
                    "input": {"node_id": "node_1", "trigger_payload": {"x": 1}},
                },
            ],
            "raw_content": [
                {"type": "tool_use", "id": "toolu_1", "name": "test_node",
                 "input": {"node_id": "node_1"}},
            ],
            "usage": {"input_tokens": 100, "output_tokens": 20},
            "stop_reason": "tool_use",
        },
        {
            "text": "The node ran successfully.",
            "tool_uses": [],
            "raw_content": [{"type": "text", "text": "The node ran successfully."}],
            "usage": {"input_tokens": 120, "output_tokens": 15},
            "stop_reason": "end_turn",
        },
    ]

    with patch(
        "app.copilot.runner_tools.test_node_against_draft",
        return_value={"node_id": "node_1", "output": {"response": "hi"}, "elapsed_ms": 5},
    ) as mock_run:
        runner = AgentRunner(db, tenant_id=TENANT, session=session, draft=draft)
        events = list(runner.send_turn("test node_1"))

    # Runner tool was called.
    assert mock_run.called

    # Event stream: tool_call → tool_result → assistant_text → done.
    kinds = [e["type"] for e in events]
    assert kinds == ["tool_call", "tool_result", "assistant_text", "done"]

    # Runner tools don't mutate the graph, so validation is None and
    # draft_version is unchanged.
    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert tool_result["validation"] is None
    assert tool_result["draft_version"] == 1
    assert tool_result["error"] is None
    assert tool_result["result"]["output"] == {"response": "hi"}
    assert draft.version == 1


def test_send_turn_runner_tool_error_surfaces_to_llm(db, session, draft, mock_anthropic):
    """test_node handler raised → runner_tools catches → returns
    {"error": "..."} → agent's _dispatch_runner_tool picks up the
    top-level error key → LLM sees is_error=True and self-corrects."""
    draft.graph_json = {
        "nodes": [{"id": "node_1", "type": "agenticNode", "data": {"label": "LLM Agent"}}],
        "edges": [],
    }

    mock_anthropic.side_effect = [
        {
            "text": "",
            "tool_uses": [
                {"id": "toolu_1", "name": "test_node", "input": {"node_id": "node_1"}},
            ],
            "raw_content": [
                {"type": "tool_use", "id": "toolu_1", "name": "test_node",
                 "input": {"node_id": "node_1"}},
            ],
            "usage": {"input_tokens": 50, "output_tokens": 15},
            "stop_reason": "tool_use",
        },
        {
            "text": "Looks like the model field is missing from the config.",
            "tool_uses": [],
            "raw_content": [{"type": "text", "text": "..."}],
            "usage": {"input_tokens": 70, "output_tokens": 15},
            "stop_reason": "end_turn",
        },
    ]

    with patch(
        "app.copilot.runner_tools.test_node_against_draft",
        return_value={
            "node_id": "node_1",
            "error": "missing 'model' field",
            "elapsed_ms": 3,
        },
    ):
        runner = AgentRunner(db, tenant_id=TENANT, session=session, draft=draft)
        events = list(runner.send_turn("test node_1"))

    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert tool_result["error"] == "missing 'model' field"


def test_send_turn_rejects_unsupported_provider(db, draft):
    session = _FakeSession(
        id=uuid.uuid4(),
        tenant_id=TENANT,
        draft_id=draft.id,
        provider="openai",  # not wired yet in 01b.i
    )
    runner = AgentRunner(db, tenant_id=TENANT, session=session, draft=draft)
    with pytest.raises(UnsupportedProviderError):
        list(runner.send_turn("hi"))


# ---------------------------------------------------------------------------
# COPILOT-03.d — auto-heal per-turn cap on suggest_fix
# ---------------------------------------------------------------------------


def test_send_turn_suggest_fix_per_turn_cap_short_circuits(
    db, session, draft, mock_anthropic,
):
    """03.d — once MAX_SUGGEST_FIX_PER_TURN suggest_fix calls have
    fired in a single turn, the runner short-circuits the next one
    with a cap-reached error WITHOUT invoking runner_tools. The LLM
    receives is_error=True and is expected to hand off to the user.
    """
    from app.copilot import runner_tools
    from app.copilot.agent import MAX_SUGGEST_FIX_PER_TURN

    draft.graph_json = {
        "nodes": [{"id": "node_1", "type": "agenticNode", "data": {"label": "x"}}],
        "edges": [],
    }

    # Build: the LLM calls suggest_fix MAX+1 times in one turn, then
    # gives up with a final text block.
    tool_call_blocks = [
        {
            "text": "",
            "tool_uses": [{
                "id": f"toolu_{i}",
                "name": "suggest_fix",
                "input": {"node_id": "node_1", "error": f"try {i}"},
            }],
            "raw_content": [{
                "type": "tool_use", "id": f"toolu_{i}",
                "name": "suggest_fix",
                "input": {"node_id": "node_1", "error": f"try {i}"},
            }],
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "tool_use",
        }
        for i in range(MAX_SUGGEST_FIX_PER_TURN + 1)
    ]
    final_block = {
        "text": "Handing off — I can't fix this automatically.",
        "tool_uses": [],
        "raw_content": [{"type": "text", "text": "Handing off — I can't fix this automatically."}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "stop_reason": "end_turn",
    }
    mock_anthropic.side_effect = [*tool_call_blocks, final_block]

    call_count = {"n": 0}

    def _fake_dispatch(tool_name, *, db, tenant_id, draft, args):
        call_count["n"] += 1
        return {
            "node_id": args["node_id"],
            "node_type": "x",
            "proposed_patch": {"retries": 3},
            "rationale": "r",
            "confidence": "medium",
            "dropped_keys": [],
            "applied": False,
            "usage": {},
        }

    with patch.object(runner_tools, "dispatch", side_effect=_fake_dispatch):
        runner = AgentRunner(db, tenant_id=TENANT, session=session, draft=draft)
        events = list(runner.send_turn("try to fix it"))

    # dispatch was called exactly MAX times — the (MAX+1)-th call was
    # short-circuited before runner_tools was hit.
    assert call_count["n"] == MAX_SUGGEST_FIX_PER_TURN

    # The capped tool_result event carries the cap-reached error and
    # points the agent at a hand-off.
    tool_results = [e for e in events if e["type"] == "tool_result"]
    assert len(tool_results) == MAX_SUGGEST_FIX_PER_TURN + 1
    capped = tool_results[-1]
    assert capped["error"] is not None
    assert "cap reached" in capped["error"].lower()
    assert capped["result"]["auto_heal_count"] == MAX_SUGGEST_FIX_PER_TURN
    assert capped["result"]["auto_heal_cap"] == MAX_SUGGEST_FIX_PER_TURN


def test_suggest_fix_counter_resets_between_turns(db, session, draft, mock_anthropic):
    """03.d — counter is per-TURN, not per-session. A second
    send_turn starts with a fresh budget."""
    from app.copilot import runner_tools
    from app.copilot.agent import MAX_SUGGEST_FIX_PER_TURN

    draft.graph_json = {
        "nodes": [{"id": "node_1", "type": "agenticNode", "data": {"label": "x"}}],
        "edges": [],
    }

    # Two turns; each makes exactly MAX suggest_fix calls.
    blocks_per_turn = [
        *[
            {
                "text": "",
                "tool_uses": [{
                    "id": f"toolu_{i}", "name": "suggest_fix",
                    "input": {"node_id": "node_1", "error": "e"},
                }],
                "raw_content": [{
                    "type": "tool_use", "id": f"toolu_{i}",
                    "name": "suggest_fix",
                    "input": {"node_id": "node_1", "error": "e"},
                }],
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "stop_reason": "tool_use",
            }
            for i in range(MAX_SUGGEST_FIX_PER_TURN)
        ],
        {
            "text": "done",
            "tool_uses": [],
            "raw_content": [{"type": "text", "text": "done"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "stop_reason": "end_turn",
        },
    ]
    mock_anthropic.side_effect = blocks_per_turn + blocks_per_turn

    dispatch_calls: list[str] = []

    def _fake_dispatch(tool_name, **kwargs):
        dispatch_calls.append(tool_name)
        return {
            "proposed_patch": {"retries": 2},
            "rationale": "r",
            "confidence": "medium",
            "dropped_keys": [],
            "applied": False,
            "usage": {},
        }

    with patch.object(runner_tools, "dispatch", side_effect=_fake_dispatch):
        runner = AgentRunner(db, tenant_id=TENANT, session=session, draft=draft)
        list(runner.send_turn("turn one — fix it"))
        list(runner.send_turn("turn two — try again"))

    # Both turns used their full budget. If the counter leaked, the
    # second turn would be capped and dispatch would only see MAX total.
    assert len(dispatch_calls) == 2 * MAX_SUGGEST_FIX_PER_TURN


def test_max_suggest_fix_per_turn_is_three():
    """Pin the constant at 3 — documentation and spec both reference
    this number; a silent bump should require a test update."""
    from app.copilot.agent import MAX_SUGGEST_FIX_PER_TURN
    assert MAX_SUGGEST_FIX_PER_TURN == 3
