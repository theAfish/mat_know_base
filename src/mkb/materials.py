"""Materials-science application services layered on the generic SDK."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mkb.exceptions import ConflictError, ValidationError
from mkb.repositories import Workflows


class _BoundOperations:
    def __init__(self, operations: Any | None = None):
        self._operations = operations

    def _call(self, name: str, *args, **kwargs):
        if self._operations is None:
            raise ConflictError(
                f"Materials operation {name!r} is unavailable for this client"
            )
        return getattr(self._operations, name)(*args, **kwargs)


class MaterialFrames(_BoundOperations):
    """Legacy-compatible knowledge-frame reads through one configured client."""

    def list(self, *, status: str | None = None) -> list[dict]:
        return self._call("list_frames", status=status)

    def get(self, project_id: str) -> dict | None:
        return self._call("get_frame", project_id)

    def history(self, project_id: str) -> list[dict]:
        return self._call("get_extraction_history", project_id)


class MaterialSpaces(_BoundOperations):
    """Manage materials extraction spaces with their established payloads."""

    def __init__(
        self,
        operations: Any | None = None,
        *,
        file_loader: Callable[[str | Path], dict] | None = None,
    ):
        super().__init__(operations)
        self._file_loader = file_loader

    def create(self, **values) -> dict:
        return self._call("create_space", **values)

    def list(self) -> list[dict]:
        return self._call("list_spaces")

    def get(self, identifier: str) -> dict | None:
        return self._call("get_space", identifier)

    def update(self, space_id: str, **changes) -> dict:
        return self._call("update_space", space_id, **changes)

    def delete(self, space_id: str) -> dict:
        return self._call("delete_space", space_id)

    def import_file(self, path: str | Path) -> dict:
        if self._file_loader is None:
            raise ConflictError("Materials space file import is unavailable")
        return self._file_loader(path)


class MaterialProjections(_BoundOperations):
    """Run, inspect, review, and export materials projections."""

    def __init__(
        self,
        operations: Any | None = None,
        *,
        projection_exporter: Callable[..., dict] | None = None,
        space_exporter: Callable[..., dict] | None = None,
    ):
        super().__init__(operations)
        self._projection_exporter = projection_exporter
        self._space_exporter = space_exporter

    def run(self, *, space_id: str, **kwargs) -> dict:
        return self._call("project", space_id=space_id, **kwargs)

    def run_all(self, *, space_id: str, **kwargs) -> dict:
        return self._call("project_all", space_id=space_id, **kwargs)

    def list(self, **filters) -> list[dict]:
        return self._call("list_projections", **filters)

    def get(self, projection_id: str) -> dict | None:
        return self._call("get_projection", projection_id)

    def delete(self, projection_id: str) -> bool:
        return self._call("delete_projection", projection_id)

    def review(self, *, space_id: str, project_id: str, **kwargs) -> dict:
        return self._call(
            "review_projections",
            space_id=space_id,
            project_id=project_id,
            **kwargs,
        )

    def review_all(self, *, space_id: str, **kwargs) -> dict:
        return self._call("review_projections_all", space_id=space_id, **kwargs)

    def export_projection(
        self,
        projection_id: str,
        output: str | Path,
        *,
        overwrite: bool = False,
    ) -> dict:
        if self._projection_exporter is None:
            raise ConflictError("Materials projection export is unavailable")
        try:
            return self._projection_exporter(
                projection_id, output, overwrite=overwrite
            )
        except Exception as exc:
            raise ValidationError(str(exc)) from exc

    def export_space(
        self,
        space: str,
        output: str | Path,
        *,
        overwrite: bool = False,
    ) -> dict:
        if self._space_exporter is None:
            raise ConflictError("Materials space export is unavailable")
        try:
            return self._space_exporter(space, output, overwrite=overwrite)
        except Exception as exc:
            raise ValidationError(str(exc)) from exc

    def export(
        self,
        projection_id: str,
        output: str | Path,
        *,
        format: str = "yaml",
        overwrite: bool = False,
    ) -> dict:
        return self._call(
            "export_projection",
            projection_id,
            output,
            format=format,
            overwrite=overwrite,
        )

    def export_all(
        self,
        space: str,
        output: str | Path,
        *,
        format: str = "yaml",
        overwrite: bool = False,
    ) -> dict:
        return self._call(
            "export_space_projections",
            space,
            output,
            format=format,
            overwrite=overwrite,
        )


class MaterialGraph(_BoundOperations):
    """Preserve the materials graph operation payloads used by the CLI and UI."""

    def extract(self, **kwargs) -> dict:
        return self._call("extract_knowledge_graph", **kwargs)

    def clear(self, **kwargs) -> dict:
        return self._call("clear_knowledge_graphs", **kwargs)

    def get(self, **kwargs) -> dict:
        return self._call("get_knowledge_graph", **kwargs)

    def review(self, **kwargs) -> dict:
        return self._call("review_knowledge_graph", **kwargs)

    def review_counts(self) -> dict:
        return self._call("get_graph_review_counts")


class MaterialFeedback(_BoundOperations):
    """Application feedback operations that retain legacy review semantics."""

    def list(self, **filters) -> list[dict]:
        return self._call("list_feedback", **filters)

    def summary(self, project_id: str) -> dict:
        return self._call("get_feedback_summary", project_id)

    def review(self, **kwargs) -> dict:
        return self._call("review_feedback", **kwargs)

    def resolve(self, **kwargs) -> dict:
        return self._call("resolve_feedback", **kwargs)


class MaterialLibrary(_BoundOperations):
    """Materials library search and manual processed-data registration."""

    def search(self, **kwargs) -> dict:
        return self._call("search_library", **kwargs)

    def link_processed(self, **kwargs) -> dict:
        return self._call("link_manual_processed_data", **kwargs)


class MaterialProjects(_BoundOperations):
    """Project and project-group operations with React-compatible payloads."""

    def list(self, *, limit: int = 50) -> list[dict]:
        return self._call("list_projects", limit=limit)

    def rename(self, project_id: str, label: str, **kwargs) -> dict:
        return self._call("rename_project", project_id, label, **kwargs)

    def delete(self, project_id: str, **kwargs) -> dict:
        return self._call("delete_project", project_id, **kwargs)

    def list_assets(self, *, project_id: str, limit: int = 100) -> list[dict]:
        return self._call("list_assets", project_id=project_id, limit=limit)

    def list_processed_assets(
        self,
        *,
        project_id: str,
        limit: int = 100,
    ) -> list[dict]:
        return self._call(
            "list_processed_assets", project_id=project_id, limit=limit
        )

    def list_groups(self) -> list[dict]:
        return self._call("list_project_groups")

    def create_group(self, name: str, **kwargs) -> dict:
        return self._call("create_project_group", name, **kwargs)

    def update_group(self, group_id: str, **changes) -> dict:
        return self._call("update_project_group", group_id, **changes)

    def delete_group(self, group_id: str) -> dict:
        return self._call("delete_project_group", group_id)

    def assign_group(self, project_ids: list[str], group_id: str | None) -> dict:
        return self._call("assign_projects_to_group", project_ids, group_id)


class MaterialWorkflows(_BoundOperations):
    """Typed workflow records plus materials review and maintenance operations."""

    def __init__(
        self,
        records: Workflows | None = None,
        operations: Any | None = None,
        *,
        schema_curator: Callable[..., Any] | None = None,
    ):
        super().__init__(operations)
        self._records = records
        self._schema_curator = schema_curator

    def get(self, workflow_id):
        if self._records is None:
            raise ConflictError("Materials workflow records are unavailable")
        return self._records.get(workflow_id)

    def require(self, workflow_id):
        if self._records is None:
            raise ConflictError("Materials workflow records are unavailable")
        return self._records.require(workflow_id)

    def list(self, **filters):
        if self._records is None:
            raise ConflictError("Materials workflow records are unavailable")
        return self._records.list(**filters)

    def review(self, extraction_id: str, **kwargs) -> dict:
        return self._call("review_raw_workflow", extraction_id, **kwargs)

    def correct(self, extraction_id: str, graph: dict, **kwargs) -> dict:
        return self._call("correct_raw_workflow", extraction_id, graph, **kwargs)

    def readiness(self, project_id: str) -> dict:
        return self._call("get_raw_workflow_extraction_readiness", project_id)

    def list_raw(self, project_id: str, **kwargs) -> list[dict]:
        return self._call("list_raw_workflows", project_id, **kwargs)

    def get_raw(self, project_id: str, **kwargs) -> dict | None:
        return self._call("get_raw_workflow", project_id, **kwargs)

    def delete_raw_version(self, project_id: str, version: int) -> dict:
        return self._call("delete_raw_workflow_version", project_id, version)

    def list_canonical(self, project_id: str, **kwargs) -> list[dict]:
        return self._call("list_canonical_workflows", project_id, **kwargs)

    def get_canonical(self, project_id: str, **kwargs) -> dict | None:
        return self._call("get_canonical_workflow", project_id, **kwargs)

    def curate_schema(self, **kwargs):
        if self._schema_curator is None:
            raise ConflictError("Workflow schema curation is unavailable")
        result = self._schema_curator(**kwargs)
        return asyncio.run(result) if asyncio.iscoroutine(result) else result

    def list_schema_proposals(self, *, status: str | None = "pending") -> list[dict]:
        return self._call("list_schema_proposals", status=status)

    def review_schema_proposal(self, proposal_id: str, **kwargs) -> dict:
        return self._call("review_schema_proposal", proposal_id, **kwargs)

    def edit_schema_proposal(self, proposal_id: str, **changes) -> dict:
        return self._call("edit_schema_proposal", proposal_id, **changes)

    def schema_proposal_revisions(self, proposal_id: str) -> list[dict]:
        return self._call("get_schema_proposal_revisions", proposal_id)

    def schema_status(self) -> dict:
        return self._call("get_workflow_schema_status")

    def schedule_reextraction(self, project_id: str, **kwargs) -> dict:
        return self._call("schedule_workflow_reextraction", project_id, **kwargs)

    def list_tasks(self, **filters) -> list[dict]:
        return self._call("list_workflow_maintenance_tasks", **filters)

    def run_task(self, task_id: str, **kwargs) -> dict:
        return self._call("run_workflow_maintenance_task", task_id, **kwargs)

    def rebuild_indexes(self, project_id: str | None = None) -> dict:
        return self._call("rebuild_workflow_indexes", project_id)

    def search(self, *args, **kwargs) -> list[dict]:
        return self._call("search_canonical_workflows", *args, **kwargs)


@dataclass(frozen=True)
class Materials:
    """Materials-specific grouped services preserving legacy application behavior."""

    frames: MaterialFrames = field(default_factory=MaterialFrames)
    spaces: MaterialSpaces = field(default_factory=MaterialSpaces)
    projections: MaterialProjections = field(default_factory=MaterialProjections)
    workflows: MaterialWorkflows = field(default_factory=MaterialWorkflows)
    graph: MaterialGraph = field(default_factory=MaterialGraph)
    feedback: MaterialFeedback = field(default_factory=MaterialFeedback)
    library: MaterialLibrary = field(default_factory=MaterialLibrary)
    projects: MaterialProjects = field(default_factory=MaterialProjects)
