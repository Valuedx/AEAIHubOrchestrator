from contextvars import ContextVar
from fastapi import Depends
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import Session, sessionmaker, DeclarativeBase

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Context variable to hold the tenant_id for the current request/thread.
# This allows the SQLAlchemy listener to re-apply the RLS GUC on every
# transaction start, ensuring context isn't lost after a commit().
tenant_id_context: ContextVar[str | None] = ContextVar("tenant_id", default=None)


class Base(DeclarativeBase):
    pass


def set_tenant_context(db: Session, tenant_id: str) -> None:
    """Set the ``app.tenant_id`` GUC on ``db`` so RLS policies defined in
    migrations 0001 / 0009 / 0010 / 0014 can filter rows to this tenant.

    This function also updates the ``tenant_id_context`` so the global
    listener can restore the setting if it's lost (e.g. after a commit).
    """
    if not tenant_id:
        raise ValueError("set_tenant_context: tenant_id must be non-empty")
    
    # Update context variable for the global listener
    tenant_id_context.set(tenant_id)
    
    # Set the GUC immediately for the current session/transaction
    db.execute(text("SELECT set_tenant_id(:tid)"), {"tid": tenant_id})


@event.listens_for(SessionLocal, "after_begin")
def restore_tenant_context(session, transaction, connection):
    """SQLAlchemy listener that automatically re-applies the ``app.tenant_id``
    GUC at the start of every transaction if a tenant_id is active in the
    current context.
    
    Targeting SessionLocal ensures this applies to all sessions created
    by the application.
    """
    tid = tenant_id_context.get()
    if tid:
        connection.execute(text("SELECT set_tenant_id(:tid)"), {"tid": tid})


def get_db():
    """FastAPI dependency for tenant-unaware DB access (health, migrations)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Imported at bottom to avoid circular dependency
from app.security.tenant import get_tenant_id  # noqa: E402


def get_tenant_db(tenant_id: str = Depends(get_tenant_id)):
    """FastAPI dependency that yields a Session with ``app.tenant_id`` set
    to the caller's tenant.
    """
    db = SessionLocal()
    try:
        set_tenant_context(db, tenant_id)
        yield db
    finally:
        db.close()
