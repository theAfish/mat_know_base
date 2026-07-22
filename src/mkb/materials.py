"""Materials-science application services layered on the generic SDK."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mkb.exceptions import ConflictError, NotFoundError, ValidationError
from mkb.managed_services import Feedback
from mkb.repositories import (
    Collections,
    ExtractionSchemas,
    Projections,
    Records,
    Sources,
    Workflows,
)


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

    def __init__(
        self,
        operations: Any | None = None,
        *,
        records: Records | None = None,
        history_reader: Callable[[str], list[dict]] | None = None,
    ):
        super().__init__(operations)
        self._records = records
        self._history_reader = history_reader

    def list(self, *, status: str | None = None) -> list[dict]:
        if self._records is not None:
            return [
                {
                    "frame_id": str(record.id),
                    "project_id": str(record.collection_id),
                    "status": record.status,
                    "times_checked": record.review_count,
                    "extraction_version": record.version,
                    "extracted_at": (
                        record.extracted_at.isoformat() if record.extracted_at else None
                    ),
                    "extraction_summary": record.summary,
                }
                for record in self._records.list(status=status, limit=1000)
            ]
        return self._call("list_frames", status=status)

    def get(self, project_id: str) -> dict | None:
        if self._records is not None:
            record = self._records.get_for_collection(project_id)
            if record is None:
                return None
            return {
                "frame_id": str(record.id),
                "project_id": str(record.collection_id),
                "status": record.status,
                "content": record.data,
                "extraction_summary": record.summary,
                "times_checked": record.review_count,
                "extraction_version": record.version,
                "extracted_at": record.extracted_at.isoformat() if record.extracted_at else None,
                "source_metadata": record.source_metadata,
                "agent_annotations": record.annotations,
                "created_at": record.created_at.isoformat() if record.created_at else None,
                "updated_at": record.updated_at.isoformat() if record.updated_at else None,
            }
        return self._call("get_frame", project_id)

    def history(self, project_id: str) -> list[dict]:
        if self._history_reader is not None:
            return self._history_reader(project_id)
        return self._call("get_extraction_history", project_id)


class MaterialSpaces(_BoundOperations):
    """Manage materials extraction spaces with their established payloads."""

    def __init__(
        self,
        operations: Any | None = None,
        *,
        schemas: ExtractionSchemas | None = None,
        file_loader: Callable[[str | Path], dict] | None = None,
    ):
        super().__init__(operations)
        self._schemas = schemas
        self._file_loader = file_loader

    @staticmethod
    def _payload(schema) -> dict:
        return {
            "space_id": str(schema.id),
            "name": schema.name,
            "description": schema.description,
            "domain": schema.domain,
            "purpose": schema.purpose,
            "extraction_schema": schema.definition,
            "system_prompt": schema.system_prompt,
            "field_descriptions": schema.field_descriptions,
            "review_prompt": schema.review_prompt,
            "review_trackable": schema.review_trackable,
            "review_allow_search": schema.review_allow_search,
            "review_search_tools": list(schema.review_search_tools),
            "post_processors": list(schema.post_processors),
            "version": schema.version,
            "created_at": schema.created_at.isoformat() if schema.created_at else None,
            "updated_at": schema.updated_at.isoformat() if schema.updated_at else None,
        }

    def create(self, **values) -> dict:
        if self._schemas is not None:
            schema = self._schemas.create(
                name=values["name"],
                domain=values["domain"],
                definition=values["extraction_schema"],
                system_prompt=values["system_prompt"],
                description=values.get("description"),
                purpose=values.get("purpose", "tabular_database"),
                field_descriptions=values.get("field_descriptions"),
            )
            return {
                "space_id": str(schema.id),
                "name": schema.name,
                "purpose": schema.purpose,
            }
        return self._call("create_space", **values)

    def list(self) -> list[dict]:
        if self._schemas is not None:
            return [self._payload(schema) for schema in self._schemas.list(limit=1000)]
        return self._call("list_spaces")

    def get(self, identifier: str) -> dict | None:
        if self._schemas is not None:
            schema = self._schemas.get(identifier)
            return self._payload(schema) if schema is not None else None
        return self._call("get_space", identifier)

    def update(self, space_id: str, **changes) -> dict:
        if self._schemas is not None:
            allowed = {
                "name", "description", "domain", "purpose", "system_prompt",
                "field_descriptions",
            }
            values = {key: value for key, value in changes.items() if key in allowed}
            if "extraction_schema" in changes:
                values["definition"] = changes["extraction_schema"]
            schema = self._schemas.update(space_id, **values)
            return {
                "space_id": str(schema.id),
                "version": schema.version,
                "purpose": schema.purpose,
            }
        return self._call("update_space", space_id, **changes)

    def delete(self, space_id: str) -> dict:
        if self._schemas is not None:
            schema = self._schemas.require(space_id)
            self._schemas.delete(space_id)
            return {"ok": True, "deleted": schema.name}
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
        projections: Projections | None = None,
        runner: Any | None = None,
        projection_exporter: Callable[..., dict] | None = None,
        space_exporter: Callable[..., dict] | None = None,
    ):
        super().__init__(operations)
        self._projections = projections
        self._runner = runner
        self._projection_exporter = projection_exporter
        self._space_exporter = space_exporter

    @staticmethod
    def _payload(projection, *, include_data: bool = True) -> dict:
        item = {
            "projection_id": str(projection.id),
            "space_id": str(projection.schema_id),
            "frame_id": str(projection.record_id),
            "project_id": str(projection.collection_id),
            "status": projection.status,
            "agent_notes": projection.notes,
            "extracted_at": projection.extracted_at.isoformat() if projection.extracted_at else None,
            "created_at": projection.created_at.isoformat() if projection.created_at else None,
            "space_version": projection.schema_version,
            "source_type": projection.source_type,
            "times_reviewed": projection.review_count,
            "review_notes": projection.review_notes,
            "reviewed_at": projection.reviewed_at.isoformat() if projection.reviewed_at else None,
            "superseded_by_id": str(projection.superseded_by_id) if projection.superseded_by_id else None,
            "supersedes_ids": list(projection.supersedes_ids),
        }
        if include_data:
            item["data"] = projection.data
            item["validation_result"] = projection.validation
        return item

    def run(self, *, space_id: str, **kwargs) -> dict:
        if self._runner is not None:
            return self._runner.project(space_id, **kwargs)
        return self._call("project", space_id=space_id, **kwargs)

    def run_all(self, *, space_id: str, **kwargs) -> dict:
        if self._runner is not None:
            return self._runner.project_all(space_id, **kwargs)
        return self._call("project_all", space_id=space_id, **kwargs)

    def list(self, **filters) -> list[dict]:
        if self._projections is not None:
            mapping = {
                "space_id": "schema_id",
                "frame_id": "record_id",
                "project_id": "collection_id",
            }
            values = {
                mapping.get(key, key): value
                for key, value in filters.items()
                if key in {*mapping, "newest_only", "include_history"}
            }
            include_data = bool(filters.get("include_data", False))
            values["include_history"] = bool(values.get("include_history", False))
            rows = self._projections.list(limit=1000, **values)
            return [self._payload(row, include_data=include_data) for row in rows]
        return self._call("list_projections", **filters)

    def get(self, projection_id: str) -> dict | None:
        if self._projections is not None:
            projection = self._projections.get(projection_id)
            return self._payload(projection) if projection is not None else None
        return self._call("get_projection", projection_id)

    def delete(self, projection_id: str) -> bool:
        return self._call("delete_projection", projection_id)

    def review(self, *, space_id: str, project_id: str, **kwargs) -> dict:
        if self._runner is not None:
            return self._runner.review_projections(
                space_id=space_id, project_id=project_id, **kwargs
            )
        return self._call(
            "review_projections",
            space_id=space_id,
            project_id=project_id,
            **kwargs,
        )

    def review_all(self, *, space_id: str, **kwargs) -> dict:
        if self._runner is not None:
            return self._runner.review_projections_all(space_id=space_id, **kwargs)
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

    def __init__(
        self,
        operations: Any | None = None,
        *,
        feedback: Feedback | None = None,
        reviewer: Callable[..., dict] | None = None,
    ):
        super().__init__(operations)
        self._feedback = feedback
        self._reviewer = reviewer

    @staticmethod
    def _payload(item) -> dict:
        return {
            "feedback_id": str(item.id),
            "source_agent": item.source_agent,
            "source_projection_id": str(item.source_projection_id) if item.source_projection_id else None,
            "target_frame_id": str(item.target_record_id),
            "target_project_id": str(item.target_collection_id),
            "category": item.category,
            "field_path": item.field_path,
            "question": item.question,
            "context": item.context,
            "status": item.status,
            "resolution_notes": item.resolution_notes,
            "resolved_by": item.resolved_by,
            "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }

    def list(self, **filters) -> list[dict]:
        if self._feedback is not None:
            project_id = filters.pop("project_id", None)
            return [
                self._payload(item)
                for item in self._feedback.list(
                    collection_id=project_id,
                    status=filters.get("status"),
                    limit=filters.get("limit", 1000),
                )
            ]
        return self._call("list_feedback", **filters)

    def summary(self, project_id: str) -> dict:
        if self._feedback is not None:
            rows = self._feedback.list(collection_id=project_id, limit=1000)
            by_status: dict[str, int] = {}
            by_category: dict[str, int] = {}
            for item in rows:
                by_status[item.status] = by_status.get(item.status, 0) + 1
                by_category[item.category] = by_category.get(item.category, 0) + 1
            return {"total": len(rows), "by_status": by_status, "by_category": by_category}
        return self._call("get_feedback_summary", project_id)

    def review(self, **kwargs) -> dict:
        if self._reviewer is not None:
            return self._reviewer(**kwargs)
        return self._call("review_feedback", **kwargs)

    def resolve(self, **kwargs) -> dict:
        if self._feedback is not None:
            item = self._feedback.resolve(
                kwargs["feedback_id"],
                status=kwargs["status"],
                notes=kwargs["notes"],
            )
            return {"feedback_id": str(item.id), "status": item.status}
        return self._call("resolve_feedback", **kwargs)


class MaterialLibrary(_BoundOperations):
    """Materials library search and manual processed-data registration."""

    def search(self, **kwargs) -> dict:
        return self._call("search_library", **kwargs)

    def link_processed(self, **kwargs) -> dict:
        return self._call("link_manual_processed_data", **kwargs)


class MaterialProjects(_BoundOperations):
    """Project and project-group operations with React-compatible payloads."""

    def __init__(
        self,
        operations: Any | None = None,
        *,
        collections: Collections | None = None,
        sources: Sources | None = None,
    ):
        super().__init__(operations)
        self._collections = collections
        self._sources = sources

    @staticmethod
    def _group_payload(group) -> dict:
        return {
            "group_id": str(group.id),
            "name": group.name,
            "description": group.description,
            "color": group.color,
            "display_order": group.display_order,
            "project_count": group.collection_count,
            "created_at": group.created_at.isoformat() if group.created_at else None,
            "updated_at": group.updated_at.isoformat() if group.updated_at else None,
        }

    def list(self, *, limit: int = 50) -> list[dict]:
        return self._call("list_projects", limit=limit)

    def rename(self, project_id: str, label: str, **kwargs) -> dict:
        if self._collections is not None:
            clean_label = (label or "").strip()
            if not clean_label:
                return {"error": "label must not be empty"}
            try:
                collection = self._collections.require(project_id)
            except NotFoundError:
                return {"error": f"Project {project_id} not found"}
            metadata = dict(collection.metadata)
            if kwargs.get("user_initiated", True):
                metadata["user_named"] = True
            updated = self._collections.update(
                project_id,
                name=clean_label,
                metadata=metadata,
            )
            return {
                "project_id": str(updated.id),
                "label": updated.name,
                "user_named": bool(updated.metadata.get("user_named")),
            }
        return self._call("rename_project", project_id, label, **kwargs)

    def delete(self, project_id: str, **kwargs) -> dict:
        return self._call("delete_project", project_id, **kwargs)

    def list_assets(self, *, project_id: str, limit: int = 100) -> list[dict]:
        if self._sources is not None:
            return [
                {
                    "asset_id": str(source.id),
                    "filename": source.filename,
                    "mime_type": source.media_type,
                    "size_bytes": source.size,
                    "status": source.status,
                }
                for source in self._sources.list(
                    collection_id=project_id,
                    limit=limit,
                )
            ]
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
        if self._collections is not None:
            return [
                self._group_payload(group)
                for group in self._collections.groups.list(limit=1000)
            ]
        return self._call("list_project_groups")

    def create_group(self, name: str, **kwargs) -> dict:
        if self._collections is not None:
            if not (name or "").strip():
                return {"error": "name must not be empty"}
            display_order = kwargs.get("display_order")
            if display_order is None:
                groups = self._collections.groups.list(limit=1000)
                display_order = max((group.display_order for group in groups), default=-1) + 1
            group = self._collections.groups.create(
                name=name,
                description=kwargs.get("description") or None,
                color=kwargs.get("color") or None,
                display_order=int(display_order),
            )
            return self._group_payload(group)
        return self._call("create_project_group", name, **kwargs)

    def update_group(self, group_id: str, **changes) -> dict:
        if self._collections is not None:
            if changes.get("name") is not None and not changes["name"].strip():
                return {"error": "name must not be empty"}
            try:
                group = self._collections.groups.update(
                    group_id,
                    name=changes.get("name"),
                    description=(
                        changes["description"].strip() or None
                        if changes.get("description") is not None
                        else None
                    ),
                    color=(
                        changes["color"].strip() or None
                        if changes.get("color") is not None
                        else None
                    ),
                    display_order=changes.get("display_order"),
                )
            except NotFoundError:
                return {"error": f"Group {group_id} not found"}
            return self._group_payload(group)
        return self._call("update_project_group", group_id, **changes)

    def delete_group(self, group_id: str) -> dict:
        if self._collections is not None:
            try:
                receipt = self._collections.groups.delete(group_id)
            except NotFoundError:
                return {"error": f"Group {group_id} not found"}
            return {
                "group_id": str(receipt.resource_id),
                "deleted": True,
                "unassigned_projects": int(
                    receipt.details.get("unassigned_collections") or 0
                ),
            }
        return self._call("delete_project_group", group_id)

    def assign_group(self, project_ids: list[str], group_id: str | None) -> dict:
        if self._collections is not None:
            if not project_ids:
                return {"updated": 0, "group_id": None}
            try:
                receipt = self._collections.assign_group(project_ids, group_id)
            except NotFoundError:
                return {"error": f"Group {group_id} not found"}
            return {
                "updated": int(receipt.details["updated"]),
                "group_id": str(receipt.resource_id) if receipt.resource_id else None,
            }
        return self._call("assign_projects_to_group", project_ids, group_id)


class MaterialWorkflows(_BoundOperations):
    """Typed workflow records plus materials review and maintenance operations."""

    def __init__(
        self,
        records: Workflows | None = None,
        operations: Any | None = None,
        *,
        schema_curator: Callable[..., Any] | None = None,
        workflow_operations: Any | None = None,
    ):
        super().__init__(operations)
        self._records = records
        self._schema_curator = schema_curator
        self._workflow_operations = workflow_operations

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
        if self._workflow_operations is not None:
            return self._workflow_operations.review_raw_workflow(extraction_id, **kwargs)
        return self._call("review_raw_workflow", extraction_id, **kwargs)

    def correct(self, extraction_id: str, graph: dict, **kwargs) -> dict:
        if self._workflow_operations is not None:
            return self._workflow_operations.correct_raw_workflow(extraction_id, graph, **kwargs)
        return self._call("correct_raw_workflow", extraction_id, graph, **kwargs)

    def readiness(self, project_id: str) -> dict:
        if self._workflow_operations is not None:
            return self._workflow_operations.get_raw_workflow_extraction_readiness(project_id)
        return self._call("get_raw_workflow_extraction_readiness", project_id)

    def list_raw(self, project_id: str, **kwargs) -> list[dict]:
        if self._workflow_operations is not None:
            return self._workflow_operations.list_raw_workflows(project_id, **kwargs)
        return self._call("list_raw_workflows", project_id, **kwargs)

    def get_raw(self, project_id: str, **kwargs) -> dict | None:
        if self._workflow_operations is not None:
            return self._workflow_operations.get_raw_workflow(project_id, **kwargs)
        return self._call("get_raw_workflow", project_id, **kwargs)

    def delete_raw_version(self, project_id: str, version: int) -> dict:
        if self._workflow_operations is not None:
            return self._workflow_operations.delete_raw_workflow_version(project_id, version)
        return self._call("delete_raw_workflow_version", project_id, version)

    def list_canonical(self, project_id: str, **kwargs) -> list[dict]:
        if self._workflow_operations is not None:
            return self._workflow_operations.list_canonical_workflows(project_id, **kwargs)
        return self._call("list_canonical_workflows", project_id, **kwargs)

    def get_canonical(self, project_id: str, **kwargs) -> dict | None:
        if self._workflow_operations is not None:
            return self._workflow_operations.get_canonical_workflow(project_id, **kwargs)
        return self._call("get_canonical_workflow", project_id, **kwargs)

    def curate_schema(self, **kwargs):
        if self._schema_curator is None:
            raise ConflictError("Workflow schema curation is unavailable")
        result = self._schema_curator(**kwargs)
        return asyncio.run(result) if asyncio.iscoroutine(result) else result

    def list_schema_proposals(self, *, status: str | None = "pending") -> list[dict]:
        if self._workflow_operations is not None:
            return self._workflow_operations.list_schema_proposals(status=status)
        return self._call("list_schema_proposals", status=status)

    def review_schema_proposal(self, proposal_id: str, **kwargs) -> dict:
        if self._workflow_operations is not None:
            return self._workflow_operations.review_schema_proposal(proposal_id, **kwargs)
        return self._call("review_schema_proposal", proposal_id, **kwargs)

    def edit_schema_proposal(self, proposal_id: str, **changes) -> dict:
        if self._workflow_operations is not None:
            return self._workflow_operations.edit_schema_proposal(proposal_id, **changes)
        return self._call("edit_schema_proposal", proposal_id, **changes)

    def schema_proposal_revisions(self, proposal_id: str) -> list[dict]:
        if self._workflow_operations is not None:
            return self._workflow_operations.get_schema_proposal_revisions(proposal_id)
        return self._call("get_schema_proposal_revisions", proposal_id)

    def schema_status(self) -> dict:
        if self._workflow_operations is not None:
            return self._workflow_operations.get_workflow_schema_status()
        return self._call("get_workflow_schema_status")

    def schedule_reextraction(self, project_id: str, **kwargs) -> dict:
        if self._workflow_operations is not None:
            return self._workflow_operations.schedule_workflow_reextraction(project_id, **kwargs)
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
