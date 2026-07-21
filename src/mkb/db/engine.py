"""Lazy compatibility database resources.

New SDK clients own :class:`mkb.adapters.SQLAlchemyDatabase` instances. The names in
this module remain only for the legacy facade and no longer create connections or
engines merely because a module was imported.
"""

from __future__ import annotations

from functools import cache
from typing import Any, Callable

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
def _global_sync_sessions():
    return sessionmaker(_legacy_sync_engine(), class_=Session, expire_on_commit=False)


def _legacy_sync_sessions():
    from mkb.legacy_context import database_var

    database = database_var.get()
    if database is not None:
        return database.session
    return _global_sync_sessions()


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


def init_db() -> None:
    """Compatibility hook retained for service call sites.

    Database provisioning and schema upgrades are deployment-owned. The current local
    database was reconciled before the retired Alembic tooling was removed.
    """
