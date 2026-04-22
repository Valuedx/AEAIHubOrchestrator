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
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.copilot import tool_layer
from app.copilot.prompts import build_system_prompt
from app.copilot.tool_definitions import COPILOT_TOOL_DEFINITIONS, to_anthropic_tools
from app.models.copilot import CopilotSession, CopilotTurn, WorkflowDraft

logger = logging.getLogger(__name__)


MAX_TOOL_ITERATIONS = 12
"""Max number of LLM → tool → LLM round-trips per user turn.

This is a stop-the-flap cap, not a target. A normal build-a-workflow
turn uses 3–6 iterations (list_node_types → get_node_schema →
add_node × N → connect_nodes × M → validate_graph).
"""

DEFAULT_MODEL_BY_PROVIDER: dict[str, str] = {
    "anthropic": "claude-sonnet-4-6",
    # openai + google land in 01b.iv
}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AgentRunnerError(RuntimeError):
    """Base class; message goes back to the caller as an error event."""


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
    own materialiser; today we only ship Anthropic."""

    system: str
    messages: list[dict[str, Any]]


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
    system = build_system_prompt(draft_snapshot=draft.graph_json or {})

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

    return _BuiltMessages(system=system, messages=messages)


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
    resp = client.messages.create(
        model=model,
        system=built.system,
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

    # -- public entry point ------------------------------------------------

    def send_turn(self, user_text: str) -> Iterator[dict[str, Any]]:
        """Drive the LLM loop for one user message. Yields events.

        Every event is a dict ready to be JSON-encoded and sent over
        SSE. The caller owns the DB transaction boundary — we flush
        but do not commit so the HTTP layer can decide on atomicity
        vs. stream-then-commit.
        """
        if self.session.provider != "anthropic":
            raise UnsupportedProviderError(
                f"Provider '{self.session.provider}' not wired up yet. "
                "COPILOT-01b.i ships Anthropic only; OpenAI / Google "
                "land in 01b.iv."
            )

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

        # 2. Build the Anthropic message list from prior history + new
        #    user message.
        built = _build_anthropic_messages(
            self.db,
            tenant_id=self.tenant_id,
            session=self.session,
            draft=self.draft,
            new_user_text=user_text,
        )

        final_text = ""

        for iteration in range(MAX_TOOL_ITERATIONS):
            # 3. Call the LLM.
            try:
                response = _call_anthropic(
                    model=self.session.model,
                    built=built,
                    tenant_id=self.tenant_id,
                )
            except Exception as exc:  # pragma: no cover — external dep
                logger.exception(
                    "Agent LLM call failed (session=%s, iter=%d)",
                    self.session.id, iteration,
                )
                yield {
                    "type": "error",
                    "message": f"LLM call failed: {exc}",
                    "recoverable": False,
                }
                return

            # 4. Persist assistant turn (always — tool_use blocks AND
            #    text are part of the same turn in the protocol).
            assistant_turn = _persist_turn(
                self.db,
                tenant_id=self.tenant_id,
                session=self.session,
                role="assistant",
                content={
                    "text": response["text"],
                    "blocks": response["raw_content"],
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
            #    and build tool_result blocks for the next iteration.
            tool_result_blocks: list[dict[str, Any]] = []
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

                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": call["id"],
                    "content": json.dumps(result_payload, default=str),
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

            # 7. Append the assistant turn (with its tool_use blocks)
            #    and the user-role tool_result message to the message
            #    list for the next iteration.
            built.messages.append(
                {"role": "assistant", "content": response["raw_content"]}
            )
            built.messages.append(
                {"role": "user", "content": tool_result_blocks}
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
        Anthropic tool_result tells the model the call failed.
        """
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


# ---------------------------------------------------------------------------
# Helpers the API layer calls
# ---------------------------------------------------------------------------


def default_model_for(provider: str) -> str:
    """Resolve a sensible default model name per provider. The API
    accepts an explicit override at session-create time."""
    try:
        return DEFAULT_MODEL_BY_PROVIDER[provider]
    except KeyError:
        raise UnsupportedProviderError(
            f"No default model configured for provider '{provider}'"
        )


def supported_providers() -> list[str]:
    """For the frontend session-create dropdown."""
    return sorted(DEFAULT_MODEL_BY_PROVIDER.keys())


def declared_tools() -> list[dict[str, Any]]:
    """Expose the raw tool definitions so the frontend can render a
    developer-facing "here are the tools the agent has" drawer if it
    wants. Not load-bearing for COPILOT-02."""
    return [dict(t) for t in COPILOT_TOOL_DEFINITIONS]
