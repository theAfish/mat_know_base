"""Typed, serializable models exposed by the public SDK."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

PageItem = TypeVar("PageItem")


class Collection(BaseModel):
    """Logical grouping of sources, backed by a legacy project during migration."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    name: str | None = None
    source_path: str | None = None
    source_count: int = 0
    group_id: uuid.UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class StorageReference(BaseModel):
    """Location of content in a configured object-store adapter."""

    model_config = ConfigDict(frozen=True)

    bucket: str
    key: str


class Source(BaseModel):
    """Original input registered in one or more collections."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    filename: str
    media_type: str
    size: int
    sha256: str
    status: str
    storage: StorageReference
    collection_ids: tuple[uuid.UUID, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Artifact(BaseModel):
    """Processed output derived from one source."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    source_id: uuid.UUID
    processing_type: str
    format: str
    size: int
    sha256: str
    source_sha256: str
    storage: StorageReference
    primary_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Record(BaseModel):
    """Extracted knowledge record backed by an existing knowledge frame."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    collection_id: uuid.UUID
    status: str
    data: Any = Field(default_factory=dict)
    summary: str | None = None
    review_count: int = 0
    version: int = 0
    extracted_at: datetime | None = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExtractionSchema(BaseModel):
    """Versioned definition for projecting records into a custom data shape."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    name: str
    description: str | None = None
    domain: str
    purpose: str
    definition: dict[str, Any] = Field(default_factory=dict)
    system_prompt: str
    field_descriptions: dict[str, Any] = Field(default_factory=dict)
    review_prompt: str | None = None
    review_trackable: bool = True
    review_allow_search: bool = False
    review_search_tools: tuple[str, ...] = ()
    post_processors: tuple[Any, ...] = ()
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Projection(BaseModel):
    """Raw, lossless projection of a record through an extraction schema."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    schema_id: uuid.UUID
    record_id: uuid.UUID
    collection_id: uuid.UUID
    source_type: str
    status: str
    data: Any = Field(default_factory=dict)
    validation: dict[str, Any] | None = None
    notes: str | None = None
    extracted_at: datetime | None = None
    schema_version: int
    review_count: int = 0
    review_notes: str | None = None
    reviewed_at: datetime | None = None
    deleted_at: datetime | None = None
    superseded_by_id: uuid.UUID | None = None
    supersedes_ids: tuple[str, ...] = ()
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Entity(BaseModel):
    """Backend-neutral node in a knowledge graph."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    type: str
    name: str
    properties: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Relation(BaseModel):
    """Directed, typed edge between two graph entities."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    source_id: uuid.UUID
    target_id: uuid.UUID
    type: str
    properties: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class Evidence(BaseModel):
    """Lossless provenance linking an SDK output to source material."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    output_type: str
    output_id: uuid.UUID
    source_id: uuid.UUID | None = None
    artifact_id: uuid.UUID | None = None
    locator: dict[str, Any] = Field(default_factory=dict)
    excerpt: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class Job(BaseModel):
    """Serializable state for a submitted background operation."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    kind: str
    status: str
    label: str | None = None
    idempotency_key: str | None = None
    progress: float | None = Field(default=None, ge=0.0, le=1.0)
    message: str | None = None
    result: Any = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class Page(BaseModel, Generic[PageItem]):
    """Bounded result page with stable pagination metadata."""

    model_config = ConfigDict(frozen=True)

    items: tuple[PageItem, ...] = ()
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    total: int | None = Field(default=None, ge=0)
    next_offset: int | None = Field(default=None, ge=0)


class OperationReceipt(BaseModel):
    """Typed acknowledgement for an accepted or completed mutation."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    operation: str
    status: str
    resource_type: str | None = None
    resource_id: uuid.UUID | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
