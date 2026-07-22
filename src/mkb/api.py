"""Compatibility facade for the Materials Knowledge Base Python API."""

from __future__ import annotations

import sys
import types

from mkb.services import (
    _api_common,
    assets as _assets,
    feedback as _feedback,
    frames as _frames,
    graphs as _graphs,
    ingest as _ingest,
    projects as _projects,
    projections as _projections,
    spaces as _spaces,
    workflows as _workflows,
)
from mkb.services.workflows import (
    extraction as _workflow_extraction,
    indexing as _workflow_indexing,
    legacy_canonicalization as _workflow_legacy_canonicalization,
    maintenance as _workflow_maintenance,
    schema_review as _workflow_schema_review,
    serialization as _workflow_serialization,
)

from mkb.services.projects import (
    serialize_group,
    rename_project,
    list_projects,
    _serialize_group,
    list_project_groups,
    create_project_group,
    update_project_group,
    delete_project_group,
    delete_project,
    assign_projects_to_group,
)

from mkb.services.workflows import (
    serialize_raw_workflow,
    serialize_canonical_workflow,
    _serialize_raw_workflow,
    list_canonical_workflows,
    get_canonical_workflow,
    _serialize_canonical_workflow,
    curate_workflow_schema,
    list_schema_proposals,
    get_schema_proposal_revisions,
    edit_schema_proposal,
    get_workflow_schema_status,
    review_schema_proposal,
    schedule_workflow_reextraction,
    list_workflow_maintenance_tasks,
    run_workflow_maintenance_task,
    rebuild_workflow_indexes,
    search_canonical_workflows,
)

from mkb.services.spaces import (
    create_space,
    update_space,
    delete_space,
    list_spaces,
    get_space,
)

from mkb.services.projections import (
    serialize_projection_payload,
    project,
    project_all,
    get_projection,
    delete_projection,
    list_projections,
    _serialize_projection_payload,
    export_projection,
    export_space_projections,
)

from mkb.services.graphs import (
    clear_knowledge_graphs,
    extract_knowledge_graph,
    get_knowledge_graph,
    review_knowledge_graph,
    get_graph_review_counts,
)

from mkb.services.feedback import (
    list_feedback,
    review_feedback,
    review_projections,
    review_projections_all,
    review_projections_session,
    review_projection_followup,
    get_feedback_summary,
    resolve_feedback,
)

from mkb.services._api_common import (
    _sha256_bytes,
    _inspect_manual_processed_dir,
    _choose_asset_for_manual_output,
    _normalize_search_query,
    _matches_search_tokens,
    SyncSessionLocal,
    init_db,
)


def ingest(directory, label=None, *, user_named=False):
    """Compatibility entry point bound to explicitly owned default resources."""
    from mkb.services.compatibility_resources import content_operations

    return content_operations().ingest(directory, label=label, user_named=user_named)


def sync(root_dir):
    from mkb.services.compatibility_resources import content_operations

    return content_operations().sync(root_dir)


def sync_project(project_id):
    from mkb.services.compatibility_resources import content_operations

    return content_operations().sync_project(project_id)


def process(project_id=None, progress_callback=None):
    from mkb.services.compatibility_resources import content_operations

    return content_operations().process(
        project_id=project_id,
        progress_callback=progress_callback,
    )


def _workflow_operations():
    from mkb.services.compatibility_resources import workflow_operations

    return workflow_operations()


def extract_raw_workflow(project_id, **kwargs):
    readiness = get_raw_workflow_extraction_readiness(project_id)
    if not readiness.get("ready"):
        return {
            "status": "error",
            "message": readiness.get("message") or "Project is not ready for workflow extraction",
        }
    return _workflow_operations().extract_raw_workflow(project_id, **kwargs)


def get_raw_workflow_extraction_readiness(project_id, **kwargs):
    return _workflow_operations().get_raw_workflow_extraction_readiness(project_id, **kwargs)


def list_raw_workflows(project_id, **kwargs):
    return _workflow_operations().list_raw_workflows(project_id, **kwargs)


def get_raw_workflow(project_id, **kwargs):
    return _workflow_operations().get_raw_workflow(project_id, **kwargs)


def delete_raw_workflow_version(project_id, version, **kwargs):
    return _workflow_operations().delete_raw_workflow_version(project_id, version, **kwargs)


