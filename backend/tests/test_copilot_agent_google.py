"""COPILOT-01b.iv — Google AI Studio + Vertex AI provider tests.

Both providers share the unified ``google-genai`` SDK and the same
``_call_google`` + ``_append_google_tool_round`` adapter functions —
only the client constructor (AI Studio vs. Vertex AI) differs, and
that split happens inside ``llm_providers._google_client``.

These tests mock out the network call at ``_call_google`` so we can
exercise:

  * provider registration (both ``google`` and ``vertex``)
  * the adapter loop through ``send_turn`` using a Google response
  * tool-call dispatch + version-bump under the Google shape (which
    doesn't emit per-call ids — we synthesise them from function name)
  * Vertex provider routes through the same adapter

The actual Google-shape wire format (``types.Content(role, parts)``
and ``function_call`` / ``function_response`` Parts) is exercised
only via a smoke test that asserts the Google state builder
produces the expected chronological history from persisted turns —
reinforcement that 01b.i's turn persistence shape is
provider-agnostic.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest


pytest.importorskip("google.genai")


from app.copilot import agent as agent_mod
from app.copilot.agent import (
    AgentRunner,
    _build_google_state,
    default_model_for,
    supported_providers,
)


TENANT = "tenant-g"


# ---------------------------------------------------------------------------
# Reuse of the minimal DB + draft/session stand-ins from
# ``test_copilot_agent``. Local copies here so the two files don't
# couple to the same fixtures.
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
    provider: str
    model: str
    status: str = "active"


class _FakeDb:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def flush(self) -> None:
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
def db():
    return _FakeDb()


def _google_session(draft, provider: str) -> _FakeSession:
    return _FakeSession(
        id=uuid.uuid4(),
        tenant_id=TENANT,
        draft_id=draft.id,
        provider=provider,
        model=default_model_for(provider),
    )


# ---------------------------------------------------------------------------
# Provider metadata
# ---------------------------------------------------------------------------


def test_supported_providers_lists_all_three():
    providers = supported_providers()
    assert set(providers) >= {"anthropic", "google", "vertex"}


def test_default_model_is_gemini_3_plus_for_google_and_vertex():
    for p in ("google", "vertex"):
        model = default_model_for(p)
        assert model.startswith("gemini-3"), (
            f"Provider {p} default {model!r} must be on Gemini 3.x — "
            "Gemini 3 Pro Preview was deprecated 2026-03-09; the copilot "
            "should default to a currently-supported release."
        )


def test_default_model_for_google_is_customtools_variant():
    # The ``-customtools`` endpoint is specifically optimised for
    # agentic tool-calling workloads like this copilot. Locking the
    # default here so a casual bump to a non-customtools variant
    # (which rates worse on tool-selection) doesn't slip past review.
    for p in ("google", "vertex"):
        assert "customtools" in default_model_for(p)


# ---------------------------------------------------------------------------
# Google loop — tool-call dispatch under the Google response shape
# ---------------------------------------------------------------------------


def test_google_send_turn_dispatches_mutation_tool_and_bumps_version(db, draft):
    session = _google_session(draft, "google")

    # First response: one add_node function call. Second: final text,
    # no tools → done.
    responses = [
        {
            "text": "",
            "tool_uses": [
                {
                    "id": "gfn_add_node_0",
                    "name": "add_node",
                    "input": {"node_type": "llm_agent"},
                },
            ],
            "raw_content": [],  # Google doesn't populate raw_content
            "usage": {"input_tokens": 200, "output_tokens": 30},
            "stop_reason": "tool_calls",
        },
        {
            "text": "Added an LLM Agent node.",
            "tool_uses": [],
            "raw_content": [],
            "usage": {"input_tokens": 250, "output_tokens": 15},
            "stop_reason": "stop",
        },
    ]

    with patch.object(agent_mod, "_call_google") as mock_call:
        mock_call.side_effect = responses
        runner = AgentRunner(db, tenant_id=TENANT, session=session, draft=draft)
        events = list(runner.send_turn("add an LLM Agent"))

    kinds = [e["type"] for e in events]
    assert kinds == ["tool_call", "tool_result", "assistant_text", "done"]
    assert events[1]["validation"] == {"errors": [], "warnings": []}
    assert events[1]["draft_version"] == 2
    assert draft.version == 2
    assert len(draft.graph_json["nodes"]) == 1

    # First call gets new_user_text (user msg not in state yet);
    # second call doesn't (tool results already appended to history).
    first_call_kwargs = mock_call.call_args_list[0].kwargs
    second_call_kwargs = mock_call.call_args_list[1].kwargs
    assert first_call_kwargs["new_user_text"] == "add an LLM Agent"
    assert second_call_kwargs["new_user_text"] is None


def test_google_readonly_tool_does_not_bump_version(db, draft):
    session = _google_session(draft, "google")
    responses = [
        {
            "text": "",
            "tool_uses": [
                {"id": "gfn_list_node_types_0", "name": "list_node_types", "input": {}},
            ],
            "raw_content": [],
            "usage": {"input_tokens": 100, "output_tokens": 20},
            "stop_reason": "tool_calls",
        },
        {
            "text": "Here are the categories.",
            "tool_uses": [],
            "raw_content": [],
            "usage": {"input_tokens": 130, "output_tokens": 10},
            "stop_reason": "stop",
        },
    ]

    with patch.object(agent_mod, "_call_google") as mock_call:
        mock_call.side_effect = responses
        runner = AgentRunner(db, tenant_id=TENANT, session=session, draft=draft)
        events = list(runner.send_turn("what nodes?"))

    tool_result = next(e for e in events if e["type"] == "tool_result")
    assert tool_result["validation"] is None
    assert tool_result["draft_version"] == 1
    assert draft.version == 1


def test_vertex_routes_through_google_adapter(db, draft):
    """Both google and vertex providers share the same adapter — only
    the client-constructor backend differs (AI Studio vs. Vertex AI).
    This test pins that contract: switching provider to ``vertex``
    still hits ``_call_google``, not a second parallel code path."""
    session = _google_session(draft, "vertex")
    with patch.object(agent_mod, "_call_google") as mock_call:
        mock_call.return_value = {
            "text": "Hello from Vertex.",
            "tool_uses": [],
            "raw_content": [],
            "usage": {"input_tokens": 50, "output_tokens": 10},
            "stop_reason": "stop",
        }
        runner = AgentRunner(db, tenant_id=TENANT, session=session, draft=draft)
        events = list(runner.send_turn("hi"))

    assert events[-1]["type"] == "done"
    assert mock_call.called
    # The build_state closure must have been called with backend="vertex"
    # — we verify by peeking at the state it produced (accessible via
    # the call args to _call_google).
    google_state = mock_call.call_args.kwargs["state"]
    assert google_state._backend == "vertex"


def test_google_provider_iteration_cap(db, draft):
    """Same stop-the-flap behaviour as Anthropic — pathological loop
    emits a recoverable error after MAX_TOOL_ITERATIONS calls."""
    session = _google_session(draft, "google")

    call_counter = {"n": 0}

    def _always_tool_use(*_a, **_kw):
        call_counter["n"] += 1
        return {
            "text": "",
            "tool_uses": [
                {
                    "id": f"gfn_list_node_types_{call_counter['n']}",
                    "name": "list_node_types",
                    "input": {},
                },
            ],
            "raw_content": [],
            "usage": {"input_tokens": 50, "output_tokens": 20},
            "stop_reason": "tool_calls",
        }

    with patch.object(agent_mod, "_call_google", side_effect=_always_tool_use):
        runner = AgentRunner(db, tenant_id=TENANT, session=session, draft=draft)
        events = list(runner.send_turn("loop"))

    errors = [e for e in events if e["type"] == "error"]
    assert errors and errors[-1]["recoverable"] is True
    assert call_counter["n"] == agent_mod.MAX_TOOL_ITERATIONS


# ---------------------------------------------------------------------------
# Google state builder — rebuild history from persisted turns
# ---------------------------------------------------------------------------


def test_build_google_state_empty_history(db, draft):
    session = _google_session(draft, "google")
    state = _build_google_state(
        db,
        tenant_id=TENANT,
        session=session,
        draft=draft,
        new_user_text="hello",
        backend="genai",
    )
    # No prior turns → empty history; system prompt populated; backend
    # recorded so the adapter's call() picks the right client.
    assert state.history == []
    assert state.system  # non-empty system prompt
    assert state._backend == "genai"


def test_build_google_state_reconstructs_from_prior_turns(db, draft):
    """Persisted user + assistant + tool turns rebuild into the
    Content(role, parts) shape Google expects. Smoke-checks the mapping
    without asserting exact ``types.Content`` internals (that's SDK
    territory)."""
    from google.genai import types

    from app.models.copilot import CopilotTurn

    session = _google_session(draft, "google")

    # Seed persisted history: user → assistant with one tool_use →
    # tool result.
    db.added.append(CopilotTurn(
        id=uuid.uuid4(),
        tenant_id=TENANT,
        session_id=session.id,
        turn_index=0,
        role="user",
        content_json={"text": "build me a flow"},
    ))
    db.added.append(CopilotTurn(
        id=uuid.uuid4(),
        tenant_id=TENANT,
        session_id=session.id,
        turn_index=1,
        role="assistant",
        content_json={"text": "Adding an LLM Agent."},
        tool_calls_json=[{
            "id": "gfn_add_node_0",
            "name": "add_node",
            "input": {"node_type": "llm_agent"},
        }],
    ))
    db.added.append(CopilotTurn(
        id=uuid.uuid4(),
        tenant_id=TENANT,
        session_id=session.id,
        turn_index=2,
        role="tool",
        content_json={
            "tool_use_id": "gfn_add_node_0",
            "name": "add_node",
            "args": {"node_type": "llm_agent"},
            "result": {"node_id": "node_1"},
        },
    ))

    state = _build_google_state(
        db,
        tenant_id=TENANT,
        session=session,
        draft=draft,
        new_user_text="next step?",
        backend="genai",
    )

    # Expected shape: user-Content → model-Content (text + function_call)
    # → user-Content (function_response).
    assert len(state.history) == 3
    assert state.history[0].role == "user"
    assert state.history[1].role == "model"
    assert state.history[2].role == "user"

    # The model turn's parts include both text and function_call.
    model_parts = state.history[1].parts
    assert any(hasattr(p, "text") and p.text for p in model_parts), model_parts
    assert any(getattr(p, "function_call", None) for p in model_parts), model_parts

    # The tool-result turn's part is a function_response.
    tool_part = state.history[2].parts[0]
    assert tool_part.function_response is not None
    assert tool_part.function_response.name == "add_node"
