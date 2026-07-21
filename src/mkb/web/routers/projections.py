import io
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from mkb import api
from mkb.web._helpers import _parse_uuid, require_service_result
from mkb.web.dependencies import get_knowledge_base
from mkb.web._models import ProjectionReviewRequest

router = APIRouter()


class ProjectionBatchExportRequest(BaseModel):
    projection_ids: list[str]
    format: str = "yaml"


@router.get("/api/projections")
def list_projections(
    limit: int = 100,
    space_id: str | None = None,
    project_id: str | None = None,
    include_data: bool = False,
    newest_only: bool = False,
    include_history: bool = False,
):
    rows = api.list_projections(
        space_id=space_id,
        project_id=project_id,
        include_data=include_data,
        newest_only=newest_only,
        include_history=include_history,
    )
    return rows[:limit]


@router.get("/api/projections/{projection_id}")
def get_projection(projection_id: str):
    row = api.get_projection(projection_id)
    if not row:
        raise HTTPException(status_code=404, detail="Projection not found")
    return row


@router.delete("/api/projections/{projection_id}", status_code=204)
def delete_projection(projection_id: str):
    found = api.delete_projection(projection_id)
    if not found:
        raise HTTPException(status_code=404, detail="Projection not found")


@router.get("/api/projections/{projection_id}/export")
def export_projection_endpoint(projection_id: str, format: str = "yaml"):
    """Download a single projection as YAML or JSON.

    For qa_benchmark spaces this returns a ZIP containing all per-question
    YAML files; for other spaces, a single YAML/JSON file with the payload.
    """
    _parse_uuid(projection_id, "projection_id")
    fmt = (format or "yaml").strip().lower()
    if fmt not in {"yaml", "json"}:
        raise HTTPException(status_code=400, detail="format must be yaml or json")

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp) / "export"
        result = api.export_projection(projection_id, out_dir, format=fmt, overwrite=True)
        require_service_result(result)
        files = [Path(p) for p in result.get("files", [])]
        if not files:
            raise HTTPException(status_code=404, detail="Nothing to export")

        if len(files) == 1 and files[0].is_file():
            data = files[0].read_bytes()
            media = "application/json" if fmt == "json" else "application/x-yaml"
            return Response(
                content=data,
                media_type=media,
                headers={
                    "Content-Disposition": f'attachment; filename="{files[0].name}"',
                },
            )

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in out_dir.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(out_dir))
        buf.seek(0)
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="projection_{projection_id}.zip"',
            },
        )


@router.post("/api/projections/export")
def export_projections_batch(body: ProjectionBatchExportRequest):
    """Export a batch of projections by ID as a ZIP archive (or single file if only one)."""
    fmt = (body.format or "yaml").strip().lower()
    if fmt not in {"yaml", "json"}:
        raise HTTPException(status_code=400, detail="format must be yaml or json")
    if not body.projection_ids:
        raise HTTPException(status_code=400, detail="No projection IDs provided")
    for pid in body.projection_ids:
        _parse_uuid(pid, "projection_id")

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp) / "export"
        out_dir.mkdir(parents=True, exist_ok=True)
        for pid in body.projection_ids:
            api.export_projection(pid, out_dir, format=fmt, overwrite=True)

        files = [p for p in out_dir.rglob("*") if p.is_file()]
        if not files:
            raise HTTPException(status_code=404, detail="Nothing to export")

        if len(files) == 1:
            data = files[0].read_bytes()
            media = "application/json" if fmt == "json" else "application/x-yaml"
            return Response(
                content=data,
                media_type=media,
                headers={"Content-Disposition": f'attachment; filename="{files[0].name}"'},
            )

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in files:
                zf.write(p, p.relative_to(out_dir))
        buf.seek(0)
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="selected_projections.zip"'},
        )


@router.get("/api/spaces/{space_id_or_name}/export")
def export_space_endpoint(space_id_or_name: str, format: str = "yaml"):
    """Download every projection for a space as a ZIP archive."""
    fmt = (format or "yaml").strip().lower()
    if fmt not in {"yaml", "json"}:
        raise HTTPException(status_code=400, detail="format must be yaml or json")

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp) / "export"
        result = api.export_space_projections(
            space_id_or_name, out_dir, format=fmt, overwrite=True
        )
        require_service_result(result)
        files = list(out_dir.rglob("*"))
        if not any(p.is_file() for p in files):
            raise HTTPException(status_code=404, detail="Nothing to export")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in out_dir.rglob("*"):
                if p.is_file():
                    zf.write(p, p.relative_to(out_dir))
        buf.seek(0)
        safe_name = str(space_id_or_name).replace("/", "_")
        return Response(
            content=buf.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="space_{safe_name}_projections.zip"',
            },
        )


@router.post("/api/projections/review")
def review_projections(body: ProjectionReviewRequest):
    _parse_uuid(body.space_id, "space_id")

    project_ids: list[str] = []
    if body.project_id:
        _parse_uuid(body.project_id, "project_id")
        project_ids = [body.project_id]
    elif body.project_ids:
        for pid in body.project_ids:
            _parse_uuid(pid, "project_id")
        project_ids = list(body.project_ids)

    mode = (body.mode or "per_project").strip().lower()
    if mode not in {"per_project", "session"}:
        raise HTTPException(status_code=400, detail=f"Invalid mode: {body.mode}")

    if mode == "session":
        if not project_ids:
            raise HTTPException(
                status_code=400,
                detail="Session-mode review requires explicit project_ids (or project_id).",
            )
        job = get_knowledge_base().jobs.submit_action(
            "review_projection_session",
            space_id=body.space_id,
            project_ids=project_ids,
            reviewer_id=body.reviewer_id,
        )
        return {"job_id": str(job.id)}

    if len(project_ids) == 1:
        job = get_knowledge_base().jobs.submit_action(
            "review_projection",
            job_project_id=project_ids[0],
            space_id=body.space_id,
            project_id=project_ids[0],
            reviewer_id=body.reviewer_id,
        )
    elif project_ids:
        job_ids: list[str] = []
        for project_id in project_ids:
            job_ids.append(
                str(
                    get_knowledge_base().jobs.submit_action(
                        "review_projection",
                        job_project_id=project_id,
                        space_id=body.space_id,
                        project_id=project_id,
                        reviewer_id=body.reviewer_id,
                    ).id
                )
            )
        return {"job_id": job_ids[0], "job_ids": job_ids}
    else:
        job = get_knowledge_base().jobs.submit_action(
            "review_projection_all",
            space_id=body.space_id,
            project_ids=project_ids or None,
            reviewer_id=body.reviewer_id,
        )
    return {"job_id": str(job.id)}
