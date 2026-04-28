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
import re
import time
from typing import Any

from app.config import settings
from app.database import SessionLocal
from app.engine.memory_service import assemble_agent_messages

logger = logging.getLogger(__name__)

_MAX_ITERATIONS_HARD_CAP = 25
_REACT_TOTAL_TIMEOUT = 200  # seconds — hard cap for entire ReAct loop


def run_react_loop(
    node_data: dict,
    context: dict[str, Any],
    tenant_id: str,
) -> dict[str, Any]:
    """Execute a ReAct agent loop with tool calling."""
    from app.engine.model_registry import default_llm_for

    config = node_data.get("config", {})
    provider = config.get("provider", "vertex")

    # Safety fallback: if 'google' (AI Studio) is requested but no valid API key
    # is available (or it's the placeholder UUID), and Vertex is configured,
    # reroute to Vertex to avoid 400 errors.
    is_valid_key = settings.google_api_key and not settings.google_api_key.startswith("5b-b4a8")
    if provider == "google" and not is_valid_key and settings.vertex_project:
        logger.warning("Rerouting 'google' provider call to 'vertex' (invalid or missing AI Studio key)")
        provider = "vertex"

    model = config.get("model") or default_llm_for(provider, role="fast")
    raw_prompt = config.get("systemPrompt", "")
    max_iterations = min(int(config.get("maxIterations", 10)), _MAX_ITERATIONS_HARD_CAP)
    tool_names: list[str] = config.get("tools", [])
    mcp_server_label: str | None = config.get("mcpServerLabel") or None
    temperature = float(config.get("temperature", 0.7))
    max_tokens = int(config.get("maxTokens", 4096))

    from app.engine.prompt_template import render_prompt

    system_prompt = render_prompt(raw_prompt, context)
    # CONCISE-01 — Instruct the agent to be concise to stay within gateway limits
    system_prompt += (
        "\n\nBe extremely concise. Summarize tool outputs unless details are requested. "
        "Avoid conversational filler. If a list is long, summarize the top 5 and ask to continue."
    )
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
        context=context,
    )

    total_usage = {"input_tokens": 0, "output_tokens": 0}
    iterations: list[dict[str, Any]] = []

    handler = _PROVIDERS.get(provider)
    if not handler:
        raise ValueError(f"Unknown LLM provider for ReAct: {provider}")

    messages = handler["init"](initial_messages)
    react_start = time.monotonic()

    for i in range(max_iterations):
        # TIMEOUT-GUARD — abort if total ReAct loop time exceeds cap
        elapsed = time.monotonic() - react_start
        if elapsed > _REACT_TOTAL_TIMEOUT:
            logger.warning(
                "ReAct total timeout after %.1fs (%d iterations)", elapsed, i,
            )
            iterations.append({"iteration": i + 1, "action": "timeout"})
            return {
                "response": f"Agent timed out after {int(elapsed)}s. Partial results may be available in the iterations log.",
                "provider": provider,
                "model": model,
                "usage": total_usage,
                "iterations": iterations,
                "total_iterations": i,
                "memory_debug": memory_debug,
            }

        logger.info("ReAct [%s/%s] iteration %d/%d (%.1fs elapsed)", provider, model, i + 1, max_iterations, elapsed)

        iteration_start = time.monotonic()
        try:
            response = handler["call"](
                model, messages, tool_defs, temperature, max_tokens,
                tenant_id=tenant_id,
            )
        except Exception as exc:
            logger.error("ReAct LLM call failed on iteration %d: %s", i + 1, exc)
            iterations.append({"iteration": i + 1, "action": "llm_error", "error": str(exc)[:500]})
            return {
                "response": f"LLM call failed: {exc}",
                "provider": provider,
                "model": model,
                "usage": total_usage,
                "iterations": iterations,
                "total_iterations": i + 1,
                "memory_debug": memory_debug,
            }

        total_usage["input_tokens"] += response["usage"]["input_tokens"]
        total_usage["output_tokens"] += response["usage"]["output_tokens"]
        
        llm_duration = time.monotonic() - iteration_start
        logger.info("ReAct iteration %d LLM call took %.1fs", i + 1, llm_duration)

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
        tools_start = time.monotonic()
        for tc in response["tool_calls"]:
            tool_name = tc["name"]
            tool_args = tc["arguments"]
            logger.info("ReAct calling tool: %s(%s)", tool_name, json.dumps(tool_args)[:200])

            try:
                result = _execute_tool(
                    tool_name, tool_args, tenant_id,
                    server_label=mcp_server_label,
                    context=context,
                )
                
                # HITL-01 — Detect tool-initiated approval requests.
                # If a tool returns AWAITING_APPROVAL, we suspend the ReAct loop
                # and the entire workflow instance.
                if isinstance(result, dict) and result.get("status") == "AWAITING_APPROVAL":
                    from app.engine.exceptions import NodeSuspendedAsync
                    logger.info("Tool %s requested approval. Suspending workflow.", tool_name)
                    raise NodeSuspendedAsync(
                        async_job_id=f"hitl-{tool_name}",
                        system="human_approval",
                        external_job_id=result.get("agent_id", "system")
                    )

            except NodeSuspendedAsync:
                raise  # propagate suspension up to dag_runner
            except Exception as exc:
                logger.error("ReAct tool %s failed: %s", tool_name, exc)
                result = {"error": f"Tool call failed: {exc}"}

            # TRUNCATION-01 — sanitize and truncate tool result to prevent context bloat
            sanitized_result = _sanitize_tool_result(result)

            tool_results.append({
                "tool_call_id": tc.get("id", tool_name),
                "name": tool_name,
                "result": sanitized_result,
            })
        
        tools_duration = time.monotonic() - tools_start
        logger.info("ReAct iteration %d tool calls (%d) took %.1fs", i + 1, len(tool_results), tools_duration)

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


