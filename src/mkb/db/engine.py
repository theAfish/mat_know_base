"""SQLAlchemy engines plus the Alembic-only schema boundary."""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from mkb.config import settings

async_engine = create_async_engine(settings.pg_dsn, echo=False)
AsyncSessionLocal = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

sync_engine = create_engine(settings.pg_dsn_sync, echo=False)
SyncSessionLocal = sessionmaker(sync_engine, class_=Session, expire_on_commit=False)


class SchemaRevisionError(RuntimeError):
    """The database has not been migrated to the application revision."""


def alembic_config() -> Config:
    return Config("alembic.ini")


def expected_schema_revision() -> str:
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if not head:
        raise SchemaRevisionError("Alembic has no configured head revision")
    return head


def current_schema_revision(engine: Engine = sync_engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def require_schema_current(engine: Engine = sync_engine) -> str:
    current = current_schema_revision(engine)
    expected = expected_schema_revision()
    if current != expected:
        current_label = current or "unversioned/empty"
        raise SchemaRevisionError(
            f"Database schema is at {current_label}; expected {expected}. "
            "Run `make migrate` before starting MKB."
        )
    return current


def upgrade_db(revision: str = "head") -> None:
    command.upgrade(alembic_config(), revision)


def reset_schema() -> None:
    """Destructively rebuild the schema through reviewed migrations only."""
    config = alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")


def init_db() -> None:
    """Compatibility name: verify schema state; never mutate it at runtime."""
    require_schema_current()
