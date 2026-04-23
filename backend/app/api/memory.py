"""Advanced memory API: profile CRUD and operator inspection endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_tenant_db
from app.models.memory import ConversationMessage, EntityFact, MemoryProfile, MemoryRecord
from app.models.workflow import ExecutionLog, WorkflowInstance
from app.security.tenant import get_tenant_id

router = APIRouter(tags=["memory"])

_DEFAULT_SCOPES = ["session", "workflow", "tenant", "entity"]


class MemoryProfileBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    description: str | None = None
    workflow_def_id: uuid.UUID | None = None
    is_default: bool = False
    instructions_text: str | None = None
    enabled_scopes: list[str] = Field(default_factory=lambda: list(_DEFAULT_SCOPES))
    max_recent_tokens: int = Field(default=1200, ge=128, le=16000)
    max_semantic_hits: int = Field(default=4, ge=0, le=20)
    include_entity_memory: bool = True
    summary_trigger_messages: int = Field(default=12, ge=1, le=200)
    summary_recent_turns: int = Field(default=6, ge=0, le=50)
    summary_max_tokens: int = Field(default=400, ge=64, le=4000)
    # MODEL-01.c: registry-tracked defaults via memory_service so a
    # tier bump flows through without editing Pydantic fields.
    summary_provider: str = "google"
    summary_model: str = Field(
        default_factory=lambda: __import__(
            "app.engine.memory_service", fromlist=["DEFAULT_SUMMARY_MODEL"]
        ).DEFAULT_SUMMARY_MODEL
    )
    episode_archive_provider: str = "google"
    episode_archive_model: str = Field(
        default_factory=lambda: __import__(
            "app.engine.memory_service", fromlist=["DEFAULT_EPISODE_ARCHIVE_MODEL"]
        ).DEFAULT_EPISODE_ARCHIVE_MODEL
    )
    episode_inactivity_minutes: int = Field(default=10080, ge=1, le=525600)
    episode_min_turns: int = Field(default=2, ge=1, le=500)
    auto_archive_on_resolved: bool = True
    promote_interactions: bool = True
    history_order: str = Field(default="summary_first", pattern="^(summary_first|recent_first)$")
    semantic_score_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    vector_store: str = "pgvector"
    entity_mappings_json: list[dict[str, Any]] = Field(default_factory=list)


class MemoryProfileCreate(MemoryProfileBase):
    pass


class MemoryProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = None
    workflow_def_id: uuid.UUID | None = None
    is_default: bool | None = None
    instructions_text: str | None = None
    enabled_scopes: list[str] | None = None
    max_recent_tokens: int | None = Field(default=None, ge=128, le=16000)
    max_semantic_hits: int | None = Field(default=None, ge=0, le=20)
    include_entity_memory: bool | None = None
    summary_trigger_messages: int | None = Field(default=None, ge=1, le=200)
    summary_recent_turns: int | None = Field(default=None, ge=0, le=50)
    summary_max_tokens: int | None = Field(default=None, ge=64, le=4000)
    summary_provider: str | None = None
    summary_model: str | None = None
    episode_archive_provider: str | None = None
    episode_archive_model: str | None = None
    episode_inactivity_minutes: int | None = Field(default=None, ge=1, le=525600)
    episode_min_turns: int | None = Field(default=None, ge=1, le=500)
    auto_archive_on_resolved: bool | None = None
    promote_interactions: bool | None = None
    history_order: str | None = Field(default=None, pattern="^(summary_first|recent_first)$")
    semantic_score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    embedding_provider: str | None = None
    embedding_model: str | None = None
    vector_store: str | None = None
    entity_mappings_json: list[dict[str, Any]] | None = None


class MemoryProfileOut(MemoryProfileBase):
    id: str
    tenant_id: str
    created_at: datetime
    updated_at: datetime


class MemoryRecordOut(BaseModel):
    id: str
    tenant_id: str
    scope: str
    scope_key: str
    kind: str
    content: str
    metadata_json: dict[str, Any]
    session_ref_id: str | None
    workflow_def_id: str | None
    entity_type: str | None
    entity_key: str | None
    source_instance_id: str | None
    source_node_id: str | None
    embedding_provider: str
    embedding_model: str
    vector_store: str
    created_at: datetime


class EntityFactOut(BaseModel):
    id: str
    tenant_id: str
    entity_type: str
    entity_key: str
    fact_name: str
    fact_value: str
    confidence: float
    valid_from: datetime
    valid_to: datetime | None
    superseded_by: str | None
    session_ref_id: str | None
    workflow_def_id: str | None
    source_instance_id: str | None
    source_node_id: str | None
    metadata_json: dict[str, Any]
    created_at: datetime


class ResolvedMemoryLogOut(BaseModel):
    node_id: str
    node_type: str
    completed_at: datetime | None
    memory_debug: dict[str, Any]
    recent_turns: list[dict[str, Any]]
    entity_facts: list[EntityFactOut]
    memory_records: list[MemoryRecordOut]


def _profile_to_out(profile: MemoryProfile) -> MemoryProfileOut:
    return MemoryProfileOut(
        id=str(profile.id),
        tenant_id=profile.tenant_id,
        name=profile.name,
        description=profile.description,
        workflow_def_id=profile.workflow_def_id,
        is_default=profile.is_default,
        instructions_text=profile.instructions_text,
        enabled_scopes=list(profile.enabled_scopes or []),
        max_recent_tokens=profile.max_recent_tokens,
        max_semantic_hits=profile.max_semantic_hits,
        include_entity_memory=profile.include_entity_memory,
        summary_trigger_messages=profile.summary_trigger_messages,
        summary_recent_turns=profile.summary_recent_turns,
        summary_max_tokens=profile.summary_max_tokens,
        summary_provider=profile.summary_provider,
        summary_model=profile.summary_model,
        episode_archive_provider=profile.episode_archive_provider,
        episode_archive_model=profile.episode_archive_model,
        episode_inactivity_minutes=profile.episode_inactivity_minutes,
        episode_min_turns=profile.episode_min_turns,
        auto_archive_on_resolved=profile.auto_archive_on_resolved,
        promote_interactions=profile.promote_interactions,
        history_order=profile.history_order,
        semantic_score_threshold=profile.semantic_score_threshold,
        embedding_provider=profile.embedding_provider,
        embedding_model=profile.embedding_model,
        vector_store=profile.vector_store,
        entity_mappings_json=list(profile.entity_mappings_json or []),
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _memory_record_to_out(record: MemoryRecord) -> MemoryRecordOut:
    return MemoryRecordOut(
        id=str(record.id),
        tenant_id=record.tenant_id,
        scope=record.scope,
        scope_key=record.scope_key,
        kind=record.kind,
        content=record.content,
        metadata_json=dict(record.metadata_json or {}),
        session_ref_id=str(record.session_ref_id) if record.session_ref_id else None,
        workflow_def_id=str(record.workflow_def_id) if record.workflow_def_id else None,
        entity_type=record.entity_type,
        entity_key=record.entity_key,
        source_instance_id=str(record.source_instance_id) if record.source_instance_id else None,
        source_node_id=record.source_node_id,
        embedding_provider=record.embedding_provider,
        embedding_model=record.embedding_model,
        vector_store=record.vector_store,
        created_at=record.created_at,
    )


def _entity_fact_to_out(fact: EntityFact) -> EntityFactOut:
    return EntityFactOut(
        id=str(fact.id),
        tenant_id=fact.tenant_id,
        entity_type=fact.entity_type,
        entity_key=fact.entity_key,
        fact_name=fact.fact_name,
        fact_value=fact.fact_value,
        confidence=fact.confidence,
        valid_from=fact.valid_from,
        valid_to=fact.valid_to,
        superseded_by=str(fact.superseded_by) if fact.superseded_by else None,
        session_ref_id=str(fact.session_ref_id) if fact.session_ref_id else None,
        workflow_def_id=str(fact.workflow_def_id) if fact.workflow_def_id else None,
        source_instance_id=str(fact.source_instance_id) if fact.source_instance_id else None,
        source_node_id=fact.source_node_id,
        metadata_json=dict(fact.metadata_json or {}),
        created_at=fact.created_at,
    )


def _apply_profile_defaults(
    db: Session,
    *,
    tenant_id: str,
    workflow_def_id: uuid.UUID | None,
    exclude_id: uuid.UUID | None = None,
) -> None:
    query = db.query(MemoryProfile).filter_by(
        tenant_id=tenant_id,
        workflow_def_id=workflow_def_id,
        is_default=True,
    )
    if exclude_id is not None:
        query = query.filter(MemoryProfile.id != exclude_id)
    for row in query.all():
        row.is_default = False


@router.get("/api/v1/memory-profiles", response_model=list[MemoryProfileOut])
def list_memory_profiles(
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    profiles = (
        db.query(MemoryProfile)
        .filter_by(tenant_id=tenant_id)
        .order_by(MemoryProfile.workflow_def_id.is_(None).desc(), MemoryProfile.updated_at.desc())
        .all()
    )
    return [_profile_to_out(profile) for profile in profiles]


@router.post("/api/v1/memory-profiles", response_model=MemoryProfileOut, status_code=201)
def create_memory_profile(
    body: MemoryProfileCreate,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    if body.is_default:
        _apply_profile_defaults(
            db,
            tenant_id=tenant_id,
            workflow_def_id=body.workflow_def_id,
        )
    profile = MemoryProfile(
        tenant_id=tenant_id,
        **body.model_dump(),
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return _profile_to_out(profile)


@router.get("/api/v1/memory-profiles/{profile_id}", response_model=MemoryProfileOut)
def get_memory_profile(
    profile_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    profile = db.query(MemoryProfile).filter_by(id=profile_id, tenant_id=tenant_id).first()
    if not profile:
        raise HTTPException(404, "Memory profile not found")
    return _profile_to_out(profile)


@router.put("/api/v1/memory-profiles/{profile_id}", response_model=MemoryProfileOut)
def update_memory_profile(
    profile_id: uuid.UUID,
    body: MemoryProfileUpdate,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    profile = db.query(MemoryProfile).filter_by(id=profile_id, tenant_id=tenant_id).first()
    if not profile:
        raise HTTPException(404, "Memory profile not found")

    updates = body.model_dump(exclude_unset=True)
    next_workflow_def_id = updates.get("workflow_def_id", profile.workflow_def_id)
    next_is_default = updates.get("is_default", profile.is_default)
    if next_is_default:
        _apply_profile_defaults(
            db,
            tenant_id=tenant_id,
            workflow_def_id=next_workflow_def_id,
            exclude_id=profile.id,
        )
    for key, value in updates.items():
        setattr(profile, key, value)
    db.commit()
    db.refresh(profile)
    return _profile_to_out(profile)


@router.delete("/api/v1/memory-profiles/{profile_id}", status_code=204)
def delete_memory_profile(
    profile_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    profile = db.query(MemoryProfile).filter_by(id=profile_id, tenant_id=tenant_id).first()
    if not profile:
        raise HTTPException(404, "Memory profile not found")
    db.delete(profile)
    db.commit()


@router.get("/api/v1/memory/records", response_model=list[MemoryRecordOut])
def list_memory_records(
    scope: str | None = None,
    scope_key: str | None = None,
    kind: str | None = None,
    entity_type: str | None = None,
    entity_key: str | None = None,
    workflow_def_id: uuid.UUID | None = None,
    source_instance_id: uuid.UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    query = db.query(MemoryRecord).filter(MemoryRecord.tenant_id == tenant_id)
    if scope:
        query = query.filter(MemoryRecord.scope == scope)
    if scope_key:
        query = query.filter(MemoryRecord.scope_key == scope_key)
    if kind:
        query = query.filter(MemoryRecord.kind == kind)
    if entity_type:
        query = query.filter(MemoryRecord.entity_type == entity_type)
    if entity_key:
        query = query.filter(MemoryRecord.entity_key == entity_key)
    if workflow_def_id:
        query = query.filter(MemoryRecord.workflow_def_id == workflow_def_id)
    if source_instance_id:
        query = query.filter(MemoryRecord.source_instance_id == source_instance_id)
    records = query.order_by(MemoryRecord.created_at.desc()).limit(limit).all()
    return [_memory_record_to_out(record) for record in records]


@router.get("/api/v1/memory/entity-facts", response_model=list[EntityFactOut])
def list_entity_facts(
    entity_type: str | None = None,
    entity_key: str | None = None,
    include_inactive: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    query = db.query(EntityFact).filter(EntityFact.tenant_id == tenant_id)
    if entity_type:
        query = query.filter(EntityFact.entity_type == entity_type)
    if entity_key:
        query = query.filter(EntityFact.entity_key == entity_key)
    if not include_inactive:
        query = query.filter(EntityFact.valid_to.is_(None))
    facts = query.order_by(EntityFact.created_at.desc()).limit(limit).all()
    return [_entity_fact_to_out(fact) for fact in facts]


@router.get("/api/v1/memory/instances/{instance_id}/resolved", response_model=list[ResolvedMemoryLogOut])
def resolve_instance_memory(
    instance_id: uuid.UUID,
    tenant_id: str = Depends(get_tenant_id),
    db: Session = Depends(get_tenant_db),
):
    instance = db.query(WorkflowInstance).filter_by(id=instance_id, tenant_id=tenant_id).first()
    if not instance:
        raise HTTPException(404, "Workflow instance not found")

    logs = (
        db.query(ExecutionLog)
        .filter_by(instance_id=instance.id)
        .order_by(ExecutionLog.completed_at.asc(), ExecutionLog.started_at.asc())
        .all()
    )

    resolved: list[ResolvedMemoryLogOut] = []
    for log in logs:
        output = log.output_json or {}
        memory_debug = {}
        if isinstance(output, dict):
            memory_debug = output.get("memory_debug") or {}
        if not memory_debug:
            continue

        recent_turn_ids = [
            uuid.UUID(item)
            for item in memory_debug.get("recent_turn_ids", [])
            if isinstance(item, str)
        ]
        memory_record_ids = [
            uuid.UUID(item)
            for item in memory_debug.get("memory_record_ids", [])
            if isinstance(item, str)
        ]
        entity_fact_ids = [
            uuid.UUID(item)
            for item in memory_debug.get("entity_fact_ids", [])
            if isinstance(item, str)
        ]

        recent_turns = (
            db.query(ConversationMessage)
            .filter(
                ConversationMessage.tenant_id == tenant_id,
                ConversationMessage.id.in_(recent_turn_ids),
            )
            .order_by(ConversationMessage.turn_index)
            .all()
            if recent_turn_ids else []
        )
        records = (
            db.query(MemoryRecord)
            .filter(
                MemoryRecord.tenant_id == tenant_id,
                MemoryRecord.id.in_(memory_record_ids),
            )
            .order_by(MemoryRecord.created_at.desc())
            .all()
            if memory_record_ids else []
        )
        facts = (
            db.query(EntityFact)
            .filter(
                EntityFact.tenant_id == tenant_id,
                EntityFact.id.in_(entity_fact_ids),
            )
            .order_by(EntityFact.created_at.desc())
            .all()
            if entity_fact_ids else []
        )

        resolved.append(
            ResolvedMemoryLogOut(
                node_id=log.node_id,
                node_type=log.node_type,
                completed_at=log.completed_at,
                memory_debug=memory_debug,
                recent_turns=[
                    {
                        "id": str(turn.id),
                        "role": turn.role,
                        "content": turn.content,
                        "message_at": turn.message_at.isoformat() if turn.message_at else None,
                        "turn_index": turn.turn_index,
                    }
                    for turn in recent_turns
                ],
                entity_facts=[_entity_fact_to_out(fact) for fact in facts],
                memory_records=[_memory_record_to_out(record) for record in records],
            )
        )

    return resolved
