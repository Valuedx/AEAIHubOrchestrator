"""Entity Extractor node handler.

Ports IntentEdge's rule-based extraction (regex, enum, number, date, free_text)
plus optional LLM fallback for missing required entities.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def _extract_entities_from_config(
    text: str,
    entity_configs: list[dict],
) -> dict[str, str]:
    """Rule-based entity extraction matching IntentEdge's logic."""
    out: dict[str, str] = {}
    for ent in entity_configs:
        name = ent.get("name", "")
        etype = ent.get("type", "free_text")

        if etype == "regex" and ent.get("pattern"):
            try:
                m = re.search(ent["pattern"], text, flags=re.IGNORECASE)
                if m:
                    out[name] = m.group(1) if m.groups() else m.group(0)
            except re.error:
                pass

        elif etype == "enum" and ent.get("enum_values"):
            for v in [str(val) for val in ent["enum_values"]]:
                if re.search(rf"\b{re.escape(v)}\b", text, flags=re.IGNORECASE):
                    out[name] = v
                    break

        elif etype == "number":
            m = re.search(r"(-?\d+(?:\.\d+)?)", text)
            if m:
                out[name] = m.group(1)

        elif etype == "date":
            m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
            if m:
                out[name] = m.group(1)

        elif etype == "free_text":
            m = re.search(
                rf"{re.escape(name)}\s*:\s*([^.,;]+)", text, flags=re.IGNORECASE
            )
            if m:
                out[name] = m.group(1).strip()

    return out


def _scope_entities(
    entity_configs: list[dict],
    intent_entity_mapping: dict[str, list[str]],
    matched_intents: list[str],
) -> list[dict]:
    """Restrict entity list to those relevant to the matched intents."""
    if not matched_intents or not intent_entity_mapping:
        return entity_configs

    allowed_names: set[str] = set()
    has_restriction = False
    for intent_name in matched_intents:
        mapping = intent_entity_mapping.get(intent_name)
        if mapping is not None:
            has_restriction = True
            allowed_names.update(mapping)

    if not has_restriction:
        return entity_configs

    return [e for e in entity_configs if e.get("name", "") in allowed_names]


def _resolve_expr(expr: str, context: dict[str, Any]) -> Any:
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
        logger.warning("Expression '%s' rejected: %s", expr, exc)
        return None
    except Exception as exc:
        logger.warning("Expression '%s' error: %s", expr, exc)
        return None


def _handle_entity_extractor(
    node_data: dict,
    context: dict[str, Any],
    tenant_id: str,
) -> dict[str, Any]:
    config = node_data.get("config", {})
    source_expr = config.get("sourceExpression", "trigger.message")
    entity_configs: list[dict] = config.get("entities", [])
    scope_from_node = config.get("scopeFromNode", "")
    intent_entity_mapping: dict = config.get("intentEntityMapping", {})
    llm_fallback = bool(config.get("llmFallback", False))
    provider = config.get("provider", "google")
    model = config.get("model", "gemini-2.5-flash")

    raw_src = _resolve_expr(source_expr, context)
    source_text = str(raw_src) if raw_src is not None else str(
        context.get("trigger", {}).get("message", "")
    )

    if not source_text.strip():
        return {
            "entities": {},
            "missing_required": [e.get("name", "") for e in entity_configs if e.get("required")],
            "extraction_method": "none",
        }

    # Scope entities by upstream intent classification
    matched_intents: list[str] = []
    if scope_from_node and scope_from_node in context:
        upstream_out = context[scope_from_node]
        if isinstance(upstream_out, dict):
            matched_intents = upstream_out.get("intents", [])

    scoped = _scope_entities(entity_configs, intent_entity_mapping, matched_intents)

    extracted = _extract_entities_from_config(source_text, scoped)

    required_names = [e.get("name", "") for e in scoped if e.get("required")]
    missing_required = [n for n in required_names if n not in extracted]

    extraction_method = "rule_based"

    # LLM fallback for missing required entities
    if llm_fallback and missing_required:
        llm_entities = _llm_extract(
            source_text, scoped, missing_required, provider, model, context,
        )
        for k, v in llm_entities.items():
            if k not in extracted:
                extracted[k] = v
        missing_required = [n for n in required_names if n not in extracted]
        extraction_method = "rule_based+llm_fallback"

    result: dict[str, Any] = {"entities": extracted}
    for k, v in extracted.items():
        result[k] = v
    result["missing_required"] = missing_required
    result["extraction_method"] = extraction_method

    logger.info(
        "Entity Extractor: extracted=%d, missing_required=%d, method=%s",
        len(extracted),
        len(missing_required),
        extraction_method,
    )

    return result


def _llm_extract(
    text: str,
    entity_configs: list[dict],
    missing_names: list[str],
    provider: str,
    model: str,
    context: dict[str, Any],
) -> dict[str, str]:
    """Use an LLM to extract entities that rule-based extraction missed."""
    from app.engine.llm_providers import call_llm

    entity_desc = "\n".join(
        f"- {e.get('name', '')} ({e.get('type', 'free_text')}): {e.get('description', '')}"
        for e in entity_configs
        if e.get("name", "") in missing_names
    )

    system_prompt = (
        "You are an entity extraction engine.\n"
        "Extract the requested entities from the user text.\n\n"
        f"Entities to extract:\n{entity_desc}\n\n"
        "Respond ONLY with a valid JSON object mapping entity names to extracted values.\n"
        "If an entity cannot be found, omit it from the result.\n"
        "Do not include any other text, explanation, or markdown formatting."
    )

    user_prompt = f"Text: {text}"

    result = call_llm(
        provider=provider,
        model=model,
        system_prompt=system_prompt,
        user_message=user_prompt,
        temperature=0.1,
        max_tokens=256,
    )

    raw_response = result.get("response", "").strip()

    from app.observability import record_generation
    record_generation(
        context.get("_trace"),
        name=f"entity_extractor:{provider}/{model}",
        provider=provider,
        model=model,
        system_prompt=system_prompt,
        user_message=user_prompt,
        response=raw_response,
        usage=result.get("usage"),
    )

    try:
        parsed = json.loads(raw_response)
        if isinstance(parsed, dict):
            return {k: str(v) for k, v in parsed.items() if k in missing_names}
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw_response)
        if m:
            try:
                parsed = json.loads(m.group(0))
                if isinstance(parsed, dict):
                    return {k: str(v) for k, v in parsed.items() if k in missing_names}
            except Exception:
                pass

    return {}
