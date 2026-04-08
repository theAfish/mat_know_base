"""SQLAlchemy engine and session factories."""

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from mkb.config import settings

# ── Async (primary – for app runtime) ──────────────────────────
async_engine = create_async_engine(settings.pg_dsn, echo=False)
AsyncSessionLocal = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

# ── Sync (for Alembic migrations & quick scripts) ─────────────
sync_engine = create_engine(settings.pg_dsn_sync, echo=False)
SyncSessionLocal = sessionmaker(sync_engine, class_=Session, expire_on_commit=False)
