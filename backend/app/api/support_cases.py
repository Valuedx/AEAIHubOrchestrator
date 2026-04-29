"""AE Ops Support — case CRUD endpoints.

Backs the ``support_cases`` table (migration ``48f869152a93``) and lets the
AE Ops Support workflow upsert / read / mutate cases via HTTP Request nodes
(rather than via direct SQLAlchemy from a code-execution node, which the
sandbox blocks).

Endpoints (all tenant-scoped via ``get_tenant_db``):

  POST   /api/v1/support-cases              upsert by (tenant, session_id)
  GET    /api/v1/support-cases              list (paged) with optional state filter
  GET    /api/v1/support-cases/{id}         fetch by id
  GET    /api/v1/support-cases/by-session/{session_id}
                                            fetch the most-recent case for a session
  PATCH  /api/v1/support-cases/{id}         partial update — state / title / category /
                                            priority / plan_json / resolved_context
  POST   /api/v1/support-cases/{id}/worknote  append a worknote entry (immutable history)
  POST   /api/v1/support-cases/{id}/evidence  append an evidence entry
  POST   /api/v1/support-cases/{id}/handoff   convenience: state=HANDED_OFF + assigned_team
  POST   /api/v1/support-cases/{id}/close     state=CLOSED, sets closed_at
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.database import get_tenant_db
from app.models.support_case import SUPPORT_CASE_STATES, SupportCase
from app.security.tenant import get_tenant_id


router = APIRouter(prefix="/api/v1/support-cases", tags=["support-cases"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class SupportCaseOut(BaseModel):
    id: uuid.UUID
    tenant_id: str
    session_id: str
    requester_id: str | None
    state: str
    title: str | None
    category: str | None
    priority: str | None
    assigned_team: str | None
    plan_json: dict[str, Any] | None
    evidence: list[Any]
    worknotes: list[Any]
    resolved_context: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None

    class Config:
        from_attributes = True


class SupportCaseUpsert(BaseModel):
    """Upsert by ``(tenant_id, session_id)``: if a row exists, return it
    unchanged; otherwise create a new ``state='NEW'`` row.

    Use ``PATCH`` to mutate state — upsert is idempotent on purpose so the
    workflow can call it on every turn without creating duplicates.
    """

    session_id: str
    requester_id: str | None = None
    title: str | None = None
    category: str | None = None
    priority: str | None = Field(None, pattern=r"^(low|medium|high|urgent)$")


class SupportCasePatch(BaseModel):
    state: str | None = None
    title: str | None = None
    category: str | None = None
    priority: str | None = Field(None, pattern=r"^(low|medium|high|urgent)$")
    assigned_team: str | None = None
    plan_json: dict[str, Any] | None = None
    resolved_context: dict[str, Any] | None = None


class WorknoteEntry(BaseModel):
    by: str  # "business" | "tech" | "system" | a username
    text: str
    ts: datetime | None = None  # defaults to server-side now()


class EvidenceEntry(BaseModel):
    kind: str  # "tool_call" | "log_excerpt" | "request_status" | …
    summary: str
    detail: dict[str, Any] | None = None
    ts: datetime | None = None


class HandoffPayload(BaseModel):
    team: str = Field(..., min_length=1, max_length=64)
    reason: str | None = None


class CloseCasePayload(BaseModel):
    resolution_summary: str
    confirmation_ref: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _validate_state(state: str | None) -> None:
    if state is not None and state not in SUPPORT_CASE_STATES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid state {state!r}. Allowed: {sorted(SUPPORT_CASE_STATES)}",
        )


def _get_or_404(db: Session, tenant_id: str, case_id: uuid.UUID) -> SupportCase:
    row = (
        db.query(SupportCase)
        .filter(SupportCase.id == case_id, SupportCase.tenant_id == tenant_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Support case {case_id} not found")
    return row


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=SupportCaseOut, status_code=201)
def upsert_support_case(
    payload: SupportCaseUpsert,
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id),
) -> SupportCase:
    """Upsert by ``(tenant_id, session_id)``. If a non-CLOSED case already
    exists, return it. Otherwise create a fresh ``NEW`` row. The HTTP
    status is 201 in both cases — workflows treat upsert as idempotent."""
    existing = (
        db.query(SupportCase)
        .filter(
            SupportCase.tenant_id == tenant_id,
            SupportCase.session_id == payload.session_id,
            SupportCase.state != "CLOSED",
        )
        .order_by(desc(SupportCase.created_at))
        .first()
    )
    if existing is not None:
        # Update non-state fields if the workflow learned new context.
        dirty = False
        if payload.title and not existing.title:
            existing.title = payload.title
            dirty = True
        if payload.category and not existing.category:
            existing.category = payload.category
            dirty = True
        if payload.priority and not existing.priority:
            existing.priority = payload.priority
            dirty = True
        if payload.requester_id and not existing.requester_id:
            existing.requester_id = payload.requester_id
            dirty = True
        if dirty:
            db.commit()
            db.refresh(existing)
        return existing

    new = SupportCase(
        tenant_id=tenant_id,
        session_id=payload.session_id,
        requester_id=payload.requester_id,
        title=payload.title,
        category=payload.category,
        priority=payload.priority,
        state="NEW",
        evidence=[],
        worknotes=[],
        resolved_context={},
    )
    db.add(new)
    db.commit()
    db.refresh(new)
    return new


@router.get("", response_model=list[SupportCaseOut])
def list_support_cases(
    state: str | None = Query(None, description="Filter by case state"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id),
) -> list[SupportCase]:
    _validate_state(state)
    q = db.query(SupportCase).filter(SupportCase.tenant_id == tenant_id)
    if state:
        q = q.filter(SupportCase.state == state)
    return q.order_by(desc(SupportCase.updated_at)).limit(limit).all()


@router.get("/{case_id}", response_model=SupportCaseOut)
def get_support_case(
    case_id: uuid.UUID,
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id),
) -> SupportCase:
    return _get_or_404(db, tenant_id, case_id)


@router.get("/by-session/{session_id}", response_model=SupportCaseOut | None)
def get_support_case_by_session(
    session_id: str,
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id),
) -> SupportCase | None:
    """Most-recent case for the given session, or null if none exists."""
    return (
        db.query(SupportCase)
        .filter(
            SupportCase.tenant_id == tenant_id,
            SupportCase.session_id == session_id,
        )
        .order_by(desc(SupportCase.created_at))
        .first()
    )


@router.patch("/{case_id}", response_model=SupportCaseOut)
def patch_support_case(
    case_id: uuid.UUID,
    payload: SupportCasePatch,
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id),
) -> SupportCase:
    case = _get_or_404(db, tenant_id, case_id)
    _validate_state(payload.state)

    if payload.state is not None:
        case.state = payload.state
    if payload.title is not None:
        case.title = payload.title
    if payload.category is not None:
        case.category = payload.category
    if payload.priority is not None:
        case.priority = payload.priority
    if payload.assigned_team is not None:
        case.assigned_team = payload.assigned_team
    if payload.plan_json is not None:
        case.plan_json = payload.plan_json
    if payload.resolved_context is not None:
        case.resolved_context = payload.resolved_context

    db.commit()
    db.refresh(case)
    return case


@router.post("/{case_id}/worknote", response_model=SupportCaseOut)
def append_worknote(
    case_id: uuid.UUID,
    payload: WorknoteEntry,
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id),
) -> SupportCase:
    case = _get_or_404(db, tenant_id, case_id)
    note = payload.model_dump()
    if note.get("ts") is None:
        note["ts"] = _utcnow().isoformat()
    else:
        note["ts"] = note["ts"].isoformat()
    case.worknotes = (case.worknotes or []) + [note]
    db.commit()
    db.refresh(case)
    return case


@router.post("/{case_id}/evidence", response_model=SupportCaseOut)
def append_evidence(
    case_id: uuid.UUID,
    payload: EvidenceEntry,
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id),
) -> SupportCase:
    case = _get_or_404(db, tenant_id, case_id)
    entry = payload.model_dump()
    if entry.get("ts") is None:
        entry["ts"] = _utcnow().isoformat()
    else:
        entry["ts"] = entry["ts"].isoformat()
    case.evidence = (case.evidence or []) + [entry]
    db.commit()
    db.refresh(case)
    return case


@router.post("/{case_id}/handoff", response_model=SupportCaseOut)
def handoff_case(
    case_id: uuid.UUID,
    payload: HandoffPayload,
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id),
) -> SupportCase:
    """Lock the case to a human team. After handoff, the workflow's
    case-resolver short-circuits to a 'this case is with team X' reply
    on every turn until a tech user explicitly reopens it via PATCH."""
    case = _get_or_404(db, tenant_id, case_id)
    case.state = "HANDED_OFF"
    case.assigned_team = payload.team
    note = {
        "by": "system",
        "text": f"Handed off to {payload.team}" + (f": {payload.reason}" if payload.reason else ""),
        "ts": _utcnow().isoformat(),
    }
    case.worknotes = (case.worknotes or []) + [note]
    db.commit()
    db.refresh(case)
    return case


@router.post("/{case_id}/close", response_model=SupportCaseOut)
def close_case(
    case_id: uuid.UUID,
    payload: CloseCasePayload,
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id),
) -> SupportCase:
    case = _get_or_404(db, tenant_id, case_id)
    case.state = "CLOSED"
    case.closed_at = _utcnow()
    note = {
        "by": "system",
        "text": f"Closed: {payload.resolution_summary}",
        "ts": _utcnow().isoformat(),
    }
    if payload.confirmation_ref:
        note["confirmation_ref"] = payload.confirmation_ref
    case.worknotes = (case.worknotes or []) + [note]
    db.commit()
    db.refresh(case)
    return case
