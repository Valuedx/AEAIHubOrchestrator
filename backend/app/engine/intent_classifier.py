"""Intent Classifier node handler.

Ports IntentEdge's hybrid scoring logic (lexical + embedding + optional LLM)
into the orchestrator's node execution model.
"""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any

logger = logging.getLogger(__name__)

EMBED_SCORE_WEIGHT = 4.0


def _normalize(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "")).strip().lower()


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    sa = sum(x * x for x in a)
    sb = sum(x * x for x in b)
    if sa == 0 or sb == 0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sa) * math.sqrt(sb))


def _match_intents(
    utt: str,
    intents_config: list[dict],
    allow_multi: bool,
    utterance_vec: list[float] | None = None,
    intent_vecs: list[list[float]] | None = None,
) -> tuple[list[str], float, dict[str, float]]:
    """Score intents against the utterance.

    Returns (matched_names, confidence, score_dict).
    """
    u = _normalize(utt)
    scored: list[tuple[str, float]] = []
    best = 0.0

    for idx, it in enumerate(intents_config):
        lexical = 0.0
        name = _normalize(it.get("name", ""))
        if name and name in u:
            lexical += 2.0
        for ex in it.get("examples", []):
            exn = _normalize(str(ex))
            if exn and exn in u:
                lexical += 1.0

        embed_score = 0.0
        if utterance_vec and intent_vecs and idx < len(intent_vecs):
            vec = intent_vecs[idx]
            if vec:
                embed_score = max(0.0, _cosine(utterance_vec, vec))

        total = lexical + embed_score * EMBED_SCORE_WEIGHT
        if total > 0:
            scored.append((it.get("name", f"intent_{idx}"), total))
            if total > best:
                best = total

    if not scored:
        return [], 0.0, {}

    scored.sort(key=lambda x: (-x[1], x[0]))
    scores_dict = {n: round(s, 4) for n, s in scored}

    if not allow_multi:
        picked = [scored[0][0]]
    else:
        picked = [n for n, s in scored if s >= max(1.0, best - 1.0)]

    conf = min(0.95, 0.5 + best * 0.1)
    return picked, conf, scores_dict


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


def _handle_intent_classifier(
    node_data: dict,
    context: dict[str, Any],
    tenant_id: str,
) -> dict[str, Any]:
    config = node_data.get("config", {})
    utt_expr = config.get("utteranceExpression", "trigger.message")
    intents_cfg: list[dict] = config.get("intents", [])
    allow_multi = bool(config.get("allowMultiIntent", False))
    mode = config.get("mode", "hybrid")
    provider = config.get("provider", "google")
    model = config.get("model", "gemini-2.5-flash")
    emb_provider = config.get("embeddingProvider", "openai")
    emb_model = config.get("embeddingModel", "text-embedding-3-small")
    cache_embeddings = bool(config.get("cacheEmbeddings", False))
    confidence_threshold = float(config.get("confidenceThreshold", 0.6))
    history_node_id = config.get("historyNodeId", "")

    raw_utt = _resolve_expr(utt_expr, context)
    utterance = str(raw_utt) if raw_utt is not None else str(
        context.get("trigger", {}).get("message", "")
    )

    if not utterance.strip():
        return {
            "intents": [],
            "confidence": 0.0,
            "fallback": True,
            "scores": {},
            "mode_used": mode,
        }

    if not intents_cfg:
        return {
            "intents": ["fallback_intent"],
            "confidence": 0.2,
            "fallback": True,
            "scores": {},
            "mode_used": mode,
        }

    # --- LLM-only mode: skip embeddings entirely ---
    if mode == "llm_only":
        return _llm_classify(
            utterance, intents_cfg, allow_multi, provider, model,
            history_node_id, context, tenant_id,
        )

    # --- Heuristic / hybrid: get intent vectors ---
    intent_vecs: list[list[float]] = []
    utterance_vec: list[float] = []

    if cache_embeddings:
        from app.engine.embedding_cache_helper import get_or_embed, _intent_text
        from app.database import SessionLocal

        texts = [_intent_text(it) for it in intents_cfg]
        db = SessionLocal()
        try:
            intent_vecs = get_or_embed(tenant_id, texts, emb_provider, emb_model, db)
        finally:
            db.close()

        from app.engine.embedding_provider import get_embedding_sync
        utterance_vec = get_embedding_sync(utterance, emb_provider, emb_model)
    else:
        from app.engine.embedding_cache_helper import embed_batch_transient, _intent_text

        texts = [_intent_text(it) for it in intents_cfg] + [utterance]
        all_vecs = embed_batch_transient(texts, emb_provider, emb_model)
        intent_vecs = all_vecs[:-1]
        utterance_vec = all_vecs[-1]

    matched, conf, scores = _match_intents(
        utterance, intents_cfg, allow_multi, utterance_vec, intent_vecs,
    )

    is_fallback = len(matched) == 0

    # --- Hybrid: LLM fallback if confidence below threshold ---
    if mode == "hybrid" and (is_fallback or conf < confidence_threshold):
        llm_result = _llm_classify(
            utterance, intents_cfg, allow_multi, provider, model,
            history_node_id, context, tenant_id,
        )
        llm_result["mode_used"] = "hybrid_llm_fallback"
        llm_result["heuristic_scores"] = scores
        return llm_result

    if is_fallback:
        matched = ["fallback_intent"]
        conf = 0.2

    return {
        "intents": matched,
        "confidence": round(conf, 3),
        "fallback": is_fallback,
        "scores": scores,
        "mode_used": "heuristic_only" if mode == "heuristic_only" else "hybrid_heuristic",
    }


