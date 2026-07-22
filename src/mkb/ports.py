"""Infrastructure interfaces used by the public SDK and domain services."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, BinaryIO, Iterable, Protocol, runtime_checkable

from mkb.models import Entity, Job, Relation


class Capabilities:
    """Stable capability names used by adapters and pipeline requirements."""

    TRANSACTIONS = "transactions"
    VECTOR_SEARCH = "vector_search"
    FULL_TEXT_SEARCH = "full_text_search"
    OBJECT_STREAMING = "object_streaming"
    GRAPH_TRAVERSAL = "graph_traversal"
    BULK_UPSERT = "bulk_upsert"
    MODEL_GENERATION = "model_generation"
    DURABLE_SUBMISSION = "durable_submission"


@dataclass(frozen=True)
class ObjectInfo:
    """Backend-neutral object identity and metadata."""

    bucket: str
    key: str
    size: int
    etag: str | None = None
    last_modified: datetime | None = None


@runtime_checkable
class Database(Protocol):
    """Minimal relational persistence lifecycle owned by one SDK client."""

    @property
    def capabilities(self) -> frozenset[str]: ...
    def session(self) -> AbstractContextManager[Any]: ...
    def transaction(self) -> AbstractContextManager[Any]: ...
    def check(self) -> None: ...
    def close(self) -> None: ...


@runtime_checkable
class ObjectStore(Protocol):
    """Object persistence kept separate from relational repositories."""

    @property
    def capabilities(self) -> frozenset[str]: ...
    def put_bytes(self, bucket: str, key: str, data: bytes) -> None: ...
    def get_bytes(self, bucket: str, key: str) -> bytes: ...
    def open(self, bucket: str, key: str) -> BinaryIO: ...
    def exists(self, bucket: str, key: str) -> bool: ...
    def delete(self, bucket: str, key: str) -> None: ...
    def list(self, bucket: str, prefix: str = "") -> Iterable[ObjectInfo]: ...
    def check(self, buckets: Iterable[str] = ()) -> None: ...
    def close(self) -> None: ...


@runtime_checkable
class GraphStore(Protocol):
    """Graph persistence kept distinct from relational and object storage."""

    @property
    def capabilities(self) -> frozenset[str]: ...
    def upsert_entity(self, entity: Entity) -> Entity: ...
    def get_entity(self, entity_id: str) -> Entity | None: ...
    def list_entities(self, *, type: str | None = None) -> list[Entity]: ...
    def upsert_relation(self, relation: Relation) -> Relation: ...
    def get_relation(self, relation_id: str) -> Relation | None: ...
    def list_relations(
        self, *, entity_id: str | None = None, type: str | None = None
    ) -> list[Relation]: ...
    def close(self) -> None: ...


@runtime_checkable
class ModelProvider(Protocol):
    """External inference provider owned by one configured client."""

    @property
    def identity(self) -> str: ...
    @property
    def capabilities(self) -> frozenset[str]: ...
    def generate(self, prompt: str, *, parameters: dict[str, Any] | None = None) -> Any: ...
    def close(self) -> None: ...


@runtime_checkable
class JobBackend(Protocol):
    """Lifecycle and capabilities for a future durable execution backend."""

    @property
    def capabilities(self) -> frozenset[str]: ...
    def create(self, job: Job) -> Job: ...
    def update(self, job_id: str, **changes: Any) -> Job: ...
    def get(self, job_id: str) -> Job | None: ...
    def list(self, *, limit: int = 100, offset: int = 0) -> list[Job]: ...
    def wait(self, job_id: str, *, timeout: float | None = None) -> Job: ...
    def cancel(self, job_id: str) -> Job: ...
    def recover_interrupted(self) -> int: ...
    def close(self) -> None: ...


@runtime_checkable
class VectorSearch(Protocol):
    """Vector indexing/query port, separate from relational metadata storage."""

    @property
    def capabilities(self) -> frozenset[str]: ...
    def upsert(
        self,
        namespace: str,
        vectors: Iterable[tuple[str, list[float], dict[str, Any]]],
    ) -> int: ...
    def query(
        self,
        namespace: str,
        vector: list[float],
        *,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[tuple[str, float, dict[str, Any]]]: ...
    def delete(self, namespace: str, identifiers: Iterable[str]) -> int: ...
    def close(self) -> None: ...


@runtime_checkable
class ContentParser(Protocol):
    """Parser adapter contract consumed through the per-client parser registry."""

    @property
    def name(self) -> str: ...
    @property
    def source_types(self) -> frozenset[str]: ...
    def parse(
        self,
        content: bytes,
        *,
        parameters: dict[str, Any] | None = None,
    ) -> Any: ...
