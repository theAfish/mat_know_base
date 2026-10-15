"""Backend-neutral storage composition for embedding MKB.

Repositories exchange public Pydantic models and UUIDs. They can wrap an existing
ORM, a document database, a remote service or an in-memory store. No SQL session,
engine, database URL or global configuration is required.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from mkb.managed_services import PostProcessorRepository
from mkb.repositories import (
    ArtifactRepository,
    CollectionGroupRepository,
    CollectionRepository,
    EvidenceRepository,
    ExtractionSchemaRepository,
    ProjectionRepository,
    RecordRepository,
    SourceRepository,
)


@runtime_checkable
class SchemaManager(Protocol):
    """Optional, explicit schema lifecycle; construction never runs migrations."""

    def initialize(self) -> int: ...
    def version(self) -> int | None: ...


@dataclass(frozen=True)
class RepositoryBundle:
    """Repositories supplied by the host application.

    Omit repositories that your integration does not use. Read-only repositories
    remain valid; writable implementations follow the ``Writable*Repository``
    protocols in :mod:`mkb.repositories`.

    ``transaction`` optionally opens a unit of work and yields another bundle
    bound to that unit. It must commit on normal exit and roll back on exceptions;
    its bundle must contain collections, records, schemas and projections, plus
    any sources, artifacts, evidence and collection groups configured here. This
    contract applies equally to SQL, document stores and remote storage services.

    Resources belong to the caller by default. When ``close_resources=True`` is
    passed to ``KnowledgeBase.from_repositories``, ``close`` is invoked once on
    client shutdown. Use one callback for a shared database/connection pool rather
    than closing it separately for every repository.
    """

    collections: CollectionRepository | None = None
    sources: SourceRepository | None = None
    artifacts: ArtifactRepository | None = None
    records: RecordRepository | None = None
    schemas: ExtractionSchemaRepository | None = None
    projections: ProjectionRepository | None = None
    evidence: EvidenceRepository | None = None
    collection_groups: CollectionGroupRepository | None = None
    post_processors: PostProcessorRepository | None = None
    schema_manager: SchemaManager | None = None
    transaction: Callable[[], AbstractContextManager[RepositoryBundle]] | None = field(
        default=None, repr=False, compare=False
    )
    close: Callable[[], None] | None = field(default=None, repr=False, compare=False)
