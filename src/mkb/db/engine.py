"""Alembic boundary and lazy compatibility database resources.

New SDK clients own :class:`mkb.adapters.SQLAlchemyDatabase` instances. The names in
this module remain only for the legacy facade and no longer create connections or
engines merely because a module was imported.
"""

from __future__ import annotations

from functools import cache
from typing import Any, Callable

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session, sessionmaker


@cache
def _legacy_async_engine():
    from mkb.config import settings

    return create_async_engine(settings.pg_dsn, echo=False)


@cache
def _legacy_async_sessions():
    return sessionmaker(
        _legacy_async_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )


@cache
def _legacy_sync_engine() -> Engine:
    from mkb.config import settings

    return create_engine(settings.pg_dsn_sync, echo=False)


@cache
def _legacy_sync_sessions():
    return sessionmaker(_legacy_sync_engine(), class_=Session, expire_on_commit=False)


class _LazyCompatibilityResource:
    """Resolve a legacy engine or factory only when it is actually used."""

    def __init__(self, factory: Callable[[], Any]):
        self._factory = factory

    def __call__(self, *args, **kwargs):
        return self._factory()(*args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._factory(), name)


# Import-compatible aliases. Explicit SDK clients never use these proxies.
async_engine = _LazyCompatibilityResource(_legacy_async_engine)
AsyncSessionLocal = _LazyCompatibilityResource(_legacy_async_sessions)
sync_engine = _LazyCompatibilityResource(_legacy_sync_engine)
SyncSessionLocal = _LazyCompatibilityResource(_legacy_sync_sessions)


class SchemaRevisionError(RuntimeError):
    """The database has not been migrated to the application revision."""


def alembic_config() -> Config:
    from mkb.resources import alembic_paths

    config_path, script_path = alembic_paths()
    config = Config(str(config_path))
    config.set_main_option("script_location", str(script_path))
    return config


def expected_schema_revision() -> str:
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if not head:
        raise SchemaRevisionError("Alembic has no configured head revision")
    return head


def current_schema_revision(engine: Engine | None = None) -> str | None:
    selected_engine = engine if engine is not None else _legacy_sync_engine()
    with selected_engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def require_schema_current(engine: Engine | None = None) -> str:
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
