"""Per-node-type execution handlers.

Each handler receives the node's data dict, the accumulated execution context,
and the tenant_id.  It returns a JSON-serializable output dict that gets stored
in the context keyed by node_id.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def check_subworkflow_recursion(
    *,
    parent_chain: list[str],
    current_wf_id: str,
    target_wf_id: str,
    target_name: str,
    max_depth: int,
) -> list[str]:
    """Validate a Sub-Workflow call against the ancestor chain.

    Builds ``parent_chain + [current_wf_id]`` and raises ``ValueError`` if
    the target workflow is already in that chain (cycle) or if the chain
    has already reached ``max_depth`` (too deep). Returns the extended
    chain on success so the caller can thread it into the child context.

    Separated from ``_handle_sub_workflow`` so it can be unit-tested
    without touching the database.
    """
    full_chain = list(parent_chain)
    if current_wf_id:
        full_chain.append(current_wf_id)

    if target_wf_id in full_chain:
        raise ValueError(
            f"Sub-Workflow: recursive cycle detected — workflow '{target_name}' "
            f"is already in the call chain"
        )
    if len(full_chain) >= max_depth:
        raise ValueError(
            f"Sub-Workflow: maximum nesting depth ({max_depth}) exceeded"
        )
    return full_chain


def dispatch_node(
    node_data: dict, context: dict[str, Any], tenant_id: str,
    db: Any = None,
) -> dict[str, Any]:
    # ── DV-01 data pinning — short-circuit dispatch ─────────────────────────
    # When operators pin a node's output in the PropertyInspector, the pinned
    # payload lives at ``node_data.pinnedOutput`` (set via
    # ``POST /workflows/{id}/nodes/{node_id}/pin``). On the next run the node
    # skips its handler entirely and returns the pin — kills LLM token cost
    # during iteration and makes Condition-branch debugging tractable.
    #
    # ``_from_pin`` is an underscore-prefixed breadcrumb so ``_get_clean_
    # context`` strips it before persisting, but ``log_entry.output_json``
    # (which doesn't strip) preserves it so the UI can badge the node as
    # "returned from pin, not a live execution".
    pinned = node_data.get("pinnedOutput")
    if isinstance(pinned, dict):
        logger.info(
            "Node %r returning pinned output (skipping execution)",
            node_data.get("label"),
        )
        return {**pinned, "_from_pin": True}

    # ── Resolve {{ env.* }} references in config (Component 6) ──
    try:
        from app.engine.prompt_template import resolve_config_env_vars
        node_data = dict(node_data)  # shallow copy to avoid mutating original
        node_data["config"] = resolve_config_env_vars(
            node_data.get("config", {}), tenant_id
        )
    except Exception as exc:
        logger.warning("Env var resolution failed (non-fatal): %s", exc)

    category = node_data.get("nodeCategory", "action")
    label = node_data.get("label", "")
    handlers = {
        "trigger": _handle_trigger,
        "agent": _handle_agent,
        "action": _handle_action,
        "logic": _handle_logic,
        "notification": _handle_action,
    }

    # ForEach / Loop / While are logic nodes with special dispatch handled by
    # dag_runner. While reuses Loop's runner (same continueExpression shape).
    if category == "logic" and label == "ForEach":
        return _handle_forEach(node_data, context, tenant_id)
    if category == "logic" and label == "Loop":
        return _handle_loop(node_data, context, tenant_id)
    if category == "logic" and label == "While":
        return _handle_while(node_data, context, tenant_id)
    # NODES-01.a — Switch: multi-branch routing. Handled inline so the
    # branch result lands on the node output the dag_runner reads.
    if category == "logic" and label == "Switch":
        return _handle_switch(node_data, context, tenant_id)

    # Conversational memory nodes — special dispatch regardless of category
    if label == "Load Conversation State":
        return _handle_load_conversation_state(node_data, context, tenant_id)
    if label == "Save Conversation State":
        return _handle_save_conversation_state(node_data, context, tenant_id)
    if label == "Archive Active Episode":
        return _handle_archive_conversation_episode(node_data, context, tenant_id)
    if label == "Bridge User Reply":
        return _handle_bridge_user_reply(node_data, context, tenant_id)
    if label == "LLM Router":
        return _handle_llm_router(node_data, context, tenant_id)
    if label == "A2A Agent Call":
        return _handle_a2a_call(node_data, context, tenant_id)
    if label == "AutomationEdge":
        return _handle_automation_edge(node_data, context, tenant_id, db)
    if label == "Sub-Workflow":
        return _handle_sub_workflow(node_data, context, tenant_id, db)
    if label == "Reflection":
        from app.engine.reflection_handler import _handle_reflection
        return _handle_reflection(node_data, context, tenant_id)
    if label == "Intent Classifier":
        from app.engine.intent_classifier import _handle_intent_classifier
        return _handle_intent_classifier(node_data, context, tenant_id)
    if label == "Entity Extractor":
        from app.engine.entity_extractor import _handle_entity_extractor
        return _handle_entity_extractor(node_data, context, tenant_id)
    if label == "Knowledge Retrieval":
        return _handle_knowledge_retrieval(node_data, context, tenant_id)
    if label == "Code":
        return _handle_code_execution(node_data, context, tenant_id)
    if label == "Notification":
        from app.engine.notification_handler import _handle_notification
        return _handle_notification(node_data, context, tenant_id)

    handler = handlers.get(category, _handle_action)
    return handler(node_data, context, tenant_id)


def _handle_trigger(
    node_data: dict, context: dict[str, Any], _tenant_id: str
) -> dict[str, Any]:
    """Trigger nodes simply pass through whatever payload started the workflow."""
    return {"output": context.get("trigger", {})}


def _handle_agent(
    node_data: dict, context: dict[str, Any], tenant_id: str
) -> dict[str, Any]:
    """Execute an LLM agent node.

    Routes to the ReAct loop if the node has tools configured,
    otherwise performs a single LLM call.
    """
    config = node_data.get("config", {})
    label = node_data.get("label", "")

    is_react = label == "ReAct Agent"

    if is_react:
        from app.engine.react_loop import run_react_loop
        return run_react_loop(node_data, context, tenant_id)

    from app.database import SessionLocal, set_tenant_context
    from app.engine.llm_providers import call_llm_streaming
    from app.engine.memory_service import assemble_agent_messages
    from app.engine.model_registry import default_llm_for
    from app.engine.prompt_template import render_prompt

    from app.config import settings
    provider = config.get("provider", settings.llm_default_provider or "vertex")
    model = config.get("model") or default_llm_for(provider, role="fast")
    raw_prompt = config.get("systemPrompt", "")
    temperature = float(config.get("temperature", 0.7))
    max_tokens = int(config.get("maxTokens", 4096))

    system_prompt = render_prompt(raw_prompt, context)
    # CTX-MGMT.J v2 — render distillBlocks separately and pass to
    # assemble_agent_messages so it lands in the per-turn user
    # message instead of the system prompt. Keeps the system prompt
    # stable across turns so provider prefix caches actually hit.
    from app.engine.distill import render_distill_blocks
    _distill_text = render_distill_blocks(context, config.get("distillBlocks"))
    db = SessionLocal()
    try:
        set_tenant_context(db, tenant_id)
        prompt_messages, memory_debug = assemble_agent_messages(
            db,
            tenant_id=tenant_id,
            workflow_def_id=str(context.get("_workflow_def_id", "") or ""),
            context=context,
            node_config=config,
            rendered_system_prompt=system_prompt,
            distill_text=_distill_text,
        )
    finally:
        db.close()

    instance_id: str = context.get("_instance_id", "")
    node_id: str = context.get("_current_node_id", "")

    logger.info(
        "Agent node [%s/%s]: messages=%d, streaming=%s",
        provider, model, len(prompt_messages),
        bool(instance_id and node_id),
    )

    result = call_llm_streaming(
        provider=provider,
        model=model,
        system_prompt=system_prompt,
        user_message="",
        temperature=temperature,
        max_tokens=max_tokens,
        instance_id=instance_id,
        node_id=node_id,
        messages=prompt_messages,
        tenant_id=tenant_id,
    )
    result["memory_debug"] = memory_debug

    logger.info(
        "Agent node [%s/%s]: tokens in=%d out=%d",
        provider, model,
        result["usage"]["input_tokens"],
        result["usage"]["output_tokens"],
    )

    from app.observability import record_generation
    record_generation(
        context.get("_trace"),
        name=f"llm:{provider}/{model}",
        provider=provider,
        model=model,
        system_prompt=system_prompt,
        user_message=prompt_messages[-1]["content"] if prompt_messages else "",
        response=result.get("response", ""),
        usage=result.get("usage"),
        metadata={"memory": memory_debug},
    )

    return result


def _handle_action(
    node_data: dict, context: dict[str, Any], tenant_id: str
) -> dict[str, Any]:
    """Execute action nodes: MCP tool calls, HTTP requests, etc."""
    config = node_data.get("config", {})
    label = node_data.get("label", "")

    if config.get("toolName"):
        return _call_mcp_tool(
            config["toolName"],
            config.get("parameters", {}),
            tenant_id,
            server_label=config.get("mcpServerLabel") or None,
        )

    if config.get("url"):
        return _call_http(config, context)

    logger.warning("Action node '%s' has no executable config", label)
    return {"output": None, "warning": "No action configured"}


def _call_mcp_tool(
    tool_name: str,
    parameters: dict,
    tenant_id: str,
    *,
    server_label: str | None = None,
) -> dict[str, Any]:
    """Invoke a tool on the MCP server resolved for this tenant + label.

    ``server_label`` is the optional ``mcpServerLabel`` config field
    from the MCP Tool node. When absent, the resolver uses the
    tenant's default server (or the legacy env-var URL when no
    default is registered).
    """
    from app.engine.mcp_client import call_tool
    from app.observability import span_tool, _NoOpSpan
    trace = _NoOpSpan()

    with span_tool(trace, tool_name=tool_name, arguments=parameters) as span:
        result = call_tool(
            tool_name,
            parameters,
            tenant_id=tenant_id,
            server_label=server_label,
        )
        span.update(output=result)
        return result


def _call_http(config: dict, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Make a generic HTTP request.

    ``url``, every header value, and ``body`` go through Jinja2 rendering
    against the execution context, so workflow authors can do:

        url:    http://localhost:8001/api/v1/support-cases/by-session/{{ trigger.session_id }}
        body:   {"session_id": "{{ trigger.session_id }}", "title": "{{ node_3.intent }}"}
        headers: { "X-Tenant-Id": "{{ trigger.tenant_id | default('default') }}" }

    Empty body is sent as ``content=None`` so GET / DELETE requests don't
    accidentally carry an empty-string body, which some servers reject.
    On JSON content-type, the response body is parsed and surfaced as
    ``json``; otherwise raw text under ``body``.
    """
    from app.engine.prompt_template import render_prompt

    ctx = context or {}

    def _render(value: Any) -> Any:
        return render_prompt(value, ctx) if isinstance(value, str) else value

    rendered_url = _render(config["url"])
    rendered_method = config.get("method", "GET")
    rendered_headers = {k: _render(v) for k, v in (config.get("headers") or {}).items()}
    rendered_body = _render(config.get("body") or "") or None

    try:
        resp = httpx.request(
            method=rendered_method,
            url=rendered_url,
            headers=rendered_headers,
            content=rendered_body,
            timeout=30.0,
        )
        out: dict[str, Any] = {
            "status_code": resp.status_code,
            "url": rendered_url,
        }
        # Parse JSON if the response advertises it — otherwise raw text. The
        # parsed dict gets dot-accessible in downstream nodes (Switch /
        # ReAct / next HTTP Request).
        ctype = (resp.headers.get("content-type") or "").lower()
        if "application/json" in ctype:
            try:
                out["json"] = resp.json()
            except Exception:
                out["body"] = resp.text[:10000]
        else:
            out["body"] = resp.text[:10000]
        return out
    except httpx.HTTPError as exc:
        logger.error("HTTP request failed: %s", exc)
        return {"error": str(exc), "url": rendered_url}


