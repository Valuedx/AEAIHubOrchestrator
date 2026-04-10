import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Boolean, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class TenantToolOverride(Base):
    __tablename__ = "tenant_tool_overrides"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    tool_name = Column(String(256), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    config_json = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        Index("ix_tool_override_tenant_tool", "tenant_id", "tool_name", unique=True),
    )
