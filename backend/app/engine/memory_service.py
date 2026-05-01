"""Shared advanced memory services for normalized conversation storage and runtime assembly."""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.engine.embedding_provider import get_embedding_sync, get_embeddings_batch_sync
from app.engine.llm_providers import call_llm
from app.engine.memory_vector_store import MemoryVectorData, get_memory_vector_store
from app.engine.prompt_template import (
    build_structured_context_block,
    count_prompt_tokens,
    truncate_to_tokens,
)
from app.models.memory import (
    ConversationEpisode,
    ConversationMessage,
    EntityFact,
    MemoryProfile,
    MemoryRecord,
)
from app.models.workflow import ConversationSession

logger = logging.getLogger(__name__)

DEFAULT_SCOPES = ["session", "workflow", "tenant", "entity"]
DEFAULT_RECENT_TOKENS = 1200
DEFAULT_SEMANTIC_HITS = 4
DEFAULT_SUMMARY_TRIGGER_MESSAGES = 12
DEFAULT_SUMMARY_RECENT_TURNS = 6
DEFAULT_SUMMARY_MAX_TOKENS = 400
DEFAULT_HISTORY_ORDER = "summary_first"
# Sourced from the central model registry. MODEL-01.c wires the
# engine-wide defaults to ``default_llm_for(provider, role="fast")`` so
# memory checkpoint summaries track the same Gemini baseline every
# other AI node uses. Bump the registry's tier default to rotate both
# in lockstep.
from app.engine.model_registry import default_llm_for as _default_llm_for

DEFAULT_SUMMARY_PROVIDER = "vertex"
DEFAULT_SUMMARY_MODEL = _default_llm_for(DEFAULT_SUMMARY_PROVIDER, role="fast")
DEFAULT_EPISODE_ARCHIVE_PROVIDER = "vertex"
DEFAULT_EPISODE_ARCHIVE_MODEL = _default_llm_for(
    DEFAULT_EPISODE_ARCHIVE_PROVIDER, role="fast"
)
DEFAULT_EPISODE_INACTIVITY_MINUTES = 10080
DEFAULT_EPISODE_MIN_TURNS = 2
DEFAULT_AUTO_ARCHIVE_ON_RESOLVED = True
DEFAULT_PROMOTE_INTERACTIONS = True
DEFAULT_EMBEDDING_PROVIDER = "vertex"
DEFAULT_EMBEDDING_MODEL = "text-embedding-005"
DEFAULT_VECTOR_STORE = "pgvector"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_expr(expr: str, context: dict[str, Any]) -> Any:
    if not expr:
        return None
    from app.engine.safe_eval import safe_eval, SafeEvalError

    upstream = {k: v for k, v in context.items() if k.startswith("node_")}
    eval_env = {"trigger": context.get("trigger", {}), "context": context}
    eval_env.update(upstream)
    try:
        return safe_eval(expr, eval_env)
    except SafeEvalError as exc:
        logger.warning("Memory expression '%s' rejected: %s", expr, exc)
        return None
    except Exception as exc:
        logger.warning("Memory expression '%s' error: %s", expr, exc)
        return None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse_scopes(raw: Any) -> list[str]:
    if isinstance(raw, list):
        scopes = [str(v) for v in raw if str(v) in DEFAULT_SCOPES]
        return scopes or list(DEFAULT_SCOPES)
    return list(DEFAULT_SCOPES)


