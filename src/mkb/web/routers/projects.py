from pathlib import Path

from fastapi import APIRouter, HTTPException, Response

from mkb.web._helpers import (
    _parse_uuid,
    require_service_result,
    require_service_result_or_not_found,
)
from mkb.web.content import asset_media_type, inline_headers
from mkb.web.dependencies import get_knowledge_base
from mkb.web.job_backend import serialize_web_job
from mkb.web._models import (
    ProjectGroupAssign,
    ProjectGroupCreate,
    ProjectGroupUpdate,
    ProjectionRunRequest,
    ProjectUpdateRequest,
    SchemaCurateRequest,
    SchemaProposalEditRequest,
    SchemaProposalReviewRequest,
    WorkflowReextractionRequest,
)

router = APIRouter()


_asset_media_type = asset_media_type
_inline_headers = inline_headers


@router.get("/api/projects")
def list_projects(limit: int = 5000):
    return get_knowledge_base().materials.projects.list(limit=limit)


@router.get("/api/projects/{project_id}")
def get_project(project_id: str):
    identifier = _parse_uuid(project_id, "project_id")
    collection = get_knowledge_base().collections.get(identifier)
    if collection is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return {
        "project_id": str(collection.id),
        "label": collection.name,
        "source_path": collection.source_path,
        "file_count": collection.source_count,
        "group_id": str(collection.group_id) if collection.group_id else None,
        "metadata": collection.metadata,
        "created_at": collection.created_at,
        "updated_at": collection.updated_at,
    }


@router.patch("/api/projects/{project_id}")
def update_project(project_id: str, body: ProjectUpdateRequest):
    _parse_uuid(project_id, "project_id")
    result = get_knowledge_base().materials.projects.rename(
        project_id, body.label, user_initiated=True
    )
    return require_service_result(result, default_status=404)


@router.delete("/api/projects/{project_id}")
def delete_project(project_id: str, delete_s3: bool = True):
    _parse_uuid(project_id, "project_id")
    result = get_knowledge_base().materials.projects.delete(
        project_id, delete_s3_objects=delete_s3
    )
    return require_service_result(result, default_status=404)


@router.get("/api/projects/{project_id}/assets")
def list_project_assets(project_id: str):
    _parse_uuid(project_id, "project_id")
    return get_knowledge_base().materials.projects.list_assets(
        project_id=project_id
    )


@router.get("/api/projects/{project_id}/processed-assets")
def list_project_processed_assets(project_id: str):
    _parse_uuid(project_id, "project_id")
    return get_knowledge_base().materials.projects.list_processed_assets(
        project_id=project_id
    )


@router.get("/api/projects/{project_id}/assets/{asset_id}/content")
def get_project_asset_content(project_id: str, asset_id: str):
    pid = _parse_uuid(project_id, "project_id")
    aid = _parse_uuid(asset_id, "asset_id")
    kb = get_knowledge_base()
    source = kb.sources.get(aid)
    if source is None or pid not in source.collection_ids:
        raise HTTPException(status_code=404, detail="Asset not found in this project")
    media_type = _asset_media_type(source.filename, source.media_type)
    if not media_type:
        raise HTTPException(
            status_code=415,
            detail="Preview is only available for PDF and Markdown files",
        )
    return Response(
        content=kb.sources.read_bytes(source.id),
        media_type=media_type,
        headers=_inline_headers(source.filename),
    )


@router.get("/api/projects/{project_id}/processed-assets/{processed_asset_id}/content")
def get_project_processed_asset_content(project_id: str, processed_asset_id: str):
    pid = _parse_uuid(project_id, "project_id")
    paid = _parse_uuid(processed_asset_id, "processed_asset_id")
    kb = get_knowledge_base()
    artifact = kb.artifacts.get(paid)
    source = kb.sources.get(artifact.source_id) if artifact is not None else None
    if artifact is None or source is None or pid not in source.collection_ids:
        raise HTTPException(
            status_code=404,
            detail="Processed asset not found in this project",
        )
    filename = artifact.primary_path or f"processed.{artifact.format}"
    media_type = _asset_media_type(filename)
    if not media_type:
        raise HTTPException(
            status_code=415,
            detail="Preview is only available for PDF and Markdown files",
        )
    return Response(
        content=kb.artifacts.read_bytes(artifact.id),
        media_type=media_type,
        headers=_inline_headers(Path(filename).name),
    )


@router.post("/api/projects/{project_id}/process")
def process_project(project_id: str):
    _parse_uuid(project_id, "project_id")
    job = get_knowledge_base().jobs.submit_action(
        "process_project",
        job_project_id=project_id,
        project_id=project_id,
    )
    return {"job_id": str(job.id)}


@router.post("/api/projects/{project_id}/extract")
def extract_project(project_id: str):
    _parse_uuid(project_id, "project_id")
    job = get_knowledge_base().jobs.submit_action(
        "extract_project",
        job_project_id=project_id,
        project_id=project_id,
    )
    return {"job_id": str(job.id)}


