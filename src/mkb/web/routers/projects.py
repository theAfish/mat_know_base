from pathlib import Path

from fastapi import APIRouter, HTTPException, Response

from mkb import api
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import Asset, ProcessedAsset, ProjectAsset
from mkb.storage.s3 import download_bytes
from mkb.services.workflows import compatibility as canonical_compat
from mkb.web._helpers import (
    _parse_uuid,
    require_service_result,
    require_service_result_or_not_found,
    start_web_job_action,
)
from mkb.web.content import asset_media_type, inline_headers
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
from mkb.web._state import jobs

router = APIRouter()


_asset_media_type = asset_media_type
_inline_headers = inline_headers


@router.get("/api/projects")
def list_projects(limit: int = 5000):
    return api.list_projects(limit=limit)


@router.get("/api/projects/{project_id}")
def get_project(project_id: str):
    rows = api.list_projects(limit=500)
    for row in rows:
        if row["project_id"] == project_id:
            return row
    raise HTTPException(status_code=404, detail="Project not found")


@router.patch("/api/projects/{project_id}")
def update_project(project_id: str, body: ProjectUpdateRequest):
    _parse_uuid(project_id, "project_id")
    result = api.rename_project(project_id, body.label, user_initiated=True)
    return require_service_result(result, default_status=404)


@router.delete("/api/projects/{project_id}")
def delete_project(project_id: str, delete_s3: bool = True):
    _parse_uuid(project_id, "project_id")
    result = api.delete_project(project_id, delete_s3_objects=delete_s3)
    return require_service_result(result, default_status=404)


@router.get("/api/projects/{project_id}/assets")
def list_project_assets(project_id: str):
    _parse_uuid(project_id, "project_id")
    return api.list_assets(project_id=project_id)


@router.get("/api/projects/{project_id}/processed-assets")
def list_project_processed_assets(project_id: str):
    _parse_uuid(project_id, "project_id")
    return api.list_processed_assets(project_id=project_id)


@router.get("/api/projects/{project_id}/assets/{asset_id}/content")
def get_project_asset_content(project_id: str, asset_id: str):
    pid = _parse_uuid(project_id, "project_id")
    aid = _parse_uuid(asset_id, "asset_id")
    with SyncSessionLocal() as session:
        asset = (
            session.query(Asset)
            .join(ProjectAsset, ProjectAsset.asset_id == Asset.asset_id)
            .filter(ProjectAsset.project_id == pid, Asset.asset_id == aid)
            .first()
        )
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found in this project")
        media_type = _asset_media_type(asset.filename, asset.mime_type)
        if not media_type:
            raise HTTPException(status_code=415, detail="Preview is only available for PDF and Markdown files")
        data = download_bytes(asset.s3_bucket, asset.s3_key)
        return Response(
            content=data,
            media_type=media_type,
            headers=_inline_headers(asset.filename),
        )


@router.get("/api/projects/{project_id}/processed-assets/{processed_asset_id}/content")
def get_project_processed_asset_content(project_id: str, processed_asset_id: str):
    pid = _parse_uuid(project_id, "project_id")
    paid = _parse_uuid(processed_asset_id, "processed_asset_id")
    with SyncSessionLocal() as session:
        row = (
            session.query(ProcessedAsset)
            .join(ProjectAsset, ProjectAsset.asset_id == ProcessedAsset.asset_id)
            .filter(
                ProjectAsset.project_id == pid,
                ProcessedAsset.processed_asset_id == paid,
            )
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="Processed asset not found in this project")

        metadata = row.conversion_metadata or {}
        filename = metadata.get("primary_relpath") or f"processed.{row.output_format}"
        media_type = _asset_media_type(filename)
        if not media_type:
            raise HTTPException(status_code=415, detail="Preview is only available for PDF and Markdown files")
        data = download_bytes(row.s3_bucket, row.s3_key)
        return Response(
            content=data,
            media_type=media_type,
            headers=_inline_headers(Path(filename).name),
        )


@router.post("/api/projects/{project_id}/process")
def process_project(project_id: str):
    _parse_uuid(project_id, "project_id")
    job_id = start_web_job_action(
        jobs,
        "process_project",
        job_project_id=project_id,
        project_id=project_id,
    )
    return {"job_id": job_id}


@router.post("/api/projects/{project_id}/extract")
def extract_project(project_id: str):
    _parse_uuid(project_id, "project_id")
    job_id = start_web_job_action(
        jobs,
        "extract_project",
        job_project_id=project_id,
        project_id=project_id,
    )
    return {"job_id": job_id}


