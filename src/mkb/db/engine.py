"""SQLAlchemy engine and session factories."""

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from mkb.config import settings

# ── Async (primary – for app runtime) ──────────────────────────
async_engine = create_async_engine(settings.pg_dsn, echo=False)
AsyncSessionLocal = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

# ── Sync (for Alembic migrations & quick scripts) ─────────────
sync_engine = create_engine(settings.pg_dsn_sync, echo=False)
SyncSessionLocal = sessionmaker(sync_engine, class_=Session, expire_on_commit=False)


def _apply_schema_compatibility(engine: Engine) -> None:
    """Patch additive schema changes for older databases."""
    compatibility_updates = {
        "knowledge_frames": {
            "extraction_version": (
                "ALTER TABLE knowledge_frames "
                "ADD COLUMN extraction_version INTEGER NOT NULL DEFAULT 0"
            ),
        },
    }

    with engine.begin() as conn:
        inspector = inspect(conn)
        for table_name, updates in compatibility_updates.items():
            if not inspector.has_table(table_name):
                continue

            existing_columns = {col["name"] for col in inspector.get_columns(table_name)}
            for column_name, ddl in updates.items():
                if column_name not in existing_columns:
                    conn.execute(text(ddl))
                    existing_columns.add(column_name)

        if inspector.has_table("projections"):
            indexes = {index["name"]: index for index in inspector.get_indexes("projections")}
            projection_index = indexes.get("ix_projection_space_frame")
            if projection_index and projection_index.get("unique"):
                conn.execute(text("DROP INDEX IF EXISTS ix_projection_space_frame"))
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_projection_space_frame "
                        "ON projections (space_id, frame_id)"
                    )
                )

        # Ensure REVIEWED value exists in projection_status enum
        conn.execute(
            text("ALTER TYPE projection_status ADD VALUE IF NOT EXISTS 'REVIEWED'")
        )


def init_db() -> None:
    """Create all tables from ORM metadata and patch older schemas."""
    from mkb.db.models import Base

    # Ensure the pgvector extension is available for the `vector` column type.
    with sync_engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        conn.commit()

    Base.metadata.create_all(sync_engine)
    _apply_schema_compatibility(sync_engine)
