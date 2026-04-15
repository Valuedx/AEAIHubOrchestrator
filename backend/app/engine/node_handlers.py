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
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def dispatch_node(
    node_data: dict, context: dict[str, Any], tenant_id: str,
    db: Any = None,
) -> dict[str, Any]:
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

    # ForEach / Loop are logic nodes with special dispatch handled by dag_runner
    if category == "logic" and label == "ForEach":
        return _handle_forEach(node_data, context, tenant_id)
    if category == "logic" and label == "Loop":
        return _handle_loop(node_data, context, tenant_id)

    # Conversational memory nodes — special dispatch regardless of category
    if label == "Load Conversation State":
        return _handle_load_conversation_state(node_data, context, tenant_id)
    if label == "Save Conversation State":
        return _handle_save_conversation_state(node_data, context, tenant_id)
    if label == "Bridge User Reply":
        return _handle_bridge_user_reply(node_data, context, tenant_id)
    if label == "LLM Router":
        return _handle_llm_router(node_data, context, tenant_id)
    if label == "A2A Agent Call":
        return _handle_a2a_call(node_data, context, tenant_id)
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

    from app.engine.llm_providers import call_llm, call_llm_streaming
    from app.engine.prompt_template import render_prompt, build_user_message

    provider = config.get("provider", "google")
    model = config.get("model", "gemini-2.5-flash")
    raw_prompt = config.get("systemPrompt", "")
    temperature = float(config.get("temperature", 0.7))
    max_tokens = int(config.get("maxTokens", 4096))

    system_prompt = render_prompt(raw_prompt, context)
    user_message = build_user_message(context)

    instance_id: str = context.get("_instance_id", "")
    node_id: str = context.get("_current_node_id", "")

    logger.info(
        "Agent node [%s/%s]: prompt_len=%d, user_msg_len=%d, streaming=%s",
        provider, model, len(system_prompt), len(user_message),
        bool(instance_id and node_id),
    )

    result = call_llm_streaming(
        provider=provider,
        model=model,
        system_prompt=system_prompt,
        user_message=user_message,
        temperature=temperature,
        max_tokens=max_tokens,
        instance_id=instance_id,
        node_id=node_id,
    )

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
        user_message=user_message,
        response=result.get("response", ""),
        usage=result.get("usage"),
    )

    return result


def _handle_action(
    node_data: dict, context: dict[str, Any], tenant_id: str
) -> dict[str, Any]:
    """Execute action nodes: MCP tool calls, HTTP requests, etc."""
    config = node_data.get("config", {})
    label = node_data.get("label", "")

    if config.get("toolName"):
        return _call_mcp_tool(config["toolName"], config.get("parameters", {}), tenant_id)

    if config.get("url"):
        return _call_http(config)

    logger.warning("Action node '%s' has no executable config", label)
    return {"output": None, "warning": "No action configured"}


def _call_mcp_tool(
    tool_name: str, parameters: dict, tenant_id: str
) -> dict[str, Any]:
    """Invoke a tool on the MCP server via Streamable HTTP transport."""
    from app.engine.mcp_client import call_tool
    from app.observability import span_tool, _NoOpSpan
    trace = _NoOpSpan()

    with span_tool(trace, tool_name=tool_name, arguments=parameters) as span:
        result = call_tool(tool_name, parameters)
        span.update(output=result)
        return result


