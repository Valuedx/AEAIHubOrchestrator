"""Encrypted credential vault for tenant secrets.

Stores per-tenant API keys and credentials encrypted at rest using Fernet
symmetric encryption.  The vault key is derived from the
ORCHESTRATOR_VAULT_KEY environment variable (a Fernet-compatible base64 key).

Usage:
    from app.security.vault import encrypt_secret, decrypt_secret

    ciphertext = encrypt_secret("sk-my-openai-key")
    plaintext  = decrypt_secret(ciphertext)
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Text, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID

from app.config import settings
from app.database import Base

logger = logging.getLogger(__name__)

_fernet = None


def _get_fernet():
    global _fernet
    if _fernet is not None:
        return _fernet

    if not settings.vault_key:
        raise RuntimeError(
            "ORCHESTRATOR_VAULT_KEY is not set. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )

    from cryptography.fernet import Fernet
    _fernet = Fernet(settings.vault_key.encode())
    return _fernet


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a plaintext secret and return the base64-encoded ciphertext."""
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a base64-encoded ciphertext and return the plaintext."""
    f = _get_fernet()
    return f.decrypt(ciphertext.encode()).decode()


def get_tenant_secret(tenant_id: str, key_name: str) -> str | None:
    """Look up a single secret by tenant and key name, returning decrypted plaintext."""
    from app.database import SessionLocal, set_tenant_context

    db = SessionLocal()
    try:
        set_tenant_context(db, tenant_id)
        row = (
            db.query(TenantSecret)
            .filter_by(tenant_id=tenant_id, key_name=key_name)
            .first()
        )
        if row is None:
            return None
        return decrypt_secret(row.encrypted_value)
    finally:
        db.close()


def _utcnow():
    return datetime.now(timezone.utc)


class TenantSecret(Base):
    """Stores encrypted credentials per tenant.

    Each row holds one named secret (e.g. 'openai_api_key') whose value is
    Fernet-encrypted at rest.
    """
    __tablename__ = "tenant_secrets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    key_name = Column(String(256), nullable=False)
    encrypted_value = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        Index("ix_tenant_secret_tenant_key", "tenant_id", "key_name", unique=True),
    )