@router.post("/api/projects/{project_id}/project")
def project_project(project_id: str, body: ProjectionRunRequest):
    _parse_uuid(project_id, "project_id")
    _parse_uuid(body.space_id, "space_id")
    source_type = (body.source_type or "frame").strip().lower()
    if source_type not in {"frame", "markdown"}:
        raise HTTPException(status_code=400, detail=f"Invalid source_type: {body.source_type}")
    label = "Project" if source_type == "frame" else "Project (markdown)"
    job_id = start_web_job_action(
        jobs,
        "project_to_space",
        job_project_id=project_id,
        label=label,
        space_id=body.space_id,
        project_id=project_id,
        source_type=source_type,
    )
    return {"job_id": job_id}


@router.post("/api/projects/{project_id}/kg-extract")
def project_kg_extract(project_id: str):
    _parse_uuid(project_id, "project_id")
    job_id = start_web_job_action(
        jobs,
        "extract_knowledge_graph",
        job_project_id=project_id,
        project_id=project_id,
    )
    return {"job_id": job_id}


@router.post("/api/projects/{project_id}/workflow-extract")
def project_workflow_extract(project_id: str):
    _parse_uuid(project_id, "project_id")
    active = jobs.find_active_job(project_id=project_id, kind="raw_workflow")
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"Workflow extraction is already {active['status'].lower()} for this project.",
        )
    readiness = api.get_raw_workflow_extraction_readiness(project_id)
    if not readiness.get("ready"):
        raise HTTPException(status_code=400, detail=readiness.get("message") or "Project is not ready for workflow extraction")
    job_id = start_web_job_action(
        jobs,
        "extract_raw_workflow",
        job_project_id=project_id,
        project_id=project_id,
    )
    return {"job_id": job_id}


@router.get("/api/projects/{project_id}/workflows")
def project_workflows(project_id: str, include_graph: bool = False):
    _parse_uuid(project_id, "project_id")
    return api.list_raw_workflows(project_id, include_graph=include_graph)


@router.get("/api/projects/{project_id}/workflows/latest")
def latest_project_workflow(project_id: str):
    _parse_uuid(project_id, "project_id")
    result = api.get_raw_workflow(project_id)
    if not result:
        raise HTTPException(status_code=404, detail="No completed raw workflow found")
    return result


@router.get("/api/projects/{project_id}/workflows/{version}")
def project_workflow_version(project_id: str, version: int):
    _parse_uuid(project_id, "project_id")
    result = api.get_raw_workflow(project_id, version=version)
    if not result:
        raise HTTPException(status_code=404, detail="Raw workflow version not found")
    return result


@router.delete("/api/projects/{project_id}/workflows/{version}")
def delete_project_workflow_version(project_id: str, version: int):
    _parse_uuid(project_id, "project_id")
    active = jobs.find_active_job(project_id=project_id, kind="raw_workflow")
    if active:
        raise HTTPException(
            status_code=409,
            detail="Workflow extraction is currently running for this project. Cancel or wait for it to finish before deleting a version.",
        )
    result = api.delete_raw_workflow_version(project_id, version)
    return require_service_result_or_not_found(result)


@router.get("/api/projects/{project_id}/canonical-workflows")
def project_canonical_workflows(project_id: str, include_graph: bool = False):
    _parse_uuid(project_id, "project_id")
    return canonical_compat.list_canonical_workflows(project_id, include_graph=include_graph)


@router.get("/api/projects/{project_id}/canonical-workflows/latest")
def latest_project_canonical_workflow(project_id: str):
    _parse_uuid(project_id, "project_id")
    result = canonical_compat.get_canonical_workflow(project_id)
    if not result:
        raise HTTPException(status_code=404, detail="No completed canonical workflow found")
    return result


@router.get("/api/projects/{project_id}/canonical-workflows/{version}")
def project_canonical_workflow_version(project_id: str, version: int):
    _parse_uuid(project_id, "project_id")
    result = canonical_compat.get_canonical_workflow(project_id, version=version)
    if not result:
        raise HTTPException(status_code=404, detail="Canonical workflow version not found")
    return result


