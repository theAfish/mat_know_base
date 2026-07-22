"""Explicit resource adapter for projection agent execution."""

from __future__ import annotations

import uuid

from mkb.agents.runtime import AgentRuntime
from mkb.ports import Database, ObjectStore


class ProjectionOperations:
    """Run projections with persistence owned by one SDK client."""

    def __init__(self, database: Database, object_store: ObjectStore | None):
        self._runtime = AgentRuntime(database, object_store)

    def project(self, space_id: str | uuid.UUID, **kwargs) -> dict:
        from mkb.agents.projection import run_projection

        values = dict(kwargs)
        frame_id = values.pop("frame_id", None)
        project_id = values.pop("project_id", None)
        return run_projection(
            uuid.UUID(str(space_id)),
            uuid.UUID(str(frame_id)) if frame_id else None,
            project_id=uuid.UUID(str(project_id)) if project_id else None,
            runtime=self._runtime,
            **values,
        )

    def project_all(self, space_id: str | uuid.UUID, **kwargs) -> dict:
        from mkb.agents.projection import run_projection_all

        return run_projection_all(
            uuid.UUID(str(space_id)), runtime=self._runtime, **kwargs
        )

    def review_projections(
        self, *, space_id: str | uuid.UUID, project_id: str | uuid.UUID, **kwargs
    ) -> dict:
        from mkb.agents.projection_reviewer import run_projection_review

        return run_projection_review(
            uuid.UUID(str(space_id)),
            uuid.UUID(str(project_id)),
            runtime=self._runtime,
            **kwargs,
        )

    def review_projections_all(self, *, space_id: str | uuid.UUID, **kwargs) -> dict:
        from mkb.agents.projection_reviewer import run_projection_review_all

        return run_projection_review_all(
            uuid.UUID(str(space_id)), runtime=self._runtime, **kwargs
        )