def _llm_classify(
    utterance: str,
    intents_cfg: list[dict],
    allow_multi: bool,
    provider: str,
    model: str,
    history_node_id: str,
    context: dict[str, Any],
    tenant_id: str,
) -> dict[str, Any]:
    """Classify intent using an LLM call."""
    from app.engine.llm_providers import call_llm
    from app.database import SessionLocal
    from app.engine.memory_service import assemble_history_text

    intent_list = "\n".join(
        f"- {it.get('name', '')}: {it.get('description', '')}"
        for it in intents_cfg
    )

    multi_note = (
        "Return one or more matching intents as an array."
        if allow_multi
        else "Return exactly ONE intent."
    )

    db = SessionLocal()
    try:
        history_block, memory_debug = assemble_history_text(
            db,
            tenant_id=tenant_id,
            workflow_def_id=str(context.get("_workflow_def_id", "") or ""),
            context=context,
            node_config={"historyNodeId": str(history_node_id or "").strip()},
        )
    finally:
        db.close()

    system_prompt = (
        "You are an intent classification engine.\n"
        "Analyze the conversation and classify the user's latest message.\n\n"
        f"Available intents:\n{intent_list}\n\n"
        f"{multi_note}\n\n"
        "Respond ONLY with a valid JSON object:\n"
        '{"intents": ["<intent_name>", ...], "confidence": 0.0-1.0}\n\n'
        "Do not include any other text, explanation, or markdown formatting."
    )

    user_prompt = (
        f"Conversation history:\n{history_block}\n\n"
        f"Latest user message: {utterance}\n\n"
        "Classify the intent:"
    )

    result = call_llm(
        provider=provider,
        model=model,
        system_prompt=system_prompt,
        user_message=user_prompt,
        temperature=0.1,
        max_tokens=128,
    )

    raw_response = result.get("response", "").strip()

    from app.observability import record_generation
    record_generation(
        context.get("_trace"),
        name=f"intent_classifier:{provider}/{model}",
        provider=provider,
        model=model,
        system_prompt=system_prompt,
        user_message=user_prompt,
        response=raw_response,
        usage=result.get("usage"),
        metadata={"memory": memory_debug},
    )

    matched: list[str] = []
    conf = 0.7
    try:
        parsed = json.loads(raw_response)
        matched = parsed.get("intents", [])
        conf = float(parsed.get("confidence", 0.7))
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw_response)
        if m:
            try:
                parsed = json.loads(m.group(0))
                matched = parsed.get("intents", [])
                conf = float(parsed.get("confidence", 0.7))
            except Exception:
                pass

    if isinstance(matched, str):
        matched = [matched]

    valid_names = {it.get("name", "") for it in intents_cfg}
    matched = [m for m in matched if m in valid_names]

    is_fallback = len(matched) == 0
    if is_fallback:
        matched = ["fallback_intent"]
        conf = 0.2

    return {
        "intents": matched,
        "confidence": round(conf, 3),
        "fallback": is_fallback,
        "scores": {},
        "mode_used": "llm_only",
        "memory_debug": memory_debug,
    }
