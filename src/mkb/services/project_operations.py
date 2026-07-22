"""Explicit resource adapter for retained materials project operations."""

from __future__ import annotations

from mkb.ports import Database, ObjectStore


class ProjectOperations:
    """Bind remaining ORM-backed project operations to one client resource set."""

    def __init__(self, database: Database, object_store: ObjectStore | None):
        self._database = database
        self._object_store = object_store

    def list_projects(self, limit: int = 50):
        from mkb.services.projects import list_projects

        return list_projects(limit, database=self._database)

    def delete_project(self, project_id, **kwargs):
        from mkb.services.projects import delete_project

        return delete_project(
            project_id,
            database=self._database,
            object_store=self._object_store,
            **kwargs,
        )

    def list_processed_assets(self, project_id=None, limit: int = 100):
        from mkb.services.assets import list_processed_assets

        return list_processed_assets(
            project_id,
            limit,
            database=self._database,
        )

    def list_assets(self, project_id=None, limit: int = 100):
        from mkb.services.assets import list_assets

        return list_assets(project_id, limit, database=self._database)
