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

    # CTX-MGMT.G — by default, the node's output exposes only a
    # SUMMARY of iterations (action + tool names, no args / no
    # results / no full LLM content). Authors who need the full
    # reasoning trace for debugging or for downstream Verifier-style
    # introspection can opt in via exposeFullIterations: True; that
    # surfaces the verbose form under `iterations_full` (separate key
    # so the summary stays the canonical default).
    expose_full_iterations = bool(config.get("exposeFullIterations", False))

    # CATEGORY-01 (V10) — optional read/case/remediation/glossary/web
    # allowlist; engine drops tools whose category is not present BEFORE
    # the model sees them. Used by the Verifier ReAct to enforce
    # read-only mechanically (its prompt has always claimed read-only
    # but nothing previously prevented a destructive call). Empty / None
    # = all categories allowed (default behavior).
    raw_categories = config.get("allowedToolCategories") or config.get("allowed_tool_categories")
    allowed_categories: set[str] | None = (
        {str(c).strip().lower() for c in raw_categories if str(c).strip()}
        if isinstance(raw_categories, list) and raw_categories
        else None
    )

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
        allowed_categories=allowed_categories,
    )

    # TOOL-NAME-NORMALIZATION — Gemini's function-calling spec disallows dots
    # in tool names, so the Vertex SDK sanitizes "case.add_worknote" →
    # "case_add_worknote" before showing it to the model. The model then
    # invokes the sanitized name, but mcp_client.call_tool needs the
    # original dotted name to route correctly. Build a map of every
    # plausible sanitization variant → original name.
    tool_name_map: dict[str, str] = {}
    for _td in tool_defs:
        _original = (_td.get("function") or {}).get("name", "")
        if not _original:
            continue
        tool_name_map[_original] = _original
        tool_name_map[_original.replace(".", "_")] = _original
        # Last-segment fallback (model occasionally drops the namespace
        # entirely). Skip if it would collide with a previously-mapped
        # original name from a different namespace.
        _last = _original.rsplit(".", 1)[-1]
        if _last and _last not in tool_name_map:
            tool_name_map[_last] = _original

    total_usage = {"input_tokens": 0, "output_tokens": 0}
    iterations: list[dict[str, Any]] = []

    # HITL-04 — Re-fire previously-planned destructive tool on resume.
    # When a destructive tool returned AWAITING_APPROVAL, we persisted
    # the (tool, args) pair into context["hitl_pending_call"] before
    # suspending. On resume the engine re-enters this node from scratch;
    # without intervention the LLM would re-deliberate and often pick a
    # different action than the one the operator actually approved
    # (it has no notion of "what was approved"). Instead, fire the
    # persisted call directly with the approval token, surface the
    # result to the model as a synthetic user turn, and let it produce
    # the confirmation reply. The approval is then consumed so any
    # additional destructive call in this turn re-engages the gate.
    # CTX-MGMT.D — hitl_pending_call lives under _runtime so it survives
    # the suspend/resume strip. Fall back to the legacy flat key for
    # any context_json still in the old shape — the dag_runner entry
    # points hoist on resume, but defence in depth.
    runtime = context.get("_runtime") or {}
    pending = runtime.get("hitl_pending_call") or context.get("hitl_pending_call")
    approval = context.get("approval") if isinstance(context.get("approval"), dict) else None
    if pending and approval and approval.get("approved"):
        pending_tool = pending.get("tool") or ""
        pending_args = dict(pending.get("arguments") or {})
        try:
            logger.info("HITL-04: re-firing approved tool %s on resume", pending_tool)
            refired_result = _execute_tool(
                pending_tool, pending_args, tenant_id,
                server_label=mcp_server_label,
                context=context,  # injects technical_approval_id from approval
            )
        except Exception as exc:
            logger.error("HITL-04: re-fire of %s failed: %s", pending_tool, exc)
            refired_result = {"error": f"Approved tool re-fire failed: {exc}"}

        sanitized_refired = _sanitize_tool_result(refired_result)
        synthetic_notice = (
            "[System notice — HITL gate cleared]\n"
            "Your previously-planned tool call was approved by the human "
            "reviewer and has just been executed on your behalf. "
            "The approval token is single-use and the action is done.\n\n"
            f"Tool: {pending_tool}\n"
            f"Arguments: {json.dumps(pending_args, default=str)[:1000]}\n"
            f"Result: {json.dumps(sanitized_refired, default=str)[:1500]}\n\n"
            "Acknowledge the action to the user and report any follow-up "
            "diagnostics. Do NOT call this tool again in this turn."
        )
        initial_messages.append({"role": "user", "content": synthetic_notice})

        iterations.append({
            "iteration": 0,
            "action": "approved_tool_executed",
            "tool_calls": [{"name": pending_tool, "arguments": pending_args}],
            "tool_results": [
                {"name": pending_tool, "result_preview": str(sanitized_refired)[:500]}
            ],
        })

        # Consume the pending marker and the approval so subsequent
        # destructive calls in this same turn engage a fresh gate.
        # Read-only tools that don't check the token are unaffected.
        if isinstance(context.get("_runtime"), dict):
            context["_runtime"].pop("hitl_pending_call", None)
        context.pop("hitl_pending_call", None)  # legacy shape
        context.pop("approval", None)

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
                **_finalize_iterations_payload(iterations, expose_full=expose_full_iterations),
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
                **_finalize_iterations_payload(iterations, expose_full=expose_full_iterations),
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
                **_finalize_iterations_payload(iterations, expose_full=expose_full_iterations),
                "total_iterations": i + 1,
                "memory_debug": memory_debug,
            }

        tool_results = []
        tools_start = time.monotonic()
        for tc in response["tool_calls"]:
            raw_name = tc["name"]
            # Translate Vertex-sanitized name (case_add_worknote) back to the
            # MCP-registered original (case.add_worknote). Falls through if
            # already a known name.
            tool_name = tool_name_map.get(raw_name, raw_name)
            if tool_name != raw_name:
                logger.info("ReAct tool name normalized: %r → %r", raw_name, tool_name)
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
                    # HITL-04 — Persist the planned (tool, args) so resume
                    # can re-fire it directly instead of re-deliberating.
                    # CTX-MGMT.D — under _runtime now so it survives the
                    # suspend/resume strip via the explicit allowlist
                    # rather than the previous "happens to not start
                    # with underscore" accident.
                    runtime_pending = context.get("_runtime")
                    if not isinstance(runtime_pending, dict):
                        runtime_pending = {}
                        context["_runtime"] = runtime_pending
                    runtime_pending["hitl_pending_call"] = {
                        "tool": tool_name,
                        "arguments": tool_args,
                        "iteration": i + 1,
                    }
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
        **_finalize_iterations_payload(iterations, expose_full=expose_full_iterations),
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


_REMEDIATION_FRAGMENTS = (
    "restart", "rerun", "terminate", "rotate", "change_schedule",
    "change_priority", "run_now", "resubmit", "kill", "cancel_",
)


def _categorize_tool(name: str) -> str:
    """Classify an MCP tool into a coarse category from its name.

    Matters for the optional `allowedToolCategories` ReAct config: the
    Verifier sandbox filters by these categories so destructive tools
    are mechanically not in scope, even if the LLM tries to call them.

    Categories:
      - "case"         — case.*           (case management)
      - "glossary"     — glossary.*       (business → workflow_id)
      - "web"          — google_search    (web search)
      - "remediation"  — anything matching a destructive verb fragment
                         (restart / rerun / terminate / rotate / kill /
                          run_now / resubmit / cancel_ / change_*)
      - "read"         — everything else (search / list / get_* /
                         diagnose / status / summary)

    Order matters — case/glossary/web are matched on prefix BEFORE the
    destructive-fragment scan so e.g. case.close stays in 'case' rather
    than being misclassified as remediation by 'close' substring.
    """
    if not name:
        return "read"
    lname = name.lower()
    if lname.startswith("case."):
        return "case"
    if lname.startswith("glossary."):
        return "glossary"
    if lname.startswith(("google_search", "google.", "web.")):
        return "web"
    for frag in _REMEDIATION_FRAGMENTS:
        if frag in lname:
            return "remediation"
    return "read"


def _load_tool_definitions(
    tool_names: list[str] | None,
    *,
    tenant_id: str,
    server_label: str | None = None,
    context: dict[str, Any] | None = None,
    allowed_categories: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Load tool definitions from the MCP server resolved for this tenant.

    If tool_names is None (empty config), auto-discovers all tools and
    filters them based on semantic relevance to the current query.

    `allowed_categories` (V10) — when provided, drops any tool whose
    category (per `_categorize_tool`) is not in the set. Applied to
    BOTH the always-pinned set and the candidate pool BEFORE the
    semantic top-K ranks them, so the Verifier's read-only sandbox
    is enforced even on tools that would otherwise be pinned.
    """
    from app.engine.mcp_client import get_openai_style_tool_defs, list_tools
    
    if not tool_names:
        raw = list_tools(tenant_id=tenant_id, server_label=server_label)

        # CATEGORY-01 (V10) — apply allowed-category allowlist BEFORE
        # the pinned/candidate split. Tools whose category isn't in
        # the allowlist are dropped here and never seen by the model.
        if allowed_categories:
            before = len(raw)
            raw = [t for t in raw if _categorize_tool(t.get("name", "")) in allowed_categories]
            logger.info(
                "ReAct allowedToolCategories=%s — kept %d/%d tools",
                sorted(allowed_categories), len(raw), before,
            )

        # ALWAYS-AVAILABLE tools — pinned through whatever filter runs so the
        # Worker can always reach for case management + glossary translation
        # + the basic AE discovery primitives that bootstrap any investigation.
        # The MCP server marks these `always_available: true` in metadata,
        # but list_tools doesn't propagate that field through the wire format
        # so the orchestrator-side allowlist is the source of truth.
        #
        # ALWAYS_AVAILABLE_NAMES intentionally includes a small set of AE
        # discovery entry points (search / list / get_status / get_summary)
        # that the embedding ranker would otherwise miss when the user query
        # uses an action verb ("restart") — embedding similarity favours
        # action tools (ae.agent.restart_service) over the discovery tools
        # the Worker needs to call FIRST. Discovery tools are read-only and
        # cheap; pinning them is essentially free.
        ALWAYS_AVAILABLE_PREFIXES = ("case.", "glossary.")
        ALWAYS_AVAILABLE_NAMES: set[str] = {
            # AE discovery / read-only diagnostics
            "ae.workflow.search",
            "ae.workflow.list",
            "ae.workflow.list_for_user",
            "ae.workflow.get_details",
            "ae.workflow.get_recent_failure_stats",
            "ae.request.list_recent",
            "ae.request.list_failed_recently",
            "ae.request.get_summary",
            "ae.request.get_failure_message",
            "ae.agent.list_running",
            "ae.agent.list_stopped",
            "ae.agent.get_status",
            # Core remediation tools — always pinned so the Worker never
            # hallucinates a tool name when these miss the semantic top-K.
            "ae.request.resubmit_from_start",
            "ae.request.resubmit_from_failure_point",
            "ae.request.restart_failed",
            "ae.request.restart",
            "ae.request.terminate_running",
            "ae.request.cancel_new_or_retry",
        }

        # Top-K domain tools exposed to the model. 15 was too tight when the
        # query word ("restart") matched action tools (ae.agent.restart_service)
        # higher than the discovery tools the Worker needs first
        # (ae.workflow.search). 25 gives the embedding ranker more headroom
        # without saturating Gemini's function-calling budget — still well
        # under the practical ~30-40 declarations limit.
        TOP_K_DOMAIN = 25

        query = str((context or {}).get("trigger", {}).get("message", "") or "").strip()

        # Split into pinned vs candidate-domain pools
        pinned: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        for t in raw:
            tname = t["name"]
            if (
                any(tname.startswith(p) for p in ALWAYS_AVAILABLE_PREFIXES)
                or tname in ALWAYS_AVAILABLE_NAMES
            ):
                pinned.append(t)
            else:
                candidates.append(t)

        # Embedding-based semantic filter for the domain pool. Replaces the
        # earlier keyword-regex scoring (the user explicitly flagged that
        # pattern as an anti-pattern — fast LLMs / embeddings should be the
        # primary classifier, not heuristics).
        #
        # Strategy:
        #   1. Embed the query (tenant-cached via embedding_cache).
        #   2. Embed each tool's "name + description" once and cache it
        #      (the same get_or_embed path Intent Classifier uses).
        #   3. Cosine-rank, take top-K.
        # Falls back to "expose all candidates" on any embedding error so a
        # transient provider hiccup never strands the agent without tools.
        ranked: list[dict[str, Any]] = candidates  # default fallback
        if query and candidates:
            try:
                from app.engine.embedding_cache_helper import get_or_embed

                embed_provider = (
                    getattr(settings, "smart_05_embedding_provider", None)
                    or settings.embedding_default_provider
                    or "vertex"
                )
                embed_model = (
                    getattr(settings, "smart_05_embedding_model", None)
                    or "text-embedding-005"
                )

                tool_texts = [
                    f"{t['name']}\n{(t.get('description') or '').strip()}"
                    for t in candidates
                ]
                with SessionLocal() as db:
                    from app.database import set_tenant_context
                    set_tenant_context(db, tenant_id)
                    query_vec = get_or_embed(
                        tenant_id, [query], embed_provider, embed_model, db
                    )[0]
                    tool_vecs = get_or_embed(
                        tenant_id, tool_texts, embed_provider, embed_model, db
                    )

                def _cosine(a: list[float], b: list[float]) -> float:
                    if not a or not b:
                        return 0.0
                    dot = 0.0
                    na = 0.0
                    nb = 0.0
                    for x, y in zip(a, b):
                        dot += x * y
                        na += x * x
                        nb += y * y
                    if na <= 0 or nb <= 0:
                        return 0.0
                    return dot / ((na ** 0.5) * (nb ** 0.5))

                scored = [
                    (_cosine(query_vec, tv), t)
                    for tv, t in zip(tool_vecs, candidates)
                ]
                scored.sort(key=lambda x: x[0], reverse=True)
                ranked = [t for _, t in scored[:TOP_K_DOMAIN]]
                top5 = ", ".join(
                    f"{t['name']}={s:.3f}" for s, t in scored[:5]
                )
                logger.info(
                    "ReAct tool filter: %d pinned + top-%d of %d via embeddings "
                    "(%s/%s); top5: %s",
                    len(pinned),
                    len(ranked),
                    len(candidates),
                    embed_provider,
                    embed_model,
                    top5,
                )
            except Exception as exc:
                logger.warning(
                    "Embedding-based tool filter failed (%s); falling back to "
                    "exposing first %d candidates plus pinned set",
                    exc,
                    TOP_K_DOMAIN,
                )
                ranked = candidates[:TOP_K_DOMAIN]

        filtered_tools = pinned + ranked
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
        
    explicit_defs = get_openai_style_tool_defs(
        tool_names,
        tenant_id=tenant_id,
        server_label=server_label,
    )
    if allowed_categories:
        before = len(explicit_defs)
        explicit_defs = [
            d for d in explicit_defs
            if _categorize_tool((d.get("function") or {}).get("name", "")) in allowed_categories
        ]
        logger.info(
            "ReAct allowedToolCategories=%s — kept %d/%d explicit tools",
            sorted(allowed_categories), len(explicit_defs), before,
        )
    return explicit_defs


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


# ---------------------------------------------------------------------------
# CTX-MGMT.G — iterations summary helper
# ---------------------------------------------------------------------------


def _summarize_iterations(iterations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the safe public summary of a ReAct loop's iterations.

    The full iterations list contains the LLM's reasoning content,
    tool call args (which may include user data / tokens), and tool
    results. Downstream nodes — including downstream LLMs reading
    via Jinja — should NOT see this state by default; the prompt is
    private to the producing agent.

    The summary preserves what's safely useful for inspection:

      * ``iteration`` — the iteration index.
      * ``action`` — one of ``tool_use`` / ``final_response`` /
        ``timeout`` / ``llm_error`` / ``max_iterations_exceeded`` /
        ``approved_tool_executed``.
      * ``tool_calls`` (when ``action == "tool_use"``) — list of
        ``{"name": str}``. Just the name. No args, no results.
        That's enough for the predicate evaluator's "did X call Y?"
        question without leaking what was passed.
      * ``content_length`` (when ``action == "final_response"``) —
        an integer for telemetry; the actual content is the agent's
        ``response`` field at the top level (which IS exposed).
      * ``error`` (when ``action`` is an error variant) — truncated
        error string.

    Authors who genuinely need the full trace (Verifier patterns,
    debug nodes) can set ``exposeFullIterations: True`` on the
    ReAct node config. The verbose form then appears under
    ``iterations_full``; the safe summary remains under
    ``iterations``.
    """
    out: list[dict[str, Any]] = []
    for entry in iterations:
        if not isinstance(entry, dict):
            continue
        action = entry.get("action") or "unknown"
        summary: dict[str, Any] = {
            "iteration": entry.get("iteration"),
            "action": action,
        }
        if action == "tool_use":
            tool_calls_in = entry.get("tool_calls") or []
            summary["tool_calls"] = [
                {"name": (tc.get("name") if isinstance(tc, dict) else None) or "<unknown>"}
                for tc in tool_calls_in
                if tc is not None
            ]
        elif action == "final_response":
            content = entry.get("content")
            if isinstance(content, str):
                summary["content_length"] = len(content)
        elif action in ("llm_error", "timeout", "max_iterations_exceeded"):
            err = entry.get("error")
            if isinstance(err, str):
                summary["error"] = err[:200]
        elif action == "approved_tool_executed":
            # HITL-04 re-fire — keep the tool name so the audit
            # trail can show which approved tool was re-invoked.
            tcs = entry.get("tool_calls") or []
            if tcs and isinstance(tcs[0], dict):
                summary["tool_name"] = tcs[0].get("name")
        out.append(summary)
    return out


def _finalize_iterations_payload(
    iterations: list[dict[str, Any]],
    *,
    expose_full: bool,
) -> dict[str, Any]:
    """Return the dict that gets merged into the ReAct loop's output.

    Always includes ``iterations`` (the safe summary). Optionally
    includes ``iterations_full`` (the verbose form, scrubbed for
    sensitive keys via the engine's ``scrub_secrets``) when the
    node config opted into ``exposeFullIterations``.
    """
    payload: dict[str, Any] = {"iterations": _summarize_iterations(iterations)}
    if expose_full:
        from app.engine.scrubber import scrub_secrets
        # scrub_secrets is pure/functional — original list untouched.
        payload["iterations_full"] = scrub_secrets(iterations)
    return payload
