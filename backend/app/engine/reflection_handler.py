"""Reflection node handler.

A Reflection node calls an LLM with a summary of the workflow's execution
history up to that point, expecting a structured JSON response.  The result
is stored in the context under the node's own key so downstream Condition
nodes can route on any field it returns.

Design constraints:
  - Read-only access to the execution history — the handler never mutates
    the shared context dict.  It only returns a value; dag_runner stores it.
  - No dynamic graph mutation.  "Spawn follow-up nodes" is achieved by
    returning a decision field (e.g. {"next_action": "escalate"}) that a
    downstream Condition node routes on — standard pattern, no engine changes.
  - Uses the same render_prompt + call_llm + record_generation stack as
    every other agent node for consistent Langfuse observability.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Hard cap: never include more than this many node outputs in the summary
# regardless of what the user configures.
_ABSOLUTE_MAX_HISTORY = 25
# Truncate individual node output JSON to this many characters
_PER_NODE_CHAR_LIMIT = 800


def _build_execution_summary(context: dict[str, Any], max_history_nodes: int) -> str:
    """Build a readable summary of recent node outputs from the context.

    Collects node_* keys in insertion order (Python 3.7+ dict ordering
    reflects insertion order, which matches execution order).  Caps at
    max_history_nodes most recent entries and truncates each to prevent
    token explosion.
    """
    node_keys = [k for k in context if k.startswith("node_")]
    # Take the most recent N
    recent = node_keys[-max_history_nodes:]

    if not recent:
        return "(no node outputs in context yet)"

    lines: list[str] = [f"=== Execution History ({len(recent)} node(s)) ===\n"]
    for key in recent:
        value = context[key]
        summary = json.dumps(value, indent=2, default=str)
        if len(summary) > _PER_NODE_CHAR_LIMIT:
            summary = summary[:_PER_NODE_CHAR_LIMIT] + "\n... (truncated)"
        lines.append(f"[{key}]\n{summary}")

    trigger = context.get("trigger")
    if trigger:
        trig_str = json.dumps(trigger, indent=2, default=str)
        if len(trig_str) > _PER_NODE_CHAR_LIMIT:
            trig_str = trig_str[:_PER_NODE_CHAR_LIMIT] + "\n... (truncated)"
        lines.insert(1, f"[trigger]\n{trig_str}")

    return "\n\n".join(lines)


def _parse_json_response(raw: str) -> dict[str, Any]:
    """Parse an LLM response as JSON, handling accidental markdown fences."""
    text = raw.strip()

    # Strip ```json ... ``` or ``` ... ``` fences
    fence = re.match(r"^```(?:json)?\s*([\s\S]*?)```$", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        # LLM returned a non-object JSON value — wrap it
        return {"reflection": parsed}
    except json.JSONDecodeError:
        # Find the first {...} block as a fallback
        match = re.search(r"\{[\s\S]+\}", text)
        if match:
            try:
                parsed = json.loads(match.group())
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        # Last resort — return raw response under a "reflection" key
        logger.warning("Reflection node could not parse LLM response as JSON")
        return {"reflection": raw, "parse_error": True}


def _handle_reflection(
    node_data: dict, context: dict[str, Any], tenant_id: str
) -> dict[str, Any]:
    """Execute a Reflection node.

    Builds an execution summary from recent node outputs, renders the
    user-configured reflectionPrompt as a Jinja2 template (with
    {{ execution_summary }} and all normal context variables available),
    calls the LLM, parses the response as JSON, and returns the result.

    The handler is intentionally read-only: it never mutates ``context``.
    """
    from app.engine.llm_providers import call_llm
    from app.engine.prompt_template import render_prompt
    from app.observability import record_generation

    config = node_data.get("config", {})

    provider: str = config.get("provider", "google")
    model: str = config.get("model", "gemini-2.5-flash")
    reflection_prompt_template: str = config.get("reflectionPrompt", "")
    output_keys: list[str] = config.get("outputKeys", [])
    max_history: int = min(
        int(config.get("maxHistoryNodes", 10)),
        _ABSOLUTE_MAX_HISTORY,
    )
    temperature: float = float(config.get("temperature", 0.3))
    max_tokens: int = int(config.get("maxTokens", 1024))

    # ── Build execution summary ─────────────────────────────────────────────
    execution_summary = _build_execution_summary(context, max_history)

    # ── Render system prompt with execution_summary injected ────────────────
    # Make a shallow copy of context to inject the summary without side effects
    render_context = {**context, "execution_summary": execution_summary}
    system_prompt = render_prompt(reflection_prompt_template, render_context)

    if not system_prompt.strip():
        logger.warning("Reflection node has an empty reflectionPrompt — using a default")
        system_prompt = (
            "You are a workflow reflection engine. "
            "Analyze the execution history below and return a structured JSON assessment."
        )

    # ── User message ─────────────────────────────────────────────────────────
    user_message = (
        "Based on the execution history provided in your system prompt, "
        "respond ONLY with a valid JSON object. "
        "Do not include any explanation, markdown, or additional text.\n\n"
        f"Execution summary:\n{execution_summary}"
    )

    if output_keys:
        keys_str = ", ".join(f'"{k}"' for k in output_keys)
        user_message += (
            f"\n\nExpected top-level keys in the JSON response: [{keys_str}]"
        )

    logger.info(
        "Reflection node [%s/%s]: summary_len=%d, prompt_len=%d, "
        "max_history=%d, expected_keys=%s",
        provider, model, len(execution_summary), len(system_prompt),
        max_history, output_keys or "any",
    )

    # ── LLM call ─────────────────────────────────────────────────────────────
    result = call_llm(
        provider=provider,
        model=model,
        system_prompt=system_prompt,
        user_message=user_message,
        temperature=temperature,
        max_tokens=max_tokens,
        tenant_id=tenant_id,
    )

    raw_response = result.get("response", "").strip()
    usage = result.get("usage")

    # ── Parse response ────────────────────────────────────────────────────────
    parsed = _parse_json_response(raw_response)

    # Warn for any expected keys that are absent in the response
    if output_keys:
        missing = [k for k in output_keys if k not in parsed]
        if missing:
            logger.warning(
                "Reflection node: expected output keys %s not found in response. "
                "Present keys: %s",
                missing, list(parsed.keys()),
            )

    # ── Langfuse observability ────────────────────────────────────────────────
    record_generation(
        context.get("_trace"),
        name=f"reflection:{provider}/{model}",
        provider=provider,
        model=model,
        system_prompt=system_prompt,
        user_message=user_message,
        response=raw_response,
        usage=usage,
        metadata={"output_keys": output_keys, "max_history_nodes": max_history},
    )

    logger.info(
        "Reflection node [%s/%s]: response_keys=%s tokens_in=%s tokens_out=%s",
        provider, model, list(parsed.keys()),
        usage.get("input_tokens") if usage else "?",
        usage.get("output_tokens") if usage else "?",
    )

    return {**parsed, "_usage": usage, "_raw_response": raw_response}
