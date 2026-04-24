import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Boolean, DateTime, Text, Index
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    username = Column(String(128), nullable=False)
    email = Column(String(256), nullable=True)
    password_hash = Column(Text, nullable=False)
    is_admin = Column(Boolean, nullable=False, default=False)
    disabled = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
    last_login_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Matches the functional unique index created in migration 0033.
        # Declared here for ORM completeness; the authoritative constraint
        # is the lower(username) index in the migration.
        Index("ix_users_tenant_id_ormhint", "tenant_id"),
    )