@router.get("/api/workflows/search")
def search_workflows(source: str | None = None, operation: str | None = None, target: str | None = None, mode: str = "strict", limit: int = 100):
    if not any((source, operation, target)):
        raise HTTPException(status_code=400, detail="Provide source, operation, or target")
    try:
        return api.search_canonical_workflows(source, operation, target, mode, limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/projects/{project_id}/workflow-reextract")
def schedule_reextraction(project_id: str, body: WorkflowReextractionRequest):
    _parse_uuid(project_id, "project_id")
    try:
        result = api.schedule_workflow_reextraction(
            project_id, reason=body.reason, requested_by=body.requested_by,
            scope=body.scope, raw_extraction_id=body.raw_extraction_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return require_service_result(result)


@router.post("/api/workflow-maintenance/{task_id}/run")
def run_maintenance_task(task_id: str):
    _parse_uuid(task_id, "task_id")
    job_id = start_web_job_action(jobs, "workflow_maintenance", task_id=task_id)
    return {"job_id": job_id, "task_id": task_id}


@router.get("/api/workflow-maintenance")
def workflow_maintenance_tasks(status: str | None = None, project_id: str | None = None):
    return api.list_workflow_maintenance_tasks(status=status, project_id=project_id)


@router.get("/api/workflow-schema")
def workflow_schema_status():
    return api.get_workflow_schema_status()


@router.post("/api/workflow-schema/curate")
def curate_schema(body: SchemaCurateRequest):
    if body.min_support < 1:
        raise HTTPException(status_code=400, detail="min_support must be at least 1")
    if body.sample_size < 1:
        raise HTTPException(status_code=400, detail="sample_size must be at least 1")
    if body.mode not in {"global", "local", "auto"}:
        raise HTTPException(status_code=400, detail="mode must be global, local, or auto")
    job_id = start_web_job_action(
        jobs,
        "curate_workflow_schema",
        min_support=body.min_support,
        author=body.author.strip() or "workflow-review/ui",
        mode=body.mode,
        sample_size=body.sample_size,
        model=body.model,
        verbose=body.verbose,
    )
    return {"job_id": job_id}


@router.get("/api/workflow-schema/proposals")
def schema_proposals(status: str | None = "pending"):
    return api.list_schema_proposals(status=status or None)


@router.post("/api/workflow-schema/proposals/{proposal_id}/review")
def review_schema_proposal_endpoint(proposal_id: str, body: SchemaProposalReviewRequest):
    _parse_uuid(proposal_id, "proposal_id")
    if not body.reviewer.strip():
        raise HTTPException(status_code=400, detail="reviewer is required")
    result = api.review_schema_proposal(
        proposal_id, decision=body.decision, reviewer=body.reviewer.strip(),
        notes=body.notes,
    )
    return require_service_result(result)


@router.patch("/api/workflow-schema/proposals/{proposal_id}")
def edit_schema_proposal_endpoint(proposal_id: str, body: SchemaProposalEditRequest):
    _parse_uuid(proposal_id, "proposal_id")
    result = api.edit_schema_proposal(
        proposal_id, payload=body.payload,
        evidence_workflow_ids=body.evidence_workflow_ids,
        rationale=body.rationale, editor=body.editor,
        change_note=body.change_note,
    )
    return require_service_result(result)


@router.get("/api/workflow-schema/proposals/{proposal_id}/revisions")
def schema_proposal_revisions(proposal_id: str):
    _parse_uuid(proposal_id, "proposal_id")
    return api.get_schema_proposal_revisions(proposal_id)


@router.get("/api/projects/{project_id}/jobs")
def project_jobs(project_id: str):
    return jobs.list_jobs(project_id=project_id, limit=100)


# ── Project groups ─────────────────────────────────────────────


@router.get("/api/project-groups")
def list_project_groups_endpoint():
    return api.list_project_groups()


@router.post("/api/project-groups")
def create_project_group_endpoint(body: ProjectGroupCreate):
    result = api.create_project_group(
        body.name,
        description=body.description,
        color=body.color,
        display_order=body.display_order,
    )
    return require_service_result(result)


@router.patch("/api/project-groups/{group_id}")
def update_project_group_endpoint(group_id: str, body: ProjectGroupUpdate):
    _parse_uuid(group_id, "group_id")
    result = api.update_project_group(
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
    result = api.delete_project_group(group_id)
    return require_service_result(result, default_status=404)


@router.post("/api/project-groups/assign")
def assign_project_group_endpoint(body: ProjectGroupAssign):
    for pid in body.project_ids:
        _parse_uuid(pid, "project_id")
    if body.group_id:
        _parse_uuid(body.group_id, "group_id")
    result = api.assign_projects_to_group(body.project_ids, body.group_id)
    return require_service_result(result, default_status=404)
