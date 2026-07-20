"""Typed graph service exposed by :class:`mkb.KnowledgeBase`."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from mkb.exceptions import NotFoundError, ValidationError
from mkb.models import Entity, Relation
from mkb.ports import GraphStore


def _id(value: str | uuid.UUID, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValidationError(f"{field} must be a UUID") from exc


class Graph:
    """Backend-independent entity, relation, and traversal operations."""

    def __init__(self, store: GraphStore):
        self._store = store

    def upsert_entity(
        self,
        *,
        type: str,
        name: str,
        properties: dict[str, Any] | None = None,
        entity_id: str | uuid.UUID | None = None,
    ) -> Entity:
        if not type.strip() or not name.strip():
            raise ValidationError("entity type and name must not be empty")
        identifier = _id(entity_id, "entity_id") if entity_id is not None else uuid.uuid4()
        previous = self._store.get_entity(str(identifier))
        now = datetime.now(timezone.utc)
        entity = Entity(
            id=identifier,
            type=type.strip(),
            name=name.strip(),
            properties=properties or {},
            created_at=previous.created_at if previous else now,
            updated_at=now,
        )
        return self._store.upsert_entity(entity)

    def get_entity(self, entity_id: str | uuid.UUID) -> Entity | None:
        return self._store.get_entity(str(_id(entity_id, "entity_id")))

    def require_entity(self, entity_id: str | uuid.UUID) -> Entity:
        entity = self.get_entity(entity_id)
        if entity is None:
            raise NotFoundError(f"Entity not found: {entity_id}")
        return entity

    def list_entities(self, *, type: str | None = None) -> list[Entity]:
        return self._store.list_entities(type=type)

    def upsert_relation(
        self,
        source_id: str | uuid.UUID,
        target_id: str | uuid.UUID,
        *,
        type: str,
        properties: dict[str, Any] | None = None,
        relation_id: str | uuid.UUID | None = None,
    ) -> Relation:
        if not type.strip():
            raise ValidationError("relation type must not be empty")
        identifier = _id(relation_id, "relation_id") if relation_id is not None else uuid.uuid4()
        previous = self._store.get_relation(str(identifier))
        now = datetime.now(timezone.utc)
        relation = Relation(
            id=identifier,
            source_id=_id(source_id, "source_id"),
            target_id=_id(target_id, "target_id"),
            type=type.strip(),
            properties=properties or {},
            created_at=previous.created_at if previous else now,
            updated_at=now,
        )
        return self._store.upsert_relation(relation)

    def get_relation(self, relation_id: str | uuid.UUID) -> Relation | None:
        return self._store.get_relation(str(_id(relation_id, "relation_id")))

    def list_relations(
        self,
        *,
        entity_id: str | uuid.UUID | None = None,
        type: str | None = None,
    ) -> list[Relation]:
        identifier = str(_id(entity_id, "entity_id")) if entity_id is not None else None
        return self._store.list_relations(entity_id=identifier, type=type)

    def neighbors(self, entity_id: str | uuid.UUID) -> list[Entity]:
        identifier = _id(entity_id, "entity_id")
        self.require_entity(identifier)
        neighbor_ids = {
            relation.target_id if relation.source_id == identifier else relation.source_id
            for relation in self._store.list_relations(entity_id=str(identifier))
        }
        return [self.require_entity(item) for item in sorted(neighbor_ids, key=str)]
