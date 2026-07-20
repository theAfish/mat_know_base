"""Public repository contracts and application-facing collection service."""

from __future__ import annotations

import uuid
from typing import Protocol

from mkb.models import Collection


class CollectionRepository(Protocol):
    def get(self, collection_id: str | uuid.UUID) -> Collection | None: ...
    def list(self, *, limit: int = 100, offset: int = 0) -> list[Collection]: ...


class Collections:
    """Typed collection operations bound to one repository instance."""

    def __init__(self, repository: CollectionRepository):
        self._repository = repository

    def get(self, collection_id: str | uuid.UUID) -> Collection | None:
        return self._repository.get(collection_id)

    def list(self, *, limit: int = 100, offset: int = 0) -> list[Collection]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        if offset < 0:
            raise ValueError("offset must be non-negative")
        return self._repository.list(limit=limit, offset=offset)
