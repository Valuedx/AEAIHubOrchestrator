"""COPILOT-01b.i — the agent runner.

Takes a user message, drives an LLM through the copilot tool surface,
persists each turn to ``copilot_turns``, and yields a stream of
events the frontend chat pane can render.

Lifecycle of a single ``send_turn`` call
----------------------------------------

::

    user msg  ─▶ persist user turn ─▶ build messages from history +
                                     system prompt + tool defs
                                     │
                                     ▼
                              ┌──────────────┐
                     ┌───────▶│ LLM provider │ (anthropic today;
                     │        └──────┬───────┘  openai/google in 01b.iv)
                     │               │
                     │    text+tool_use blocks
                     │               │
                     │        ┌──────▼───────┐
                     │        │ assistant    │ persist
                     │        │ turn         │ yield assistant_text event
                     │        └──────┬───────┘
                     │               │
                     │       tool_use blocks? ──── no ──▶  done
                     │               │ yes
                     │               ▼
                     │        for each tool_use:
                     │          - yield tool_call event
                     │          - dispatch via tool_layer
                     │          - persist tool turn
                     │          - if mutation: bump draft.version
                     │          - yield tool_result event
                     │               │
                     └───────────────┘ feed tool_results back

The loop caps at ``MAX_TOOL_ITERATIONS`` iterations per turn so a
pathological "flap" (LLM keeps calling tools forever) can't burn
unbounded cost. Today's cap is 12 — generous for a normal build-this-
workflow turn, tight enough to catch runaway loops.

Event shapes
------------

Every event is a JSON object with a ``type`` discriminator. Frontend
renders them; see ``codewiki/copilot.md`` §6 for the shape contract.

::

    {"type": "assistant_text", "text": "..."}
    {"type": "tool_call", "id": "...", "name": "...", "args": {...}}
    {"type": "tool_result", "id": "...", "name": "...", "result": {...},
     "validation": {...} | null, "draft_version": N}
    {"type": "error", "message": "...", "recoverable": bool}
    {"type": "done", "turns_added": [uuid, ...], "final_text": "..."}
"""

from __future__ import annotations

import json
import logging
import uuid
import base64
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.copilot import tool_layer
from app.copilot.prompts import build_system_prompt, build_system_prompt_split
from app.copilot.tool_definitions import (
    COPILOT_TOOL_DEFINITIONS,
    to_anthropic_tools,
    to_google_tools,
)
from app.models.copilot import CopilotSession, CopilotTurn, WorkflowDraft

logger = logging.getLogger(__name__)


MAX_TOOL_ITERATIONS = 12
"""Max number of LLM → tool → LLM round-trips per user turn.

This is a stop-the-flap cap, not a target. A normal build-a-workflow
turn uses 3–6 iterations (list_node_types → get_node_schema →
add_node × N → connect_nodes × M → validate_graph).
"""

from app.engine.model_registry import (
    LLM_TIER_DEFAULTS,
    default_llm_for,
    find_llm_model,
    is_allowed_llm,
)

# Copilot default model per provider. Sourced from the central model
# registry's ``copilot`` tier so swapping the agentic-tools endpoint
# (e.g. when Google promotes Gemini 3.x Pro out of preview) is a
# one-line edit in ``model_registry.py`` — every caller that reads
# this dict picks up the change automatically.
#
# * google / vertex default to ``gemini-3.1-pro-preview-customtools``
#   (the agentic-tool-calling-optimised variant; see
#   https://ai.google.dev/gemini-api/docs/gemini-3 ).
# * anthropic defaults to ``claude-sonnet-4-6``.
# * openai is absent today — the copilot OpenAI adapter lands in
#   COPILOT-01b.iv. Adding it means populating ``LLM_TIER_DEFAULTS["copilot"]["openai"]``
#   in the registry plus the three adapter functions here.
DEFAULT_MODEL_BY_PROVIDER: dict[str, str] = dict(LLM_TIER_DEFAULTS["copilot"])


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AgentRunnerError(RuntimeError):
    """Base class; message goes back to the caller as an error event."""


# ---------------------------------------------------------------------------
# Auto-heal loop cap (COPILOT-03.d)
# ---------------------------------------------------------------------------

# Hard per-turn ceiling on ``suggest_fix`` invocations so a flapping
# loop (fix → new failure → new fix → ...) can't burn the token
# budget. Three tries are enough for a real config issue and cheap
# enough that a bad agent doesn't rack up cost; past three, the
# runner forces a hand-off so the user can take over. The separate
# per-draft cap in ``runner_tools.MAX_SUGGEST_FIX_PER_DRAFT`` (5)
# bounds lifetime usage across turns; this cap bounds per-turn
# burst.
MAX_SUGGEST_FIX_PER_TURN = 3


class UnsupportedProviderError(AgentRunnerError):
    """Raised when session.provider isn't wired up yet.

    01b.i ships Anthropic only. OpenAI + Google follow in 01b.iv —
    same session API, no frontend changes needed.
    """


# ---------------------------------------------------------------------------
# Session + turn persistence helpers
# ---------------------------------------------------------------------------


def _persist_turn(
    db: Session,
    *,
    tenant_id: str,
    session: CopilotSession,
    role: str,
    content: dict[str, Any],
    tool_calls: list[dict[str, Any]] | None = None,
    token_usage: dict[str, Any] | None = None,
) -> CopilotTurn:
    """Append one turn to copilot_turns. Caller owns the commit."""
    # Next turn_index = count of existing turns in this session.
    # We deliberately don't use a counter on CopilotSession because
    # race-free COUNT(*) off the index is simpler than maintaining a
    # separate running total.
    next_idx = (
        db.query(CopilotTurn)
        .filter_by(tenant_id=tenant_id, session_id=session.id)
        .count()
    )
    turn = CopilotTurn(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        session_id=session.id,
        turn_index=next_idx,
        role=role,
        content_json=content,
        tool_calls_json=tool_calls,
        token_usage_json=token_usage,
    )
    db.add(turn)
    return turn


# ---------------------------------------------------------------------------
# Message-history reconstruction
# ---------------------------------------------------------------------------


@dataclass
class _BuiltMessages:
    """The shape the Anthropic SDK wants. Each provider will have its
    own materialiser; today we only ship Anthropic.

    COPILOT-V2 — ``system_static`` / ``system_dynamic`` carry the split
    system prompt for prefix caching. ``system`` (concatenation) stays
    populated for any caller that doesn't care about caching."""

    system: str
    messages: list[dict[str, Any]]
    system_static: str = ""
    system_dynamic: str = ""


def _build_anthropic_messages(
    db: Session,
    *,
    tenant_id: str,
    session: CopilotSession,
    draft: WorkflowDraft,
    new_user_text: str,
) -> _BuiltMessages:
    """Assemble the system prompt + chat history + the new user turn
    into Anthropic's message-list shape.

    History is reconstructed from ``copilot_turns`` rather than kept
    in-memory across requests — a fresh agent runner per HTTP call
    lets horizontal scaling work without sticky sessions.
    """
    system_static, system_dynamic = build_system_prompt_split(
        draft_snapshot=draft.graph_json or {},
    )
    system = system_static + "\n\n" + system_dynamic

    # Load turns in chronological order.
    prior_turns = (
        db.query(CopilotTurn)
        .filter_by(tenant_id=tenant_id, session_id=session.id)
        .order_by(CopilotTurn.turn_index)
        .all()
    )

    messages: list[dict[str, Any]] = []
    pending_tool_use_for_role: list[dict[str, Any]] = []

    for turn in prior_turns:
        if turn.role == "user":
            text = (turn.content_json or {}).get("text", "")
            messages.append({"role": "user", "content": text})
        elif turn.role == "assistant":
            # Assistant turns may have both text and tool_use blocks.
            # We rebuild the block list so Anthropic sees the exact
            # shape of the previous turn.
            content = (turn.content_json or {}).get("blocks")
            if content is None:
                text = (turn.content_json or {}).get("text", "")
                content = [{"type": "text", "text": text}] if text else []
            messages.append({"role": "assistant", "content": content})
            # Any tool_use blocks in this assistant turn need matching
            # tool_result blocks in the NEXT user message.
            pending_tool_use_for_role = [
                b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"
            ]
        elif turn.role == "tool":
            # Tool turns are stored one-per-tool-call for readability.
            # Anthropic expects them grouped as a single user message
            # whose content is the list of tool_result blocks matching
            # the preceding assistant's tool_use blocks.
            tr = turn.content_json or {}
            tool_result_block = {
                "type": "tool_result",
                "tool_use_id": tr.get("tool_use_id"),
                "content": json.dumps(tr.get("result", {}), default=str),
            }
            # If the previous message is already a user-role
            # tool_result aggregate, append there; otherwise start
            # a fresh one.
            if (
                messages
                and messages[-1]["role"] == "user"
                and isinstance(messages[-1]["content"], list)
                and messages[-1]["content"]
                and isinstance(messages[-1]["content"][0], dict)
                and messages[-1]["content"][0].get("type") == "tool_result"
            ):
                messages[-1]["content"].append(tool_result_block)
            else:
                messages.append({"role": "user", "content": [tool_result_block]})

    # Finally, the new user message that just arrived.
    messages.append({"role": "user", "content": new_user_text})

    return _BuiltMessages(
        system=system,
        messages=messages,
        system_static=system_static,
        system_dynamic=system_dynamic,
    )


# ---------------------------------------------------------------------------
# Anthropic provider adapter
# ---------------------------------------------------------------------------