def _handle_logic(
    node_data: dict, context: dict[str, Any], _tenant_id: str
) -> dict[str, Any]:
    """Evaluate condition/merge logic nodes.

    For conditions, evaluates a simple expression against the context.
    For merges, aggregates upstream outputs.
    """
    config = node_data.get("config", {})
    label = node_data.get("label", "")

    if "condition" in config:
        from app.engine.safe_eval import safe_eval, SafeEvalError

        expr = config["condition"]
        upstream = {k: v for k, v in context.items() if k.startswith("node_")}
        eval_env = {"output": upstream, "context": context, "trigger": context.get("trigger", {})}
        eval_env.update(upstream)
        try:
            result = bool(safe_eval(expr, eval_env))
        except SafeEvalError as exc:
            logger.warning("Condition expression rejected by safe evaluator: %s", exc)
            result = False
        except Exception:
            result = False
        return {"branch": "true" if result else "false", "evaluated": expr}

    if config.get("strategy") == "waitAll":
        upstream = {k: v for k, v in context.items() if k.startswith("node_")}
        return {"merged": upstream, "strategy": "waitAll"}

    # CTX-MGMT.E — Merge with strategy=waitAny fires when ANY active
    # upstream source is satisfied (the engine's ready-check honors
    # this via `_is_waitany_merge` in dag_runner). The handler's
    # output reports which source's data was available so downstream
    # nodes can read it via Jinja `{{ node_merge.value }}` /
    # `{{ node_merge.from }}`.
    if config.get("strategy") == "waitAny":
        # Find the first active upstream that has a value in context.
        # The engine guarantees AT LEAST ONE is present by the time
        # this handler fires (otherwise the ready-check wouldn't
        # have admitted us).
        upstream = {k: v for k, v in context.items() if k.startswith("node_")}
        # Filter to nodes that are direct upstreams of this merge.
        # We don't have edge info inside the handler, so we use the
        # full upstream set; the user can disambiguate via `from`
        # being any upstream that actually fired into us.
        for key, value in upstream.items():
            if value is not None:
                return {
                    "merged": value,
                    "value": value,
                    "from": key,
                    "strategy": "waitAny",
                }
        # Fallback: nothing in upstream — shouldn't happen given the
        # ready-check, but defensive.
        return {"merged": None, "value": None, "from": None, "strategy": "waitAny"}

    logger.warning("Logic node '%s' has no handler", label)
    return {"output": None}


