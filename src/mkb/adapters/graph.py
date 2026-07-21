"""Small in-memory graph adapter for local SDK use and tests."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from mkb.exceptions import BackendUnavailableError, ConflictError
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


class Neo4jGraphStore:
    """Optional Neo4j adapter using fixed labels and relationship types."""

    capabilities = frozenset(
        {Capabilities.GRAPH_TRAVERSAL, Capabilities.BULK_UPSERT}
    )

    def __init__(
        self,
        uri: str | None = None,
        *,
        auth: tuple[str, str] | None = None,
        database: str | None = None,
        driver: Any | None = None,
    ) -> None:
        if driver is None:
            try:
                from neo4j import GraphDatabase
            except ImportError as exc:
                raise BackendUnavailableError(
                    "Neo4j support requires 'mat-know-base[neo4j]'"
                ) from exc
            if not uri:
                raise ValueError("uri is required when driver is not supplied")
            driver = GraphDatabase.driver(uri, auth=auth)
        self._driver = driver
        self._database = database
        self._closed = False

    def _session(self):
        if self._closed:
            raise BackendUnavailableError("Neo4j graph store is closed")
        return self._driver.session(database=self._database)

    @staticmethod
    def _timestamp(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    @staticmethod
    def _properties(value: str | None) -> dict[str, Any]:
        return json.loads(value) if value else {}

    @classmethod
    def _entity(cls, record: Any) -> Entity:
        return Entity(
            id=record["id"],
            type=record["type"],
            name=record["name"],
            properties=cls._properties(record.get("properties_json")),
            created_at=record.get("created_at"),
            updated_at=record.get("updated_at"),
        )

    @classmethod
    def _relation(cls, record: Any) -> Relation:
        return Relation(
            id=record["id"],
            source_id=record["source_id"],
            target_id=record["target_id"],
            type=record["type"],
            properties=cls._properties(record.get("properties_json")),
            created_at=record.get("created_at"),
            updated_at=record.get("updated_at"),
        )

    def upsert_entity(self, entity: Entity) -> Entity:
        with self._session() as session:
            session.run(
                """
                MERGE (e:MKBEntity {id: $id})
                SET e.type = $type, e.name = $name,
                    e.properties_json = $properties_json,
                    e.created_at = $created_at, e.updated_at = $updated_at
                """,
                id=str(entity.id),
                type=entity.type,
                name=entity.name,
                properties_json=json.dumps(entity.properties, sort_keys=True),
                created_at=self._timestamp(entity.created_at),
                updated_at=self._timestamp(entity.updated_at),
            ).consume()
        return entity

    def get_entity(self, entity_id: str | uuid.UUID) -> Entity | None:
        with self._session() as session:
            record = session.run(
                """
                MATCH (e:MKBEntity {id: $id})
                RETURN e.id AS id, e.type AS type, e.name AS name,
                       e.properties_json AS properties_json,
                       e.created_at AS created_at, e.updated_at AS updated_at
                """,
                id=str(entity_id),
            ).single()
        return self._entity(record) if record is not None else None

    def list_entities(self, *, type: str | None = None) -> list[Entity]:
        with self._session() as session:
            records = session.run(
                """
                MATCH (e:MKBEntity)
                WHERE $type IS NULL OR e.type = $type
                RETURN e.id AS id, e.type AS type, e.name AS name,
                       e.properties_json AS properties_json,
                       e.created_at AS created_at, e.updated_at AS updated_at
                ORDER BY e.id
                """,
                type=type,
            )
            return [self._entity(record) for record in records]

    def upsert_relation(self, relation: Relation) -> Relation:
        with self._session() as session:
            record = session.run(
                """
                MATCH (source:MKBEntity {id: $source_id})
                MATCH (target:MKBEntity {id: $target_id})
                MERGE (source)-[r:MKBRelation {id: $id}]->(target)
                SET r.type = $type, r.properties_json = $properties_json,
                    r.created_at = $created_at, r.updated_at = $updated_at
                RETURN count(r) AS matched
                """,
                id=str(relation.id),
                source_id=str(relation.source_id),
                target_id=str(relation.target_id),
                type=relation.type,
                properties_json=json.dumps(relation.properties, sort_keys=True),
                created_at=self._timestamp(relation.created_at),
                updated_at=self._timestamp(relation.updated_at),
            ).single()
        if record is None or int(record["matched"]) != 1:
            raise ConflictError("Both relation endpoints must exist")
        return relation

    def get_relation(self, relation_id: str | uuid.UUID) -> Relation | None:
        with self._session() as session:
            record = session.run(
                """
                MATCH (source:MKBEntity)-[r:MKBRelation {id: $id}]->(target:MKBEntity)
                RETURN r.id AS id, source.id AS source_id, target.id AS target_id,
                       r.type AS type, r.properties_json AS properties_json,
                       r.created_at AS created_at, r.updated_at AS updated_at
                """,
                id=str(relation_id),
            ).single()
        return self._relation(record) if record is not None else None

    def list_relations(
        self,
        *,
        entity_id: str | uuid.UUID | None = None,
        type: str | None = None,
    ) -> list[Relation]:
        with self._session() as session:
            records = session.run(
                """
                MATCH (source:MKBEntity)-[r:MKBRelation]->(target:MKBEntity)
                WHERE ($entity_id IS NULL OR source.id = $entity_id OR target.id = $entity_id)
                  AND ($type IS NULL OR r.type = $type)
                RETURN r.id AS id, source.id AS source_id, target.id AS target_id,
                       r.type AS type, r.properties_json AS properties_json,
                       r.created_at AS created_at, r.updated_at AS updated_at
                ORDER BY r.id
                """,
                entity_id=str(entity_id) if entity_id is not None else None,
                type=type,
            )
            return [self._relation(record) for record in records]

    def close(self) -> None:
        if not self._closed:
            self._driver.close()
            self._closed = True