def _sanitize_tool_result(result: Any, max_chars: int = 8000) -> Any:
    """Sanitize and truncate tool output to prevent LLM context bloat."""
    try:
        if isinstance(result, (dict, list)):
            import json
            result_str = json.dumps(result, default=str)
            if len(result_str) > max_chars:
                return result_str[:max_chars] + f"\n... [truncated {len(result_str) - max_chars} chars]"
            return result
        
        s = str(result)
        if len(s) > max_chars:
            return s[:max_chars] + f"\n... [truncated {len(s) - max_chars} chars]"
        return result
    except Exception:
        s = str(result)
        if len(s) > max_chars:
            return s[:max_chars] + "... [truncated]"
        return s


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def _execute_tool(
    tool_name: str,
    arguments: dict,
    tenant_id: str,
    *,
    server_label: str | None = None,
    context: dict[str, Any] | None = None,
) -> Any:
    """Execute a tool on the MCP server resolved for this tenant + label."""
    # HITL-02 — If we have a pending/approved HITL payload in context,
    # pass the approval ID to the tool so it can bypass the guard.
    if context and isinstance(context.get("approval"), dict):
        approval = context["approval"]
        # The Technical User screen passes the approval payload on resume.
        if approval.get("approved"):
            arguments["technical_approval_id"] = approval.get("approval_id") or "human-approved"

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
    context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Load tool definitions from the MCP server resolved for this tenant.

    If tool_names is None (empty config), auto-discovers all tools and
    filters them based on semantic relevance to the current query.
    """
    from app.engine.mcp_client import get_openai_style_tool_defs, list_tools
    
    if tool_names is None:
        raw = list_tools(tenant_id=tenant_id, server_label=server_label)
        
        # SMART-FILTER: If we have context, prune tools that are clearly irrelevant
        # to the current user query to save tokens and avoid LLM confusion.
        filtered_tools = raw
        query = str(context.get("trigger", {}).get("message", "") or "").lower()
        if query and context:
            # Simple keyword scoring for "Smart" filtering
            keywords = set(re.findall(r"\w+", query))
            if keywords:
                scored = []
                for t in raw:
                    name = t["name"].lower()
                    desc = t["description"].lower()
                    score = 0
                    # Boost for direct matches in name/desc
                    for kw in keywords:
                        if kw in name: score += 5
                        if kw in desc: score += 2
                    # Base score for essential diagnostic/status tools
                    if any(x in name for x in ("status", "health", "summary")):
                        score += 1
                    scored.append((score, t))
                
                # Take tools with score > 0, or at least the top 15 most relevant
                scored.sort(key=lambda x: x[0], reverse=True)
                filtered_tools = [t for score, t in scored if score > 0][:15]
                
                # Ensure we have at least SOME tools if scoring was too aggressive
                if not filtered_tools:
                    filtered_tools = raw[:10]
        
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            }
            for t in filtered_tools
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

    google_tools = None
    if tools:
        def strip_non_standard(obj: Any) -> Any:
            if not isinstance(obj, dict):
                return obj
            new_obj = {}
            for k, v in obj.items():
                if k in ("examples", "title", "default"):
                    continue
                if isinstance(v, dict):
                    new_obj[k] = strip_non_standard(v)
                elif isinstance(v, list):
                    new_obj[k] = [strip_non_standard(i) for i in v]
                else:
                    new_obj[k] = v
            return new_obj

        func_decls = []
        for t in tools:
            # GCP-04 — strip 'examples', 'title', 'default' from the JSON schema. 
            # The google-genai SDK's FunctionDeclaration/Schema Pydantic models 
            # use extra='forbidden', so non-standard fields cause 422 errors.
            params = strip_non_standard(t.get("function", {}).get("parameters", {}))

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

    logger.info("ReAct starting Google GenAI call (model=%s, tools=%d)", model, len(google_tools) if google_tools else 0)
    resp = client.models.generate_content(model=model, contents=contents, config=config)
    logger.info("ReAct Google GenAI call finished")
    usage = resp.usage_metadata

    tool_calls = []
    text_parts = []
    thought_parts = []
    raw_parts = []
    
    # SAFETY: Ensure we have candidates and a content object before iterating
    if resp.candidates and len(resp.candidates) > 0 and resp.candidates[0].content:
        content = resp.candidates[0].content
        # SAFETY: Some SDK versions or model states might return parts as None
        if content.parts:
            raw_parts = list(content.parts)
            for part in content.parts:
                if getattr(part, "thought", None) or getattr(part, "thought_signature", None):
                    thought_parts.append({
                        "thought": getattr(part, "thought", None),
                        "thought_signature": getattr(part, "thought_signature", None)
                    })
                
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
        "thought_parts": thought_parts,
        "raw_parts": raw_parts,
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
    
    if response.get("raw_parts"):
        # GEMINI-3: Replay raw parts to maintain perfect fidelity (signatures, etc)
        assistant_parts.extend(response["raw_parts"])
    else:
        # GEMINI-3 Fallback: Replay thought parts FIRST if present
        for tp in response.get("thought_parts", []):
            assistant_parts.append(types.Part(
                thought=tp.get("thought"),
                thought_signature=tp.get("thought_signature")
            ))

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
