"""Repositories that expose the existing MKB schema through typed SDK models."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from mkb.models import Collection
from mkb.ports import Database


class SQLAlchemyCollectionRepository:
    """Read current ``research_projects`` rows without copying or rewriting them."""

    def __init__(self, database: Database):
        self._database = database

    @staticmethod
    def _model(row) -> Collection:
        return Collection(
            id=row.project_id,
            name=row.label,
            source_path=row.source_path,
            source_count=row.file_count,
            group_id=row.group_id,
            metadata=dict(row.metadata_ or {}),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def get(self, collection_id: str | uuid.UUID) -> Collection | None:
        from mkb.db.models import ResearchProject

        identifier = uuid.UUID(str(collection_id))
        with self._database.session() as session:
            row = session.get(ResearchProject, identifier)
            return self._model(row) if row else None

    def list(self, *, limit: int = 100, offset: int = 0) -> list[Collection]:
        from mkb.db.models import ResearchProject

        statement = (
            select(ResearchProject)
            .order_by(ResearchProject.created_at.desc(), ResearchProject.project_id)
            .limit(limit)
            .offset(offset)
        )
        with self._database.session() as session:
            return [self._model(row) for row in session.scalars(statement)]