def _handle_forEach(
    node_data: dict, context: dict[str, Any], _tenant_id: str
) -> dict[str, Any]:
    """Evaluate the array expression and return metadata for the DAG runner.

    The actual iteration over downstream nodes is handled by dag_runner.py,
    which reads the returned 'items' list and 'itemVariable' name.
    """
    from app.engine.safe_eval import safe_eval, SafeEvalError

    config = node_data.get("config", {})
    array_expr = config.get("arrayExpression", "")
    item_var = config.get("itemVariable", "item")

    if not array_expr:
        logger.warning("ForEach node has no arrayExpression configured")
        return {"items": [], "itemVariable": item_var}

    upstream = {k: v for k, v in context.items() if k.startswith("node_")}
    eval_env = {"output": upstream, "context": context, "trigger": context.get("trigger", {})}
    eval_env.update(upstream)

    # Add loop item from parent forEach if nested. CTX-MGMT.D — _runtime
    # is canonical; fall back to legacy flat keys for in-flight context.
    runtime = context.get("_runtime") or {}
    if "loop_item" in runtime:
        eval_env[runtime.get("loop_item_var", "item")] = runtime["loop_item"]
    elif "_loop_item" in context:
        eval_env[context.get("_loop_item_var", "item")] = context["_loop_item"]

    try:
        items = safe_eval(array_expr, eval_env)
    except SafeEvalError as exc:
        logger.warning("ForEach arrayExpression rejected: %s", exc)
        items = []

    if not isinstance(items, (list, tuple)):
        logger.warning("ForEach expression did not evaluate to a list: %s", type(items).__name__)
        items = [items] if items is not None else []

    logger.info("ForEach node evaluated: %d items, variable='%s'", len(items), item_var)
    return {"items": list(items), "itemVariable": item_var}


def _handle_loop(
    node_data: dict, context: dict[str, Any], _tenant_id: str
) -> dict[str, Any]:
    """Return loop configuration for dag_runner to drive iteration.

    The actual re-execution of downstream body nodes is handled by
    _run_loop_iterations in dag_runner.py, which reads 'continueExpression'
    and 'maxIterations' from this node's output.  An empty continueExpression
    means "run for maxIterations iterations unconditionally".
    """
    config = node_data.get("config", {})
    continue_expr = config.get("continueExpression", "")
    max_iterations = min(int(config.get("maxIterations", 10)), 25)

    logger.info(
        "Loop node: continueExpression=%r, maxIterations=%d",
        continue_expr, max_iterations,
    )
    return {"continueExpression": continue_expr, "maxIterations": max_iterations}


def _handle_while(
    node_data: dict, context: dict[str, Any], _tenant_id: str
) -> dict[str, Any]:
    """NODES-01.b — While loop: thin wrapper over Loop with a required
    condition for clearer author intent.

    Maps the node's ``condition`` config onto the same
    ``{continueExpression, maxIterations}`` shape the dag_runner's
    existing loop machinery reads, so no runner changes are needed.
    An empty condition is still accepted (it would be caught by
    save-time validation) and degrades to unconditional iteration up
    to ``maxIterations``, matching Loop semantics.
    """
    config = node_data.get("config", {})
    condition = config.get("condition", "")
    max_iterations = min(int(config.get("maxIterations", 10)), 25)

    logger.info(
        "While node: condition=%r, maxIterations=%d",
        condition, max_iterations,
    )
    return {"continueExpression": condition, "maxIterations": max_iterations}


def _handle_switch(
    node_data: dict, context: dict[str, Any], _tenant_id: str
) -> dict[str, Any]:
    """NODES-01.a — Switch: multi-case routing.

    Evaluates ``expression`` against the DAG context and returns
    ``{"branch": <matched-value-or-"default">}``. dag_runner's
    branch-pruning path (``_update_after_node_completion``) treats the
    returned branch the same way it treats Condition's true/false
    branch: every outgoing edge whose ``sourceHandle`` doesn't match
    is pruned.

    Matching is first-match-wins by string equality on the case's
    ``value`` field. ``matchMode="equals_ci"`` is case-insensitive —
    useful when the upstream value comes from an LLM ("Refund" vs.
    "refund"). If nothing matches, branch is ``"default"``.
    """
    from app.engine.safe_eval import safe_eval, SafeEvalError

    config = node_data.get("config", {})
    expr = str(config.get("expression", "") or "")
    cases = config.get("cases", []) or []
    match_mode = str(config.get("matchMode", "equals") or "equals")

    if not expr:
        logger.warning("Switch node has empty expression; routing to default.")
        return {"branch": "default", "evaluated": None}

    upstream = {k: v for k, v in context.items() if k.startswith("node_")}
    eval_env = {
        "output": upstream,
        "context": context,
        "trigger": context.get("trigger", {}),
    }
    eval_env.update(upstream)
    try:
        value = safe_eval(expr, eval_env)
    except SafeEvalError as exc:
        logger.warning("Switch expression rejected by safe evaluator: %s", exc)
        return {"branch": "default", "evaluated": None, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning("Switch expression eval error: %s", exc)
        return {"branch": "default", "evaluated": None, "error": str(exc)}

    # Normalise for matching. Non-string values are stringified so an
    # author can switch on ints / bools / enum-likes without juggling
    # quotes in the case list.
    value_s = str(value) if value is not None else ""
    if match_mode == "equals_ci":
        value_s_cmp = value_s.casefold()
    else:
        value_s_cmp = value_s

    for case in cases:
        if not isinstance(case, dict):
            continue
        case_value = str(case.get("value", ""))
        case_cmp = case_value.casefold() if match_mode == "equals_ci" else case_value
        if case_cmp == value_s_cmp:
            return {"branch": case_value, "evaluated": value_s, "match": "case"}

    return {"branch": "default", "evaluated": value_s, "match": "default"}


# ---------------------------------------------------------------------------
# Stateful Re-Trigger Pattern — Conversational Memory Nodes
# ---------------------------------------------------------------------------

def _resolve_expr(expr: str, context: dict[str, Any]) -> Any:
    """Safely evaluate a dot-notation expression against the DAG context."""
    from app.engine.safe_eval import safe_eval, SafeEvalError

    upstream = {k: v for k, v in context.items() if k.startswith("node_")}
    eval_env = {
        "trigger": context.get("trigger", {}),
        "context": context,
    }
    eval_env.update(upstream)
    try:
        return safe_eval(expr, eval_env)
    except SafeEvalError as exc:
        logger.warning("Expression '%s' rejected by safe evaluator: %s", expr, exc)
        return None
    except Exception as exc:
        logger.warning("Expression '%s' evaluation error: %s", expr, exc)
        return None


def _handle_bridge_user_reply(
    node_data: dict, context: dict[str, Any], _tenant_id: str
) -> dict[str, Any]:
    """Set the user-visible reply for external clients polling run context.

    Writes ``orchestrator_user_reply`` which the DAG runner promotes to context
    root. Prefer *messageExpression* when set; otherwise resolve *responseNodeId*
    like Save Conversation State (``response`` / ``output`` on that node).
    """
    config = node_data.get("config", {})
    msg_expr = str(config.get("messageExpression", "") or "").strip()
    response_node_id = str(config.get("responseNodeId", "") or "").strip()

    text = ""
    memory_debug = None
    if msg_expr:
        raw = _resolve_expr(msg_expr, context)
        text = str(raw).strip() if raw is not None else ""
    elif response_node_id and response_node_id in context:
        node_out = context[response_node_id]
        if isinstance(node_out, dict):
            text = str(
                node_out.get("response", node_out.get("output", ""))
            ).strip()
            if isinstance(node_out.get("memory_debug"), dict):
                memory_debug = node_out.get("memory_debug")
        else:
            text = str(node_out).strip()

    if not text:
        logger.warning(
            "Bridge User Reply: no text resolved (messageExpression=%r responseNodeId=%r)",
            msg_expr,
            response_node_id,
        )

    return {
        "orchestrator_user_reply": text,
        "text": text,
        "source": "messageExpression" if msg_expr else ("responseNodeId" if response_node_id else ""),
        "memory_debug": memory_debug,
    }


def _handle_load_conversation_state(
    node_data: dict, context: dict[str, Any], tenant_id: str
) -> dict[str, Any]:
    """Fetch conversation history from persistent storage.

    Reads `session_id` from the trigger payload (or via a configurable
    expression), queries the conversation_sessions table, and returns the
    full message array so downstream nodes can reference it as context.
    If no session exists yet, an empty one is created automatically.
    """
    config = node_data.get("config", {})
    session_id_expr = config.get("sessionIdExpression", "trigger.session_id")

    raw = _resolve_expr(session_id_expr, context)
    session_id = str(raw) if raw else str(context.get("trigger", {}).get("session_id", ""))
    if not session_id:
        session_id = str(uuid.uuid4())
        logger.warning(
            "Load Conversation State: session_id could not be resolved; "
            "generated ephemeral id=%s", session_id,
        )

    from app.database import SessionLocal, set_tenant_context
    from app.engine.memory_service import load_conversation_state

    db = SessionLocal()
    try:
        set_tenant_context(db, tenant_id)
        payload = load_conversation_state(db, tenant_id=tenant_id, session_id=session_id)
        db.commit()
        logger.info(
            "Load Conversation State: session=%s messages=%d",
            session_id,
            payload["message_count"],
        )
        return payload
    finally:
        db.close()


def _handle_save_conversation_state(
    node_data: dict, context: dict[str, Any], tenant_id: str
) -> dict[str, Any]:
    """Append the current turn to persistent conversation history.

    Reads the user message (via `userMessageExpression`) and the assistant
    response (from `responseNodeId`'s output) then upserts both into the
    conversation_sessions table under the resolved `session_id`.
    """
    config = node_data.get("config", {})
    session_id_expr = config.get("sessionIdExpression", "trigger.session_id")
    response_node_id = config.get("responseNodeId", "")
    user_msg_expr = config.get("userMessageExpression", "trigger.message")

    raw = _resolve_expr(session_id_expr, context)
    session_id = str(raw) if raw else str(context.get("trigger", {}).get("session_id", ""))
    if not session_id:
        return {"error": "session_id could not be resolved", "saved": False}

    raw_user = _resolve_expr(user_msg_expr, context)
    user_message = str(raw_user) if raw_user is not None else ""

    assistant_response = ""
    response_output: Any = None
    if response_node_id and response_node_id in context:
        response_output = context[response_node_id]
        if isinstance(response_output, dict):
            assistant_response = str(
                response_output.get("response", response_output.get("output", ""))
            )
        else:
            assistant_response = str(response_output)

    def _should_promote_memory_records(node_out: Any, assistant_text: str) -> bool:
        if not assistant_text.strip():
            return False
        if not isinstance(node_out, dict):
            return True
        if node_out.get("error"):
            return False
        status = str(node_out.get("status", "") or "").strip().lower()
        if status in {"error", "failed", "cancelled"}:
            return False
        return True

    from app.database import SessionLocal, set_tenant_context
    from app.engine.memory_service import (
        get_active_episode,
        get_or_create_active_episode,
        append_conversation_turns,
        build_conversation_idempotency_key,
        memory_debug_to_node_config,
        promote_entity_facts,
        promote_memory_records,
        refresh_rolling_summary,
        resolve_memory_policy,
    )

    db = SessionLocal()
    try:
        set_tenant_context(db, tenant_id)
        instance_id = str(context.get("_instance_id", "") or "")
        current_node_id = str(context.get("_current_node_id", "") or "")
        workflow_def_id = str(context.get("_workflow_def_id", "") or "")
        # CTX-MGMT.D — loop_iteration is under _runtime now; fall back
        # to the legacy flat key for any context still in the old shape.
        runtime = context.get("_runtime") or {}
        raw_loop_iteration = runtime.get("loop_iteration")
        if raw_loop_iteration is None:
            raw_loop_iteration = context.get("_loop_iteration")
        try:
            loop_iteration = int(raw_loop_iteration) if raw_loop_iteration is not None else None
        except (TypeError, ValueError):
            loop_iteration = None
        idempotency_key = build_conversation_idempotency_key(
            session_id=session_id,
            instance_id=instance_id or None,
            node_id=current_node_id or None,
            loop_iteration=loop_iteration,
            user_message=user_message,
            assistant_response=assistant_response,
        )

        session, persisted_rows = append_conversation_turns(
            db,
            tenant_id=tenant_id,
            session_id=session_id,
            user_message=user_message,
            assistant_response=assistant_response,
            workflow_def_id=workflow_def_id or None,
            instance_id=instance_id or None,
            node_id=current_node_id or None,
            idempotency_key=idempotency_key,
        )
        policy_node_config = {}
        if isinstance(response_output, dict):
            policy_node_config = memory_debug_to_node_config(response_output.get("memory_debug"))
        policy = resolve_memory_policy(
            db,
            tenant_id=tenant_id,
            workflow_def_id=workflow_def_id or None,
            node_config=policy_node_config,
            context=context,
        )
        summary_updated = False
        fact_rows: list[Any] = []
        memory_rows: list[Any] = []
        active_episode = None
        if policy.enabled:
            if persisted_rows:
                active_episode = get_or_create_active_episode(
                    db,
                    session=session,
                    tenant_id=tenant_id,
                    workflow_def_id=workflow_def_id or None,
                    memory_profile_id=policy.selected_profile_id,
                    starting_turn=min(row.turn_index for row in persisted_rows),
                )
            else:
                active_episode = get_active_episode(db, session=session, lock=True)
            summary_updated = refresh_rolling_summary(
                db,
                session=session,
                episode=active_episode,
                policy=policy,
            )
            fact_rows = promote_entity_facts(
                db,
                tenant_id=tenant_id,
                workflow_def_id=workflow_def_id or None,
                session_ref_id=session.id,
                instance_id=instance_id or None,
                node_id=current_node_id or None,
                context=context,
                policy=policy,
            )
            if _should_promote_memory_records(response_output, assistant_response):
                memory_rows = promote_memory_records(
                    db,
                    tenant_id=tenant_id,
                    session=session,
                    episode=active_episode,
                    workflow_def_id=workflow_def_id or None,
                    instance_id=instance_id or None,
                    node_id=current_node_id or None,
                    context=context,
                    policy=policy,
                    user_message=user_message,
                    assistant_response=assistant_response,
                    conversation_idempotency_key=idempotency_key,
                )
        else:
            active_episode = get_active_episode(db, session=session, lock=True)
            if active_episode is not None:
                active_episode.last_activity_at = session.last_message_at or active_episode.last_activity_at
                active_episode.updated_at = active_episode.last_activity_at
        # Extract IDs before commit to avoid ObjectDeletedError (expired instance)
        active_ep_id = str(active_episode.id) if active_episode else None
        session_ref_id = str(session.id)
        
        db.commit()

        total = session.message_count
        logger.info(
            "Save Conversation State: session=%s total_messages=%d", session_id, total
        )
        return {
            "session_id": session_id,
            "session_ref_id": session_ref_id,
            "message_count": total,
            "saved": True,
            "active_episode_id": active_ep_id,
            "summary_updated": summary_updated,
            "promoted_memory_records": len(memory_rows),
            "promoted_entity_facts": len(fact_rows),
        }
    finally:
        db.close()


def _handle_archive_conversation_episode(
    node_data: dict, context: dict[str, Any], tenant_id: str
) -> dict[str, Any]:
    """Archive and reset the active episode for a long-lived conversation session."""
    config = node_data.get("config", {})
    session_id_expr = str(config.get("sessionIdExpression", "trigger.session_id") or "trigger.session_id")
    summary_expr = str(config.get("summaryExpression", "") or "").strip()
    title_expr = str(config.get("titleExpression", "") or "").strip()
    reason = str(config.get("reason", "manual") or "manual").strip().lower()
    if reason not in {"resolved", "inactive", "manual"}:
        reason = "manual"
    memory_profile_id = str(config.get("memoryProfileId", "") or "").strip() or None

    raw = _resolve_expr(session_id_expr, context)
    session_id = str(raw) if raw else str(context.get("trigger", {}).get("session_id", ""))
    if not session_id:
        return {"error": "session_id could not be resolved", "archived": False}

    summary_text = ""
    if summary_expr:
        summary_value = _resolve_expr(summary_expr, context)
        summary_text = str(summary_value).strip() if summary_value is not None else ""

    title = ""
    if title_expr:
        title_value = _resolve_expr(title_expr, context)
        title = str(title_value).strip() if title_value is not None else ""

    from app.database import SessionLocal, set_tenant_context
    from app.engine.memory_service import archive_active_episode, get_or_create_session

    db = SessionLocal()
    try:
        set_tenant_context(db, tenant_id)
        workflow_def_id = str(context.get("_workflow_def_id", "") or "") or None
        instance_id = str(context.get("_instance_id", "") or "") or None
        current_node_id = str(context.get("_current_node_id", "") or "") or None

        session = get_or_create_session(
            db,
            tenant_id=tenant_id,
            session_id=session_id,
            lock=True,
        )
        episode, memory_rows = archive_active_episode(
            db,
            tenant_id=tenant_id,
            session=session,
            workflow_def_id=workflow_def_id,
            instance_id=instance_id,
            node_id=current_node_id,
            context=context,
            reason=reason,
            provided_summary=summary_text,
            provided_title=title,
            memory_profile_id=memory_profile_id,
        )
        db.commit()

        archived = bool(episode and episode.status == "archived")
        return {
            "session_id": session_id,
            "archived": archived,
            "episode_id": str(episode.id) if episode else None,
            "title": episode.title if episode else None,
            "archive_reason": episode.archive_reason if archived and episode else None,
            "archived_at": episode.archived_at.isoformat() if archived and episode and episode.archived_at else None,
            "memory_record_ids": [str(row.id) for row in memory_rows],
            "memory_records_created": len(memory_rows),
            "summary_text": episode.checkpoint_summary_text if episode else "",
        }
    finally:
        db.close()


def _handle_llm_router(
    node_data: dict, context: dict[str, Any], tenant_id: str
) -> dict[str, Any]:
    """Classify the user's intent using a lightweight LLM call.

    Reads the conversation history from a Load Conversation State node
    (configured via `historyNodeId`), builds a strict classification prompt,
    and returns `{"intent": "<label>"}` for downstream Condition nodes to
    branch on.  Temperature is forced to 0.1 for deterministic output.
    """
    from app.engine.model_registry import default_llm_for

    config = node_data.get("config", {})
    from app.config import settings
    provider = config.get("provider", settings.llm_default_provider or "vertex")
    logger.info("LLMRouter: node_id=%s, provider=%s, config_keys=%s", node_data.get("id"), provider, list(config.keys()))
    model = config.get("model") or default_llm_for(provider, role="fast")
    intents: list[str] = config.get("intents", [])
    user_msg_expr = config.get("userMessageExpression", "trigger.message")

    raw_user = _resolve_expr(user_msg_expr, context)
    user_message = str(raw_user) if raw_user is not None else str(
        context.get("trigger", {}).get("message", "")
    )

    # Build the classification system prompt
    intents_str = ", ".join(f'"{i}"' for i in intents) if intents else '"general"'
    system_prompt = (
        "You are an intent classification engine. "
        "Analyze the conversation and classify the user's latest message.\n\n"
        f"Available intents: [{intents_str}]\n\n"
        "Respond ONLY with a valid JSON object in this exact format:\n"
        '{"intent": "<one of the available intents>"}\n\n'
        "Do not include any other text, explanation, or markdown formatting."
    )

    from app.database import SessionLocal, set_tenant_context
    from app.engine.memory_service import assemble_history_text

    db = SessionLocal()
    try:
        set_tenant_context(db, tenant_id)
        history_block, memory_debug = assemble_history_text(
            db,
            tenant_id=tenant_id,
            workflow_def_id=str(context.get("_workflow_def_id", "") or ""),
            context=context,
            node_config={
                "historyNodeId": str(config.get("historyNodeId", "") or "").strip(),
            },
        )
    finally:
        db.close()

    user_prompt = (
        f"Conversation history:\n{history_block}\n\n"
        f"Latest user message: {user_message}\n\n"
        "Classify the intent:"
    )

    from app.engine.llm_providers import call_llm

    result = call_llm(
        provider=provider,
        model=model,
        system_prompt=system_prompt,
        user_message=user_prompt,
        temperature=0.1,
        max_tokens=64,
        tenant_id=tenant_id,
    )

    raw_response = result.get("response", "").strip()

    # Parse JSON — handle accidental markdown code fences
    intent = "unknown"
    try:
        parsed = json.loads(raw_response)
        intent = parsed.get("intent", "unknown")
    except json.JSONDecodeError:
        match = re.search(r'\{[^}]+\}', raw_response)
        if match:
            try:
                intent = json.loads(match.group()).get("intent", "unknown")
            except Exception:
                pass

    # Clamp to the configured intent list; fall back to the first entry
    if intents and intent not in intents:
        logger.warning(
            "LLM Router returned unknown intent '%s'; falling back to '%s'",
            intent, intents[0],
        )
        intent = intents[0]

    from app.observability import record_generation
    record_generation(
        context.get("_trace"),
        name=f"llm_router:{provider}/{model}",
        provider=provider,
        model=model,
        system_prompt=system_prompt,
        user_message=user_prompt,
        response=raw_response,
        usage=result.get("usage"),
        metadata={"memory": memory_debug},
    )

    logger.info("LLM Router classified intent='%s' (model=%s/%s)", intent, provider, model)
    return {
        "intent": intent,
        "raw_response": raw_response,
        "usage": result.get("usage"),
        "memory_debug": memory_debug,
    }


# ---------------------------------------------------------------------------
# A2A Agent Call — outbound task delegation
# ---------------------------------------------------------------------------

def _handle_a2a_call(
    node_data: dict, context: dict[str, Any], tenant_id: str
) -> dict[str, Any]:
    """Delegate a task to an external A2A-compatible agent and return its result.

    Flow:
      1. Fetch the remote agent card to discover the agent's base URL and
         validate the requested skill exists.
      2. Submit the task via tasks/send.
      3. Poll tasks/get until the task reaches a terminal state.
      4. Extract and return the response text so downstream nodes can use it.

    The ``apiKeySecret`` config field should reference a vault secret
    (e.g. ``{{ env.REMOTE_AGENT_KEY }}``) — it is resolved by
    ``resolve_config_env_vars`` before this handler is called.
    """
    from app.engine.a2a_client import (
        fetch_agent_card,
        send_task,
        poll_until_done,
        extract_response_parts,
        extract_response_text,
    )

    config          = node_data.get("config", {})
    agent_card_url  = config.get("agentCardUrl", "").strip()
    skill_id        = config.get("skillId", "").strip()
    message_expr    = config.get("messageExpression", "trigger.message")
    api_key         = config.get("apiKeySecret", "").strip()
    timeout         = int(config.get("timeoutSeconds", 300))

    if not agent_card_url:
        return {"error": "agentCardUrl is not configured", "state": "failed"}
    if not api_key:
        return {"error": "apiKeySecret is not configured (use a vault reference)", "state": "failed"}

    # Resolve the message text from the DAG context
    raw_msg = _resolve_expr(message_expr, context)
    message = str(raw_msg) if raw_msg is not None else str(
        context.get("trigger", {}).get("message", "")
    )

    # 1. Discover — fetch agent card and resolve agent base URL
    try:
        card = fetch_agent_card(agent_card_url)
    except Exception as exc:
        logger.error("A2A Agent Call: failed to fetch agent card: %s", exc)
        return {"error": f"Agent card fetch failed: {exc}", "state": "failed"}

    agent_url = card.get("url", "")
    if not agent_url:
        return {"error": "Agent card has no 'url' field", "state": "failed"}

    # If no skill_id supplied, use the first available skill
    available_skills = [s["id"] for s in card.get("skills", [])]
    if not skill_id:
        if not available_skills:
            return {"error": "Remote agent exposes no skills", "state": "failed"}
        skill_id = available_skills[0]
        logger.info("A2A Agent Call: no skillId configured, using first skill '%s'", skill_id)
    elif skill_id not in available_skills:
        return {
            "error": f"Skill '{skill_id}' not found on remote agent. Available: {available_skills}",
            "state": "failed",
        }

    # Carry the session_id across if one exists in the current context
    session_id = str(context.get("trigger", {}).get("session_id", uuid.uuid4()))

    # 2. Send task
    try:
        task = send_task(
            agent_url=agent_url,
            skill_id=skill_id,
            message=message,
            api_key=api_key,
            session_id=session_id,
        )
    except Exception as exc:
        logger.error("A2A Agent Call: tasks/send failed: %s", exc)
        return {"error": f"tasks/send failed: {exc}", "state": "failed"}

    task_id = task["id"]
    initial_state = task["status"]["state"]
    logger.info(
        "A2A Agent Call: task submitted id=%s state=%s agent=%s",
        task_id, initial_state, agent_url,
    )

    # 3. Poll until terminal state
    try:
        final_task = poll_until_done(
            agent_url=agent_url,
            task_id=task_id,
            api_key=api_key,
            timeout_seconds=timeout,
        )
    except TimeoutError as exc:
        logger.error("A2A Agent Call: polling timed out: %s", exc)
        return {
            "error": str(exc),
            "state": "timeout",
            "task_id": task_id,
        }
    except Exception as exc:
        logger.error("A2A Agent Call: polling failed: %s", exc)
        return {"error": f"Polling failed: {exc}", "state": "failed", "task_id": task_id}

    final_state = final_task["status"]["state"]

    # 4. Extract the full Part surface (A2A-01.c). ``response`` stays
    #    a plain string for back compat with existing downstream
    #    expressions; data + files are exposed alongside so workflows
    #    that need the structured payload can read it without
    #    re-parsing ``task.artifacts``.
    parts = extract_response_parts(final_task)
    response_text = parts["text"]

    logger.info(
        "A2A Agent Call: completed task=%s state=%s response_len=%d "
        "data_parts=%d file_parts=%d",
        task_id, final_state, len(response_text),
        len(parts["data"]), len(parts["files"]),
    )

    return {
        "task_id":  task_id,
        "state":    final_state,
        "response": response_text,
        "data":     parts["data"],
        "files":    parts["files"],
        "skill_id": skill_id,
        "agent":    card.get("name", agent_url),
        "task":     final_task,
    }


# ---------------------------------------------------------------------------
# Knowledge Retrieval — RAG chunk search
# ---------------------------------------------------------------------------

def _handle_knowledge_retrieval(
    node_data: dict, context: dict[str, Any], tenant_id: str
) -> dict[str, Any]:
    """Search one or more knowledge bases and return relevant chunks.

    The output includes ``context_text`` — a concatenated block of retrieved
    content that downstream LLM Agent nodes can reference in their system
    prompt via ``{{ node_X.context_text }}``.
    """
    config = node_data.get("config", {})
    kb_ids_raw = config.get("knowledgeBaseIds", [])
    query_expr = config.get("queryExpression", "trigger.message")
    top_k = int(config.get("topK", 5))
    score_threshold = float(config.get("scoreThreshold", 0.0))

    if not kb_ids_raw:
        logger.warning("Knowledge Retrieval node has no knowledgeBaseIds configured")
        return {"chunks": [], "context_text": "", "query": "", "chunk_count": 0}

    raw_query = _resolve_expr(query_expr, context)
    query = str(raw_query) if raw_query is not None else str(
        context.get("trigger", {}).get("message", "")
    )

    if not query.strip():
        logger.warning("Knowledge Retrieval: query resolved to empty string")
        return {"chunks": [], "context_text": "", "query": "", "chunk_count": 0}

    try:
        kb_ids = [uuid.UUID(kid) for kid in kb_ids_raw if kid]
    except (ValueError, AttributeError) as exc:
        logger.warning("Knowledge Retrieval: invalid knowledgeBaseIds: %s", exc)
        return {"chunks": [], "context_text": "", "query": query, "chunk_count": 0, "error": str(exc)}

    from app.database import SessionLocal, set_tenant_context
    from app.engine.retriever import retrieve_chunks

    db = SessionLocal()
    try:
        set_tenant_context(db, tenant_id)
        chunks = retrieve_chunks(
            db=db,
            kb_ids=kb_ids,
            query=query,
            tenant_id=tenant_id,
            top_k=top_k,
            score_threshold=score_threshold,
        )
    finally:
        db.close()

    context_text = "\n\n---\n\n".join(
        c["content"] for c in chunks
    ) if chunks else ""

    logger.info(
        "Knowledge Retrieval: query_len=%d, results=%d, context_text_len=%d",
        len(query), len(chunks), len(context_text),
    )

    return {
        "chunks": chunks,
        "context_text": context_text,
        "query": query,
        "chunk_count": len(chunks),
    }


# ---------------------------------------------------------------------------
# Sub-Workflow — nested workflow execution
# ---------------------------------------------------------------------------

def _handle_sub_workflow(
    node_data: dict, context: dict[str, Any], tenant_id: str,
    db: Any = None,
) -> dict[str, Any]:
    """Execute another saved workflow as a child instance and return its outputs.

    Creates a real WorkflowInstance for the child so it gets its own logs,
    checkpoints, and debuggability.  The child runs synchronously within the
    parent's execution thread.
    """
    from app.engine.safe_eval import safe_eval, SafeEvalError

    config = node_data.get("config", {})
    workflow_id = config.get("workflowId", "").strip()
    version_policy = config.get("versionPolicy", "latest")
    pinned_version = int(config.get("pinnedVersion", 1))
    input_mapping: dict = config.get("inputMapping", {})
    output_node_ids: list = config.get("outputNodeIds", [])
    max_depth = int(config.get("maxDepth", 10))

    if not workflow_id:
        raise ValueError("Sub-Workflow: workflowId is not configured")

    need_own_session = db is None
    if need_own_session:
        from app.database import SessionLocal, set_tenant_context
        db = SessionLocal()
        set_tenant_context(db, tenant_id)

    try:
        return _execute_sub_workflow(
            db=db,
            context=context,
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            version_policy=version_policy,
            pinned_version=pinned_version,
            input_mapping=input_mapping,
            output_node_ids=output_node_ids,
            max_depth=max_depth,
        )
    finally:
        if need_own_session:
            db.close()


def _execute_sub_workflow(
    db: Any,
    context: dict[str, Any],
    tenant_id: str,
    workflow_id: str,
    version_policy: str,
    pinned_version: int,
    input_mapping: dict,
    output_node_ids: list,
    max_depth: int,
) -> dict[str, Any]:
    from app.engine.safe_eval import safe_eval, SafeEvalError
    from app.models.workflow import WorkflowDefinition, WorkflowSnapshot, WorkflowInstance

    # 1. Load the workflow definition
    wf_def = db.query(WorkflowDefinition).filter_by(
        id=workflow_id, tenant_id=tenant_id,
    ).first()
    if not wf_def:
        raise ValueError(f"Sub-Workflow: workflow definition '{workflow_id}' not found")

    # 2. Resolve graph_json based on version policy
    if version_policy == "pinned":
        if pinned_version == wf_def.version:
            graph_json = wf_def.graph_json
        else:
            snap = db.query(WorkflowSnapshot).filter_by(
                workflow_def_id=workflow_id, version=pinned_version,
            ).first()
            if not snap:
                raise ValueError(
                    f"Sub-Workflow: snapshot version {pinned_version} not found "
                    f"for workflow '{wf_def.name}'"
                )
            graph_json = snap.graph_json
    else:
        graph_json = wf_def.graph_json

    # 3. Recursion protection: walk the parent chain. CTX-MGMT.D —
    # parent_chain lives under _runtime now; fall back to the legacy
    # flat key for any context still in the old shape.
    runtime = context.get("_runtime") or {}
    parent_chain: list[str] = list(runtime.get("parent_chain") or context.get("_parent_chain") or [])
    current_wf_id = str(context.get("_workflow_def_id", ""))
    full_chain = check_subworkflow_recursion(
        parent_chain=parent_chain,
        current_wf_id=current_wf_id,
        target_wf_id=str(workflow_id),
        target_name=wf_def.name,
        max_depth=max_depth,
    )

    # 4. Build trigger_payload from input mapping
    trigger_payload: dict[str, Any] = {}
    if input_mapping:
        upstream = {k: v for k, v in context.items() if k.startswith("node_")}
        eval_env = {
            "output": upstream,
            "context": context,
            "trigger": context.get("trigger", {}),
        }
        eval_env.update(upstream)
        # CTX-MGMT.D — _runtime canonical, legacy fall-through.
        if "loop_item" in runtime:
            eval_env[runtime.get("loop_item_var", "item")] = runtime["loop_item"]
        elif "_loop_item" in context:
            eval_env[context.get("_loop_item_var", "item")] = context["_loop_item"]

        for child_key, expr in input_mapping.items():
            if not isinstance(expr, str) or not expr.strip():
                continue
            try:
                trigger_payload[child_key] = safe_eval(expr.strip(), eval_env)
            except SafeEvalError as exc:
                logger.warning(
                    "Sub-Workflow inputMapping key '%s' expression rejected: %s",
                    child_key, exc,
                )
                trigger_payload[child_key] = None

    # 5. Create the child WorkflowInstance
    parent_instance_id = context.get("_instance_id")
    parent_node_id = context.get("_current_node_id")

    child_instance = WorkflowInstance(
        tenant_id=tenant_id,
        workflow_def_id=wf_def.id,
        status="queued",
        trigger_payload=trigger_payload,
        definition_version_at_start=wf_def.version if version_policy == "latest" else pinned_version,
        parent_instance_id=parent_instance_id,
        parent_node_id=parent_node_id,
    )
    db.add(child_instance)
    db.commit()
    db.refresh(child_instance)

    child_id = str(child_instance.id)
    logger.info(
        "Sub-Workflow: created child instance %s for workflow '%s' (parent=%s node=%s)",
        child_id, wf_def.name, parent_instance_id, parent_node_id,
    )

    # 6. Inject parent chain into the child's context for nested
    # recursion checks. CTX-MGMT.D — parent_chain lives under _runtime
    # so it survives a HITL inside the child without losing the chain.
    # `_workflow_def_id` stays top-level — it's repopulated each
    # invocation by execute_graph from instance.workflow_def_id.
    child_instance.context_json = {
        "_runtime": {"parent_chain": full_chain},
        "_workflow_def_id": str(workflow_id),
    }
    db.commit()

    # 7. Execute the child workflow
    from app.engine.dag_runner import execute_graph
    execute_graph(db, child_id)

    # 8. Read the result
    db.refresh(child_instance)
    child_status = child_instance.status

    if child_status == "suspended":
        raise ValueError(
            f"Sub-Workflow: child workflow '{wf_def.name}' suspended (HITL). "
            f"Human Approval nodes inside sub-workflows are not supported in v1. "
            f"Child instance: {child_id}"
        )

    if child_status == "failed":
        from app.models.workflow import ExecutionLog
        failed_log = (
            db.query(ExecutionLog)
            .filter_by(instance_id=child_instance.id, status="failed")
            .first()
        )
        error_detail = failed_log.error if failed_log else "unknown error"
        raise ValueError(
            f"Sub-Workflow: child workflow '{wf_def.name}' failed — {error_detail}"
        )

    if child_status == "cancelled":
        raise ValueError(
            f"Sub-Workflow: child workflow '{wf_def.name}' was cancelled"
        )

    # 9. Extract child outputs
    child_context = child_instance.context_json or {}
    if output_node_ids:
        result = {
            k: v for k, v in child_context.items()
            if k in output_node_ids
        }
    else:
        result = {
            k: v for k, v in child_context.items()
            if k.startswith("node_") or k == "trigger"
        }

    # CTX-MGMT.E — child-evidence promotion. If the child workflow
    # appended findings to ``_runtime.shared_evidence`` during its
    # run, propagate them up to the parent's ``_runtime.shared_evidence``
    # via append-merge. Lets sub-workflows surface structured
    # evidence to the parent without dumping their full context_json
    # — preserves Anthropic's scoped-per-agent-context pattern.
    child_runtime = (child_context.get("_runtime") or {}) if isinstance(child_context, dict) else {}
    child_evidence = child_runtime.get("shared_evidence")
    if isinstance(child_evidence, list) and child_evidence:
        from app.engine.dag_runner import _get_runtime
        parent_runtime = _get_runtime(context)
        existing = parent_runtime.get("shared_evidence")
        if isinstance(existing, list):
            parent_runtime["shared_evidence"] = [*existing, *child_evidence]
        else:
            parent_runtime["shared_evidence"] = list(child_evidence)
        logger.info(
            "Sub-Workflow: promoted %d evidence entries from child "
            "workflow '%s' to parent _runtime.shared_evidence",
            len(child_evidence), wf_def.name,
        )

    logger.info(
        "Sub-Workflow: child %s completed with %d output keys (workflow='%s')",
        child_id, len(result), wf_def.name,
    )

    return {
        "child_instance_id": child_id,
        "child_workflow_name": wf_def.name,
        "child_status": child_status,
        "outputs": result,
    }


# ---------------------------------------------------------------------------
# Code Execution — sandboxed Python subprocess
# ---------------------------------------------------------------------------

def _handle_code_execution(
    node_data: dict, context: dict[str, Any], tenant_id: str
) -> dict[str, Any]:
    """Run user-provided Python code in a sandboxed subprocess."""
    from app.config import settings
    from app.engine.sandbox import run_python_sandbox

    if not settings.code_sandbox_enabled:
        return {"error": "Code execution is disabled by the administrator"}

    config = node_data.get("config", {})
    code = config.get("code", "").strip()
    language = config.get("language", "python")
    timeout = min(
        int(config.get("timeout", 30)),
        settings.code_sandbox_timeout_max,
    )

    if not code:
        return {"error": "No code provided", "output": {}}

    if language != "python":
        return {"error": f"Unsupported language: {language}"}

    # Build inputs from upstream node outputs
    inputs: dict[str, Any] = {}
    for k, v in context.items():
        if k.startswith("node_") or k == "trigger":
            inputs[k] = v

    logger.info(
        "Code node: language=%s, code_len=%d, timeout=%d",
        language, len(code), timeout,
    )

    result = run_python_sandbox(
        code=code,
        inputs=inputs,
        timeout=timeout,
        output_limit=settings.code_sandbox_output_limit_bytes,
    )

    if result.error:
        logger.warning(
            "Code node failed after %d ms: %s", result.duration_ms, result.error
        )
        return {
            "error": result.error,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
        }

    logger.info("Code node completed in %d ms", result.duration_ms)
    return {
        **result.output,
        "_sandbox_meta": {
            "duration_ms": result.duration_ms,
            "stderr": result.stderr if result.stderr else None,
        },
    }


# ---------------------------------------------------------------------------
# AutomationEdge — async-external submission (Pattern C default, webhook opt-in)
# ---------------------------------------------------------------------------

def _handle_automation_edge(
    node_data: dict, context: dict[str, Any], tenant_id: str, db: Any,
) -> dict[str, Any]:
    """Submit a workflow to AutomationEdge and suspend until Beat poll or
    webhook reports completion.

    This handler NEVER returns a completed output dict — on success it
    raises ``NodeSuspendedAsync`` so ``_execute_single_node`` marks the
    parent instance ``suspended`` with ``suspended_reason='async_external'``.
    The parent resumes via ``poll_async_jobs`` (AE-04) or
    ``POST /api/v1/async-jobs/{id}/complete`` (AE-05), at which point
    the AE response is merged into ``context[node_X]`` and downstream
    nodes can branch on it.

    Raises:
        NodeSuspendedAsync: on successful submission (control-flow, not error).
        ValueError: if integration config is incomplete or required params
            can't be resolved.
        RuntimeError: if AE rejects the /execute call (success=false or
            non-200 status).
    """
    import secrets
    import uuid as _uuid
    from datetime import timedelta

    from app.engine.automationedge_client import (
        AEConnection,
        submit_workflow,
    )
    from app.engine.exceptions import NodeSuspendedAsync
    from app.engine.integration_resolver import resolve_integration_config
    from app.engine.safe_eval import safe_eval, SafeEvalError
    from app.models.workflow import AsyncJob

    config = node_data.get("config", {}) or {}

    # 1. Resolve connection config — node > tenant_integration default
    merged = resolve_integration_config(
        db,
        tenant_id=tenant_id,
        system="automationedge",
        node_config=config,
        required_fields=("baseUrl", "orgCode", "workflowName"),
    )

    conn = AEConnection(
        base_url=merged["baseUrl"],
        tenant_id=tenant_id,
        credentials_secret_prefix=merged.get("credentialsSecretPrefix", "AUTOMATIONEDGE"),
        auth_mode=merged.get("authMode", "ae_session"),
        org_code=merged["orgCode"],
        source=merged.get("source", "AE AI Hub Orchestrator"),
        user_id=merged.get("userId", "orchestrator"),
    )

    # 2. Resolve AE workflow input params via inputMapping
    #    Config shape: inputMapping = [
    #        {"name": "search_term", "valueExpression": "node_1.response", "type": "String"},
    #        ...
    #    ]
    params: list[dict[str, Any]] = []
    for spec in merged.get("inputMapping", []) or []:
        if not isinstance(spec, dict):
            continue
        pname = spec.get("name")
        expr = spec.get("valueExpression")
        ptype = spec.get("type", "String")
        if not pname:
            continue
        try:
            value = safe_eval(expr, context) if expr else spec.get("defaultValue")
        except SafeEvalError as exc:
            logger.warning(
                "AE inputMapping for '%s' rejected expression %r: %s",
                pname, expr, exc,
            )
            value = None
        params.append({"name": pname, "value": value, "type": ptype})

    # 3. Webhook auth — if enabled, generate per-job secret now so we can
    #    pass it into the AE workflow as one of its input params. Operator
    #    designs the AE workflow to echo it back in the callback.
    completion_mode = merged.get("completionMode", "poll")
    webhook_auth = merged.get("webhookAuth", "token")
    webhook_token: str | None = None
    webhook_hmac_secret: str | None = None
    if completion_mode == "webhook":
        if webhook_auth in ("token", "both"):
            webhook_token = secrets.token_urlsafe(32)
        if webhook_auth in ("hmac", "both"):
            webhook_hmac_secret = secrets.token_urlsafe(32)

    # 4. Build the async_jobs row BEFORE the submit so a flush failure
    #    doesn't leave an orphan AE job running in the wild with no
    #    tracking row. We commit only after the submit succeeds.
    async_job_id = _uuid.uuid4()
    instance_id = context.get("_instance_id")
    current_node_id = context.get("_current_node_id")
    if not instance_id or not current_node_id:
        # This shouldn't happen — dag_runner injects both before dispatch.
        raise ValueError(
            "AutomationEdge handler requires _instance_id and _current_node_id "
            "in context (orchestrator invariant)"
        )

    poll_interval_seconds = int(merged.get("pollIntervalSeconds", 30) or 30)
    timeout_seconds = int(merged.get("timeoutSeconds", 3600) or 3600)
    max_diverted_seconds = int(merged.get("maxDivertedSeconds", 604800) or 604800)

    metadata: dict[str, Any] = {
        "base_url": conn.base_url,
        "org_code": conn.org_code,
        "credentials_secret_prefix": conn.credentials_secret_prefix,
        "auth_mode": conn.auth_mode,
        "user_id": conn.user_id,
        "source": conn.source,
        "completion_mode": completion_mode,
        "poll_interval_seconds": poll_interval_seconds,
        "timeout_seconds": timeout_seconds,
        "max_diverted_seconds": max_diverted_seconds,
    }
    if completion_mode == "webhook":
        # Record the expected auth mode so the webhook endpoint applies
        # the right check (token / hmac / both) against the callback.
        metadata["webhook_auth"] = webhook_auth
    if webhook_token:
        metadata["webhook_token"] = webhook_token
    if webhook_hmac_secret:
        metadata["webhook_hmac_secret"] = webhook_hmac_secret

    # 5. Submit to AE. If webhook mode, include the callback URL + auth
    #    secret as input params so the AE workflow author can wire their
    #    terminal HTTP step.
    if completion_mode == "webhook":
        callback_param = merged.get("webhookCallbackParamName", "callback_url")
        # Caller constructs the base URL at submission time because we don't
        # have a trusted way to know our own public URL otherwise.
        callback_base = merged.get("webhookCallbackBaseUrl", "").rstrip("/")
        if callback_base:
            params.append({
                "name": callback_param,
                "value": f"{callback_base}/api/v1/async-jobs/{async_job_id}/complete",
                "type": "String",
            })
            if webhook_token:
                params.append({
                    "name": merged.get("webhookTokenParamName", "callback_token"),
                    "value": webhook_token,
                    "type": "String",
                })

    automation_request_id = submit_workflow(
        conn,
        workflow_name=merged["workflowName"],
        params=params,
        source_id=str(instance_id),
    )

    # 6. Persist the async_jobs row now that we know the external id
    now = _utcnow()
    job = AsyncJob(
        id=async_job_id,
        instance_id=instance_id,
        node_id=current_node_id,
        system="automationedge",
        external_job_id=str(automation_request_id),
        status="submitted",
        metadata_json=metadata,
        submitted_at=now,
        next_poll_at=now + timedelta(seconds=poll_interval_seconds),
    )
    db.add(job)
    db.commit()

    logger.info(
        "AutomationEdge submitted: tenant=%s node=%s ae_request_id=%s async_job=%s",
        tenant_id, current_node_id, automation_request_id, async_job_id,
    )

    # 7. Signal suspension — dag_runner catches NodeSuspendedAsync and
    #    flips the instance to status=suspended, suspended_reason=async_external.
    raise NodeSuspendedAsync(
        async_job_id=str(async_job_id),
        system="automationedge",
        external_job_id=str(automation_request_id),
    )


def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)