def _call_http(config: dict) -> dict[str, Any]:
    """Make a generic HTTP request."""
    try:
        resp = httpx.request(
            method=config.get("method", "GET"),
            url=config["url"],
            headers=config.get("headers", {}),
            content=config.get("body", None),
            timeout=30.0,
        )
        return {
            "status_code": resp.status_code,
            "body": resp.text[:10000],
        }
    except httpx.HTTPError as exc:
        logger.error("HTTP request failed: %s", exc)
        return {"error": str(exc)}


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
        return {"merged": upstream}

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

    # Add loop item from parent forEach if nested
    if "_loop_item" in context:
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
    if msg_expr:
        raw = _resolve_expr(msg_expr, context)
        text = str(raw).strip() if raw is not None else ""
    elif response_node_id and response_node_id in context:
        node_out = context[response_node_id]
        if isinstance(node_out, dict):
            text = str(
                node_out.get("response", node_out.get("output", ""))
            ).strip()
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

    from app.database import SessionLocal
    from app.models.workflow import ConversationSession
    from sqlalchemy.exc import IntegrityError

    db = SessionLocal()
    try:
        session = (
            db.query(ConversationSession)
            .filter_by(session_id=session_id, tenant_id=tenant_id)
            .first()
        )
        if not session:
            try:
                session = ConversationSession(
                    session_id=session_id,
                    tenant_id=tenant_id,
                    messages=[],
                )
                db.add(session)
                db.commit()
                db.refresh(session)
            except IntegrityError:
                # Another concurrent DAG instance created the session first
                db.rollback()
                session = (
                    db.query(ConversationSession)
                    .filter_by(session_id=session_id, tenant_id=tenant_id)
                    .first()
                )

        messages = session.messages or [] if session else []
        logger.info(
            "Load Conversation State: session=%s messages=%d", session_id, len(messages)
        )
        return {
            "session_id": session_id,
            "messages": messages,
            "message_count": len(messages),
        }
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
    if response_node_id and response_node_id in context:
        node_out = context[response_node_id]
        if isinstance(node_out, dict):
            assistant_response = str(
                node_out.get("response", node_out.get("output", ""))
            )
        else:
            assistant_response = str(node_out)

    now = datetime.now(timezone.utc).isoformat()
    new_messages: list[dict] = []
    if user_message:
        new_messages.append({"role": "user", "content": user_message, "timestamp": now})
    if assistant_response:
        new_messages.append(
            {"role": "assistant", "content": assistant_response, "timestamp": now}
        )

    from app.database import SessionLocal
    from app.models.workflow import ConversationSession
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm.attributes import flag_modified

    db = SessionLocal()
    try:
        # Use with_for_update() to lock the row for the duration of the append,
        # preventing a lost-update race when multiple DAG instances share a session.
        session = (
            db.query(ConversationSession)
            .filter_by(session_id=session_id, tenant_id=tenant_id)
            .with_for_update()
            .first()
        )
        if not session:
            try:
                session = ConversationSession(
                    session_id=session_id,
                    tenant_id=tenant_id,
                    messages=new_messages,
                )
                db.add(session)
                db.commit()
            except IntegrityError:
                db.rollback()
                session = (
                    db.query(ConversationSession)
                    .filter_by(session_id=session_id, tenant_id=tenant_id)
                    .with_for_update()
                    .first()
                )
                if session:
                    session.messages = (session.messages or []) + new_messages
                    flag_modified(session, "messages")
                    db.commit()
        else:
            session.messages = (session.messages or []) + new_messages
            flag_modified(session, "messages")
            db.commit()

        total = len(session.messages) if session else len(new_messages)
        logger.info(
            "Save Conversation State: session=%s total_messages=%d", session_id, total
        )
        return {"session_id": session_id, "message_count": total, "saved": True}
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
    config = node_data.get("config", {})
    provider = config.get("provider", "google")
    model = config.get("model", "gemini-2.5-flash")
    intents: list[str] = config.get("intents", [])
    history_node_id = config.get("historyNodeId", "")
    user_msg_expr = config.get("userMessageExpression", "trigger.message")

    # Pull conversation history from the Load Conversation State node output
    messages: list[dict] = []
    if history_node_id and history_node_id in context:
        node_out = context[history_node_id]
        if isinstance(node_out, dict):
            messages = node_out.get("messages", [])

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

    # Include the last 10 messages as context (avoid unbounded token growth)
    history_lines = [
        f"{m.get('role', 'user').upper()}: {m.get('content', '')}"
        for m in messages[-10:]
    ]
    history_block = "\n".join(history_lines) if history_lines else "(no prior messages)"
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
    )

    logger.info("LLM Router classified intent='%s' (model=%s/%s)", intent, provider, model)
    return {
        "intent": intent,
        "raw_response": raw_response,
        "usage": result.get("usage"),
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

    # 4. Extract response text
    response_text = extract_response_text(final_task)

    logger.info(
        "A2A Agent Call: completed task=%s state=%s response_len=%d",
        task_id, final_state, len(response_text),
    )

    return {
        "task_id":  task_id,
        "state":    final_state,
        "response": response_text,
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

    from app.database import SessionLocal
    from app.engine.retriever import retrieve_chunks

    db = SessionLocal()
    try:
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
        from app.database import SessionLocal
        db = SessionLocal()

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

    # 3. Recursion protection: walk the parent chain
    parent_chain: list[str] = list(context.get("_parent_chain", []))
    current_wf_id = str(context.get("_workflow_def_id", ""))
    if current_wf_id:
        full_chain = parent_chain + [current_wf_id]
    else:
        full_chain = parent_chain

    if str(workflow_id) in full_chain:
        raise ValueError(
            f"Sub-Workflow: recursive cycle detected — workflow '{wf_def.name}' "
            f"is already in the call chain"
        )
    if len(full_chain) >= max_depth:
        raise ValueError(
            f"Sub-Workflow: maximum nesting depth ({max_depth}) exceeded"
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
        if "_loop_item" in context:
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

    # 6. Inject parent chain into the child's context for nested recursion checks
    child_instance.context_json = {
        "_parent_chain": full_chain,
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

