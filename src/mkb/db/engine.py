"""SQLAlchemy engine and session factories."""

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import text

from mkb.config import settings

# ── Async (primary – for app runtime) ──────────────────────────
async_engine = create_async_engine(settings.pg_dsn, echo=False)
AsyncSessionLocal = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

# ── Sync (for Alembic migrations & quick scripts) ─────────────
sync_engine = create_engine(settings.pg_dsn_sync, echo=False)
SyncSessionLocal = sessionmaker(sync_engine, class_=Session, expire_on_commit=False)


def init_db() -> None:
    """Create all tables from ORM metadata (idempotent)."""
    from mkb.db.models import Base
    # Ensure the pgvector extension is available for the `vector` column type.
    with sync_engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        conn.commit()

    Base.metadata.create_all(sync_engine)
