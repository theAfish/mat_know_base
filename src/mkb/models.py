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
