"""ReAct (Reason + Act) iterative tool-calling loop.

Implements the pattern:
  1. Send system prompt + context + conversation history to LLM
  2. If LLM returns tool_calls → execute each tool → append results
  3. Repeat until LLM gives a final text response or maxIterations reached
  4. Track cumulative token usage across all iterations

Supports Google Gemini, OpenAI, and Anthropic tool-calling APIs.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.config import settings
from app.database import SessionLocal
from app.engine.memory_service import assemble_agent_messages

logger = logging.getLogger(__name__)

_MAX_ITERATIONS_HARD_CAP = 25


def run_react_loop(
    node_data: dict,
    context: dict[str, Any],
    tenant_id: str,
) -> dict[str, Any]:
    """Execute a ReAct agent loop with tool calling."""
    from app.engine.model_registry import default_llm_for

    config = node_data.get("config", {})
    provider = config.get("provider")
    if not provider or (provider == "google" and settings.llm_default_provider == "vertex"):
        provider = settings.llm_default_provider

    model = config.get("model") or default_llm_for(provider, role="fast")
    raw_prompt = config.get("systemPrompt", "")
    max_iterations = min(int(config.get("maxIterations", 10)), _MAX_ITERATIONS_HARD_CAP)
    tool_names: list[str] = config.get("tools", [])
    mcp_server_label: str | None = config.get("mcpServerLabel") or None
    temperature = float(config.get("temperature", 0.7))
    max_tokens = int(config.get("maxTokens", 4096))

    from app.engine.prompt_template import render_prompt

    system_prompt = render_prompt(raw_prompt, context)
    from app.database import set_tenant_context
    db = SessionLocal()
    try:
        set_tenant_context(db, tenant_id)
        initial_messages, memory_debug = assemble_agent_messages(
            db,
            tenant_id=tenant_id,
            workflow_def_id=str(context.get("_workflow_def_id", "") or ""),
            context=context,
            node_config=config,
            rendered_system_prompt=system_prompt,
        )
    finally:
        db.close()

    tool_defs = _load_tool_definitions(
        tool_names if tool_names else None,
        tenant_id=tenant_id,
        server_label=mcp_server_label,
    )

    total_usage = {"input_tokens": 0, "output_tokens": 0}
    iterations: list[dict[str, Any]] = []

    handler = _PROVIDERS.get(provider)
    if not handler:
        raise ValueError(f"Unknown LLM provider for ReAct: {provider}")

    messages = handler["init"](initial_messages)

    for i in range(max_iterations):
        logger.info("ReAct [%s/%s] iteration %d/%d", provider, model, i + 1, max_iterations)

        response = handler["call"](
            model, messages, tool_defs, temperature, max_tokens,
            tenant_id=tenant_id,
        )
        total_usage["input_tokens"] += response["usage"]["input_tokens"]
        total_usage["output_tokens"] += response["usage"]["output_tokens"]

        if not response.get("tool_calls"):
            iterations.append({
                "iteration": i + 1,
                "action": "final_response",
                "content": response["content"],
            })
            return {
                "response": response["content"],
                "provider": provider,
                "model": model,
                "usage": total_usage,
                "iterations": iterations,
                "total_iterations": i + 1,
                "memory_debug": memory_debug,
            }

        tool_results = []
        for tc in response["tool_calls"]:
            tool_name = tc["name"]
            tool_args = tc["arguments"]
            logger.info("ReAct calling tool: %s(%s)", tool_name, json.dumps(tool_args)[:200])

            result = _execute_tool(
                tool_name, tool_args, tenant_id,
                server_label=mcp_server_label,
            )
            tool_results.append({
                "tool_call_id": tc.get("id", tool_name),
                "name": tool_name,
                "result": result,
            })

        iterations.append({
            "iteration": i + 1,
            "action": "tool_calls",
            "tool_calls": [{"name": tc["name"], "arguments": tc["arguments"]} for tc in response["tool_calls"]],
            "tool_results": [{"name": tr["name"], "result_preview": str(tr["result"])[:500]} for tr in tool_results],
        })

        messages = handler["append_tool_results"](messages, response, tool_results)

    final_text = "Maximum iterations reached without final answer."
    iterations.append({"iteration": max_iterations, "action": "max_iterations_exceeded"})

    return {
        "response": final_text,
        "provider": provider,
        "model": model,
        "usage": total_usage,
        "iterations": iterations,
        "total_iterations": max_iterations,
        "memory_debug": memory_debug,
    }


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def _execute_tool(
    tool_name: str,
    arguments: dict,
    tenant_id: str,
    *,
    server_label: str | None = None,
) -> Any:
    """Execute a tool on the MCP server resolved for this tenant + label."""
    from app.engine.mcp_client import call_tool
    return call_tool(
        tool_name, arguments,
        tenant_id=tenant_id,
        server_label=server_label,
    )


def _load_tool_definitions(
    tool_names: list[str] | None,
    *,
    tenant_id: str,
    server_label: str | None = None,
) -> list[dict[str, Any]]:
    """Load tool definitions from the MCP server resolved for this tenant.

    If tool_names is None (empty config), auto-discovers all tools.
    Returns OpenAI-style function definitions.
    """
    from app.engine.mcp_client import get_openai_style_tool_defs, list_tools
    if tool_names is None:
        raw = list_tools(tenant_id=tenant_id, server_label=server_label)
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            }
            for t in raw
        ]
    return get_openai_style_tool_defs(
        tool_names,
        tenant_id=tenant_id,
        server_label=server_label,
    )


# ---------------------------------------------------------------------------
# Provider-specific message handling
# ---------------------------------------------------------------------------

def _openai_init(messages: list[dict]) -> list[dict]:
    return list(messages)


def _openai_call(
    model: str, messages: list[dict], tools: list[dict],
    temperature: float, max_tokens: int,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    # ADMIN-03 — per-tenant OpenAI key via tenant_secrets.
    from app.engine.llm_credentials_resolver import (
        get_openai_api_key,
        get_openai_base_url,
    )
    from openai import OpenAI

    client = OpenAI(
        api_key=get_openai_api_key(tenant_id),
        base_url=get_openai_base_url(tenant_id),
    )

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = tools

    resp = client.chat.completions.create(**kwargs)
    choice = resp.choices[0]
    usage = resp.usage

    tool_calls = None
    if choice.message.tool_calls:
        tool_calls = [
            {
                "id": tc.id,
                "name": tc.function.name,
                "arguments": json.loads(tc.function.arguments),
            }
            for tc in choice.message.tool_calls
        ]

    return {
        "content": choice.message.content or "",
        "tool_calls": tool_calls,
        "raw_message": choice.message,
        "usage": {
            "input_tokens": usage.prompt_tokens if usage else 0,
            "output_tokens": usage.completion_tokens if usage else 0,
        },
    }


def _openai_append(messages: list[dict], response: dict, tool_results: list[dict]) -> list[dict]:
    messages = list(messages)
    messages.append({
        "role": "assistant",
        "content": response["content"] or None,
        "tool_calls": [
            {
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])},
            }
            for tc in response["tool_calls"]
        ],
    })
    for tr in tool_results:
        messages.append({
            "role": "tool",
            "tool_call_id": tr["tool_call_id"],
            "content": json.dumps(tr["result"], default=str),
        })
    return messages


def _anthropic_init(messages: list[dict]) -> dict:
    system = "\n\n".join(
        str(msg.get("content", "")) for msg in messages if msg.get("role") == "system"
    )
    convo = [
        {"role": msg.get("role", "user"), "content": str(msg.get("content", ""))}
        for msg in messages
        if msg.get("role") in {"user", "assistant"}
    ]
    return {"system": system, "messages": convo}


def _anthropic_call(
    model: str, state: dict, tools: list[dict],
    temperature: float, max_tokens: int,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    # ADMIN-03 — per-tenant Anthropic key via tenant_secrets.
    from app.engine.llm_credentials_resolver import get_anthropic_api_key
    from anthropic import Anthropic

    client = Anthropic(api_key=get_anthropic_api_key(tenant_id))

    anthropic_tools = [
        {
            "name": t["function"]["name"],
            "description": t["function"]["description"],
            "input_schema": t["function"]["parameters"],
        }
        for t in tools
    ] if tools else []

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": state["system"] or "You are a helpful assistant.",
        "messages": state["messages"],
        "temperature": temperature,
    }
    if anthropic_tools:
        kwargs["tools"] = anthropic_tools

    resp = client.messages.create(**kwargs)

    text_parts = []
    tool_calls = []
    for block in resp.content:
        if hasattr(block, "text"):
            text_parts.append(block.text)
        elif block.type == "tool_use":
            tool_calls.append({
                "id": block.id,
                "name": block.name,
                "arguments": block.input,
            })

    return {
        "content": "".join(text_parts),
        "tool_calls": tool_calls or None,
        "raw_content": resp.content,
        "usage": {
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
        },
    }


def _anthropic_append(state: dict, response: dict, tool_results: list[dict]) -> dict:
    messages = list(state["messages"])
    messages.append({"role": "assistant", "content": response["raw_content"]})

    tool_result_blocks = [
        {
            "type": "tool_result",
            "tool_use_id": tr["tool_call_id"],
            "content": json.dumps(tr["result"], default=str),
        }
        for tr in tool_results
    ]
    messages.append({"role": "user", "content": tool_result_blocks})

    return {"system": state["system"], "messages": messages}


def _google_init(messages: list[dict]) -> dict:
    from google.genai import types

    system = "\n\n".join(
        str(msg.get("content", "")) for msg in messages if msg.get("role") == "system"
    )
    convo = [msg for msg in messages if msg.get("role") in {"user", "assistant"}]
    if not convo:
        return {"system": system, "history": [], "user_message": ""}

    history_msgs = convo[:-1]
    current = convo[-1]
    history = [
        types.Content(
            role="model" if msg.get("role") == "assistant" else "user",
            parts=[types.Part.from_text(text=str(msg.get("content", "")))],
        )
        for msg in history_msgs
    ]
    return {
        "system": system,
        "history": history,
        "user_message": str(current.get("content", "")),
    }


def _google_call_backend(
    backend: str,
    model: str, state: dict, tools: list[dict],
    temperature: float, max_tokens: int,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Shared ReAct tool-calling loop for both AI Studio and Vertex —
    same wire format through the unified ``google-genai`` SDK. Client
    construction + env-var validation lives in ``llm_providers._google_client``.
    ``tenant_id`` routes through so VERTEX-02's per-tenant project
    override resolves for the Vertex branch.
    """
    from app.engine.llm_providers import _google_client
    from google.genai import types

    client = _google_client(backend, tenant_id=tenant_id)

    def strip_examples(schema: Any) -> Any:
        if isinstance(schema, dict):
            return {k: strip_examples(v) for k, v in schema.items() if k != "examples"}
        if isinstance(schema, list):
            return [strip_examples(i) for i in schema]
        return schema

    google_tools = None
    if tools:
        func_decls = []
        for t in tools:
            # ADMIN-04 — Gemini's FunctionDeclaration validation in the 
            # google-genai SDK is strict and does not permit 'examples' 
            # in the parameters schema.
            params = strip_examples(t["function"]["parameters"])
            func_decls.append(types.FunctionDeclaration(
                name=t["function"]["name"],
                description=t["function"]["description"],
                parameters=params,
            ))
        google_tools = [types.Tool(function_declarations=func_decls)]

    contents = list(state["history"])
    contents.append(types.Content(
        role="user",
        parts=[types.Part.from_text(text=state["user_message"])],
    ))

    config = types.GenerateContentConfig(
        system_instruction=state["system"] or None,
        temperature=temperature,
        max_output_tokens=max_tokens,
        tools=google_tools,
    )

    resp = client.models.generate_content(model=model, contents=contents, config=config)
    usage = resp.usage_metadata

    tool_calls = []
    text_parts = []
    if resp.candidates and resp.candidates[0].content:
        for part in resp.candidates[0].content.parts:
            if part.function_call:
                fc = part.function_call
                tool_calls.append({
                    "id": fc.name,
                    "name": fc.name,
                    "arguments": dict(fc.args) if fc.args else {},
                })
            elif part.text:
                text_parts.append(part.text)

    return {
        "content": "".join(text_parts),
        "tool_calls": tool_calls or None,
        "raw_response": resp,
        "usage": {
            "input_tokens": usage.prompt_token_count if usage else 0,
            "output_tokens": usage.candidates_token_count if usage else 0,
        },
    }


