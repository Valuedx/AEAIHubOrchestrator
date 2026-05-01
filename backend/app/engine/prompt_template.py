"""Jinja2 prompt templating with context variable injection.

System prompts can reference upstream node outputs and trigger data using
Jinja2 syntax:

    You are an IT support assistant.
    The user reported: {{ trigger.user_query }}
    Request status: {{ node_1.output.status }}
    Logs summary: {{ node_2.body | truncate(500) }}

All keys in the execution context are available as top-level variables.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from jinja2 import Environment, BaseLoader, TemplateSyntaxError, Undefined

logger = logging.getLogger(__name__)
_DEFAULT_CONTEXT_TOKEN_BUDGET = 1200

# CTX-MGMT.H v2 — per-render thread-local capture of read/miss events.
# Set by ``render_prompt`` for the duration of a single render (only
# when tracing is enabled for the instance), consumed by the
# ``_DotDict`` and ``_PermissiveUndefined`` __getattr__/__getitem__
# hooks. Events are then stashed on ``_runtime['_pending_render_events']``
# for the runner to flush via ``context_trace.flush_render_events``.
_render_state = threading.local()


def _capture(op: str, key: str) -> None:
    """Append one (op, key) tuple to the active render's capture list,
    if any. No-op when no render is active or capture is off."""
    pending = getattr(_render_state, "pending", None)
    if pending is None or not isinstance(key, str) or not key:
        return
    pending.append((op, key))


def count_prompt_tokens(text: str) -> int:
    """Approximate token count using tiktoken when available."""
    if not text:
        return 0
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Trim text to roughly *max_tokens* tokens."""
    if not text or max_tokens <= 0:
        return ""
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        toks = enc.encode(text)
        if len(toks) <= max_tokens:
            return text
        return enc.decode(toks[:max_tokens]).rstrip() + "\n... (truncated)"
    except Exception:
        approx_chars = max_tokens * 4
        if len(text) <= approx_chars:
            return text
        return text[:approx_chars].rstrip() + "\n... (truncated)"


class _PermissiveUndefined(Undefined):
    """Returns empty string for missing variables instead of raising."""

    def __init__(self, hint=None, obj=None, name=None, exc=None):
        # Jinja constructs this with a name when a top-level lookup
        # fails. CTX-MGMT.H v2 — that's a miss; capture it.
        try:
            super().__init__(hint=hint, obj=obj, name=name, exc=exc)  # type: ignore[arg-type]
        except TypeError:
            # Older Jinja signature compatibility.
            super().__init__()
        if name:
            _capture("miss", str(name))

    def __str__(self) -> str:
        return ""

    def __iter__(self):
        return iter([])

    def __bool__(self) -> bool:
        return False

    def __getattr__(self, name: str) -> "_PermissiveUndefined":
        # CTX-MGMT.H v2 — chained access on an undefined: still a miss.
        if not name.startswith("_"):
            _capture("miss", name)
        return _PermissiveUndefined()

    def __getitem__(self, name: Any) -> "_PermissiveUndefined":
        # CTX-MGMT.H v2 — bracket-access on an undefined: still a miss.
        _capture("miss", str(name))
        return _PermissiveUndefined()


_env = Environment(
    loader=BaseLoader(),
    autoescape=False,
    undefined=_PermissiveUndefined,
    keep_trailing_newline=True,
)


class _DotDict(dict):
    """Dict subclass that allows attribute-style access for Jinja2 templates."""

    def __getattr__(self, name: str) -> Any:
        # CTX-MGMT.H v2 — capture reads + misses against the dict's
        # immediate key. Skip dunder / sentinel names to avoid noise
        # from Jinja's internal probing.
        try:
            val = self[name]
        except KeyError:
            if not name.startswith("_"):
                _capture("miss", name)
            return _PermissiveUndefined()
        if not name.startswith("_"):
            _capture("read", name)
        return _wrap(val)


def _wrap(value: Any) -> Any:
    """Recursively wrap dicts for dot-access in templates."""
    if isinstance(value, dict):
        return _DotDict(value)
    if isinstance(value, list):
        return [_wrap(v) for v in value]
    return value