def _call_anthropic(
    *,
    model: str,
    built: _BuiltMessages,
    tenant_id: str,
    max_tokens: int = 4096,
    temperature: float = 0.2,
) -> dict[str, Any]:
    """One round-trip to Anthropic. Returns a structured result:

    ::

        {
          "text_blocks": [...],
          "tool_use_blocks": [{id, name, input}, ...],
          "raw_content": <sdk block list — replay back to next turn>,
          "usage": {input_tokens, output_tokens},
          "stop_reason": "end_turn" | "tool_use" | ...,
        }
    """
    from anthropic import Anthropic  # lazy import — keeps module optional in tests

    from app.engine.llm_credentials_resolver import get_anthropic_api_key

    client = Anthropic(api_key=get_anthropic_api_key(tenant_id))

    # COPILOT-V2 — when split system blocks are available, send them
    # as two text blocks so we can mark the static prefix cacheable
    # via cache_control. Anthropic's prompt cache hits when the
    # prefix is byte-stable across requests; the dynamic draft
    # snapshot stays uncached because it changes after every
    # mutation.
    if built.system_static and built.system_dynamic:
        system_param: Any = [
            {
                "type": "text",
                "text": built.system_static,
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": built.system_dynamic,
            },
        ]
    else:
        system_param = built.system

    resp = client.messages.create(
        model=model,
        system=system_param,
        messages=built.messages,
        tools=to_anthropic_tools(),
        max_tokens=max_tokens,
        temperature=temperature,
    )

    text_blocks: list[str] = []
    tool_uses: list[dict[str, Any]] = []
    # ``resp.content`` is a list of block objects. We also keep the
    # raw list around because Anthropic wants us to replay it as the
    # assistant turn's content on the next call (tool_use_id pairing
    # only works when the exact blocks come back).
    raw_content = []
    for block in resp.content:
        as_dict = _block_to_dict(block)
        raw_content.append(as_dict)
        if as_dict.get("type") == "text":
            text_blocks.append(as_dict.get("text", ""))
        elif as_dict.get("type") == "tool_use":
            tool_uses.append({
                "id": as_dict["id"],
                "name": as_dict["name"],
                "input": as_dict.get("input", {}),
            })

    return {
        "text": "".join(text_blocks).strip(),
        "tool_uses": tool_uses,
        "raw_content": raw_content,
        "usage": {
            "input_tokens": getattr(resp.usage, "input_tokens", 0),
            "output_tokens": getattr(resp.usage, "output_tokens", 0),
        },
        "stop_reason": getattr(resp, "stop_reason", None),
    }


def _block_to_dict(block: Any) -> dict[str, Any]:
    """Coerce a SDK block object into a JSON-safe dict we can persist
    and replay. The SDK objects have ``.model_dump()`` under the hood;
    we also support hand-built dicts from tests."""
    if isinstance(block, dict):
        return dict(block)
    if hasattr(block, "model_dump"):
        return block.model_dump()
    # Last-resort: marshal known fields by hand.
    out = {"type": getattr(block, "type", "unknown")}
    for attr in ("text", "id", "name", "input"):
        if hasattr(block, attr):
            out[attr] = getattr(block, attr)
    return out


def _append_anthropic_tool_round(
    state: _BuiltMessages,
    response: dict[str, Any],
    tool_result_payloads: list[dict[str, Any]],
) -> _BuiltMessages:
    """Append one tool round to Anthropic's message list shape.

    ``tool_result_payloads`` is the normalised-per-call list the runner
    built: ``[{tool_use_id, result, is_error}, ...]``. Anthropic wants
    these as a single user-role message with a list of ``tool_result``
    content blocks, preceded by the assistant's raw_content (which
    includes both text and tool_use blocks from the previous turn).
    """
    state.messages.append(
        {"role": "assistant", "content": response["raw_content"]}
    )
    state.messages.append({
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": t["tool_use_id"],
                "content": json.dumps(t["result"], default=str),
                "is_error": bool(t.get("is_error")),
            }
            for t in tool_result_payloads
        ],
    })
    return state


# ---------------------------------------------------------------------------
# Google / Vertex adapter
#
# Both providers go through the unified ``google-genai`` SDK — they
# share every byte of wire format, request shape, and response parsing.
# Only the ``genai.Client`` constructor differs (AI Studio = api_key;
# Vertex = vertexai=True + project + location via VERTEX-02's
# per-tenant target resolver). See llm_providers._google_client.
# ---------------------------------------------------------------------------


@dataclass
class _GoogleState:
    """State for the Google adapter. Mirrors the ReAct-loop pattern:
    ``history`` is a list of ``types.Content(role, parts)`` objects
    already assembled from prior turns; we append to it as the tool
    loop progresses."""

    system: str
    history: list[Any]  # list[types.Content] — kept as Any so tests
                        # that don't import google.genai still work.
    _backend: str       # "genai" (AI Studio) or "vertex" — for client
                        # construction. Underscore because it's an
                        # internal adapter concern, not LLM state.