def _google_call(
    model: str, state: dict, tools: list[dict],
    temperature: float, max_tokens: int,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    return _google_call_backend(
        "genai", model, state, tools, temperature, max_tokens,
        tenant_id=tenant_id,
    )


def _vertex_call(
    model: str, state: dict, tools: list[dict],
    temperature: float, max_tokens: int,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    return _google_call_backend(
        "vertex", model, state, tools, temperature, max_tokens,
        tenant_id=tenant_id,
    )


def _google_append(state: dict, response: dict, tool_results: list[dict]) -> dict:
    from google.genai import types

    history = list(state["history"])

    assistant_parts = []
    if response["content"]:
        assistant_parts.append(types.Part.from_text(text=response["content"]))
    for tc in (response["tool_calls"] or []):
        assistant_parts.append(types.Part.from_function_call(
            name=tc["name"],
            args=tc["arguments"],
        ))
    history.append(types.Content(role="model", parts=assistant_parts))

    user_parts = []
    for tr in tool_results:
        user_parts.append(types.Part.from_function_response(
            name=tr["name"],
            response=tr["result"] if isinstance(tr["result"], dict) else {"result": tr["result"]},
        ))
    history.append(types.Content(role="user", parts=user_parts))

    return {
        "system": state["system"],
        "history": history,
        "user_message": "Continue based on the tool results above.",
    }


_PROVIDERS: dict[str, dict[str, Any]] = {
    "openai": {
        "init": _openai_init,
        "call": _openai_call,
        "append_tool_results": _openai_append,
    },
    "anthropic": {
        "init": _anthropic_init,
        "call": _anthropic_call,
        "append_tool_results": _anthropic_append,
    },
    "google": {
        "init": _google_init,
        "call": _google_call,
        "append_tool_results": _google_append,
    },
    "vertex": {
        # Vertex reuses the genai-backed init + append helpers; only
        # ``call`` differs (different Client constructor).
        "init": _google_init,
        "call": _vertex_call,
        "append_tool_results": _google_append,
    },
}
