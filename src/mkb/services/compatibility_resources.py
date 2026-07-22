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


@lru_cache(maxsize=1)
def content_operations() -> ContentOperations:
    from mkb.adapters import S3ObjectStore, SQLAlchemyDatabase

    return ContentOperations(
        SQLAlchemyDatabase(settings.pg_dsn_sync),
        S3ObjectStore(
            endpoint_url=settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
        ),
        raw_bucket=settings.s3_bucket_raw,
        processed_bucket=settings.s3_bucket_processed,
    )


@lru_cache(maxsize=1)
def workflow_operations() -> WorkflowOperations:
    from mkb.adapters import S3ObjectStore, SQLAlchemyDatabase

    return WorkflowOperations(
        SQLAlchemyDatabase(settings.pg_dsn_sync),
        S3ObjectStore(
            endpoint_url=settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
        ),
    )


@lru_cache(maxsize=1)
def frame_operations() -> FrameOperations:
    from mkb.adapters import S3ObjectStore, SQLAlchemyDatabase

    return FrameOperations(
        SQLAlchemyDatabase(settings.pg_dsn_sync),
        S3ObjectStore(
            endpoint_url=settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
        ),
    )


@lru_cache(maxsize=1)
def asset_operations() -> AssetOperations:
    from mkb.adapters import S3ObjectStore, SQLAlchemyDatabase

    return AssetOperations(
        SQLAlchemyDatabase(settings.pg_dsn_sync),
        S3ObjectStore(endpoint_url=settings.s3_endpoint, access_key=settings.s3_access_key, secret_key=settings.s3_secret_key),
        processed_bucket=settings.s3_bucket_processed,
    )
