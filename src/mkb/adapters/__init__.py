"""Default persistence adapters for the MKB SDK."""

from mkb.adapters.database import SQLAlchemyDatabase
from mkb.adapters.graph import InMemoryGraphStore, Neo4jGraphStore
from mkb.adapters.legacy_jobs import SQLAlchemyLegacyJobBackend
from mkb.adapters.generic_repositories import (
    GenericArtifactRepository,
    GenericCollectionGroupRepository,
    GenericCollectionRepository,
    GenericExtractionSchemaRepository,
    GenericEvidenceRepository,
    GenericFeedbackRepository,
    GenericJobBackend,
    GenericProjectionRepository,
    GenericPostProcessorRepository,
    GenericRecordRepository,
    GenericSchemaManager,
    GenericSkillRepository,
    GenericSourceRepository,
)
from mkb.adapters.object_store import FileObjectStore, S3ObjectStore
from mkb.adapters.repositories import (
    SQLAlchemyArtifactRepository,
    SQLAlchemyCollectionGroupRepository,
    SQLAlchemyCollectionRepository,
    SQLAlchemyExtractionSchemaRepository,
    SQLAlchemyFeedbackRepository,
    SQLAlchemyPostProcessorRepository,
    SQLAlchemyProjectionRepository,
    SQLAlchemyRecordRepository,
    SQLAlchemySourceRepository,
    SQLAlchemySkillRepository,
    SQLAlchemyWorkflowRepository,
)

__all__ = [
    "FileObjectStore",
    "GenericArtifactRepository",
    "GenericCollectionGroupRepository",
    "GenericCollectionRepository",
    "GenericExtractionSchemaRepository",
    "GenericEvidenceRepository",
    "GenericFeedbackRepository",
    "GenericJobBackend",
    "GenericProjectionRepository",
    "GenericPostProcessorRepository",
    "GenericRecordRepository",
    "GenericSchemaManager",
    "GenericSkillRepository",
    "GenericSourceRepository",
    "InMemoryGraphStore",
    "Neo4jGraphStore",
    "S3ObjectStore",
    "SQLAlchemyArtifactRepository",
    "SQLAlchemyCollectionGroupRepository",
    "SQLAlchemyCollectionRepository",
    "SQLAlchemyDatabase",
    "SQLAlchemyLegacyJobBackend",
    "SQLAlchemyExtractionSchemaRepository",
    "SQLAlchemyFeedbackRepository",
    "SQLAlchemyPostProcessorRepository",
    "SQLAlchemyProjectionRepository",
    "SQLAlchemyRecordRepository",
    "SQLAlchemySourceRepository",
    "SQLAlchemySkillRepository",
    "SQLAlchemyWorkflowRepository",
]
