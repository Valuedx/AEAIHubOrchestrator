"""SupportCase ORM model.

Backs the AE Ops Support workflow's case-state machine. Created by migration
``48f869152a93_ae_ops_support_cases.py``. Tenant-scoped via the standard
``app.tenant_id`` GUC RLS pattern (forced row security enabled).

The workflow reaches this table via the ``/api/v1/support-cases`` REST router
(not via direct SQLAlchemy from a code-execution node — the sandbox blocks
SQLAlchemy imports).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# State-machine values per the AE Ops Support design prompt. Kept as a plain
# string for forward-compat (the prompt enumerated 11 states; a Postgres ENUM
# would force a migration on every value tweak).
SUPPORT_CASE_STATES = {
    "NEW",
    "PLANNING",
    "NEED_INFO",
    "WAITING_APPROVAL",
    "READY_TO_EXECUTE",
    "EXECUTING",
    "WAITING_ON_TEAM",
    "RESOLVED_PENDING_CONFIRMATION",
    "HANDED_OFF",
    "CLOSED",
    "FAILED",
}


class SupportCase(Base):
    __tablename__ = "support_cases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False)
    session_id = Column(String(128), nullable=False)
    requester_id = Column(String(128), nullable=True)
    state = Column(String(48), nullable=False, default="NEW")
    title = Column(String(256), nullable=True)
    category = Column(String(64), nullable=True)
    priority = Column(String(16), nullable=True)
    assigned_team = Column(String(64), nullable=True)

    # Set by the planner ReAct agent before approval; consumed by the
    # executor on approve. Free-form JSONB so the schema can evolve without
    # migrations.
    plan_json = Column(JSONB, nullable=True)
    evidence = Column(JSONB, nullable=False, default=list)
    worknotes = Column(JSONB, nullable=False, default=list)
    resolved_context = Column(JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
    closed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_support_cases_tenant_session_orm", "tenant_id", "session_id"),
        Index("ix_support_cases_tenant_state_orm", "tenant_id", "state"),
    )
