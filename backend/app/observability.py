"""Langfuse observability for the orchestrator module.

Uses Langfuse v4's OpenTelemetry-based API and reads the standard
``LANGFUSE_*`` environment variables.

Provides:
  - trace_workflow()  — root trace per workflow execution
  - span_node()       — child span per node execution
  - record_generation() — LLM generation recording with token usage
  - span_tool()       — tool execution span (ReAct loop, MCP calls)

All operations are no-op safe: disabled Langfuse never breaks execution.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Generator, Optional

logger = logging.getLogger("orchestrator.observability")

_langfuse_client = None
_langfuse_available: Optional[bool] = None


def _is_enabled() -> bool:
    # Prefer the standard Langfuse env vars, but keep backward compatibility
    # with ORCHESTRATOR_-scoped settings.
    env_val = (os.environ.get("LANGFUSE_ENABLED") or "").strip().lower()
    if env_val:
        return env_val in ("1", "true", "yes", "on")
    env_val = (os.environ.get("ORCHESTRATOR_LANGFUSE_ENABLED") or "").strip().lower()
    if env_val:
        return env_val in ("1", "true", "yes", "on")
    try:
        from app.config import settings
        return bool(settings.langfuse_enabled)
    except Exception:
        return False


def get_langfuse():
    global _langfuse_client, _langfuse_available

    if _langfuse_available is False:
        return None

    if _langfuse_client is not None:
        return _langfuse_client

    if not _is_enabled():
        _langfuse_available = False
        logger.info("Langfuse observability disabled (set LANGFUSE_ENABLED=true to enable)")
        return None

    try:
        from langfuse import Langfuse
        from app.config import settings

        _langfuse_client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        _langfuse_available = True
        logger.info(
            "Langfuse observability initialized (host=%s)",
            settings.langfuse_host,
        )
        return _langfuse_client
    except Exception as exc:
        _langfuse_available = False
        logger.warning("Langfuse initialization failed: %s", exc)
        return None


def flush() -> None:
    if _langfuse_client is not None:
        try:
            _langfuse_client.flush()
        except Exception:
            pass


def shutdown() -> None:
    global _langfuse_client, _langfuse_available
    if _langfuse_client is not None:
        try:
            _langfuse_client.shutdown()
        except Exception:
            pass
        _langfuse_client = None
        _langfuse_available = None


class _NoOpSpan:
    def update(self, **_kw):
        return self

    def end(self, **_kw):
        return None

    def span(self, **_kw):
        return _NoOpSpan()

    def generation(self, **_kw):
        return _NoOpSpan()


@contextmanager
def _noop_ctx() -> Generator:
    yield _NoOpSpan()


# ---------------------------------------------------------------------------
# Trace & span helpers
# ---------------------------------------------------------------------------

@contextmanager
def trace_workflow(
    *,
    workflow_id: str,
    instance_id: str,
    tenant_id: str,
    workflow_name: str = "",
    trigger_payload: Any = None,
    tags: list[str] | None = None,
) -> Generator:
    """Root trace for an entire workflow execution."""
    lf = get_langfuse()
    if lf is None:
        yield _NoOpSpan()
        return

    try:
        trace_tags = ["orchestrator", f"tenant:{tenant_id}"]
        if tags:
            trace_tags.extend(tags)

        # Extract session_id from trigger_payload (A2A chat session) if provided
        session_id = f"wf-{workflow_id}"
        if isinstance(trigger_payload, dict):
            session_id = trigger_payload.get("session_id") or session_id

        trace = lf.trace(
            name=f"workflow:{workflow_name or workflow_id}",
            session_id=session_id,
            user_id=tenant_id,
            tags=trace_tags,
            input={"trigger_payload": trigger_payload},
            metadata={
                "workflow_id": workflow_id,
                "instance_id": instance_id,
                "tenant_id": tenant_id,
            },
        )
        yield trace
    except Exception as exc:
        logger.debug("Langfuse trace_workflow error: %s", exc)
        yield _NoOpSpan()


@contextmanager
def span_node(
    parent: Any,
    *,
    node_id: str,
    node_type: str,
    node_label: str = "",
    input_data: Any = None,
    checkpoint_id: str | None = None,
) -> Generator:
    """Child span for a single node execution.

    Args:
        checkpoint_id: UUID of the InstanceCheckpoint written after this node
            completes.  When provided, it is stored in span metadata so the
            Langfuse trace links directly to the DB snapshot.  Pass ``None``
            (the default) when the checkpoint is not yet available at span
            creation time — callers can supply it later via ``span.update()``.
    """
    if isinstance(parent, _NoOpSpan):
        yield _NoOpSpan()
        return

    lf = get_langfuse()
    if lf is None:
        yield _NoOpSpan()
        return

    try:
        meta: dict = {"node_id": node_id, "node_type": node_type}
        if checkpoint_id is not None:
            meta["checkpoint_id"] = checkpoint_id
        with lf.start_as_current_observation(
            name=f"node:{node_label or node_id}",
            as_type="span",
            input=input_data,
            metadata=meta,
        ) as span:
            yield span
    except Exception as exc:
        logger.debug("Langfuse span_node error: %s", exc)
        yield _NoOpSpan()


def record_generation(
    parent: Any,
    *,
    name: str,
    provider: str,
    model: str,
    system_prompt: str = "",
    user_message: str = "",
    response: str = "",
    usage: dict[str, int] | None = None,
    metadata: dict | None = None,
) -> None:
    """Record an LLM generation with token usage in the current context."""
    if isinstance(parent, _NoOpSpan):
        return

    lf = get_langfuse()
    if lf is None:
        return

    try:
        gen = lf.start_observation(
            name=name,
            as_type="generation",
            input={"system_prompt": system_prompt[:500], "user_message": user_message[:1000]},
            output=response[:2000] if response else None,
            model=model,
            usage_details=usage,
            metadata={"provider": provider, **(metadata or {})},
        )
        gen.end()
    except Exception as exc:
        logger.debug("Langfuse record_generation error: %s", exc)


@contextmanager
def span_tool(
    parent: Any,
    *,
    tool_name: str,
    arguments: Any = None,
) -> Generator:
    """Span for a tool execution (MCP call, HTTP request, ReAct tool)."""
    if isinstance(parent, _NoOpSpan):
        yield _NoOpSpan()
        return

    lf = get_langfuse()
    if lf is None:
        yield _NoOpSpan()
        return

    try:
        with lf.start_as_current_observation(
            name=f"tool:{tool_name}",
            as_type="tool",
            input=arguments,
            metadata={"tool_name": tool_name},
        ) as span:
            yield span
    except Exception as exc:
        logger.debug("Langfuse span_tool error: %s", exc)
        yield _NoOpSpan()