@router.post("/api/projects/{project_id}/project")
def project_project(project_id: str, body: ProjectionRunRequest):
    _parse_uuid(project_id, "project_id")
    _parse_uuid(body.space_id, "space_id")
    source_type = (body.source_type or "frame").strip().lower()
    if source_type not in {"frame", "markdown"}:
        raise HTTPException(status_code=400, detail=f"Invalid source_type: {body.source_type}")
    label = "Project" if source_type == "frame" else "Project (markdown)"
    job = get_knowledge_base().jobs.submit_action(
        "project_to_space",
        job_project_id=project_id,
        label=label,
        space_id=body.space_id,
        project_id=project_id,
        source_type=source_type,
    )
    return {"job_id": str(job.id)}


@router.post("/api/projects/{project_id}/kg-extract")
def project_kg_extract(project_id: str):
    _parse_uuid(project_id, "project_id")
    job = get_knowledge_base().jobs.submit_action(
        "extract_knowledge_graph",
        job_project_id=project_id,
        project_id=project_id,
    )
    return {"job_id": str(job.id)}


@router.post("/api/projects/{project_id}/workflow-extract")
def project_workflow_extract(project_id: str):
    _parse_uuid(project_id, "project_id")
    active = get_knowledge_base().jobs.find_active(
        project_id=project_id, kind="raw_workflow"
    )
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"Workflow extraction is already {active.status.lower()} for this project.",
        )
    readiness = get_knowledge_base().materials.workflows.readiness(project_id)
    if not readiness.get("ready"):
        raise HTTPException(status_code=400, detail=readiness.get("message") or "Project is not ready for workflow extraction")
    job = get_knowledge_base().jobs.submit_action(
        "extract_raw_workflow",
        job_project_id=project_id,
        project_id=project_id,
    )
    return {"job_id": str(job.id)}


@router.get("/api/projects/{project_id}/workflows")
def project_workflows(project_id: str, include_graph: bool = False):
    _parse_uuid(project_id, "project_id")
    return get_knowledge_base().materials.workflows.list_raw(
        project_id, include_graph=include_graph
    )


@router.get("/api/projects/{project_id}/workflows/latest")
def latest_project_workflow(project_id: str):
    _parse_uuid(project_id, "project_id")
    result = get_knowledge_base().materials.workflows.get_raw(project_id)
    if not result:
        raise HTTPException(status_code=404, detail="No completed raw workflow found")
    return result


@router.get("/api/projects/{project_id}/workflows/{version}")
def project_workflow_version(project_id: str, version: int):
    _parse_uuid(project_id, "project_id")
    result = get_knowledge_base().materials.workflows.get_raw(
        project_id, version=version
    )
    if not result:
        raise HTTPException(status_code=404, detail="Raw workflow version not found")
    return result


@router.delete("/api/projects/{project_id}/workflows/{version}")
def delete_project_workflow_version(project_id: str, version: int):
    _parse_uuid(project_id, "project_id")
    active = get_knowledge_base().jobs.find_active(
        project_id=project_id, kind="raw_workflow"
    )
    if active:
        raise HTTPException(
            status_code=409,
            detail="Workflow extraction is currently running for this project. Cancel or wait for it to finish before deleting a version.",
        )
    result = get_knowledge_base().materials.workflows.delete_raw_version(
        project_id, version
    )
    return require_service_result_or_not_found(result)


@router.get("/api/projects/{project_id}/canonical-workflows")
def project_canonical_workflows(project_id: str, include_graph: bool = False):
    _parse_uuid(project_id, "project_id")
    return get_knowledge_base().materials.workflows.list_canonical(
        project_id, include_graph=include_graph
    )


@router.get("/api/projects/{project_id}/canonical-workflows/latest")
def latest_project_canonical_workflow(project_id: str):
    _parse_uuid(project_id, "project_id")
    result = get_knowledge_base().materials.workflows.get_canonical(project_id)
    if not result:
        raise HTTPException(status_code=404, detail="No completed canonical workflow found")
    return result


@router.get("/api/projects/{project_id}/canonical-workflows/{version}")
def project_canonical_workflow_version(project_id: str, version: int):
    _parse_uuid(project_id, "project_id")
    result = get_knowledge_base().materials.workflows.get_canonical(
        project_id, version=version
    )
    if not result:
        raise HTTPException(status_code=404, detail="Canonical workflow version not found")
    return result