def _parse_history_order(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in {"summary_first", "recent_first"}:
        return value
    return DEFAULT_HISTORY_ORDER


def _latest_user_message(context: dict[str, Any]) -> str:
    trigger = context.get("trigger", {}) or {}
    for key in ("message", "user_message", "text", "prompt"):
        val = trigger.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _find_history_output(
    context: dict[str, Any],
    preferred_node_id: str = "",
) -> tuple[str | None, dict[str, Any] | None]:
    if preferred_node_id:
        node_out = context.get(preferred_node_id)
        if isinstance(node_out, dict) and "session_id" in node_out and "messages" in node_out:
            return preferred_node_id, node_out
    for key, value in context.items():
        if not key.startswith("node_") or not isinstance(value, dict):
            continue
        if "session_id" in value and "messages" in value:
            return key, value
    return None, None


def serialize_message(message: ConversationMessage) -> dict[str, Any]:
    return {
        "role": message.role,
        "content": message.content,
        "timestamp": _iso(message.message_at),
    }


def build_conversation_idempotency_key(
    *,
    session_id: str,
    instance_id: str | None,
    node_id: str | None,
    loop_iteration: int | None,
    user_message: str,
    assistant_response: str,
) -> str:
    payload = {
        "session_id": session_id,
        "instance_id": instance_id or "",
        "node_id": node_id or "",
        "loop_iteration": loop_iteration,
        "user_message": user_message.strip(),
        "assistant_response": assistant_response.strip(),
    }
    encoded = repr(sorted(payload.items())).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_memory_record_dedupe_key(
    *,
    tenant_id: str,
    scope: str,
    scope_key: str,
    kind: str,
    conversation_idempotency_key: str,
) -> str:
    payload = {
        "tenant_id": tenant_id,
        "scope": scope,
        "scope_key": scope_key,
        "kind": kind,
        "conversation_idempotency_key": conversation_idempotency_key,
    }
    encoded = repr(sorted(payload.items())).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_episode_archive_dedupe_key(
    *,
    session_id: str,
    episode_id: str,
    end_turn: int,
    reason: str,
) -> str:
    payload = {
        "session_id": session_id,
        "episode_id": episode_id,
        "end_turn": end_turn,
        "reason": reason,
    }
    encoded = repr(sorted(payload.items())).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def get_or_create_session(
    db: Session,
    *,
    tenant_id: str,
    session_id: str,
    lock: bool = False,
) -> ConversationSession:
    query = db.query(ConversationSession).filter_by(session_id=session_id, tenant_id=tenant_id)
    if lock:
        query = query.with_for_update()
    session = query.first()
    if session:
        return session

    session = ConversationSession(
        session_id=session_id,
        tenant_id=tenant_id,
        message_count=0,
        summary_through_turn=0,
    )
    db.add(session)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        query = db.query(ConversationSession).filter_by(session_id=session_id, tenant_id=tenant_id)
        if lock:
            query = query.with_for_update()
        session = query.first()
        if session:
            return session
        raise
    return session


def get_active_episode(
    db: Session,
    *,
    session: ConversationSession,
    lock: bool = False,
) -> ConversationEpisode | None:
    active_episode_id = session.active_episode_id

    # Fast path: session already tracks its active episode by ID.
    if active_episode_id:
        q = (
            db.query(ConversationEpisode)
            .filter_by(id=active_episode_id, session_ref_id=session.id, status="active")
        )
        if lock:
            q = q.with_for_update()
        episode = q.first()
        if episode is not None:
            return episode
        # Pointer is stale — fall through to full scan below.

    # Full scan: find the most recent active episode for this session.
    q = (
        db.query(ConversationEpisode)
        .filter_by(session_ref_id=session.id, status="active")
        .order_by(ConversationEpisode.created_at.desc())
    )
    if lock:
        q = q.with_for_update()
    episode = q.first()

    if episode is not None:
        # Sync the session pointer if it was absent or stale.
        if session.active_episode_id != episode.id:
            session.active_episode_id = episode.id
            session.updated_at = _utcnow()
        return episode

    # No active episode found — clear stale pointer if present.
    if active_episode_id is not None:
        session.active_episode_id = None
        session.updated_at = _utcnow()
    return None


def _sync_session_working_summary(
    session: ConversationSession,
    episode: ConversationEpisode | None,
    *,
    clear: bool = False,
) -> None:
    changed = False
    if episode and episode.status == "active":
        if getattr(session, "active_episode_id", None) != episode.id:
            setattr(session, "active_episode_id", episode.id)
            changed = True
        if getattr(session, "summary_text", None) != episode.checkpoint_summary_text:
            session.summary_text = episode.checkpoint_summary_text
            changed = True
        if getattr(session, "summary_updated_at", None) != episode.summary_updated_at:
            session.summary_updated_at = episode.summary_updated_at
            changed = True
        if getattr(session, "summary_through_turn", None) != episode.summary_through_turn:
            session.summary_through_turn = episode.summary_through_turn
            changed = True
    elif clear:
        if getattr(session, "active_episode_id", None) is not None:
            setattr(session, "active_episode_id", None)
            changed = True
        if getattr(session, "summary_text", None) is not None:
            session.summary_text = None
            changed = True
        if getattr(session, "summary_updated_at", None) is not None:
            session.summary_updated_at = None
            changed = True
        if getattr(session, "summary_through_turn", None) != session.message_count:
            session.summary_through_turn = session.message_count
            changed = True
    else:
        if getattr(session, "active_episode_id", None) is not None:
            setattr(session, "active_episode_id", None)
            changed = True
    if changed:
        session.updated_at = _utcnow()


def get_or_create_active_episode(
    db: Session,
    *,
    session: ConversationSession,
    tenant_id: str,
    workflow_def_id: str | None,
    memory_profile_id: str | None,
    starting_turn: int,
) -> ConversationEpisode:
    existing = get_active_episode(db, session=session, lock=True)
    if existing:
        if workflow_def_id:
            existing.workflow_def_id = uuid.UUID(workflow_def_id)
        if memory_profile_id:
            existing.memory_profile_id = uuid.UUID(memory_profile_id)
        existing.last_activity_at = session.last_message_at or _utcnow()
        existing.updated_at = _utcnow()
        _sync_session_working_summary(session, existing)
        db.flush()
        return existing

    episode = ConversationEpisode(
        tenant_id=tenant_id,
        session_ref_id=session.id,
        workflow_def_id=uuid.UUID(workflow_def_id) if workflow_def_id else None,
        memory_profile_id=uuid.UUID(memory_profile_id) if memory_profile_id else None,
        status="active",
        start_turn=max(1, starting_turn),
        summary_through_turn=max(0, starting_turn - 1),
        last_activity_at=session.last_message_at or _utcnow(),
    )
    db.add(episode)
    db.flush()
    _sync_session_working_summary(session, episode)
    db.flush()
    return episode


def load_conversation_state(
    db: Session,
    *,
    tenant_id: str,
    session_id: str,
) -> dict[str, Any]:
    session = get_or_create_session(db, tenant_id=tenant_id, session_id=session_id)
    active_episode = get_active_episode(db, session=session)
    _sync_session_working_summary(session, active_episode)
    messages = (
        db.query(ConversationMessage)
        .filter_by(session_ref_id=session.id)
        .order_by(ConversationMessage.turn_index)
        .all()
    )
    return {
        "session_id": session.session_id,
        "session_ref_id": str(session.id),
        "messages": [serialize_message(msg) for msg in messages],
        "message_count": session.message_count,
        "summary_text": session.summary_text or "",
        "summary_through_turn": session.summary_through_turn,
        "active_episode_id": str(active_episode.id) if active_episode else None,
        "active_episode": (
            {
                "id": str(active_episode.id),
                "status": active_episode.status,
                "start_turn": active_episode.start_turn,
                "end_turn": active_episode.end_turn,
                "title": active_episode.title,
                "checkpoint_summary_text": active_episode.checkpoint_summary_text or "",
                "summary_through_turn": active_episode.summary_through_turn,
                "last_activity_at": _iso(active_episode.last_activity_at),
                "archived_at": _iso(active_episode.archived_at),
            }
            if active_episode else None
        ),
    }


def append_conversation_turns(
    db: Session,
    *,
    tenant_id: str,
    session_id: str,
    user_message: str,
    assistant_response: str,
    workflow_def_id: str | None,
    instance_id: str | None,
    node_id: str | None,
    idempotency_key: str,
) -> tuple[ConversationSession, list[ConversationMessage]]:
    session = get_or_create_session(
        db,
        tenant_id=tenant_id,
        session_id=session_id,
        lock=True,
    )

    existing = (
        db.query(ConversationMessage)
        .filter_by(session_ref_id=session.id, idempotency_key=idempotency_key)
        .all()
    )
    if existing:
        return session, existing

    now = _utcnow()
    next_turn = session.message_count + 1
    rows: list[ConversationMessage] = []
    wf_uuid = uuid.UUID(workflow_def_id) if workflow_def_id else None
    inst_uuid = uuid.UUID(instance_id) if instance_id else None

    if user_message:
        rows.append(
            ConversationMessage(
                session_ref_id=session.id,
                tenant_id=tenant_id,
                session_id=session_id,
                turn_index=next_turn,
                role="user",
                content=user_message,
                message_at=now,
                workflow_def_id=wf_uuid,
                instance_id=inst_uuid,
                node_id=node_id,
                idempotency_key=idempotency_key,
                created_at=now,
            )
        )
        next_turn += 1
    if assistant_response:
        rows.append(
            ConversationMessage(
                session_ref_id=session.id,
                tenant_id=tenant_id,
                session_id=session_id,
                turn_index=next_turn,
                role="assistant",
                content=assistant_response,
                message_at=now,
                workflow_def_id=wf_uuid,
                instance_id=inst_uuid,
                node_id=node_id,
                idempotency_key=idempotency_key,
                created_at=now,
            )
        )

    for row in rows:
        db.add(row)

    if rows:
        session.message_count += len(rows)
        session.last_message_at = rows[-1].message_at
        session.updated_at = now
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            session = get_or_create_session(
                db,
                tenant_id=tenant_id,
                session_id=session_id,
                lock=True,
            )
            existing = (
                db.query(ConversationMessage)
                .filter_by(session_ref_id=session.id, idempotency_key=idempotency_key)
                .order_by(ConversationMessage.turn_index)
                .all()
            )
            return session, existing

    return session, rows


def _summary_text_from_messages(
    existing_summary: str,
    messages: list[ConversationMessage],
    *,
    max_tokens: int,
) -> str:
    lines: list[str] = []
    if existing_summary.strip():
        lines.append(existing_summary.strip())
    for msg in messages:
        content = " ".join((msg.content or "").split())
        if not content:
            continue
        lines.append(f"{msg.role.upper()}: {content}")
    return truncate_to_tokens("\n".join(lines), max_tokens)


def _llm_checkpoint_summary(
    *,
    existing_summary: str,
    messages: list[ConversationMessage],
    max_tokens: int,
    provider: str,
    model: str,
    mode: str,
    tenant_id: str | None = None,
) -> str:
    message_lines = [
        f"{msg.role.upper()}: {' '.join((msg.content or '').split())}"
        for msg in messages
        if (msg.content or "").strip()
    ]
    if not message_lines:
        return truncate_to_tokens(existing_summary or "", max_tokens)

    system_prompt = (
        "You maintain concise issue-scoped working memory for a workflow agent. "
        "Return plain text only. Preserve the current issue, decisions, facts, open questions, and next actions. "
        "Do not include markdown bullets unless the content naturally needs them."
    )
    if mode == "archive":
        system_prompt = (
            "You write a final archived issue summary for a workflow agent. Return plain text only. "
            "Capture the original problem, important facts, actions taken, and the current resolution or handoff state. "
            "Do not invent details."
        )

    user_message = "\n\n".join(
        part
        for part in [
            f"Existing summary:\n{existing_summary.strip()}" if existing_summary.strip() else "",
            "New conversation turns:\n" + "\n".join(message_lines),
            f"Keep the summary within roughly {max_tokens} tokens.",
        ]
        if part
    )
    try:
        result = call_llm(
            provider=provider,
            model=model,
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=0.0,
            max_tokens=max_tokens,
            tenant_id=tenant_id,
        )
        text = str(result.get("response", "") or "").strip()
        if text:
            return truncate_to_tokens(text, max_tokens)
    except Exception as exc:
        logger.warning("LLM %s summary generation failed for %s/%s: %s", mode, provider, model, exc)
    return _summary_text_from_messages(existing_summary, messages, max_tokens=max_tokens)


def refresh_rolling_summary(
    db: Session,
    *,
    session: ConversationSession,
    episode: ConversationEpisode | None,
    policy: "EffectiveMemoryPolicy",
) -> bool:
    if episode is None or episode.status != "active":
        return False

    target_turn = max(episode.start_turn - 1, session.message_count - policy.summary_recent_turns)
    if target_turn <= episode.summary_through_turn:
        return False
    if (
        episode.checkpoint_summary_text
        and (target_turn - episode.summary_through_turn) < policy.summary_trigger_messages
    ):
        return False

    new_rows = (
        db.query(ConversationMessage)
        .filter(
            ConversationMessage.session_ref_id == session.id,
            ConversationMessage.turn_index >= episode.start_turn,
            ConversationMessage.turn_index > episode.summary_through_turn,
            ConversationMessage.turn_index <= target_turn,
        )
        .order_by(ConversationMessage.turn_index)
        .all()
    )
    if not new_rows:
        return False

    episode.checkpoint_summary_text = _llm_checkpoint_summary(
        existing_summary=episode.checkpoint_summary_text or "",
        messages=new_rows,
        max_tokens=policy.summary_max_tokens,
        provider=policy.summary_provider,
        model=policy.summary_model,
        mode="checkpoint",
        tenant_id=session.tenant_id,
    )
    episode.summary_updated_at = _utcnow()
    episode.summary_through_turn = target_turn
    episode.last_activity_at = session.last_message_at or episode.last_activity_at
    episode.updated_at = episode.summary_updated_at
    _sync_session_working_summary(session, episode)
    db.flush()
    return True


@dataclass
class EffectiveMemoryPolicy:
    enabled: bool = True
    history_node_id: str | None = None
    recent_token_budget: int = DEFAULT_RECENT_TOKENS
    max_semantic_hits: int = DEFAULT_SEMANTIC_HITS
    include_entity_memory: bool = True
    scopes: list[str] = field(default_factory=lambda: list(DEFAULT_SCOPES))
    instructions: list[str] = field(default_factory=list)
    summary_trigger_messages: int = DEFAULT_SUMMARY_TRIGGER_MESSAGES
    summary_recent_turns: int = DEFAULT_SUMMARY_RECENT_TURNS
    summary_max_tokens: int = DEFAULT_SUMMARY_MAX_TOKENS
    history_order: str = DEFAULT_HISTORY_ORDER
    summary_provider: str = DEFAULT_SUMMARY_PROVIDER
    summary_model: str = DEFAULT_SUMMARY_MODEL
    episode_archive_provider: str = DEFAULT_EPISODE_ARCHIVE_PROVIDER
    episode_archive_model: str = DEFAULT_EPISODE_ARCHIVE_MODEL
    episode_inactivity_minutes: int = DEFAULT_EPISODE_INACTIVITY_MINUTES
    episode_min_turns: int = DEFAULT_EPISODE_MIN_TURNS
    auto_archive_on_resolved: bool = DEFAULT_AUTO_ARCHIVE_ON_RESOLVED
    promote_interactions: bool = DEFAULT_PROMOTE_INTERACTIONS
    semantic_score_threshold: float = 0.0
    embedding_provider: str = DEFAULT_EMBEDDING_PROVIDER
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    vector_store: str = DEFAULT_VECTOR_STORE
    entity_mappings: list[dict[str, Any]] = field(default_factory=list)
    selected_profile_id: str | None = None


def _load_default_profile(
    db: Session,
    *,
    tenant_id: str,
    workflow_def_id: str | None,
) -> MemoryProfile | None:
    query = db.query(MemoryProfile).filter_by(tenant_id=tenant_id, is_default=True)
    if workflow_def_id:
        workflow_profile = (
            query.filter_by(workflow_def_id=uuid.UUID(workflow_def_id))
            .order_by(MemoryProfile.updated_at.desc())
            .first()
        )
        if workflow_profile:
            return workflow_profile
    return (
        query.filter(MemoryProfile.workflow_def_id.is_(None))
        .order_by(MemoryProfile.updated_at.desc())
        .first()
    )


def resolve_memory_policy(
    db: Session,
    *,
    tenant_id: str,
    workflow_def_id: str | None,
    node_config: dict[str, Any],
    context: dict[str, Any],
) -> EffectiveMemoryPolicy:
    if node_config.get("memoryEnabled") is False:
        return EffectiveMemoryPolicy(enabled=False)

    tenant_profile = (
        db.query(MemoryProfile)
        .filter_by(tenant_id=tenant_id, workflow_def_id=None, is_default=True)
        .order_by(MemoryProfile.updated_at.desc())
        .first()
    )

    selected_profile = None
    raw_profile_id = str(node_config.get("memoryProfileId", "") or "").strip()
    if raw_profile_id:
        try:
            selected_profile = (
                db.query(MemoryProfile)
                .filter_by(id=uuid.UUID(raw_profile_id), tenant_id=tenant_id)
                .first()
            )
        except ValueError:
            logger.warning("Invalid memoryProfileId: %s", raw_profile_id)

    workflow_profile = _load_default_profile(db, tenant_id=tenant_id, workflow_def_id=workflow_def_id)
    primary = selected_profile or workflow_profile

    scopes = _parse_scopes(node_config.get("memoryScopes"))
    if not node_config.get("memoryScopes") and primary and isinstance(primary.enabled_scopes, list):
        scopes = _parse_scopes(primary.enabled_scopes)
    elif not node_config.get("memoryScopes") and tenant_profile and isinstance(tenant_profile.enabled_scopes, list):
        scopes = _parse_scopes(tenant_profile.enabled_scopes)

    include_entity_memory = bool(
        node_config.get(
            "includeEntityMemory",
            primary.include_entity_memory if primary else (
                tenant_profile.include_entity_memory if tenant_profile else True
            ),
        )
    )
    if not include_entity_memory and "entity" in scopes:
        scopes = [scope for scope in scopes if scope != "entity"]

    history_node_id, _ = _find_history_output(
        context,
        preferred_node_id=str(node_config.get("historyNodeId", "") or "").strip(),
    )

    instructions: list[str] = []
    if tenant_profile and tenant_profile.instructions_text:
        instructions.append(tenant_profile.instructions_text)
    if primary and primary.instructions_text and primary.id != getattr(tenant_profile, "id", None):
        instructions.append(primary.instructions_text)

    policy = EffectiveMemoryPolicy(
        enabled=True,
        history_node_id=history_node_id,
        recent_token_budget=int(
            node_config.get(
                "maxRecentTokens",
                primary.max_recent_tokens if primary else (
                    tenant_profile.max_recent_tokens if tenant_profile else DEFAULT_RECENT_TOKENS
                ),
            )
        ),
        max_semantic_hits=int(
            node_config.get(
                "maxSemanticHits",
                primary.max_semantic_hits if primary else (
                    tenant_profile.max_semantic_hits if tenant_profile else DEFAULT_SEMANTIC_HITS
                ),
            )
        ),
        include_entity_memory=include_entity_memory,
        scopes=scopes,
        instructions=[s for s in instructions if s and str(s).strip()],
        summary_trigger_messages=int(
            primary.summary_trigger_messages if primary else (
                tenant_profile.summary_trigger_messages if tenant_profile else DEFAULT_SUMMARY_TRIGGER_MESSAGES
            )
        ),
        summary_recent_turns=int(
            primary.summary_recent_turns if primary else (
                tenant_profile.summary_recent_turns if tenant_profile else DEFAULT_SUMMARY_RECENT_TURNS
            )
        ),
        summary_max_tokens=int(
            primary.summary_max_tokens if primary else (
                tenant_profile.summary_max_tokens if tenant_profile else DEFAULT_SUMMARY_MAX_TOKENS
            )
        ),
        history_order=_parse_history_order(
            node_config.get(
                "historyOrder",
                primary.history_order if primary else (
                tenant_profile.history_order if tenant_profile else DEFAULT_HISTORY_ORDER
            ),
        )
        ),
        summary_provider=(
            primary.summary_provider if primary else (
                tenant_profile.summary_provider if tenant_profile else DEFAULT_SUMMARY_PROVIDER
            )
        ),
        summary_model=(
            primary.summary_model if primary else (
                tenant_profile.summary_model if tenant_profile else DEFAULT_SUMMARY_MODEL
            )
        ),
        episode_archive_provider=(
            primary.episode_archive_provider if primary else (
                tenant_profile.episode_archive_provider
                if tenant_profile else DEFAULT_EPISODE_ARCHIVE_PROVIDER
            )
        ),
        episode_archive_model=(
            primary.episode_archive_model if primary else (
                tenant_profile.episode_archive_model
                if tenant_profile else DEFAULT_EPISODE_ARCHIVE_MODEL
            )
        ),
        episode_inactivity_minutes=int(
            primary.episode_inactivity_minutes if primary else (
                tenant_profile.episode_inactivity_minutes
                if tenant_profile else DEFAULT_EPISODE_INACTIVITY_MINUTES
            )
        ),
        episode_min_turns=int(
            primary.episode_min_turns if primary else (
                tenant_profile.episode_min_turns if tenant_profile else DEFAULT_EPISODE_MIN_TURNS
            )
        ),
        auto_archive_on_resolved=bool(
            primary.auto_archive_on_resolved if primary else (
                tenant_profile.auto_archive_on_resolved
                if tenant_profile else DEFAULT_AUTO_ARCHIVE_ON_RESOLVED
            )
        ),
        promote_interactions=bool(
            primary.promote_interactions if primary else (
                tenant_profile.promote_interactions if tenant_profile else DEFAULT_PROMOTE_INTERACTIONS
            )
        ),
        semantic_score_threshold=float(
            primary.semantic_score_threshold if primary else (
                tenant_profile.semantic_score_threshold if tenant_profile else 0.0
            )
        ),
        embedding_provider=(
            primary.embedding_provider if primary else (
                tenant_profile.embedding_provider if tenant_profile else DEFAULT_EMBEDDING_PROVIDER
            )
        ),
        embedding_model=(
            primary.embedding_model if primary else (
                tenant_profile.embedding_model if tenant_profile else DEFAULT_EMBEDDING_MODEL
            )
        ),
        vector_store=(
            primary.vector_store if primary else (
                tenant_profile.vector_store if tenant_profile else DEFAULT_VECTOR_STORE
            )
        ),
        entity_mappings=list(
            (tenant_profile.entity_mappings_json if tenant_profile else []) or []
        ) + list((primary.entity_mappings_json if primary else []) or []),
        selected_profile_id=str(primary.id) if primary else None,
    )
    return policy


def memory_debug_to_node_config(memory_debug: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(memory_debug, dict):
        return {}

    node_config: dict[str, Any] = {}
    if "enabled" in memory_debug:
        node_config["memoryEnabled"] = bool(memory_debug.get("enabled"))

    profile_id = str(memory_debug.get("profile_id", "") or "").strip()
    if profile_id:
        node_config["memoryProfileId"] = profile_id

    raw_scopes = memory_debug.get("scopes")
    if isinstance(raw_scopes, list):
        node_config["memoryScopes"] = [str(scope) for scope in raw_scopes if str(scope) in DEFAULT_SCOPES]

    if "include_entity_memory" in memory_debug:
        node_config["includeEntityMemory"] = bool(memory_debug.get("include_entity_memory"))

    if "history_order" in memory_debug:
        node_config["historyOrder"] = _parse_history_order(memory_debug.get("history_order"))

    return node_config


def _pack_recent_messages(
    messages: list[ConversationMessage],
    *,
    max_tokens: int,
) -> list[ConversationMessage]:
    picked: list[ConversationMessage] = []
    used = 0
    for message in reversed(messages):
        content = message.content or ""
        cost = count_prompt_tokens(content) + 4
        if picked and used + cost > max_tokens:
            break
        picked.append(message)
        used += cost
    return list(reversed(picked))


def _entity_refs_from_policy(
    policy: EffectiveMemoryPolicy,
    context: dict[str, Any],
) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for mapping in policy.entity_mappings:
        entity_type = str(mapping.get("entityType", "entity") or "entity").strip()
        entity_key_expr = str(mapping.get("entityKeyExpression", "") or "").strip()
        entity_key = _resolve_expr(entity_key_expr, context)
        if entity_key is None or str(entity_key).strip() == "":
            continue
        refs.append((entity_type, str(entity_key).strip()))
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        out.append(ref)
    return out


def promote_entity_facts(
    db: Session,
    *,
    tenant_id: str,
    workflow_def_id: str | None,
    session_ref_id: uuid.UUID | None,
    instance_id: str | None,
    node_id: str | None,
    context: dict[str, Any],
    policy: EffectiveMemoryPolicy,
) -> list[EntityFact]:
    """Promote structured facts with last-write-wins semantics and one active fact per key."""
    created: list[EntityFact] = []
    now = _utcnow()
    workflow_uuid = uuid.UUID(workflow_def_id) if workflow_def_id else None
    instance_uuid = uuid.UUID(instance_id) if instance_id else None

    for mapping in policy.entity_mappings:
        entity_type = str(mapping.get("entityType", "entity") or "entity").strip()
        entity_key_expr = str(mapping.get("entityKeyExpression", "") or "").strip()
        entity_key = _resolve_expr(entity_key_expr, context)
        if entity_key is None or str(entity_key).strip() == "":
            continue
        entity_key_str = str(entity_key).strip()

        for fact_cfg in mapping.get("facts", []) or []:
            fact_name = str(fact_cfg.get("name", "") or "").strip()
            value_expr = str(fact_cfg.get("valueExpression", "") or "").strip()
            if not fact_name or not value_expr:
                continue

            fact_value = _resolve_expr(value_expr, context)
            if fact_value is None or str(fact_value).strip() == "":
                continue
            fact_value_str = str(fact_value).strip()
            confidence = float(fact_cfg.get("confidence", 1.0) or 1.0)
            for attempt in range(2):
                try:
                    with db.begin_nested():
                        current = (
                            db.query(EntityFact)
                            .filter_by(
                                tenant_id=tenant_id,
                                entity_type=entity_type,
                                entity_key=entity_key_str,
                                fact_name=fact_name,
                                valid_to=None,
                            )
                            .with_for_update()
                            .order_by(EntityFact.created_at.desc())
                            .first()
                        )
                        if current and str(current.fact_value) == fact_value_str:
                            if confidence > float(current.confidence or 0.0):
                                current.confidence = confidence
                            break

                        fact = EntityFact(
                            tenant_id=tenant_id,
                            entity_type=entity_type,
                            entity_key=entity_key_str,
                            fact_name=fact_name,
                            fact_value=fact_value_str,
                            confidence=confidence,
                            valid_from=now,
                            session_ref_id=session_ref_id,
                            workflow_def_id=workflow_uuid,
                            source_instance_id=instance_uuid,
                            source_node_id=node_id,
                            metadata_json={
                                "mapping_name": mapping.get("name", ""),
                                "value_expression": value_expr,
                                "resolution_strategy": "last_write_wins",
                            },
                        )
                        db.add(fact)
                        db.flush()

                        if current:
                            current.valid_to = now
                            current.superseded_by = fact.id

                        created.append(fact)
                        break
                except IntegrityError:
                    if attempt == 0:
                        continue
                    logger.warning(
                        "Entity fact promotion raced for %s/%s/%s/%s; using winner from concurrent transaction",
                        tenant_id,
                        entity_type,
                        entity_key_str,
                        fact_name,
                    )

    return created


def promote_memory_records(
    db: Session,
    *,
    tenant_id: str,
    session: ConversationSession | None,
    episode: ConversationEpisode | None,
    workflow_def_id: str | None,
    instance_id: str | None,
    node_id: str | None,
    context: dict[str, Any],
    policy: EffectiveMemoryPolicy,
    user_message: str,
    assistant_response: str,
    conversation_idempotency_key: str,
) -> list[MemoryRecord]:
    text = "\n".join(
        part
        for part in [
            f"User: {user_message.strip()}" if user_message.strip() else "",
            f"Assistant: {assistant_response.strip()}" if assistant_response.strip() else "",
        ]
        if part
    ).strip()
    if not text or not policy.promote_interactions:
        return []

    workflow_uuid = uuid.UUID(workflow_def_id) if workflow_def_id else None
    instance_uuid = uuid.UUID(instance_id) if instance_id else None
    entity_refs = _entity_refs_from_policy(policy, context) if "entity" in policy.scopes else []
    scope_targets: list[tuple[str, str, str | None, str | None]] = []
    if "session" in policy.scopes and session is not None:
        scope_targets.append(("session", session.session_id, None, None))
    if "workflow" in policy.scopes and workflow_def_id:
        scope_targets.append(("workflow", workflow_def_id, None, None))
    if "tenant" in policy.scopes:
        scope_targets.append(("tenant", tenant_id, None, None))
    if "entity" in policy.scopes:
        for entity_type, entity_key in entity_refs:
            scope_targets.append(("entity", f"{entity_type}:{entity_key}", entity_type, entity_key))

    created: list[MemoryRecord] = []
    for scope, scope_key, entity_type, entity_key in scope_targets:
        dedupe_key = build_memory_record_dedupe_key(
            tenant_id=tenant_id,
            scope=scope,
            scope_key=scope_key,
            kind="interaction",
            conversation_idempotency_key=conversation_idempotency_key,
        )
        for attempt in range(2):
            try:
                with db.begin_nested():
                    existing = (
                        db.query(MemoryRecord)
                        .filter_by(tenant_id=tenant_id, dedupe_key=dedupe_key)
                        .first()
                    )
                    if existing:
                        break

                    record = MemoryRecord(
                        tenant_id=tenant_id,
                        scope=scope,
                        scope_key=scope_key,
                        kind="interaction",
                        content=text,
                        metadata_json={
                            "session_id": session.session_id if session else None,
                            "workflow_def_id": workflow_def_id,
                            "episode_id": str(episode.id) if episode else None,
                            "memory_class": "interaction",
                        },
                        session_ref_id=session.id if scope == "session" and session else None,
                        workflow_def_id=workflow_uuid,
                        entity_type=entity_type,
                        entity_key=entity_key,
                        source_instance_id=instance_uuid,
                        source_node_id=node_id,
                        dedupe_key=dedupe_key,
                        embedding_provider=policy.embedding_provider,
                        embedding_model=policy.embedding_model,
                        vector_store=policy.vector_store,
                    )
                    db.add(record)
                    db.flush()
                    created.append(record)
                    break
            except IntegrityError:
                if attempt == 0:
                    continue
                logger.warning(
                    "Memory record promotion raced for %s/%s/%s; using winner from concurrent transaction",
                    tenant_id,
                    scope,
                    scope_key,
                )

    if not created:
        return created

    try:
        embeddings = get_embeddings_batch_sync(
            [record.content for record in created],
            policy.embedding_provider,
            policy.embedding_model,
        )
        store = get_memory_vector_store(policy.vector_store, db=db)
        store.add_embeddings(
            tenant_id=tenant_id,
            provider=policy.embedding_provider,
            model=policy.embedding_model,
            records=[
                MemoryVectorData(record_id=record.id, embedding=embedding)
                for record, embedding in zip(created, embeddings)
            ],
        )
    except Exception as exc:
        logger.warning("Memory embedding promotion failed: %s", exc)

    return created


def resolve_episode_policy(
    db: Session,
    *,
    tenant_id: str,
    episode: ConversationEpisode | None,
    workflow_def_id: str | None = None,
    memory_profile_id: str | None = None,
) -> EffectiveMemoryPolicy:
    node_config: dict[str, Any] = {}
    selected_profile_id = memory_profile_id or (
        str(episode.memory_profile_id) if episode and episode.memory_profile_id else ""
    )
    if selected_profile_id:
        node_config["memoryProfileId"] = selected_profile_id
    effective_workflow_def_id = workflow_def_id or (
        str(episode.workflow_def_id) if episode and episode.workflow_def_id else None
    )
    return resolve_memory_policy(
        db,
        tenant_id=tenant_id,
        workflow_def_id=effective_workflow_def_id,
        node_config=node_config,
        context={},
    )


def _load_episode_messages(
    db: Session,
    *,
    session: ConversationSession,
    episode: ConversationEpisode,
    start_turn: int | None = None,
    end_turn: int | None = None,
) -> list[ConversationMessage]:
    lower = episode.start_turn if start_turn is None else max(episode.start_turn, start_turn)
    upper_bound = episode.end_turn if episode.end_turn is not None else session.message_count
    upper = upper_bound if end_turn is None else min(upper_bound, end_turn)
    if upper < lower:
        return []
    return (
        db.query(ConversationMessage)
        .filter(
            ConversationMessage.session_ref_id == session.id,
            ConversationMessage.turn_index >= lower,
            ConversationMessage.turn_index <= upper,
        )
        .order_by(ConversationMessage.turn_index)
        .all()
    )


def _derive_episode_title(
    *,
    summary_text: str,
    messages: list[ConversationMessage],
) -> str:
    for msg in messages:
        if msg.role == "user" and (msg.content or "").strip():
            return truncate_to_tokens(" ".join(msg.content.split()), 32)
    if summary_text.strip():
        return truncate_to_tokens(summary_text.strip(), 32)
    return "Conversation episode"


def _persist_episode_memory_records(
    db: Session,
    *,
    tenant_id: str,
    session: ConversationSession,
    episode: ConversationEpisode,
    workflow_def_id: str | None,
    instance_id: str | None,
    node_id: str | None,
    context: dict[str, Any],
    policy: EffectiveMemoryPolicy,
    content: str,
    title: str,
    archive_reason: str,
    archive_dedupe_key: str,
) -> list[MemoryRecord]:
    workflow_uuid = uuid.UUID(workflow_def_id) if workflow_def_id else None
    instance_uuid = uuid.UUID(instance_id) if instance_id else None
    entity_refs = _entity_refs_from_policy(policy, context) if "entity" in policy.scopes else []
    scope_targets: list[tuple[str, str, str | None, str | None]] = []
    if "session" in policy.scopes:
        scope_targets.append(("session", session.session_id, None, None))
    if "workflow" in policy.scopes and workflow_def_id:
        scope_targets.append(("workflow", workflow_def_id, None, None))
    if "tenant" in policy.scopes:
        scope_targets.append(("tenant", tenant_id, None, None))
    if "entity" in policy.scopes:
        for entity_type, entity_key in entity_refs:
            scope_targets.append(("entity", f"{entity_type}:{entity_key}", entity_type, entity_key))

    created: list[MemoryRecord] = []
    for scope, scope_key, entity_type, entity_key in scope_targets:
        dedupe_key = build_memory_record_dedupe_key(
            tenant_id=tenant_id,
            scope=scope,
            scope_key=scope_key,
            kind="episode",
            conversation_idempotency_key=archive_dedupe_key,
        )
        for attempt in range(2):
            try:
                with db.begin_nested():
                    existing = (
                        db.query(MemoryRecord)
                        .filter_by(tenant_id=tenant_id, dedupe_key=dedupe_key)
                        .first()
                    )
                    if existing:
                        created.append(existing)
                        break

                    record = MemoryRecord(
                        tenant_id=tenant_id,
                        scope=scope,
                        scope_key=scope_key,
                        kind="episode",
                        content=content,
                        metadata_json={
                            "session_id": session.session_id,
                            "workflow_def_id": workflow_def_id,
                            "episode_id": str(episode.id),
                            "title": title,
                            "archive_reason": archive_reason,
                            "memory_class": "episode",
                        },
                        session_ref_id=session.id if scope == "session" else None,
                        workflow_def_id=workflow_uuid,
                        entity_type=entity_type,
                        entity_key=entity_key,
                        source_instance_id=instance_uuid,
                        source_node_id=node_id,
                        dedupe_key=dedupe_key,
                        embedding_provider=policy.embedding_provider,
                        embedding_model=policy.embedding_model,
                        vector_store=policy.vector_store,
                    )
                    db.add(record)
                    db.flush()
                    created.append(record)
                    break
            except IntegrityError:
                if attempt == 0:
                    continue
                logger.warning(
                    "Episode archive raced for %s/%s/%s; using winner from concurrent transaction",
                    tenant_id,
                    scope,
                    scope_key,
                )

    if not created:
        return created

    new_records = [record for record in created if record.embedding is None]
    if not new_records:
        return created

    try:
        embeddings = get_embeddings_batch_sync(
            [record.content for record in new_records],
            policy.embedding_provider,
            policy.embedding_model,
        )
        store = get_memory_vector_store(policy.vector_store, db=db)
        store.add_embeddings(
            tenant_id=tenant_id,
            provider=policy.embedding_provider,
            model=policy.embedding_model,
            records=[
                MemoryVectorData(record_id=record.id, embedding=embedding)
                for record, embedding in zip(new_records, embeddings)
            ],
        )
    except Exception as exc:
        logger.warning("Episode embedding promotion failed: %s", exc)

    return created


def archive_active_episode(
    db: Session,
    *,
    tenant_id: str,
    session: ConversationSession,
    workflow_def_id: str | None,
    instance_id: str | None,
    node_id: str | None,
    context: dict[str, Any],
    reason: str,
    provided_summary: str = "",
    provided_title: str = "",
    memory_profile_id: str | None = None,
) -> tuple[ConversationEpisode | None, list[MemoryRecord]]:
    episode = get_active_episode(db, session=session, lock=True)
    if episode is None or episode.status != "active":
        _sync_session_working_summary(session, None, clear=True)
        db.flush()
        return None, []

    policy = resolve_episode_policy(
        db,
        tenant_id=tenant_id,
        episode=episode,
        workflow_def_id=workflow_def_id or (str(episode.workflow_def_id) if episode.workflow_def_id else None),
        memory_profile_id=memory_profile_id,
    )
    if memory_profile_id:
        episode.memory_profile_id = uuid.UUID(memory_profile_id)
    if reason == "resolved" and not policy.auto_archive_on_resolved:
        return episode, []

    end_turn = session.message_count
    all_messages = _load_episode_messages(db, session=session, episode=episode, end_turn=end_turn)
    turn_count = len(all_messages)

    existing_summary = episode.checkpoint_summary_text or ""
    unsummarized = [
        msg for msg in all_messages
        if msg.turn_index > episode.summary_through_turn
    ]
    final_summary = provided_summary.strip() or _llm_checkpoint_summary(
        existing_summary=existing_summary,
        messages=unsummarized or all_messages,
        max_tokens=policy.summary_max_tokens,
        provider=policy.episode_archive_provider,
        model=policy.episode_archive_model,
        mode="archive",
        tenant_id=tenant_id,
    )
    title = provided_title.strip() or episode.title or _derive_episode_title(
        summary_text=final_summary,
        messages=all_messages,
    )
    archive_key = build_episode_archive_dedupe_key(
        session_id=session.session_id,
        episode_id=str(episode.id),
        end_turn=end_turn,
        reason=reason,
    )

    created_records: list[MemoryRecord] = []
    if turn_count >= policy.episode_min_turns and final_summary.strip():
        created_records = _persist_episode_memory_records(
            db,
            tenant_id=tenant_id,
            session=session,
            episode=episode,
            workflow_def_id=workflow_def_id or (str(episode.workflow_def_id) if episode.workflow_def_id else None),
            instance_id=instance_id,
            node_id=node_id,
            context=context,
            policy=policy,
            content=final_summary.strip(),
            title=title,
            archive_reason=reason,
            archive_dedupe_key=archive_key,
        )

    now = _utcnow()
    episode.status = "archived"
    episode.end_turn = end_turn
    episode.title = title
    episode.archive_reason = reason
    episode.archive_metadata_json = {
        "summary_text": final_summary.strip(),
        "memory_record_ids": [str(record.id) for record in created_records],
        "memory_profile_id": str(episode.memory_profile_id) if episode.memory_profile_id else None,
        "turn_count": turn_count,
    }
    episode.archived_memory_record_id = created_records[0].id if created_records else None
    episode.archived_at = now
    episode.updated_at = now
    episode.summary_updated_at = now
    episode.summary_through_turn = end_turn
    episode.checkpoint_summary_text = final_summary.strip()
    session.active_episode_id = None
    _sync_session_working_summary(session, None, clear=True)
    db.flush()
    return episode, created_records


def _active_entity_facts(
    db: Session,
    *,
    tenant_id: str,
    entity_refs: list[tuple[str, str]],
) -> list[EntityFact]:
    if not entity_refs:
        return []
    clauses = [
        and_(EntityFact.entity_type == entity_type, EntityFact.entity_key == entity_key)
        for entity_type, entity_key in entity_refs
    ]
    return (
        db.query(EntityFact)
        .filter(
            EntityFact.tenant_id == tenant_id,
            EntityFact.valid_to.is_(None),
            or_(*clauses),
        )
        .order_by(EntityFact.entity_type, EntityFact.entity_key, EntityFact.fact_name)
        .all()
    )


def _format_entity_facts(facts: list[EntityFact]) -> str:
    if not facts:
        return ""
    groups: dict[tuple[str, str], list[str]] = {}
    for fact in facts:
        groups.setdefault((fact.entity_type, fact.entity_key), []).append(
            f"- {fact.fact_name}: {fact.fact_value} (confidence={fact.confidence:.2f})"
        )
    blocks = []
    for (entity_type, entity_key), lines in groups.items():
        blocks.append(f"{entity_type}:{entity_key}\n" + "\n".join(lines))
    return "\n\n".join(blocks)


def retrieve_memory_records(
    db: Session,
    *,
    tenant_id: str,
    workflow_def_id: str | None,
    session: ConversationSession | None,
    active_episode: ConversationEpisode | None,
    query_text: str,
    policy: EffectiveMemoryPolicy,
    context: dict[str, Any],
) -> list[tuple[MemoryRecord, float]]:
    if not query_text.strip() or policy.max_semantic_hits <= 0:
        return []

    entity_refs = _entity_refs_from_policy(policy, context) if "entity" in policy.scopes else []
    clauses = []
    if "session" in policy.scopes and session is not None:
        clauses.append(and_(MemoryRecord.scope == "session", MemoryRecord.scope_key == session.session_id))
    if "workflow" in policy.scopes and workflow_def_id:
        clauses.append(and_(MemoryRecord.scope == "workflow", MemoryRecord.scope_key == workflow_def_id))
    if "tenant" in policy.scopes:
        clauses.append(and_(MemoryRecord.scope == "tenant", MemoryRecord.scope_key == tenant_id))
    if "entity" in policy.scopes and entity_refs:
        clauses.extend(
            and_(
                MemoryRecord.scope == "entity",
                MemoryRecord.entity_type == entity_type,
                MemoryRecord.entity_key == entity_key,
            )
            for entity_type, entity_key in entity_refs
        )
    if not clauses:
        return []

    candidates = (
        db.query(MemoryRecord)
        .filter(MemoryRecord.tenant_id == tenant_id, or_(*clauses))
        .all()
    )
    if not candidates:
        return []
    if active_episode is not None:
        active_episode_id = str(active_episode.id)
        candidates = [
            record
            for record in candidates
            if not (
                record.kind == "interaction"
                and str((record.metadata_json or {}).get("episode_id") or "") == active_episode_id
            )
        ]
        if not candidates:
            return []

    by_backend: dict[tuple[str, str, str], list[MemoryRecord]] = {}
    for record in candidates:
        by_backend.setdefault(
            (record.vector_store, record.embedding_provider, record.embedding_model),
            [],
        ).append(record)

    scored: list[tuple[MemoryRecord, float]] = []
    for (backend, provider, model), records in by_backend.items():
        try:
            query_embedding = get_embedding_sync(query_text, provider, model)
            store = get_memory_vector_store(backend, db=db)
            hits = store.search(
                tenant_id=tenant_id,
                provider=provider,
                model=model,
                record_ids=[record.id for record in records],
                query_embedding=query_embedding,
                top_k=policy.max_semantic_hits,
            )
        except Exception as exc:
            logger.warning("Memory retrieval failed for %s/%s/%s: %s", backend, provider, model, exc)
            continue

        index = {record.id: record for record in records}
        for hit in hits:
            if hit.score < policy.semantic_score_threshold:
                continue
            record = index.get(hit.record_id)
            if record is not None:
                adjusted_score = float(hit.score)
                if record.kind == "episode":
                    adjusted_score += 0.15
                if session and record.scope == "session" and record.scope_key == session.session_id:
                    adjusted_score += 0.05
                scored.append((record, adjusted_score))

    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[: policy.max_semantic_hits]


def build_history_block(
    *,
    session: ConversationSession | None,
    active_episode: ConversationEpisode | None = None,
    all_messages: list[ConversationMessage],
    policy: EffectiveMemoryPolicy,
) -> tuple[str, list[ConversationMessage]]:
    summary_text = ""
    cutoff = 0
    lower_bound = 1
    if active_episode is not None:
        summary_text = (active_episode.checkpoint_summary_text or "").strip()
        cutoff = active_episode.summary_through_turn
        lower_bound = active_episode.start_turn
    elif session is not None:
        summary_text = (session.summary_text or "").strip()
        cutoff = session.summary_through_turn
    recent_source = [
        msg for msg in all_messages
        if msg.turn_index >= lower_bound and msg.turn_index > cutoff
    ]
    recent = _pack_recent_messages(recent_source, max_tokens=policy.recent_token_budget)
    summary_block = f"Earlier conversation summary:\n{summary_text}" if summary_text else ""
    return summary_block, recent


def _empty_memory_debug(
    policy: "EffectiveMemoryPolicy",
    history_node_id: str | None,
    session_id: str = "",
) -> dict[str, Any]:
    """Return a fully-keyed memory debug dict with zeroed-out history fields."""
    return {
        "enabled": bool(policy.enabled),
        "profile_id": policy.selected_profile_id,
        "history_node_id": history_node_id,
        "session_id": session_id,
        "active_episode_id": None,
        "summary_used": False,
        "summary_through_turn": 0,
        "recent_turn_count": 0,
        "recent_turn_ids": [],
        "recent_token_budget": policy.recent_token_budget,
        "scopes": policy.scopes,
        "include_entity_memory": policy.include_entity_memory,
        "history_order": policy.history_order,
        "summary_trigger_messages": policy.summary_trigger_messages,
        "summary_recent_turns": policy.summary_recent_turns,
        "summary_max_tokens": policy.summary_max_tokens,
        "summary_provider": policy.summary_provider,
        "summary_model": policy.summary_model,
        "embedding_provider": policy.embedding_provider,
        "embedding_model": policy.embedding_model,
        "vector_store": policy.vector_store,
    }


def assemble_history_text(
    db: Session,
    *,
    tenant_id: str,
    workflow_def_id: str | None,
    context: dict[str, Any],
    node_config: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Build a token-budgeted conversational history text block for classifier-style prompts."""
    policy = resolve_memory_policy(
        db,
        tenant_id=tenant_id,
        workflow_def_id=workflow_def_id,
        node_config=node_config,
        context=context,
    )
    history_node_id, history_output = _find_history_output(context, policy.history_node_id or "")
    if not history_output:
        return "(no prior messages)", _empty_memory_debug(policy, history_node_id)

    session_id = str(history_output.get("session_id", "") or "")
    if not session_id:
        return "(no prior messages)", _empty_memory_debug(policy, history_node_id)

    session = get_or_create_session(db, tenant_id=tenant_id, session_id=session_id)
    active_episode = get_active_episode(db, session=session)
    _sync_session_working_summary(session, active_episode)
    all_messages = (
        db.query(ConversationMessage)
        .filter_by(session_ref_id=session.id)
        .order_by(ConversationMessage.turn_index)
        .all()
    )
    summary_block, recent_messages = build_history_block(
        session=session,
        active_episode=active_episode,
        all_messages=all_messages,
        policy=policy,
    )

    recent_block = ""
    if recent_messages:
        recent_block = "Recent turns:\n" + "\n".join(
            f"{msg.role.upper()}: {msg.content}" for msg in recent_messages
        )
    history_parts: list[str] = (
        [recent_block, summary_block]
        if policy.history_order == "recent_first"
        else [summary_block, recent_block]
    )
    history_parts = [part for part in history_parts if part]

    return "\n\n".join(history_parts) if history_parts else "(no prior messages)", {
        "enabled": bool(policy.enabled),
        "profile_id": policy.selected_profile_id,
        "history_node_id": history_node_id,
        "session_id": session.session_id,
        "active_episode_id": str(active_episode.id) if active_episode else None,
        "summary_used": bool(summary_block),
        "summary_through_turn": (
            active_episode.summary_through_turn if active_episode else session.summary_through_turn
        ),
        "recent_turn_count": len(recent_messages),
        "recent_turn_ids": [str(msg.id) for msg in recent_messages],
        "recent_token_budget": policy.recent_token_budget,
        "scopes": policy.scopes,
        "include_entity_memory": policy.include_entity_memory,
        "history_order": policy.history_order,
        "summary_trigger_messages": policy.summary_trigger_messages,
        "summary_recent_turns": policy.summary_recent_turns,
        "summary_max_tokens": policy.summary_max_tokens,
        "summary_provider": policy.summary_provider,
        "summary_model": policy.summary_model,
        "embedding_provider": policy.embedding_provider,
        "embedding_model": policy.embedding_model,
        "vector_store": policy.vector_store,
    }


def assemble_agent_messages(
    db: Session,
    *,
    tenant_id: str,
    workflow_def_id: str | None,
    context: dict[str, Any],
    node_config: dict[str, Any],
    rendered_system_prompt: str,
    distill_text: str = "",
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    policy = resolve_memory_policy(
        db,
        tenant_id=tenant_id,
        workflow_def_id=workflow_def_id,
        node_config=node_config,
        context=context,
    )
    if not policy.enabled:
        messages = []
        if rendered_system_prompt.strip():
            messages.append({"role": "system", "content": rendered_system_prompt})
        # CTX-MGMT.J v2 — distill goes into the per-turn user message,
        # NOT the system prompt. Keeps the system prompt stable across
        # turns so the provider's prefix-cache hits.
        user_parts: list[str] = []
        if distill_text:
            user_parts.append(distill_text)
        structured = build_structured_context_block(context)
        if structured:
            user_parts.append(structured)
        messages.append({"role": "user", "content": "\n\n".join(user_parts)})
        return messages, {"enabled": False}

    history_node_id, history_output = _find_history_output(context, policy.history_node_id or "")
    session = None
    active_episode = None
    all_messages: list[ConversationMessage] = []
    session_id = ""
    if history_output:
        session_id = str(history_output.get("session_id", "") or "")
    if session_id:
        session = get_or_create_session(db, tenant_id=tenant_id, session_id=session_id)
        active_episode = get_active_episode(db, session=session)
        _sync_session_working_summary(session, active_episode)
        all_messages = (
            db.query(ConversationMessage)
            .filter_by(session_ref_id=session.id)
            .order_by(ConversationMessage.turn_index)
            .all()
        )

    summary_block, recent_messages = build_history_block(
        session=session,
        active_episode=active_episode,
        all_messages=all_messages,
        policy=policy,
    )

    entity_refs = _entity_refs_from_policy(policy, context) if policy.include_entity_memory else []
    facts = _active_entity_facts(db, tenant_id=tenant_id, entity_refs=entity_refs) if entity_refs else []
    semantic_hits = retrieve_memory_records(
        db,
        tenant_id=tenant_id,
        workflow_def_id=workflow_def_id,
        session=session,
        active_episode=active_episode,
        query_text=_latest_user_message(context),
        policy=policy,
        context=context,
    )

    messages: list[dict[str, str]] = []
    system_parts = [part.strip() for part in policy.instructions if part and part.strip()]
    if rendered_system_prompt.strip():
        system_parts.append(rendered_system_prompt.strip())
    if system_parts:
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})
    summary_message = [{"role": "assistant", "content": summary_block}] if summary_block else []
    recent_turn_messages = [
        {"role": message.role, "content": message.content}
        for message in recent_messages
        if message.role in {"user", "assistant"}
    ]
    ordered_history_messages = (
        recent_turn_messages + summary_message
        if policy.history_order == "recent_first"
        else summary_message + recent_turn_messages
    )
    messages.extend(ordered_history_messages)

    final_sections: list[str] = []
    facts_block = _format_entity_facts(facts)
    if facts_block:
        final_sections.append(f"Entity memory:\n{facts_block}")
    if semantic_hits:
        final_sections.append(
            "Relevant prior memories:\n" + "\n\n".join(
                f"[{record.scope}:{record.kind} score={score:.3f}]\n{record.content}"
                for record, score in semantic_hits
            )
        )
    current_user = _latest_user_message(context)
    if current_user:
        final_sections.append(f"Latest user message:\n{current_user}")
    structured_block = build_structured_context_block(
        context,
        exclude_node_ids={history_node_id} if history_node_id else set(),
    )
    if structured_block:
        final_sections.append(f"Workflow context:\n{structured_block}")
    # CTX-MGMT.J v2 — distill ride-along on the per-turn user message.
    # Pre-v2 the rendered distill was appended to the system prompt,
    # which busted the provider's prefix cache every time the
    # underlying source changed (each new worknote, each new finding).
    # Putting distill here keeps the system prompt stable so the
    # cached prefix can grow turn over turn while the recent-evidence
    # block lives where it always changes anyway.
    if distill_text:
        final_sections.append(distill_text)
    messages.append({"role": "user", "content": "\n\n".join(section for section in final_sections if section)})

    memory_debug = {
        "enabled": True,
        "profile_id": policy.selected_profile_id,
        "history_node_id": history_node_id,
        "session_id": session.session_id if session else "",
        "active_episode_id": str(active_episode.id) if active_episode else None,
        "summary_used": bool(summary_block),
        "summary_through_turn": (
            active_episode.summary_through_turn if active_episode else (session.summary_through_turn if session else 0)
        ),
        "recent_turn_count": len(recent_messages),
        "recent_turn_ids": [str(msg.id) for msg in recent_messages],
        "entity_fact_ids": [str(fact.id) for fact in facts],
        "memory_record_ids": [str(record.id) for record, _ in semantic_hits],
        "recent_token_budget": policy.recent_token_budget,
        "max_semantic_hits": policy.max_semantic_hits,
        "scopes": policy.scopes,
        "include_entity_memory": policy.include_entity_memory,
        "history_order": policy.history_order,
        "summary_trigger_messages": policy.summary_trigger_messages,
        "summary_recent_turns": policy.summary_recent_turns,
        "summary_max_tokens": policy.summary_max_tokens,
        "summary_provider": policy.summary_provider,
        "summary_model": policy.summary_model,
        "embedding_provider": policy.embedding_provider,
        "embedding_model": policy.embedding_model,
        "vector_store": policy.vector_store,
    }
    return messages, memory_debug
