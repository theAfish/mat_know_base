"""Resource owner for the deprecated module-level compatibility API.

New callers should create :class:`mkb.sdk.KnowledgeBase` directly.  This module
keeps the old ``mkb.api`` entry point operational without relying on ambient
context variables.
"""

from __future__ import annotations

from functools import lru_cache

from mkb.config import settings
from mkb.services.content_operations import ContentOperations
from mkb.services.workflow_operations import WorkflowOperations
from mkb.services.frame_operations import FrameOperations
from mkb.services.asset_operations import AssetOperations


def _resources():
    """Build one database and its configured object store."""
    from mkb.adapters import SQLAlchemyDatabase, create_object_store

    database = SQLAlchemyDatabase(settings.pg_dsn_sync)
    object_store = create_object_store(
        settings.object_store_backend,
        database=database,
        root=settings.object_store_root,
        endpoint=settings.s3_endpoint,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
    )
    return database, object_store


@lru_cache(maxsize=1)
def content_operations() -> ContentOperations:
    database, object_store = _resources()
    return ContentOperations(
        database,
        object_store,
        raw_bucket=settings.s3_bucket_raw,
        processed_bucket=settings.s3_bucket_processed,
    )


@lru_cache(maxsize=1)
def workflow_operations() -> WorkflowOperations:
    return WorkflowOperations(*_resources())


@lru_cache(maxsize=1)
def frame_operations() -> FrameOperations:
    return FrameOperations(*_resources())


@lru_cache(maxsize=1)
def asset_operations() -> AssetOperations:
    database, object_store = _resources()
    return AssetOperations(
        database,
        object_store,
        processed_bucket=settings.s3_bucket_processed,
    )