def _build_google_state(
    db: Session,
    *,
    tenant_id: str,
    session: CopilotSession,
    draft: WorkflowDraft,
    new_user_text: str,
    backend: str,
) -> _GoogleState:
    """Replay prior copilot_turns into Google's ``types.Content`` list.

    Mapping from our normalised turn storage to Google's shape:

      user turn             → Content(role="user", parts=[Part.from_text])
      assistant turn        → Content(role="model", parts=[text Part +
                                       function_call Parts...])
      tool turn (one row)   → Part.from_function_response in a user-role
                              Content; consecutive tool turns merge.

    The new user message is NOT added to history here — the adapter's
    call() prepends it at request time, mirroring ReAct's
    ``state['user_message']`` pattern.
    """
    from google.genai import types

    # COPILOT-V2 — split + concat. Google's SDK takes one
    # system_instruction string; implicit caching kicks in when the
    # prefix is byte-stable. Building from the split helper is
    # functionally identical to build_system_prompt() but keeps the
    # caching hint shape (static first, dynamic second) explicit.
    system_static, system_dynamic = build_system_prompt_split(
        draft_snapshot=draft.graph_json or {},
    )
    system = system_static + "\n\n" + system_dynamic

    prior_turns = (
        db.query(CopilotTurn)
        .filter_by(tenant_id=tenant_id, session_id=session.id)
        .order_by(CopilotTurn.turn_index)
        .all()
    )

    history: list[Any] = []
    pending_responses: list[Any] = []  # accumulate consecutive tool turns

    def _flush_pending_responses() -> None:
        if pending_responses:
            history.append(types.Content(role="user", parts=list(pending_responses)))
            pending_responses.clear()

    for turn in prior_turns:
        if turn.role == "user":
            _flush_pending_responses()
            text = (turn.content_json or {}).get("text", "")
            history.append(types.Content(
                role="user",
                parts=[types.Part.from_text(text=text)],
            ))
        elif turn.role == "assistant":
            _flush_pending_responses()
            content = turn.content_json or {}
            parts: list[Any] = []

            # GEMINI-3: Prioritise raw blocks (raw_content) for perfect fidelity
            if content.get("blocks"):
                for b in content["blocks"]:
                    parts.append(types.Part.model_validate(b))
            else:
                # Fallback for older turns or those saved without raw blocks
                # Replay thought parts if present (from the first fix attempt)
                for tp in content.get("thought_parts", []):
                    sig = tp.get("thought_signature")
                    if isinstance(sig, str):
                        sig = base64.b64decode(sig)
                    parts.append(types.Part(
                        thought=tp.get("thought"),
                        thought_signature=sig
                    ))

                if content.get("text"):
                    parts.append(types.Part.from_text(text=content["text"]))
                
                tcs = turn.tool_calls_json or content.get("tool_calls") or []
                for tc in tcs:
                    parts.append(types.Part.from_function_call(
                        name=tc["name"],
                        args=tc.get("input") or tc.get("args") or {},
                    ))

            if parts:
                history.append(types.Content(role="model", parts=parts))
        elif turn.role == "tool":
            tr = turn.content_json or {}
            result = tr.get("result", {})
            # Google wants response as a dict.
            response_dict = result if isinstance(result, dict) else {"result": result}
            pending_responses.append(types.Part.from_function_response(
                name=tr.get("name", ""),
                response=response_dict,
            ))

    _flush_pending_responses()

    return _GoogleState(system=system, history=history, _backend=backend)


def _call_google(
    *,
    model: str,
    state: _GoogleState,
    tenant_id: str,
    new_user_text: str | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.2,
) -> dict[str, Any]:
    """One round-trip to Google AI Studio / Vertex AI. Returns the same
    normalised shape as ``_call_anthropic`` so the runner loop doesn't
    branch on provider.

    ``new_user_text`` is the just-submitted user message that has NOT
    yet been persisted into ``state.history``. On tool-result rounds
    it's ``None`` — the tool results already live in state.history
    (appended by ``_append_google_tool_round``).
    """
    from google.genai import types

    from app.engine.llm_providers import _google_client
    client = _google_client(state._backend, tenant_id=tenant_id)
    contents = list(state.history)
    if new_user_text:
        contents.append(types.Content(
            role="user",
            parts=[types.Part.from_text(text=new_user_text)],
        ))

    config = types.GenerateContentConfig(
        system_instruction=state.system or None,
        temperature=temperature,
        max_output_tokens=max_tokens,
        tools=to_google_tools(),
    )

    resp = client.models.generate_content(
        model=model, contents=contents, config=config,
    )

    text_blocks: list[str] = []
    tool_uses: list[dict[str, Any]] = []
    thought_parts: list[dict[str, Any]] = []
    raw_content: list[dict[str, Any]] = []

    if resp.candidates and resp.candidates[0].content:
        for part in resp.candidates[0].content.parts:
            # Capture for persistence (JSON-safe via model_dump)
            raw_content.append(part.model_dump(mode="json"))

            if getattr(part, "thought", None) or getattr(part, "thought_signature", None):
                # Capture thought for replay (required by Gemini 3)
                sig = getattr(part, "thought_signature", None)
                thought_parts.append({
                    "thought": getattr(part, "thought", None),
                    "thought_signature": base64.b64encode(sig).decode("utf-8") if sig else None
                })
            
            if getattr(part, "function_call", None):
                fc = part.function_call
                tool_uses.append({
                    "id": f"gfn_{fc.name}_{len(tool_uses)}",
                    "name": fc.name,
                    "input": dict(fc.args) if fc.args else {},
                })
            elif getattr(part, "text", None):
                text_blocks.append(part.text)

    usage = getattr(resp, "usage_metadata", None)
    return {
        "text": "".join(text_blocks).strip(),
        "tool_uses": tool_uses,
        "thought_parts": thought_parts,
        # raw_content is used to preserve perfect fidelity for Gemini 3
        "raw_content": raw_content,
        "usage": {
            "input_tokens": getattr(usage, "prompt_token_count", 0) if usage else 0,
            "output_tokens": getattr(usage, "candidates_token_count", 0) if usage else 0,
        },
        "stop_reason": getattr(resp, "finish_reason", None),
    }


