"""Small in-memory graph adapter for local SDK use and tests."""

from __future__ import annotations

import uuid

from mkb.exceptions import ConflictError
from mkb.models import Entity, Relation
from mkb.ports import Capabilities


class InMemoryGraphStore:
    """Process-local graph store with deterministic upsert semantics."""

    capabilities = frozenset(
        {Capabilities.GRAPH_TRAVERSAL, Capabilities.BULK_UPSERT}
    )

    def __init__(self) -> None:
        self._entities: dict[uuid.UUID, Entity] = {}
        self._relations: dict[uuid.UUID, Relation] = {}

    def upsert_entity(self, entity: Entity) -> Entity:
        self._entities[entity.id] = entity
        return entity

    def get_entity(self, entity_id: str | uuid.UUID) -> Entity | None:
        return self._entities.get(uuid.UUID(str(entity_id)))

    def list_entities(self, *, type: str | None = None) -> list[Entity]:
        values = self._entities.values()
        return sorted(
            (entity for entity in values if type is None or entity.type == type),
            key=lambda entity: str(entity.id),
        )

    def upsert_relation(self, relation: Relation) -> Relation:
        if relation.source_id not in self._entities or relation.target_id not in self._entities:
            raise ConflictError("Both relation endpoints must exist")
        self._relations[relation.id] = relation
        return relation

    def get_relation(self, relation_id: str | uuid.UUID) -> Relation | None:
        return self._relations.get(uuid.UUID(str(relation_id)))

    def list_relations(
        self,
        *,
        entity_id: str | uuid.UUID | None = None,
        type: str | None = None,
    ) -> list[Relation]:
        identifier = uuid.UUID(str(entity_id)) if entity_id is not None else None
        return sorted(
            (
                relation
                for relation in self._relations.values()
                if (type is None or relation.type == type)
                and (
                    identifier is None
                    or relation.source_id == identifier
                    or relation.target_id == identifier
                )
            ),
            key=lambda relation: str(relation.id),
        )

    def close(self) -> None:
        self._entities.clear()
        self._relations.clear()
