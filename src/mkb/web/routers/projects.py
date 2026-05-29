from fastapi import APIRouter, HTTPException

from mkb import api
from mkb.web._helpers import _parse_uuid
from mkb.web._models import (
    ProjectGroupAssign,
    ProjectGroupCreate,
    ProjectGroupUpdate,
    ProjectionRunRequest,
    ProjectUpdateRequest,
)
from mkb.web._state import jobs

router = APIRouter()


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
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.delete("/api/projects/{project_id}")
def delete_project(project_id: str, delete_s3: bool = True):
    _parse_uuid(project_id, "project_id")
    result = api.delete_project(project_id, delete_s3_objects=delete_s3)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/api/projects/{project_id}/assets")
def list_project_assets(project_id: str):
    _parse_uuid(project_id, "project_id")
    return api.list_assets(project_id=project_id)


@router.get("/api/projects/{project_id}/processed-assets")
def list_project_processed_assets(project_id: str):
    _parse_uuid(project_id, "project_id")
    return api.list_processed_assets(project_id=project_id)


@router.post("/api/projects/{project_id}/process")
def process_project(project_id: str):
    _parse_uuid(project_id, "project_id")
    job_id = jobs.start_job(
        kind="process",
        label="Process",
        project_id=project_id,
        target=api.process,
        kwargs={"project_id": project_id},
    )
    return {"job_id": job_id}


@router.post("/api/projects/{project_id}/extract")
def extract_project(project_id: str):
    _parse_uuid(project_id, "project_id")
    job_id = jobs.start_job(
        kind="extract",
        label="Extract",
        project_id=project_id,
        target=api.extract,
        kwargs={"project_id": project_id},
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
    job_id = jobs.start_job(
        kind="project",
        label=label,
        project_id=project_id,
        target=api.project,
        kwargs={
            "space_id": body.space_id,
            "project_id": project_id,
            "source_type": source_type,
        },
    )
    return {"job_id": job_id}


@router.post("/api/projects/{project_id}/kg-extract")
def project_kg_extract(project_id: str):
    _parse_uuid(project_id, "project_id")
    job_id = jobs.start_job(
        kind="knowledge_graph",
        label="Extract Graph",
        project_id=project_id,
        target=api.extract_knowledge_graph,
        kwargs={"project_id": project_id},
    )
    return {"job_id": job_id}


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
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


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
    if "error" in result:
        status = 404 if "not found" in result["error"] else 400
        raise HTTPException(status_code=status, detail=result["error"])
    return result


@router.delete("/api/project-groups/{group_id}")
def delete_project_group_endpoint(group_id: str):
    _parse_uuid(group_id, "group_id")
    result = api.delete_project_group(group_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/api/project-groups/assign")
def assign_project_group_endpoint(body: ProjectGroupAssign):
    for pid in body.project_ids:
        _parse_uuid(pid, "project_id")
    if body.group_id:
        _parse_uuid(body.group_id, "group_id")
    result = api.assign_projects_to_group(body.project_ids, body.group_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result