def _append_google_tool_round(
    state: _GoogleState,
    response: dict[str, Any],
    tool_result_payloads: list[dict[str, Any]],
) -> _GoogleState:
    """Append the assistant's model-role turn + the batched
    function_response user turn to state.history, mirroring ReAct's
    ``_google_append``. The next ``_call_google`` has ``new_user_text``
    set to None since history already carries everything."""
    from google.genai import types

    assistant_parts: list[Any] = []
    
    if response.get("raw_content"):
        # GEMINI-3: Prefer raw blocks if present
        for b in response["raw_content"]:
            assistant_parts.append(types.Part.model_validate(b))
    else:
        # GEMINI-3 Fallback: Replay thought parts FIRST if present
        for tp in response.get("thought_parts", []):
            sig = tp.get("thought_signature")
            if isinstance(sig, str):
                sig = base64.b64decode(sig)
            assistant_parts.append(types.Part(
                thought=tp.get("thought"),
                thought_signature=sig
            ))

        if response["text"]:
            assistant_parts.append(types.Part.from_text(text=response["text"]))
        for tc in response["tool_uses"]:
            assistant_parts.append(types.Part.from_function_call(
                name=tc["name"],
                args=tc["input"],
            ))
    if assistant_parts:
        state.history.append(types.Content(role="model", parts=assistant_parts))

    response_parts: list[Any] = []
    for t in tool_result_payloads:
        result = t["result"]
        response_dict = result if isinstance(result, dict) else {"result": result}
        response_parts.append(types.Part.from_function_response(
            name=t["name"],
            response=response_dict,
        ))
    if response_parts:
        state.history.append(types.Content(role="user", parts=response_parts))

    return state


# ---------------------------------------------------------------------------
# Provider adapter registry
#
# Each adapter bundles three callables. The runner loop is agnostic to
# which provider is in play — only the state object's shape differs,
# and it's opaque to the loop.
# ---------------------------------------------------------------------------


def _build_anthropic_state(
    db: Session,
    *,
    tenant_id: str,
    session: CopilotSession,
    draft: WorkflowDraft,
    new_user_text: str,
) -> _BuiltMessages:
    """Alias of ``_build_anthropic_messages`` — kept as a named export
    so the provider registry at the bottom of the module stays uniform
    across providers."""
    return _build_anthropic_messages(
        db,
        tenant_id=tenant_id,
        session=session,
        draft=draft,
        new_user_text=new_user_text,
    )


def _call_anthropic_adapter(
    *,
    model: str,
    state: _BuiltMessages,
    tenant_id: str,
    new_user_text: str | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.2,
) -> dict[str, Any]:
    """Normalised signature matching ``_call_google``. The adapter
    ignores ``new_user_text`` on tool-result rounds — the Anthropic
    state already carries the full message list."""
    return _call_anthropic(
        model=model,
        built=state,
        tenant_id=tenant_id,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def _call_google_adapter(**kwargs: Any) -> dict[str, Any]:
    """Thin wrapper so tests can patch ``_call_google`` by name — the
    adapter registry binds this wrapper at import time, but the wrapper
    dispatches by module lookup at call time. Same pattern as
    ``_call_anthropic_adapter`` → ``_call_anthropic``."""
    return _call_google(**kwargs)


_PROVIDER_ADAPTERS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "build_state": _build_anthropic_state,
        "call": _call_anthropic_adapter,
        "append_tool_round": _append_anthropic_tool_round,
        "initial_user_goes_in_state": True,  # message list already
                                             # contains the new user turn
    },
    "google": {
        "build_state": lambda **kw: _build_google_state(**kw, backend="genai"),
        "call": _call_google_adapter,
        "append_tool_round": _append_google_tool_round,
        "initial_user_goes_in_state": False,  # call() takes new_user_text
                                              # as a kwarg on first iter
    },
    "vertex": {
        "build_state": lambda **kw: _build_google_state(**kw, backend="vertex"),
        "call": _call_google_adapter,
        "append_tool_round": _append_google_tool_round,
        "initial_user_goes_in_state": False,
    },
}


# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------


class AgentRunner:
    """One ``send_turn`` call per user message. The runner is cheap to
    construct; callers spin up a fresh one for each HTTP request so
    horizontal scaling does not need sticky sessions."""

    def __init__(
        self,
        db: Session,
        *,
        tenant_id: str,
        session: CopilotSession,
        draft: WorkflowDraft,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.session = session
        self.draft = draft
        # COPILOT-03.d — per-turn counter for auto-heal suggest_fix
        # calls. Reset at the start of each send_turn. The agent-
        # dispatch layer short-circuits further suggest_fix calls
        # once this hits MAX_SUGGEST_FIX_PER_TURN.
        self._suggest_fix_count = 0

    # -- public entry point ------------------------------------------------

    def send_turn(self, user_text: str) -> Iterator[dict[str, Any]]:
        """Drive the LLM loop for one user message. Yields events.

        Every event is a dict ready to be JSON-encoded and sent over
        SSE. The caller owns the DB transaction boundary — we flush
        but do not commit so the HTTP layer can decide on atomicity
        vs. stream-then-commit.

        Provider-agnostic: the adapter registered in ``_PROVIDER_ADAPTERS``
        for ``session.provider`` owns state shape, LLM round-trip, and
        message-history reconstruction. All three providers that ship in
        01b.iv (anthropic / google / vertex) use this same loop.
        """
        adapter = _PROVIDER_ADAPTERS.get(self.session.provider)
        if adapter is None:
            raise UnsupportedProviderError(
                f"Provider '{self.session.provider}' is not supported. "
                f"Known providers: {sorted(_PROVIDER_ADAPTERS.keys())}."
            )

        # COPILOT-03.d — reset the per-turn auto-heal counter. Each
        # user turn gets a fresh budget of MAX_SUGGEST_FIX_PER_TURN
        # suggest_fix calls.
        self._suggest_fix_count = 0

        # 1. Persist the user turn FIRST so if anything else blows up,
        #    the history is still consistent.
        user_turn = _persist_turn(
            self.db,
            tenant_id=self.tenant_id,
            session=self.session,
            role="user",
            content={"text": user_text},
        )
        self.db.flush()
        turns_added: list[str] = [str(user_turn.id)]

        # 2. Build the provider-specific initial state from prior turns.
        state = adapter["build_state"](
            db=self.db,
            tenant_id=self.tenant_id,
            session=self.session,
            draft=self.draft,
            new_user_text=user_text,
        )

        # Anthropic state already contains the new user message in its
        # `messages` list (see _build_anthropic_messages). Google state
        # doesn't — the adapter's call() takes new_user_text as a kwarg
        # on the first iteration only.
        pending_new_user = (
            None
            if adapter["initial_user_goes_in_state"]
            else user_text
        )

        final_text = ""

        for iteration in range(MAX_TOOL_ITERATIONS):
            # 3. Call the LLM.
            try:
                response = adapter["call"](
                    model=self.session.model,
                    state=state,
                    tenant_id=self.tenant_id,
                    new_user_text=pending_new_user,
                )
            except Exception as exc:  # pragma: no cover — external dep
                logger.exception(
                    "Agent LLM call failed (session=%s, iter=%d, provider=%s)",
                    self.session.id, iteration, self.session.provider,
                )
                yield {
                    "type": "error",
                    "message": f"LLM call failed: {exc}",
                    "recoverable": False,
                }
                return
            pending_new_user = None  # consumed on first iteration

            # 4. Persist assistant turn (always — tool_use blocks AND
            #    text are part of the same turn in the protocol).
            assistant_turn = _persist_turn(
                self.db,
                tenant_id=self.tenant_id,
                session=self.session,
                role="assistant",
                content={
                    "text": response["text"],
                    # raw_content is only populated by Anthropic; for
                    # Google it stays [] and history reconstruction
                    # falls back to normalised tool_calls.
                    "blocks": response.get("raw_content") or [],
                    # GEMINI-3: Persist thought parts for history reconstruction
                    "thought_parts": response.get("thought_parts") or [],
                },
                tool_calls=response["tool_uses"] or None,
                token_usage=response["usage"],
            )
            self.db.flush()
            turns_added.append(str(assistant_turn.id))

            if response["text"]:
                yield {"type": "assistant_text", "text": response["text"]}

            # 5. If there are no tool calls, we're done.
            if not response["tool_uses"]:
                final_text = response["text"]
                break

            # 6. Dispatch each tool call, persist a tool turn per call,
            #    and build the normalised tool_result payloads the
            #    adapter will translate into its provider-specific shape.
            tool_result_payloads: list[dict[str, Any]] = []
            for call in response["tool_uses"]:
                yield {
                    "type": "tool_call",
                    "id": call["id"],
                    "name": call["name"],
                    "args": call["input"],
                }

                result_payload, validation_out, draft_version, dispatch_error = (
                    self._dispatch_tool(call["name"], call["input"])
                )

                tool_turn_content: dict[str, Any] = {
                    "tool_use_id": call["id"],
                    "name": call["name"],
                    "args": call["input"],
                    "result": result_payload,
                }
                if dispatch_error:
                    tool_turn_content["error"] = dispatch_error

                _persist_turn(
                    self.db,
                    tenant_id=self.tenant_id,
                    session=self.session,
                    role="tool",
                    content=tool_turn_content,
                )
                self.db.flush()

                tool_result_payloads.append({
                    "tool_use_id": call["id"],
                    "name": call["name"],
                    "result": result_payload,
                    "is_error": bool(dispatch_error),
                })

                yield {
                    "type": "tool_result",
                    "id": call["id"],
                    "name": call["name"],
                    "result": result_payload,
                    "validation": validation_out,
                    "draft_version": draft_version,
                    "error": dispatch_error,
                }

            # 7. Fold the assistant turn + the tool results into the
            #    adapter's state so the next iteration's call() sees
            #    them as prior context.
            state = adapter["append_tool_round"](
                state, response, tool_result_payloads,
            )
        else:
            # for/else: fell through without break → hit MAX_TOOL_ITERATIONS.
            logger.warning(
                "Agent hit MAX_TOOL_ITERATIONS (%d) in session %s",
                MAX_TOOL_ITERATIONS, self.session.id,
            )
            yield {
                "type": "error",
                "message": (
                    f"Agent exceeded {MAX_TOOL_ITERATIONS} tool iterations "
                    "without settling on a final reply. Stopping to avoid "
                    "runaway cost. Try rephrasing or breaking the request "
                    "into smaller pieces."
                ),
                "recoverable": True,
            }

        yield {
            "type": "done",
            "turns_added": turns_added,
            "final_text": final_text,
        }

    # -- tool dispatch -----------------------------------------------------

    def _dispatch_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None, int, str | None]:
        """Dispatch one tool call against the current draft.

        Returns ``(result_payload, validation_or_None, draft_version, error_or_None)``.
        Errors are returned as strings so the LLM can read them back
        and correct — we do NOT raise. The ``is_error`` flag on the
        tool_result tells the model the call failed.

        Routes between two tool families:

        * **Pure tools** (``tool_layer.TOOL_NAMES``) — graph dict in,
          graph dict out; mutations persist here.
        * **Runner tools** (``runner_tools.RUNNER_TOOL_NAMES``) —
          stateful; touch the engine (node handlers, credential
          resolution, MCP calls). No automatic graph mutation.
        """
        from app.copilot import runner_tools

        if tool_name in runner_tools.RUNNER_TOOL_NAMES:
            return self._dispatch_runner_tool(tool_name, args)

        if tool_name not in tool_layer.TOOL_NAMES:
            return (
                {"error": f"Unknown tool '{tool_name}'"},
                None,
                self.draft.version,
                f"unknown tool '{tool_name}'",
            )

        try:
            new_graph, result = tool_layer.dispatch(
                tool_name, self.draft.graph_json or {}, args or {},
            )
        except tool_layer.ToolLayerError as exc:
            # Tool-layer errors are expected failure modes (bad args,
            # missing node, etc.). Bubble the message to the LLM so
            # it can self-correct.
            return ({"error": str(exc)}, None, self.draft.version, str(exc))
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("Unexpected tool dispatch failure: %s", tool_name)
            return (
                {"error": f"Internal error in {tool_name}: {exc}"},
                None,
                self.draft.version,
                str(exc),
            )

        if new_graph is not None:
            # Mutation tool — persist the new graph + bump version.
            self.draft.graph_json = new_graph
            self.draft.version += 1
            validation = tool_layer.validate_graph(new_graph)
            self.db.flush()
            return (result, validation, self.draft.version, None)

        return (result, None, self.draft.version, None)

    def _dispatch_runner_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None, int, str | None]:
        """Dispatch a stateful runner tool. Runner tools don't mutate
        the graph (at least in 01b.ii.a — ``test_node`` only), so
        ``validation`` is always None and ``draft_version`` is unchanged.

        Runner-tool exceptions bubble up the same way pure-tool
        ones do: caught, logged, returned as
        ``{"error": "..."}`` so the LLM sees the failure and
        self-corrects. ``runner_tools.test_node_against_draft``
        already catches handler exceptions internally — anything
        that escapes here is a bug in the tool, not the handler.
        """
        from app.copilot import runner_tools

        # COPILOT-03.d — auto-heal per-turn cap. suggest_fix is the
        # chokepoint in the {execute_draft fail → get_node_error →
        # suggest_fix → update_node_config → execute_draft} heal loop;
        # capping it caps the whole loop without needing to detect the
        # pattern. The per-draft cap inside suggest_fix itself bounds
        # lifetime usage; this one bounds single-turn burst so a
        # degenerate flap doesn't spend the user's budget in silence.
        if tool_name == "suggest_fix":
            if self._suggest_fix_count >= MAX_SUGGEST_FIX_PER_TURN:
                msg = (
                    f"Auto-heal cap reached for this turn "
                    f"({self._suggest_fix_count}/{MAX_SUGGEST_FIX_PER_TURN} "
                    "suggest_fix calls). Hand off to the user — "
                    "too many fix attempts in one turn usually means "
                    "the failure is structural and the user should "
                    "take over."
                )
                return (
                    {
                        "error": msg,
                        "auto_heal_count": self._suggest_fix_count,
                        "auto_heal_cap": MAX_SUGGEST_FIX_PER_TURN,
                    },
                    None,
                    self.draft.version,
                    msg,
                )
            self._suggest_fix_count += 1

        try:
            result = runner_tools.dispatch(
                tool_name,
                db=self.db,
                tenant_id=self.tenant_id,
                draft=self.draft,
                args=args or {},
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception(
                "Unexpected runner-tool dispatch failure: %s", tool_name,
            )
            return (
                {"error": f"Internal error in {tool_name}: {exc}"},
                None,
                self.draft.version,
                str(exc),
            )

        # Runner-tool convention: top-level ``"error"`` in the result
        # means the tool ran but the operation failed (e.g. a handler
        # raised). Surface that as the dispatch error so the LLM
        # sees ``is_error=True`` and reads the message.
        tool_error = result.get("error") if isinstance(result, dict) else None
        return (result, None, self.draft.version, tool_error)


# ---------------------------------------------------------------------------
# Helpers the API layer calls
# ---------------------------------------------------------------------------


def default_model_for(provider: str) -> str:
    """Resolve the copilot default model for ``provider``.

    Delegates to :func:`app.engine.model_registry.default_llm_for` with
    ``role="copilot"`` so both call sites share a single lookup path.
    """
    try:
        return default_llm_for(provider, role="copilot")
    except Exception as exc:  # UnknownModelError from the registry.
        raise UnsupportedProviderError(
            f"No copilot-capable default model configured for provider '{provider}'"
        ) from exc


def supported_providers() -> list[str]:
    """For the frontend session-create dropdown."""
    return sorted(DEFAULT_MODEL_BY_PROVIDER.keys())


def validate_session_model(
    provider: str, model: str, *, tenant_id: str | None = None
) -> None:
    """Reject session-create with an unknown / disallowed model.

    Copilot sessions may pick any registry entry whose ``copilot_ok``
    flag is set — that excludes ``lite`` tier Gemini 3.x / 2.5
    variants (no ``supports_thinking``).

    MODEL-01.e: when ``tenant_id`` is provided, the tenant's
    ``allowed_model_families`` list is consulted — e.g. a tenant
    pinned to ``["2.5"]`` gets 3.x rejected at session-create.
    Preview-gate similarly follows tenant policy (future knob);
    today preview is allowed unless the family allowlist excludes 3.x.
    """
    allowed_families: list[str] | None = None
    if tenant_id:
        try:
            from app.engine.tenant_policy_resolver import get_effective_policy

            policy = get_effective_policy(tenant_id)
            allowed_families = policy.allowed_model_families
        except Exception:  # pragma: no cover — defensive
            allowed_families = None

    if not is_allowed_llm(provider, model, allowed_families=allowed_families):
        if allowed_families:
            raise UnsupportedProviderError(
                f"Model {model!r} is blocked by tenant policy "
                f"(allowed families: {allowed_families})."
            )
        raise UnsupportedProviderError(
            f"Model {model!r} is not available for provider {provider!r}. "
            f"Pick a model listed in /api/v1/copilot/sessions/providers."
        )
    entry = find_llm_model(provider, model)
    if entry is not None and not entry.copilot_ok:
        raise UnsupportedProviderError(
            f"Model {model!r} is not copilot-capable (typically a lite "
            f"variant without thinking support). Pick a Flash or Pro tier."
        )


def declared_tools() -> list[dict[str, Any]]:
    """Expose the raw tool definitions so the frontend can render a
    developer-facing "here are the tools the agent has" drawer if it
    wants. Not load-bearing for COPILOT-02."""
    return [dict(t) for t in COPILOT_TOOL_DEFINITIONS]