def render_prompt(template_str: str, context: dict[str, Any]) -> str:
    """Render a Jinja2 template string with the execution context.

    Wraps dict values so they're dot-accessible in templates:
        {{ node_1.response }} instead of {{ node_1["response"] }}

    CTX-MGMT.H v2 — when context tracing is enabled for this
    instance (``_runtime['context_trace_enabled']``), reads and
    misses encountered during render are appended to
    ``_runtime['_pending_render_events']`` for the runner to flush
    after the per-node post-handler pipeline. Disabled by default;
    fast no-op when off.
    """
    if not template_str or ("{{" not in template_str and "{%" not in template_str):
        return template_str

    safe_context = {k: _wrap(v) for k, v in context.items()}

    runtime = context.get("_runtime") if isinstance(context.get("_runtime"), dict) else None
    capture_on = bool(runtime and runtime.get("context_trace_enabled"))
    prior_pending = getattr(_render_state, "pending", None)
    if capture_on:
        _render_state.pending = []

    try:
        tmpl = _env.from_string(template_str)
        return tmpl.render(**safe_context)
    except (TemplateSyntaxError, Exception) as exc:
        logger.warning("Prompt template rendering failed: %s", exc)
        return template_str
    finally:
        if capture_on:
            captured = getattr(_render_state, "pending", []) or []
            existing = runtime.get("_pending_render_events") or []
            existing.extend(captured)
            runtime["_pending_render_events"] = existing
        # Restore prior thread-local state (handles nested renders).
        _render_state.pending = prior_pending


def build_structured_context_block(
    context: dict[str, Any],
    *,
    exclude_node_ids: set[str] | None = None,
    max_tokens: int = _DEFAULT_CONTEXT_TOKEN_BUDGET,
) -> str:
    """Assemble non-conversation context into a token-budgeted block."""
    parts: list[str] = []
    remaining = max_tokens
    excluded = exclude_node_ids or set()

    trigger = context.get("trigger")
    if trigger and remaining > 0:
        block = f"**Trigger input:**\n```json\n{json.dumps(trigger, indent=2, default=str)}\n```"
        parts.append(truncate_to_tokens(block, remaining))
        remaining = max(0, remaining - count_prompt_tokens(parts[-1]))

    # CTX-MGMT.D — loop_item lives under _runtime; legacy fall-back.
    runtime = context.get("_runtime") or {}
    loop_item = runtime.get("loop_item")
    if loop_item is None:
        loop_item = context.get("_loop_item")
    if loop_item is not None and remaining > 0:
        block = (
            "**Current loop item:**\n```json\n"
            f"{json.dumps(loop_item, indent=2, default=str)}\n```"
        )
        parts.append(truncate_to_tokens(block, remaining))
        remaining = max(0, remaining - count_prompt_tokens(parts[-1]))

    for key, value in context.items():
        if not key.startswith("node_") or key in excluded or remaining <= 0:
            continue
        summary = json.dumps(value, indent=2, default=str)
        block = f"**Output of {key}:**\n```json\n{summary}\n```"
        trimmed = truncate_to_tokens(block, remaining)
        parts.append(trimmed)
        remaining = max(0, remaining - count_prompt_tokens(trimmed))

    if not parts:
        return "No upstream data available. Please respond based on your system instructions."

    return "\n\n".join(parts)


def build_user_message(context: dict[str, Any]) -> str:
    """Backward-compatible wrapper for general non-memory agent prompting."""
    return build_structured_context_block(context)


def resolve_config_env_vars(config: dict, tenant_id: str) -> dict:
    """Resolve {{ env.SECRET_NAME }} references in node config values.

    Scans all string values in the config dict, replaces any
    ``{{ env.XYZ }}`` pattern by looking up secret ``XYZ`` from
    the tenant's encrypted vault.  Non-string values are returned as-is.
    """
    import re
    _ENV_PATTERN = re.compile(r"\{\{\s*env\.(\w+)\s*\}\}")

    resolved = {}
    for key, value in config.items():
        if not isinstance(value, str) or "{{" not in value:
            resolved[key] = value
            continue

        matches = _ENV_PATTERN.findall(value)
        if not matches:
            resolved[key] = value
            continue

        result = value
        for secret_name in matches:
            try:
                from app.security.vault import get_tenant_secret
                secret_value = get_tenant_secret(tenant_id, secret_name)
                if secret_value is not None:
                    result = result.replace(
                        f"{{{{ env.{secret_name} }}}}", secret_value
                    )
                    # Also replace without spaces around the expression
                    result = result.replace(
                        f"{{{{env.{secret_name}}}}}", secret_value
                    )
            except Exception as exc:
                logger.warning(
                    "Could not resolve env.%s for tenant %s: %s",
                    secret_name, tenant_id, exc,
                )
        resolved[key] = result

    return resolved

