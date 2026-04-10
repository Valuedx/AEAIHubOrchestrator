"""Multi-provider LLM abstraction.

Each provider function accepts (model, system_prompt, user_message, **kwargs)
and returns a standardized dict:
  {
    "response": str,
    "usage": {"input_tokens": int, "output_tokens": int},
    "model": str,
    "provider": str,
  }
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)


def call_llm(
    provider: str,
    model: str,
    system_prompt: str,
    user_message: str,
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """Route to the appropriate provider and return a standardized response."""
    providers = {
        "google": _call_google,
        "openai": _call_openai,
        "anthropic": _call_anthropic,
    }
    handler = providers.get(provider)
    if not handler:
        raise ValueError(f"Unknown LLM provider: {provider}")

    return handler(
        model=model,
        system_prompt=system_prompt,
        user_message=user_message,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def call_llm_streaming(
    provider: str,
    model: str,
    system_prompt: str,
    user_message: str,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    instance_id: str = "",
    node_id: str = "",
) -> dict[str, Any]:
    """Stream a response from the provider, publishing tokens to Redis as they arrive.

    Falls back to the non-streaming ``call_llm`` variant if:
    - ``instance_id`` or ``node_id`` are empty (no channel to publish to)
    - Redis is unavailable (publish errors are non-fatal in streaming_llm.py)
    - The provider doesn't support streaming

    Returns the same standardized dict as ``call_llm``.
    """
    if not instance_id or not node_id:
        logger.debug("call_llm_streaming: missing instance_id/node_id, falling back to non-streaming")
        return call_llm(provider, model, system_prompt, user_message, temperature, max_tokens)

    from app.engine.streaming_llm import stream_google, stream_openai, stream_anthropic

    streaming_providers = {
        "google": stream_google,
        "openai": stream_openai,
        "anthropic": stream_anthropic,
    }
    handler = streaming_providers.get(provider)
    if not handler:
        raise ValueError(f"Unknown LLM provider: {provider}")

    return handler(
        model=model,
        system_prompt=system_prompt,
        user_message=user_message,
        temperature=temperature,
        max_tokens=max_tokens,
        instance_id=instance_id,
        node_id=node_id,
    )


def _build_google_client():
    """Build a google.genai Client using Vertex AI (preferred) or API key."""
    import os
    from google import genai

    # Prefer Vertex AI with service account credentials
    if settings.google_project:
        if settings.google_application_credentials:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = settings.google_application_credentials
        client = genai.Client(
            vertexai=True,
            project=settings.google_project,
            location=settings.google_location or "us-central1",
        )
        logger.info("Google GenAI client: Vertex AI (project=%s)", settings.google_project)
        return client

    # Fallback to API key
    if settings.google_api_key:
        client = genai.Client(api_key=settings.google_api_key)
        logger.info("Google GenAI client: API key")
        return client

    raise ValueError(
        "Neither ORCHESTRATOR_GOOGLE_PROJECT (Vertex AI) nor "
        "ORCHESTRATOR_GOOGLE_API_KEY is configured"
    )


def _call_google(
    model: str,
    system_prompt: str,
    user_message: str,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    from google.genai import types

    client = _build_google_client()
    response = client.models.generate_content(
        model=model,
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt or None,
            temperature=temperature,
            max_output_tokens=max_tokens,
        ),
    )

    usage = response.usage_metadata
    return {
        "response": response.text or "",
        "usage": {
            "input_tokens": usage.prompt_token_count if usage else 0,
            "output_tokens": usage.candidates_token_count if usage else 0,
        },
        "model": model,
        "provider": "google",
    }


def _call_openai(
    model: str,
    system_prompt: str,
    user_message: str,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    if not settings.openai_api_key:
        raise ValueError("ORCHESTRATOR_OPENAI_API_KEY is not configured")

    from openai import OpenAI

    client = OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )

    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_message})

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    choice = response.choices[0]
    usage = response.usage
    return {
        "response": choice.message.content or "",
        "usage": {
            "input_tokens": usage.prompt_tokens if usage else 0,
            "output_tokens": usage.completion_tokens if usage else 0,
        },
        "model": model,
        "provider": "openai",
    }


def _call_anthropic(
    model: str,
    system_prompt: str,
    user_message: str,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    if not settings.anthropic_api_key:
        raise ValueError("ORCHESTRATOR_ANTHROPIC_API_KEY is not configured")

    from anthropic import Anthropic

    client = Anthropic(api_key=settings.anthropic_api_key)

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt or "You are a helpful assistant.",
        messages=[{"role": "user", "content": user_message}],
        temperature=temperature,
    )

    text = "".join(
        block.text for block in response.content if hasattr(block, "text")
    )
    return {
        "response": text,
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
        "model": model,
        "provider": "anthropic",
    }
