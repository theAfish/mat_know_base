"""Default persistence adapters for the MKB SDK."""

from mkb.adapters.database import SQLAlchemyDatabase
from mkb.adapters.graph import InMemoryGraphStore
from mkb.adapters.generic_repositories import (
    GenericArtifactRepository,
    GenericCollectionRepository,
    GenericExtractionSchemaRepository,
    GenericProjectionRepository,
    GenericRecordRepository,
    GenericSchemaManager,
    GenericSourceRepository,
)
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
    "GenericArtifactRepository",
    "GenericCollectionRepository",
    "GenericExtractionSchemaRepository",
    "GenericProjectionRepository",
    "GenericRecordRepository",
    "GenericSchemaManager",
    "GenericSourceRepository",
    "InMemoryGraphStore",
    "S3ObjectStore",
    "SQLAlchemyArtifactRepository",
    "SQLAlchemyCollectionRepository",
    "SQLAlchemyDatabase",
    "SQLAlchemyExtractionSchemaRepository",
    "SQLAlchemyProjectionRepository",
    "SQLAlchemyRecordRepository",
    "SQLAlchemySourceRepository",
]
