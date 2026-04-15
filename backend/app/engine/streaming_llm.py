"""Token-by-token streaming LLM helpers with Redis pub/sub bridge.

Architecture
------------
                   Celery worker                   FastAPI SSE
                   ────────────                   ──────────
  LLM streaming call
        │
        │ token arrives
        ▼
  publish_token(instance_id, node_id, token)
        │
        ▼
  Redis channel: orch:stream:{instance_id}
  message: {"node_id": "...", "token": "...", "done": false}
        │
        ▼                              subscribe(orch:stream:{instance_id})
                                             │
                                             ▼
                               event: token
                               data: {"node_id": "...", "token": "..."}
                                             │
                                             ▼
                                       browser live display

Channel lifetime
----------------
A ``done`` message is published after the last token.  The SSE subscriber
drains the channel but does not close the connection — the workflow may
continue with more nodes.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_CHANNEL_PREFIX = "orch:stream"

# ---------------------------------------------------------------------------
# Sync Redis client (for Celery worker threads)
# ---------------------------------------------------------------------------

_sync_redis_client = None


def _sync_redis():
    global _sync_redis_client
    if _sync_redis_client is None:
        from redis import Redis
        from app.config import settings
        _sync_redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _sync_redis_client


def _channel(instance_id: str) -> str:
    return f"{_CHANNEL_PREFIX}:{instance_id}"


def publish_token(instance_id: str, node_id: str, token: str) -> None:
    """Publish a single streaming token to the instance Redis channel."""
    try:
        msg = json.dumps({"node_id": node_id, "token": token, "done": False})
        _sync_redis().publish(_channel(instance_id), msg)
    except Exception as exc:
        logger.debug("Redis publish_token failed (non-fatal): %s", exc)


def publish_stream_end(instance_id: str, node_id: str) -> None:
    """Signal that all tokens for this node have been published."""
    try:
        msg = json.dumps({"node_id": node_id, "token": "", "done": True})
        _sync_redis().publish(_channel(instance_id), msg)
    except Exception as exc:
        logger.debug("Redis publish_stream_end failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Per-provider streaming implementations
# ---------------------------------------------------------------------------

def stream_google(
    model: str,
    system_prompt: str,
    user_message: str,
    temperature: float,
    max_tokens: int,
    instance_id: str,
    node_id: str,
    messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Stream Google Gemini, publishing tokens to Redis.  Returns the same
    dict format as ``_call_google`` in llm_providers.py."""
    from app.config import settings
    if not settings.google_api_key:
        raise ValueError("ORCHESTRATOR_GOOGLE_API_KEY is not configured")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.google_api_key)
    normalized = messages or (
        ([{"role": "system", "content": system_prompt}] if system_prompt else [])
        + [{"role": "user", "content": user_message}]
    )
    system_parts = [str(msg.get("content", "")) for msg in normalized if msg.get("role") == "system"]
    contents: list[Any] = []
    for msg in normalized:
        role = msg.get("role")
        if role == "system":
            continue
        mapped_role = "model" if role == "assistant" else "user"
        contents.append(
            types.Content(
                role=mapped_role,
                parts=[types.Part.from_text(text=str(msg.get("content", "")))],
            )
        )

    full_text = ""
    usage_meta = None

    for chunk in client.models.generate_content_stream(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction="\n\n".join(part for part in system_parts if part) or None,
            temperature=temperature,
            max_output_tokens=max_tokens,
        ),
    ):
        token = chunk.text or ""
        if token:
            full_text += token
            publish_token(instance_id, node_id, token)
        if chunk.usage_metadata:
            usage_meta = chunk.usage_metadata

    publish_stream_end(instance_id, node_id)

    return {
        "response": full_text,
        "usage": {
            "input_tokens": usage_meta.prompt_token_count if usage_meta else 0,
            "output_tokens": usage_meta.candidates_token_count if usage_meta else 0,
        },
        "model": model,
        "provider": "google",
    }


def stream_openai(
    model: str,
    system_prompt: str,
    user_message: str,
    temperature: float,
    max_tokens: int,
    instance_id: str,
    node_id: str,
    messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Stream OpenAI, publishing tokens to Redis."""
    from app.config import settings
    if not settings.openai_api_key:
        raise ValueError("ORCHESTRATOR_OPENAI_API_KEY is not configured")

    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)

    payload_messages = messages or (
        ([{"role": "system", "content": system_prompt}] if system_prompt else [])
        + [{"role": "user", "content": user_message}]
    )

    full_text = ""
    input_tokens = 0
    output_tokens = 0

    with client.chat.completions.create(
        model=model,
        messages=payload_messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
        stream_options={"include_usage": True},
    ) as stream:
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                token = chunk.choices[0].delta.content
                full_text += token
                publish_token(instance_id, node_id, token)
            if chunk.usage:
                input_tokens = chunk.usage.prompt_tokens or 0
                output_tokens = chunk.usage.completion_tokens or 0

    publish_stream_end(instance_id, node_id)

    return {
        "response": full_text,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "model": model,
        "provider": "openai",
    }


def stream_anthropic(
    model: str,
    system_prompt: str,
    user_message: str,
    temperature: float,
    max_tokens: int,
    instance_id: str,
    node_id: str,
    messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Stream Anthropic, publishing tokens to Redis."""
    from app.config import settings
    if not settings.anthropic_api_key:
        raise ValueError("ORCHESTRATOR_ANTHROPIC_API_KEY is not configured")

    from anthropic import Anthropic

    client = Anthropic(api_key=settings.anthropic_api_key)
    normalized = messages or (
        ([{"role": "system", "content": system_prompt}] if system_prompt else [])
        + [{"role": "user", "content": user_message}]
    )
    system_parts = [str(msg.get("content", "")) for msg in normalized if msg.get("role") == "system"]
    anthropic_messages = [
        {"role": msg.get("role", "user"), "content": str(msg.get("content", ""))}
        for msg in normalized
        if msg.get("role") in {"user", "assistant"}
    ]

    full_text = ""
    input_tokens = 0
    output_tokens = 0

    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system="\n\n".join(part for part in system_parts if part) or "You are a helpful assistant.",
        messages=anthropic_messages,
        temperature=temperature,
    ) as stream:
        for token in stream.text_stream:
            full_text += token
            publish_token(instance_id, node_id, token)
        msg = stream.get_final_message()
        if msg and msg.usage:
            input_tokens = msg.usage.input_tokens
            output_tokens = msg.usage.output_tokens

    publish_stream_end(instance_id, node_id)

    return {
        "response": full_text,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "model": model,
        "provider": "anthropic",
    }