def review_raw_workflow(extraction_id, **kwargs):
    return _workflow_operations().review_raw_workflow(extraction_id, **kwargs)


def correct_raw_workflow(extraction_id, graph, **kwargs):
    return _workflow_operations().correct_raw_workflow(extraction_id, graph, **kwargs)


def _frame_operations():
    from mkb.services.compatibility_resources import frame_operations

    return frame_operations()


def extract(**kwargs):
    return _frame_operations().extract(**kwargs)


def get_frame(project_id, **kwargs):
    return _frame_operations().get_frame(project_id, **kwargs)


def list_frames(**kwargs):
    return _frame_operations().list_frames(**kwargs)


def get_extraction_history(project_id, **kwargs):
    return _frame_operations().get_extraction_history(project_id, **kwargs)


def _asset_operations():
    from mkb.services.compatibility_resources import asset_operations

    return asset_operations()


def list_processed_assets(**kwargs):
    return _asset_operations().list_processed_assets(**kwargs)


def link_manual_processed_data(*args, **kwargs):
    return _asset_operations().link_manual_processed_data(*args, **kwargs)


def list_assets(**kwargs):
    return _asset_operations().list_assets(**kwargs)


def search_library(query, **kwargs):
    return _asset_operations().search_library(query, **kwargs)

__all__ = [
    "SyncSessionLocal",
    "_choose_asset_for_manual_output",
    "_inspect_manual_processed_dir",
    "_matches_search_tokens",
    "_normalize_search_query",
    "_serialize_canonical_workflow",
    "_serialize_group",
    "_serialize_projection_payload",
    "_serialize_raw_workflow",
    "_sha256_bytes",
    "assign_projects_to_group",
    "clear_knowledge_graphs",
    "correct_raw_workflow",
    "create_project_group",
    "create_space",
    "curate_workflow_schema",
    "delete_project",
    "delete_project_group",
    "delete_projection",
    "delete_raw_workflow_version",
    "delete_space",
    "edit_schema_proposal",
    "export_projection",
    "export_space_projections",
    "extract",
    "extract_knowledge_graph",
    "extract_raw_workflow",
    "get_canonical_workflow",
    "get_extraction_history",
    "get_feedback_summary",
    "get_frame",
    "get_graph_review_counts",
    "get_knowledge_graph",
    "get_projection",
    "get_raw_workflow",
    "get_raw_workflow_extraction_readiness",
    "get_schema_proposal_revisions",
    "get_space",
    "get_workflow_schema_status",
    "ingest",
    "init_db",
    "link_manual_processed_data",
    "list_assets",
    "list_canonical_workflows",
    "list_feedback",
    "list_frames",
    "list_processed_assets",
    "list_project_groups",
    "list_projections",
    "list_projects",
    "list_raw_workflows",
    "list_schema_proposals",
    "list_spaces",
    "list_workflow_maintenance_tasks",
    "process",
    "project",
    "project_all",
    "rebuild_workflow_indexes",
    "rename_project",
    "resolve_feedback",
    "review_feedback",
    "review_knowledge_graph",
    "review_projection_followup",
    "review_projections",
    "review_projections_all",
    "review_projections_session",
    "review_raw_workflow",
    "review_schema_proposal",
    "run_workflow_maintenance_task",
    "schedule_workflow_reextraction",
    "search_canonical_workflows",
    "search_library",
    "serialize_canonical_workflow",
    "serialize_group",
    "serialize_projection_payload",
    "serialize_raw_workflow",
    "sync",
    "sync_project",
    "update_project_group",
    "update_space",
]

_MIRRORED_MODULES = (
    _api_common,
    _assets,
    _feedback,
    _frames,
    _graphs,
    _ingest,
    _projects,
    _projections,
    _spaces,
    _workflows,
    _workflow_extraction,
    _workflow_indexing,
    _workflow_legacy_canonicalization,
    _workflow_maintenance,
    _workflow_schema_review,
    _workflow_serialization,
)

class _ApiFacadeModule(types.ModuleType):
    """Mirror monkeypatches on this facade into split service modules."""

    def __setattr__(self, name: str, value):  # type: ignore[override]
        super().__setattr__(name, value)
        for module in _MIRRORED_MODULES:
            if hasattr(module, name):
                setattr(module, name, value)


sys.modules[__name__].__class__ = _ApiFacadeModule
