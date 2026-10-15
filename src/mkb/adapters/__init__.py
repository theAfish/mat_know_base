"""Optional persistence adapters, imported only when selected by a consumer.

Importing a filesystem or graph adapter does not load SQLAlchemy, PostgreSQL or
legacy application services. Install the corresponding extra for each backend.
"""

from importlib import import_module

_EXPORTS = {
    "FileObjectStore": "mkb.adapters.object_store",
    "GenericArtifactRepository": "mkb.adapters.generic_repositories",
    "GenericCollectionGroupRepository": "mkb.adapters.generic_repositories",
    "GenericCollectionRepository": "mkb.adapters.generic_repositories",
    "GenericEvidenceRepository": "mkb.adapters.generic_repositories",
    "GenericExtractionSchemaRepository": "mkb.adapters.generic_repositories",
    "GenericFeedbackRepository": "mkb.adapters.generic_repositories",
    "GenericJobBackend": "mkb.adapters.generic_repositories",
    "GenericPostProcessorRepository": "mkb.adapters.generic_repositories",
    "GenericProjectionRepository": "mkb.adapters.generic_repositories",
    "GenericRecordRepository": "mkb.adapters.generic_repositories",
    "GenericSchemaManager": "mkb.adapters.generic_repositories",
    "GenericSkillRepository": "mkb.adapters.generic_repositories",
    "GenericSourceRepository": "mkb.adapters.generic_repositories",
    "InMemoryGraphStore": "mkb.adapters.graph",
    "Neo4jGraphStore": "mkb.adapters.graph",
    "ProcessorParser": "mkb.adapters.parsers",
    "S3ObjectStore": "mkb.adapters.object_store",
    "SQLAlchemyArtifactRepository": "mkb.adapters.repositories",
    "SQLAlchemyCollectionGroupRepository": "mkb.adapters.repositories",
    "SQLAlchemyCollectionRepository": "mkb.adapters.repositories",
    "SQLAlchemyDatabase": "mkb.adapters.database",
    "SQLAlchemyExtractionSchemaRepository": "mkb.adapters.repositories",
    "SQLAlchemyFeedbackRepository": "mkb.adapters.repositories",
    "SQLAlchemyLegacyJobBackend": "mkb.adapters.legacy_jobs",
    "SQLAlchemyPostProcessorRepository": "mkb.adapters.repositories",
    "SQLAlchemyProjectionRepository": "mkb.adapters.repositories",
    "SQLAlchemyRecordRepository": "mkb.adapters.repositories",
    "SQLAlchemySkillRepository": "mkb.adapters.repositories",
    "SQLAlchemySourceRepository": "mkb.adapters.repositories",
    "SQLAlchemyWorkflowRepository": "mkb.adapters.repositories",
    "SqlObjectStore": "mkb.adapters.sql_object_store",
    "TesseractParser": "mkb.adapters.parsers",
    "create_object_store": "mkb.adapters.object_store",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
