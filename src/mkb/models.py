"""Typed, serializable models exposed by the public SDK."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
