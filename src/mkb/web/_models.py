"""Pydantic request/response models shared across non-upload routers.

Upload-related models (UploadProject, UploadFileItem, UploadInitResponse, ...)
intentionally live in ``mkb.web.api_server`` because ``tests/test_api_upload_ingest.py``
imports them from there. Don't move them without also updating those tests.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class SpaceRef(BaseModel):
    space_id: str


class ProjectionRunRequest(BaseModel):
    space_id: str
    source_type: str = "frame"  # "frame" | "markdown"


class SpaceCreateRequest(BaseModel):
    name: str
    domain: str
    extraction_schema: dict
    system_prompt: str
    field_descriptions: dict
    description: str | None = None
    purpose: str = "tabular_database"
    review_prompt: str | None = None
    review_trackable: bool = True


class SpaceUpdateRequest(BaseModel):
    name: str | None = None
    domain: str | None = None
    description: str | None = None
    purpose: str | None = None
    extraction_schema: dict | None = None
    system_prompt: str | None = None
    field_descriptions: dict | None = None
    review_prompt: str | None = None
    review_trackable: bool | None = None


class ProjectionReviewRequest(BaseModel):
    space_id: str
    project_id: str | None = None
    # Restrict review to a subset of projects in the space. When None/empty,
    # behaves as before (all projects with completed projections).
    project_ids: list[str] | None = None
    # Execution mode:
    #   "per_project" — default. One reviewer session per project (legacy).
    #   "session"     — one reviewer session sees ALL selected projects.
    mode: str = "per_project"


class FeedbackResolveRequest(BaseModel):
    status: str
    notes: str = ""


class FeedbackReviewRequest(BaseModel):
    project_id: str | None = None


class GraphReviewRequest(BaseModel):
    mode: str = "auto"
    seed_count: int = 10


class AssistantChatRequest(BaseModel):
    message: str


class ProjectUpdateRequest(BaseModel):
    label: str


class ProjectGroupCreate(BaseModel):
    name: str
    description: str | None = None
    color: str | None = None
    display_order: int | None = None


class ProjectGroupUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    color: str | None = None
    display_order: int | None = None


class ProjectGroupAssign(BaseModel):
    project_ids: list[str]
    group_id: str | None = None  # None ungroups


class SettingsUpdateRequest(BaseModel):
    pdf_backend: str | None = None
    mineru_api_base: str | None = None
    mineru_api_token: str | None = None
    mineru_api_model_version: str | None = None
    mineru_api_language: str | None = None
    mineru_api_enable_ocr: bool | None = None
    mineru_api_enable_formula: bool | None = None
    mineru_api_enable_table: bool | None = None
    mineru_api_timeout: int | None = None
    extraction_model: str | None = None
    vision_model: str | None = None
    log_level: str | None = None
    max_concurrent_jobs: int | None = None


# Helper for typed Any field usage in some response shapes.
_AnyDict = dict[str, Any]
