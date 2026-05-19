"""Database session factory and FastAPI/WSGI dependency helper."""

from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import get_current_tenant

DATABASE_URL = os.environ.get("DATABASE_URI", "postgresql://localhost/healops")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session]:
    """Yield a DB session with the Postgres session variable set for RLS."""
    db = SessionLocal()
    tenant = get_current_tenant()
    # Propagate tenant to the Postgres session so RLS policies can read it.
    db.execute(text(f"SET LOCAL app.current_tenant_id = '{tenant}'"))
    try:
        yield db
    finally:
        db.close()
