"""Instance-owned SQLAlchemy database adapter."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from mkb.ports import Capabilities


class SQLAlchemyDatabase:
    """Own an engine and session factory for exactly one SDK instance."""

    capabilities = frozenset({Capabilities.TRANSACTIONS})

    def __init__(self, url: str, **engine_options):
        self.url = url
        self.engine: Engine = create_engine(url, **engine_options)
        self._sessions = sessionmaker(self.engine, class_=Session, expire_on_commit=False)
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Database adapter is closed")

    def session(self) -> Session:
        """Return an uncommitted session context owned by the caller."""
        self._ensure_open()
        return self._sessions()

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """Commit on success and roll back on failure."""
        self._ensure_open()
        with self._sessions() as session, session.begin():
            yield session

    def check(self) -> None:
        self._ensure_open()
        with self.engine.connect() as connection:
            connection.execute(text("select 1"))

    def migration_revision(self) -> str | None:
        """Return the Alembic revision recorded by this database, when present."""
        self._ensure_open()
        from sqlalchemy import inspect

        with self.engine.connect() as connection:
            if "alembic_version" not in inspect(connection).get_table_names():
                return None
            return connection.execute(
                text("select version_num from alembic_version")
            ).scalar_one_or_none()

    def close(self) -> None:
        if self._closed:
            return
        self.engine.dispose()
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed
