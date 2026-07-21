"""Typed graph service exposed by :class:`mkb.KnowledgeBase`."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from mkb.exceptions import ConflictError, NotFoundError, ProviderError, ValidationError
from mkb.models import Entity, GraphResult, GraphReview, Relation
from mkb.ports import GraphStore, ModelProvider


def _id(value: str | uuid.UUID, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValidationError(f"{field} must be a UUID") from exc


class Graph:
    """Backend-independent entity, relation, and traversal operations."""

    def __init__(self, store: GraphStore, model_provider: ModelProvider | None = None):
        self._store = store
        self._model_provider = model_provider
        self._reviews: list[GraphReview] = []
        self._legacy_loader: Callable[..., dict[str, Any]] | None = None
        self._legacy_extractor: Callable[..., dict[str, Any]] | None = None
        self._legacy_reviewer: Callable[..., dict[str, Any]] | None = None
        self._legacy_loaded = False

    def _bind_compatibility(
        self,
        *,
        loader: Callable[..., dict[str, Any]] | None = None,
        extractor: Callable[..., dict[str, Any]] | None = None,
        reviewer: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        """Bind injected legacy operations without importing global services."""
        self._legacy_loader = loader
        self._legacy_extractor = extractor
        self._legacy_reviewer = reviewer

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
        self._ensure_legacy_loaded()
        return self._store.get_entity(str(_id(entity_id, "entity_id")))

    def require_entity(self, entity_id: str | uuid.UUID) -> Entity:
        entity = self.get_entity(entity_id)
        if entity is None:
            raise NotFoundError(f"Entity not found: {entity_id}")
        return entity

    def list_entities(self, *, type: str | None = None) -> list[Entity]:
        self._ensure_legacy_loaded()
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
        self._ensure_legacy_loaded()
        return self._store.get_relation(str(_id(relation_id, "relation_id")))

    def list_relations(
        self,
        *,
        entity_id: str | uuid.UUID | None = None,
        type: str | None = None,
    ) -> list[Relation]:
        self._ensure_legacy_loaded()
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

    def query(
        self,
        *,
        entity_type: str | None = None,
        relation_type: str | None = None,
        name_contains: str | None = None,
        properties: dict[str, Any] | None = None,
    ) -> GraphResult:
        """Query graph elements using portable exact property filters."""
        self._ensure_legacy_loaded()
        entities = self._store.list_entities(type=entity_type)
        if name_contains is not None:
            needle = name_contains.casefold()
            entities = [item for item in entities if needle in item.name.casefold()]
        if properties:
            entities = [
                item
                for item in entities
                if all(item.properties.get(key) == value for key, value in properties.items())
            ]
        entity_ids = {item.id for item in entities}
        relations = self._store.list_relations(type=relation_type)
        if entity_type is not None or name_contains is not None or properties:
            relations = [
                item
                for item in relations
                if item.source_id in entity_ids or item.target_id in entity_ids
            ]
        return GraphResult(entities=tuple(entities), relations=tuple(relations))

    def traverse(
        self,
        entity_id: str | uuid.UUID,
        *,
        max_depth: int = 1,
        direction: str = "both",
    ) -> GraphResult:
        """Breadth-first traversal with a bounded portable depth."""
        if max_depth < 0 or max_depth > 10:
            raise ValidationError("max_depth must be between 0 and 10")
        if direction not in {"both", "out", "in"}:
            raise ValidationError("direction must be 'both', 'out', or 'in'")
        start = self.require_entity(entity_id)
        visited = {start.id}
        frontier = {start.id}
        relations: dict[uuid.UUID, Relation] = {}
        for _depth in range(max_depth):
            next_frontier: set[uuid.UUID] = set()
            for identifier in frontier:
                for relation in self._store.list_relations(entity_id=str(identifier)):
                    if direction == "out" and relation.source_id != identifier:
                        continue
                    if direction == "in" and relation.target_id != identifier:
                        continue
                    relations[relation.id] = relation
                    neighbor = (
                        relation.target_id
                        if relation.source_id == identifier
                        else relation.source_id
                    )
                    if neighbor not in visited:
                        next_frontier.add(neighbor)
            visited.update(next_frontier)
            frontier = next_frontier
            if not frontier:
                break
        entities = tuple(self.require_entity(item) for item in sorted(visited, key=str))
        return GraphResult(
            entities=entities,
            relations=tuple(relations[key] for key in sorted(relations, key=str)),
            metadata={"start_id": str(start.id), "max_depth": max_depth, "direction": direction},
        )

    def extract(
        self,
        content: Any = None,
        *,
        extractor: Callable[[Any], dict[str, Any]] | None = None,
        parameters: dict[str, Any] | None = None,
        collection_id: str | uuid.UUID | None = None,
        record_id: str | uuid.UUID | None = None,
    ) -> GraphResult:
        """Extract and upsert a graph using a consumer handler or model provider."""
        if extractor is not None:
            payload = extractor(content)
        elif self._legacy_extractor is not None:
            operation_result = self._legacy_extractor(
                project_id=collection_id,
                frame_id=record_id,
                **(parameters or {}),
            )
            self._legacy_loaded = False
            self._ensure_legacy_loaded()
            snapshot = self.query()
            return snapshot.model_copy(
                update={"metadata": {"operation_result": operation_result}}
            )
        elif self._model_provider is not None:
            prompt = (
                "Extract a JSON object with entities and relations. Each entity needs "
                "name and type; each relation needs source_id, target_id, and type.\n\n"
                f"Input:\n{content}"
            )
            try:
                payload = self._model_provider.generate(prompt, parameters=parameters)
            except Exception as exc:
                raise ProviderError("Graph extraction provider failed") from exc
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError as exc:
                    raise ProviderError("Graph extraction provider returned invalid JSON") from exc
        else:
            raise ConflictError("Graph extraction requires an extractor or model provider")
        if not isinstance(payload, dict):
            raise ValidationError("graph extractor must return a dictionary")
        aliases: dict[str, uuid.UUID] = {}
        entities: list[Entity] = []
        for raw in payload.get("entities", payload.get("concepts", [])):
            if not isinstance(raw, dict):
                raise ValidationError("every extracted entity must be a dictionary")
            name = str(raw.get("name") or raw.get("label") or "").strip()
            type_name = str(raw.get("type") or raw.get("category") or "concept").strip()
            if not name:
                raise ValidationError("every extracted entity requires a name")
            raw_identifier = raw.get("id")
            identifier = self._extracted_id(raw_identifier, f"entity:{type_name}:{name}")
            entity = self.upsert_entity(
                entity_id=identifier,
                name=name,
                type=type_name,
                properties=self._element_properties(
                    raw,
                    excluded={"id", "name", "label", "type", "category"},
                ),
            )
            entities.append(entity)
            for alias in (raw_identifier, name):
                if alias is not None:
                    aliases[str(alias)] = identifier
        relations: list[Relation] = []
        for raw in payload.get("relations", []):
            if not isinstance(raw, dict):
                raise ValidationError("every extracted relation must be a dictionary")
            source = self._resolve_endpoint(raw.get("source_id", raw.get("source")), aliases)
            target = self._resolve_endpoint(raw.get("target_id", raw.get("target")), aliases)
            type_name = str(raw.get("type") or raw.get("relation") or "related_to").strip()
            relation = self.upsert_relation(
                source,
                target,
                relation_id=self._extracted_id(
                    raw.get("id"), f"relation:{source}:{type_name}:{target}"
                ),
                type=type_name,
                properties=self._element_properties(
                    raw,
                    excluded={
                        "id",
                        "source_id",
                        "source",
                        "target_id",
                        "target",
                        "type",
                        "relation",
                    },
                ),
            )
            relations.append(relation)
        return GraphResult(
            entities=tuple(entities),
            relations=tuple(relations),
            metadata={"extracted": True},
        )

    def review(
        self,
        target_id: str | uuid.UUID | None = None,
        *,
        reviewer: Callable[[Entity | Relation], dict[str, Any]] | None = None,
        notes: str | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> GraphReview:
        """Review one graph element and apply explicit, typed modifications."""
        if target_id is None and reviewer is None and self._legacy_reviewer is not None:
            result = self._legacy_reviewer(**(parameters or {}))
            review = GraphReview(
                target_type="graph",
                decision="accepted",
                notes=notes,
                metadata={"operation_result": result},
                created_at=datetime.now(timezone.utc),
            )
            self._reviews.append(review)
            self._legacy_loaded = False
            return review
        if target_id is None:
            raise ValidationError("target_id is required for element review")
        identifier = _id(target_id, "target_id")
        target: Entity | Relation | None = self._store.get_entity(str(identifier))
        target_type = "entity"
        if target is None:
            target = self._store.get_relation(str(identifier))
            target_type = "relation"
        if target is None:
            raise NotFoundError(f"Graph element not found: {target_id}")
        if reviewer is not None:
            decision = reviewer(target)
        elif self._model_provider is not None:
            prompt = (
                "Review this graph element. Return JSON with decision accepted, modified, "
                f"or rejected; optional changes and notes.\n\n{target.model_dump_json()}"
            )
            try:
                decision = self._model_provider.generate(prompt, parameters=parameters)
            except Exception as exc:
                raise ProviderError("Graph review provider failed") from exc
            if isinstance(decision, str):
                try:
                    decision = json.loads(decision)
                except json.JSONDecodeError as exc:
                    raise ProviderError("Graph review provider returned invalid JSON") from exc
        else:
            raise ConflictError("Graph review requires a reviewer or model provider")
        if not isinstance(decision, dict):
            raise ValidationError("graph reviewer must return a dictionary")
        outcome = str(decision.get("decision") or "accepted").lower()
        if outcome not in {"accepted", "modified", "rejected"}:
            raise ValidationError("graph review decision is invalid")
        changes = dict(decision.get("changes") or {})
        if outcome == "modified":
            self._apply_review_changes(target, changes)
        review = GraphReview(
            target_type=target_type,
            target_id=identifier,
            decision=outcome,
            notes=notes or decision.get("notes"),
            changes=changes,
            created_at=datetime.now(timezone.utc),
        )
        self._reviews.append(review)
        return review

    def list_reviews(self, *, target_id: str | uuid.UUID | None = None) -> list[GraphReview]:
        identifier = _id(target_id, "target_id") if target_id is not None else None
        return [
            review
            for review in self._reviews
            if identifier is None or review.target_id == identifier
        ]

    @staticmethod
    def _extracted_id(value: Any, fallback: str) -> uuid.UUID:
        if value is not None:
            try:
                return uuid.UUID(str(value))
            except ValueError:
                return uuid.uuid5(uuid.NAMESPACE_URL, str(value))
        return uuid.uuid5(uuid.NAMESPACE_URL, fallback)

    @staticmethod
    def _resolve_endpoint(value: Any, aliases: dict[str, uuid.UUID]) -> uuid.UUID:
        if value is None:
            raise ValidationError("every extracted relation requires endpoints")
        if str(value) in aliases:
            return aliases[str(value)]
        try:
            return uuid.UUID(str(value))
        except ValueError as exc:
            raise ValidationError(f"unknown extracted relation endpoint: {value}") from exc

    def _apply_review_changes(
        self,
        target: Entity | Relation,
        changes: dict[str, Any],
    ) -> None:
        if isinstance(target, Entity):
            self.upsert_entity(
                entity_id=target.id,
                type=str(changes.get("type", target.type)),
                name=str(changes.get("name", target.name)),
                properties={**target.properties, **dict(changes.get("properties") or {})},
            )
        else:
            self.upsert_relation(
                target.source_id,
                target.target_id,
                relation_id=target.id,
                type=str(changes.get("type", target.type)),
                properties={**target.properties, **dict(changes.get("properties") or {})},
            )

    @staticmethod
    def _element_properties(
        raw: dict[str, Any],
        *,
        excluded: set[str],
    ) -> dict[str, Any]:
        properties = dict(raw.get("properties") or {})
        properties.update(
            {key: value for key, value in raw.items() if key not in excluded | {"properties"}}
        )
        return properties

    def _ensure_legacy_loaded(self) -> None:
        if self._legacy_loader is None or self._legacy_loaded:
            return
        result = self._legacy_loader()
        graph = result.get("graph", result) if isinstance(result, dict) else {}
        self._legacy_loaded = True
        if isinstance(graph, dict):
            self.extract(graph, extractor=lambda payload: payload)
