"""Instance-owned SQLAlchemy database adapter."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


class SQLAlchemyDatabase:
    """Own an engine and session factory for exactly one SDK instance."""

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

    def close(self) -> None:
        if self._closed:
            return
        self.engine.dispose()
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed
