"""Explicit resource adapter for materials workflow operations."""

from __future__ import annotations

from mkb.ports import Database, ObjectStore


class WorkflowOperations:
    """Expose workflow operations without environment-bound persistence."""

    def __init__(self, database: Database, object_store: ObjectStore | None):
        self._database = database
        self._object_store = object_store

    def extract_raw_workflow(self, project_id, **kwargs):
        from mkb.services.workflows.extraction import extract_raw_workflow

        return extract_raw_workflow(
            project_id,
            database=self._database,
            object_store=self._object_store,
            **kwargs,
        )

    def get_raw_workflow_extraction_readiness(self, project_id, **kwargs):
        from mkb.services.workflows.extraction import get_raw_workflow_extraction_readiness

        return get_raw_workflow_extraction_readiness(
            project_id, database=self._database, **kwargs
        )

    def list_raw_workflows(self, project_id, **kwargs):
        from mkb.services.workflows.extraction import list_raw_workflows

        return list_raw_workflows(project_id, database=self._database, **kwargs)

    def get_raw_workflow(self, project_id, **kwargs):
        from mkb.services.workflows.extraction import get_raw_workflow

        return get_raw_workflow(project_id, database=self._database, **kwargs)

    def delete_raw_workflow_version(self, project_id, version, **kwargs):
        from mkb.services.workflows.extraction import delete_raw_workflow_version

        return delete_raw_workflow_version(
            project_id, version, database=self._database, **kwargs
        )

    def review_raw_workflow(self, extraction_id, **kwargs):
        from mkb.services.workflows.extraction import review_raw_workflow

        return review_raw_workflow(extraction_id, database=self._database, **kwargs)

    def correct_raw_workflow(self, extraction_id, graph, **kwargs):
        from mkb.services.workflows.extraction import correct_raw_workflow

        return correct_raw_workflow(
            extraction_id, graph, database=self._database, **kwargs
        )

    def canonicalize_workflow(self, project_id, **kwargs):
        from mkb.services.workflows.legacy_canonicalization import canonicalize_workflow

        return canonicalize_workflow(
            project_id,
            database=self._database,
            object_store=self._object_store,
            **kwargs,
        )

    def list_canonical_workflows(self, project_id, **kwargs):
        from mkb.services.workflows.legacy_canonicalization import list_canonical_workflows

        return list_canonical_workflows(project_id, database=self._database, **kwargs)

    def get_canonical_workflow(self, project_id, **kwargs):
        from mkb.services.workflows.legacy_canonicalization import get_canonical_workflow

        return get_canonical_workflow(project_id, database=self._database, **kwargs)

    def delete_canonical_workflow_version(self, project_id, version, **kwargs):
        from mkb.services.workflows.legacy_canonicalization import delete_canonical_workflow_version

        return delete_canonical_workflow_version(
            project_id, version, database=self._database, **kwargs
        )

    def rebuild_workflow_indexes(self, **kwargs):
        from mkb.services.workflows.indexing import rebuild_workflow_indexes

        return rebuild_workflow_indexes(database=self._database, **kwargs)

    def search_canonical_workflows(self, **kwargs):
        from mkb.services.workflows.indexing import search_canonical_workflows

        return search_canonical_workflows(database=self._database, **kwargs)

    def __getattr__(self, name):
        """Bind the remaining workflow-service functions by their public name."""
        modules = (
            "mkb.services.workflows.maintenance",
            "mkb.services.workflows.schema_review",
        )
        for module_name in modules:
            module = __import__(module_name, fromlist=[name])
            operation = getattr(module, name, None)
            if callable(operation):
                def bound(*args, _operation=operation, _module=module_name, **kwargs):
                    resources = {"database": self._database}
                    if _module.endswith("maintenance"):
                        resources["object_store"] = self._object_store
                    return _operation(*args, **resources, **kwargs)
                return bound
        raise AttributeError(name)
