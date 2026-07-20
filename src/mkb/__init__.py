"""mat-know-base: Scientific knowledge extraction and knowledge graph construction."""

from mkb.exceptions import (
    BackendUnavailableError,
    ConflictError,
    MKBError,
    NotFoundError,
    PipelineExecutionError,
    ProviderError,
    ValidationError,
)
from mkb.sdk import KnowledgeBase, MKBConfig
from mkb.ports import Database, ObjectInfo, ObjectStore
from mkb.models import (
    Artifact,
    Collection,
    ExtractionSchema,
    Projection,
    Record,
    Source,
    StorageReference,
)

__version__ = "0.1.0"

__all__ = [
    "Database",
    "Artifact",
    "BackendUnavailableError",
    "Collection",
    "ConflictError",
    "ExtractionSchema",
    "KnowledgeBase",
    "MKBError",
    "MKBConfig",
    "ObjectInfo",
    "ObjectStore",
    "NotFoundError",
    "PipelineExecutionError",
    "Projection",
    "Record",
    "ProviderError",
    "Source",
    "StorageReference",
    "ValidationError",
    "__version__",
]
