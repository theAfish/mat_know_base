"""Runtime API service functions."""

from __future__ import annotations

from mkb.services._api_common import (
    logger,
)


def setup() -> None:
    """Upgrade the database through the reviewed Alembic chain."""
    from mkb.db.engine import upgrade_db

    upgrade_db()

def reset_db() -> None:
    """Drop all tables and recreate them. Destructive!"""
    from mkb.db.engine import reset_schema

    reset_schema()
    logger.info("Database reset complete.")


# ── Ingestion / Sync ─────────────────────────────────────────────
