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
import os
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
    messages: list[dict[str, Any]] | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Route to the appropriate provider and return a standardized response.

    The ``vertex`` provider shares the same request/response code as
    ``google`` — both hit the unified ``google-genai`` SDK; only the
    ``Client`` constructor differs (AI Studio api-key vs. Vertex
    project + location). See ``_call_google``.

    ``tenant_id`` is threaded through to the Vertex client factory so
    VERTEX-02's per-tenant project override resolves. OpenAI /
    Anthropic paths accept the kwarg and ignore it — the handler
    signatures stay uniform.
    """
    providers = {
        "google": _call_google,
        "vertex": _call_vertex,
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
        messages=messages,
        tenant_id=tenant_id,
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
    messages: list[dict[str, Any]] | None = None,
    tenant_id: str | None = None,
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
        return call_llm(
            provider,
            model,
            system_prompt,
            user_message,
            temperature,
            max_tokens,
            messages=messages,
            tenant_id=tenant_id,
        )

    from app.engine.streaming_llm import (
        stream_anthropic,
        stream_google,
        stream_openai,
        stream_vertex,
    )

    streaming_providers = {
        "google": stream_google,
        "vertex": stream_vertex,
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
        messages=messages,
        tenant_id=tenant_id,
    )


def _coerce_messages(
    system_prompt: str,
    user_message: str,
    messages: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if messages:
        return messages
    out: list[dict[str, Any]] = []
    if system_prompt:
        out.append({"role": "system", "content": system_prompt})
    out.append({"role": "user", "content": user_message})
    return out


def _resolve_vertex_target(tenant_id: str | None) -> tuple[str, str]:
    """Resolve the (project, location) Vertex AI target for a caller.

    VERTEX-02 precedence, highest first:

      1. ``tenant_integrations`` row with ``system='vertex'`` and
         ``is_default=True`` for this tenant. Its ``config_json`` may
         override ``project`` and/or ``location`` (missing keys fall
         through to the env defaults).
      2. ``settings.vertex_project`` + ``settings.vertex_location``.

    Passing ``tenant_id=None`` is the "internal / cross-tenant" path —
    schema priming, CLI scripts, etc. — which always uses the env
    defaults so an unconfigured caller can't accidentally read another
    tenant's routing.

    Missing project at the end of resolution is not an error here; the
    caller (``_google_client``) raises a specific ValueError so the
    error message points at ``ORCHESTRATOR_VERTEX_PROJECT``.
    """
    if tenant_id is None:
        return settings.vertex_project, settings.vertex_location

    from app.database import SessionLocal, set_tenant_context
    from app.models.workflow import TenantIntegration

    db = SessionLocal()
    try:
        set_tenant_context(db, tenant_id)
        row = (
            db.query(TenantIntegration)
            .filter_by(tenant_id=tenant_id, system="vertex", is_default=True)
            .first()
        )
        if row is None:
            return settings.vertex_project, settings.vertex_location
        cfg = row.config_json or {}
        return (
            cfg.get("project") or settings.vertex_project,
            cfg.get("location") or settings.vertex_location,
        )
    finally:
        db.close()


def _google_client(backend: str, tenant_id: str | None = None):  # noqa: ANN202 — return type is SDK-internal
    """Build a ``google.genai.Client`` for either AI Studio or Vertex AI.

    Factored out so the request / response code in ``_call_google`` +
    ``_call_vertex`` (and the streaming variants) stays identical — only
    the client constructor differs:

      * ``backend='genai'`` — AI Studio via ``api_key``
      * ``backend='vertex'`` — Vertex AI via ``vertexai=True`` +
        project + location, authenticated through Application Default
        Credentials (ADC).

    For ``backend='vertex'``, ``tenant_id`` (when provided) resolves the
    project/location through the per-tenant registry (VERTEX-02). Pass
    ``None`` for the process-global env-var fallback.

    Raises ``ValueError`` with a specific env-var name so operators
    know exactly which setting to populate.
    """
    from google import genai

    if backend == "vertex":
        # Ensure SDK can find the JSON key if provided in .env
        if settings.google_application_credentials and not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = settings.google_application_credentials

        project, location = _resolve_vertex_target(tenant_id)
        if not project:
            raise ValueError("ORCHESTRATOR_VERTEX_PROJECT is not configured")
        return genai.Client(
            vertexai=True,
            project=project,
            location=location,
        )
    if backend == "genai":
        # ADMIN-03 — per-tenant Google AI Studio key via the LLM
        # credentials resolver (tenant_secrets → env fallback). 
        from app.engine.llm_credentials_resolver import get_google_api_key

        try:
            api_key = get_google_api_key(tenant_id)
            return genai.Client(api_key=api_key)
        except ValueError as exc:
            # Smart Fallback: if no API key is found but Vertex is configured,
            # use Vertex instead of failing. This satisfies "it should use 
            # my .env vertex configuration".
            project, _ = _resolve_vertex_target(tenant_id)
            if project:
                logger.info("No Google AI Studio key found; falling back to Vertex AI backend.")
                return _google_client("vertex", tenant_id=tenant_id)
            raise exc
    raise ValueError(f"Unknown google backend: {backend!r}")


def _call_google_backend(
    *,
    backend: str,
    model: str,
    system_prompt: str,
    user_message: str,
    temperature: float,
    max_tokens: int,
    messages: list[dict[str, Any]] | None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Shared Gemini request path used by both AI Studio and Vertex.

    The ``provider`` field on the returned dict mirrors ``backend`` so
    downstream code and Langfuse traces can still distinguish the two
    backends even though the wire format is identical. ``tenant_id``
    only affects the Vertex branch — it picks the per-tenant project
    (VERTEX-02). AI Studio ignores it.
    """
    from google.genai import types

    client = _google_client(backend, tenant_id=tenant_id)
    normalized = _coerce_messages(system_prompt, user_message, messages)
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

    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction="\n\n".join(part for part in system_parts if part) or None,
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
        "provider": "vertex" if backend == "vertex" else "google",
    }


def _call_google(
    model: str,
    system_prompt: str,
    user_message: str,
    temperature: float,
    max_tokens: int,
    messages: list[dict[str, Any]] | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    return _call_google_backend(
        backend="genai",
        model=model,
        system_prompt=system_prompt,
        user_message=user_message,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=messages,
        tenant_id=tenant_id,
    )


def _call_vertex(
    model: str,
    system_prompt: str,
    user_message: str,
    temperature: float,
    max_tokens: int,
    messages: list[dict[str, Any]] | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Call Gemini via Vertex AI (enterprise Google Cloud endpoint).

    Same wire format as ``_call_google`` since both use the unified
    ``google-genai`` SDK. Auth is Application Default Credentials —
    typically a service-account JSON path in
    ``GOOGLE_APPLICATION_CREDENTIALS`` or, on GKE / Cloud Run,
    workload identity. Region + project come from
    ``ORCHESTRATOR_VERTEX_PROJECT`` / ``ORCHESTRATOR_VERTEX_LOCATION``.
    """
    return _call_google_backend(
        backend="vertex",
        model=model,
        system_prompt=system_prompt,
        user_message=user_message,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=messages,
        tenant_id=tenant_id,
    )


def _call_openai(
    model: str,
    system_prompt: str,
    user_message: str,
    temperature: float,
    max_tokens: int,
    messages: list[dict[str, Any]] | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    # ADMIN-03 — per-tenant OpenAI key via tenant_secrets (LLM_OPENAI_*)
    # with env fallback. Raises a remediation-bearing ValueError if
    # neither tenant nor env has a key.
    from app.engine.llm_credentials_resolver import (
        get_openai_api_key,
        get_openai_base_url,
    )
    from openai import OpenAI

    client = OpenAI(
        api_key=get_openai_api_key(tenant_id),
        base_url=get_openai_base_url(tenant_id),
    )

    payload_messages = _coerce_messages(system_prompt, user_message, messages)

    response = client.chat.completions.create(
        model=model,
        messages=payload_messages,
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
    messages: list[dict[str, Any]] | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    # ADMIN-03 — per-tenant Anthropic key via tenant_secrets.
    from app.engine.llm_credentials_resolver import get_anthropic_api_key
    from anthropic import Anthropic

    client = Anthropic(api_key=get_anthropic_api_key(tenant_id))

    normalized = _coerce_messages(system_prompt, user_message, messages)
    system_parts = [str(msg.get("content", "")) for msg in normalized if msg.get("role") == "system"]
    anthropic_messages = [
        {"role": msg.get("role", "user"), "content": str(msg.get("content", ""))}
        for msg in normalized
        if msg.get("role") in {"user", "assistant"}
    ]

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system="\n\n".join(part for part in system_parts if part) or "You are a helpful assistant.",
        messages=anthropic_messages,
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
