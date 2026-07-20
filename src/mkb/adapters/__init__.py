"""Default persistence adapters for the MKB SDK."""

from mkb.adapters.database import SQLAlchemyDatabase
from mkb.adapters.object_store import FileObjectStore, S3ObjectStore
from mkb.adapters.repositories import (
    SQLAlchemyArtifactRepository,
    SQLAlchemyCollectionRepository,
    SQLAlchemyExtractionSchemaRepository,
    SQLAlchemyProjectionRepository,
    SQLAlchemyRecordRepository,
    SQLAlchemySourceRepository,
)

__all__ = [
    "FileObjectStore",
    "S3ObjectStore",
    "SQLAlchemyArtifactRepository",
    "SQLAlchemyCollectionRepository",
    "SQLAlchemyDatabase",
    "SQLAlchemyExtractionSchemaRepository",
    "SQLAlchemyProjectionRepository",
    "SQLAlchemyRecordRepository",
    "SQLAlchemySourceRepository",
]
