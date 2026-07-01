"""Runtime API service functions."""

from __future__ import annotations

from mkb.services._api_common import (
    init_db,
    logger,
)


def setup() -> None:
    """Ensure database tables exist (idempotent)."""
    init_db()

def reset_db() -> None:
    """Drop all tables and recreate them. Destructive!"""
    from mkb.db.engine import sync_engine
    from mkb.db.models import Base

    Base.metadata.drop_all(sync_engine)
    Base.metadata.create_all(sync_engine)
    logger.info("Database reset complete.")


# ── Ingestion / Sync ─────────────────────────────────────────────

