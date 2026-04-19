from fastapi import Depends
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker, DeclarativeBase

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def set_tenant_context(db: Session, tenant_id: str) -> None:
    """Set the ``app.tenant_id`` GUC on ``db`` so RLS policies defined in
    migrations 0001 / 0009 / 0010 / 0014 can filter rows to this tenant.

    IMPORTANT: PostgreSQL superusers bypass RLS entirely. For these
    policies to actually enforce isolation, the application must connect
    as a non-superuser role. See SETUP_GUIDE.md.
    """
    if not tenant_id:
        raise ValueError("set_tenant_context: tenant_id must be non-empty")
    db.execute(text("SELECT set_tenant_id(:tid)"), {"tid": tenant_id})


def get_db():
    """FastAPI dependency for tenant-unaware DB access (health, migrations).

    Most endpoints should use ``get_tenant_db`` instead so the RLS GUC is
    set automatically.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Imported at bottom to keep the module import order simple; security.tenant
# re-exports get_tenant_id from security.jwt_auth which in turn imports config.
from app.security.tenant import get_tenant_id  # noqa: E402


def get_tenant_db(tenant_id: str = Depends(get_tenant_id)):
    """FastAPI dependency that yields a Session with ``app.tenant_id`` set
    to the caller's tenant. Use this for every endpoint that reads or
    writes tenant-scoped tables.
    """
    db = SessionLocal()
    try:
        set_tenant_context(db, tenant_id)
        yield db
    finally:
        db.close()
