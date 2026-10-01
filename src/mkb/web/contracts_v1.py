"""Stable integration contracts for the MKB v1 API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ProvenanceReference(BaseModel):
    """Logical reference to an immutable resource owned by another service."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    service: Literal["mkb", "kdg", "oaw"]
    resource_type: str = Field(min_length=1)
    resource_id: str = Field(min_length=1)
    revision: str | None = None


class IntegrationEvent(BaseModel):
    """Versioned event envelope used by durable cross-service feeds."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    occurred_at: datetime
    producer: Literal["mkb"] = "mkb"
    subject: ProvenanceReference
    actor: ProvenanceReference | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    schema_version: Literal["1"] = "1"
    payload: dict[str, Any] = Field(default_factory=dict)


class ApiCapability(BaseModel):
    """One stable resource family exposed by this API version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    path: str
    status: Literal["available", "planned"]


class ApiDiscovery(BaseModel):
    """Machine-readable compatibility handshake for service connectors."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    service: Literal["mkb"] = "mkb"
    api_version: Literal["v1"] = "v1"
    capabilities: tuple[ApiCapability, ...]