@router.get("/api/workflows/search")
def search_workflows(source: str | None = None, operation: str | None = None, target: str | None = None, mode: str = "strict", limit: int = 100):
    if not any((source, operation, target)):
        raise HTTPException(status_code=400, detail="Provide source, operation, or target")
    try:
        return get_knowledge_base().materials.workflows.search(
            source, operation, target, mode, limit
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/projects/{project_id}/workflow-reextract")
def schedule_reextraction(project_id: str, body: WorkflowReextractionRequest):
    _parse_uuid(project_id, "project_id")
    try:
        result = get_knowledge_base().materials.workflows.schedule_reextraction(
            project_id, reason=body.reason, requested_by=body.requested_by,
            scope=body.scope, raw_extraction_id=body.raw_extraction_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return require_service_result(result)


@router.post("/api/workflow-maintenance/{task_id}/run")
def run_maintenance_task(task_id: str):
    _parse_uuid(task_id, "task_id")
    job = get_knowledge_base().jobs.submit_action(
        "workflow_maintenance", task_id=task_id
    )
    return {"job_id": str(job.id), "task_id": task_id}


@router.get("/api/workflow-maintenance")
def workflow_maintenance_tasks(status: str | None = None, project_id: str | None = None):
    return get_knowledge_base().materials.workflows.list_tasks(
        status=status, project_id=project_id
    )


@router.get("/api/workflow-schema")
def workflow_schema_status():
    return get_knowledge_base().materials.workflows.schema_status()


@router.post("/api/workflow-schema/curate")
def curate_schema(body: SchemaCurateRequest):
    if body.min_support < 1:
        raise HTTPException(status_code=400, detail="min_support must be at least 1")
    if body.sample_size < 1:
        raise HTTPException(status_code=400, detail="sample_size must be at least 1")
    if body.mode not in {"global", "local", "auto"}:
        raise HTTPException(status_code=400, detail="mode must be global, local, or auto")
    job = get_knowledge_base().jobs.submit_action(
        "curate_workflow_schema",
        min_support=body.min_support,
        author=body.author.strip() or "workflow-review/ui",
        mode=body.mode,
        sample_size=body.sample_size,
        model=body.model,
        verbose=body.verbose,
    )
    return {"job_id": str(job.id)}


@router.get("/api/workflow-schema/proposals")
def schema_proposals(status: str | None = "pending"):
    return get_knowledge_base().materials.workflows.list_schema_proposals(
        status=status or None
    )


@router.post("/api/workflow-schema/proposals/{proposal_id}/review")
def review_schema_proposal_endpoint(proposal_id: str, body: SchemaProposalReviewRequest):
    _parse_uuid(proposal_id, "proposal_id")
    if not body.reviewer.strip():
        raise HTTPException(status_code=400, detail="reviewer is required")
    result = get_knowledge_base().materials.workflows.review_schema_proposal(
        proposal_id, decision=body.decision, reviewer=body.reviewer.strip(),
        notes=body.notes,
    )
    return require_service_result(result)


@router.patch("/api/workflow-schema/proposals/{proposal_id}")
def edit_schema_proposal_endpoint(proposal_id: str, body: SchemaProposalEditRequest):
    _parse_uuid(proposal_id, "proposal_id")
    result = get_knowledge_base().materials.workflows.edit_schema_proposal(
        proposal_id, payload=body.payload,
        evidence_workflow_ids=body.evidence_workflow_ids,
        rationale=body.rationale, editor=body.editor,
        change_note=body.change_note,
    )
    return require_service_result(result)


@router.get("/api/workflow-schema/proposals/{proposal_id}/revisions")
def schema_proposal_revisions(proposal_id: str):
    _parse_uuid(proposal_id, "proposal_id")
    return get_knowledge_base().materials.workflows.schema_proposal_revisions(
        proposal_id
    )


@router.get("/api/projects/{project_id}/jobs")
def project_jobs(project_id: str):
    return [
        serialize_web_job(job)
        for job in get_knowledge_base().jobs.list(project_id=project_id, limit=100)
    ]


# ── Project groups ─────────────────────────────────────────────


@router.get("/api/project-groups")
def list_project_groups_endpoint():
    return get_knowledge_base().materials.projects.list_groups()


@router.post("/api/project-groups")
def create_project_group_endpoint(body: ProjectGroupCreate):
    result = get_knowledge_base().materials.projects.create_group(
        body.name,
        description=body.description,
        color=body.color,
        display_order=body.display_order,
    )
    return require_service_result(result)


@router.patch("/api/project-groups/{group_id}")
def update_project_group_endpoint(group_id: str, body: ProjectGroupUpdate):
    _parse_uuid(group_id, "group_id")
    result = get_knowledge_base().materials.projects.update_group(
        group_id,
        name=body.name,
        description=body.description,
        color=body.color,
        display_order=body.display_order,
    )
    return require_service_result_or_not_found(result)


@router.delete("/api/project-groups/{group_id}")
def delete_project_group_endpoint(group_id: str):
    _parse_uuid(group_id, "group_id")
    result = get_knowledge_base().materials.projects.delete_group(group_id)
    return require_service_result(result, default_status=404)


@router.post("/api/project-groups/assign")
def assign_project_group_endpoint(body: ProjectGroupAssign):
    for pid in body.project_ids:
        _parse_uuid(pid, "project_id")
    if body.group_id:
        _parse_uuid(body.group_id, "group_id")
    result = get_knowledge_base().materials.projects.assign_group(
        body.project_ids, body.group_id
    )
    return require_service_result(result, default_status=404)
