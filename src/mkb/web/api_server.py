from __future__ import annotations

import queue
import shutil
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from mkb import api
from mkb.agents.orchestrator import create_orchestrator_runner, send_message
from mkb.agents.tools.orchestrator_tools import get_pending_workflows


_EVENT_LIMIT = 60
_UPLOAD_TEMP = Path("data/uploads/_temp")
_UPLOAD_ROOT = Path("data/uploads")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_uuid(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}: {value!r}") from exc


def _safe_child(base: Path, rel: str) -> Path:
    p = Path(rel)
    if p.is_absolute():
        raise HTTPException(status_code=400, detail="Absolute path rejected")
    full = (base / p).resolve()
    try:
        full.relative_to(base.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path traversal rejected") from exc
    return full


@dataclass
class AssistantSession:
    runner: Any
    session_id: str


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._queues: dict[str, queue.Queue] = {}
        self._lock = threading.Lock()

    def start_job(
        self,
        *,
        kind: str,
        label: str,
        target,
        project_id: str | None = None,
        args: tuple[Any, ...] | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> str:
        job_id = str(uuid.uuid4())
        q: queue.Queue = queue.Queue()

        with self._lock:
            self._queues[job_id] = q
            self._jobs[job_id] = {
                "job_id": job_id,
                "kind": kind,
                "label": label,
                "status": "RUNNING",
                "project_id": project_id,
                "result": None,
                "error": None,
                "current_message": "Queued",
                "events": [],
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }

        worker_args = args or ()
        worker_kwargs = dict(kwargs or {})

        def progress_callback(event: dict[str, Any] | str) -> None:
            if isinstance(event, str):
                q.put({"type": "progress", "message": event})
            elif isinstance(event, dict):
                q.put({"type": "progress", **event})
            else:
                q.put({"type": "progress", "message": str(event)})

        if "progress_callback" not in worker_kwargs:
            worker_kwargs["progress_callback"] = progress_callback

        def runner() -> None:
            try:
                q.put({"type": "progress", "message": f"Started {label.lower()}"})
                result = target(*worker_args, **worker_kwargs)
                q.put({"type": "done", "result": result})
            except Exception as exc:  # noqa: BLE001
                q.put({"type": "error", "error": str(exc)})

        threading.Thread(target=runner, daemon=True).start()
        return job_id

    def _drain(self) -> None:
        with self._lock:
            items = list(self._queues.items())

        for job_id, q in items:
            while True:
                try:
                    event = q.get_nowait()
                except queue.Empty:
                    break

                with self._lock:
                    job = self._jobs.get(job_id)
                    if job is None:
                        continue

                    et = event.get("type")
                    if et == "progress":
                        message = event.get("message") or event.get("label") or "Working"
                        job["current_message"] = str(message)
                        payload = {"message": str(message), "timestamp": _now_iso()}
                        for key in ["stage", "tool", "action", "label", "element_type", "filename", "asset_id"]:
                            if key in event:
                                payload[key] = event[key]
                        job["events"].append(payload)
                        if len(job["events"]) > _EVENT_LIMIT:
                            job["events"] = job["events"][-_EVENT_LIMIT:]
                    elif et == "done":
                        job["status"] = "COMPLETED"
                        job["result"] = event.get("result")
                        job["current_message"] = "Completed"
                        self._queues.pop(job_id, None)
                    elif et == "error":
                        job["status"] = "FAILED"
                        job["error"] = event.get("error") or "Unknown error"
                        job["current_message"] = job["error"]
                        self._queues.pop(job_id, None)
                    job["updated_at"] = _now_iso()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        self._drain()
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def list_jobs(self, *, limit: int = 100, project_id: str | None = None) -> list[dict[str, Any]]:
        self._drain()
        with self._lock:
            rows = list(self._jobs.values())
        if project_id is not None:
            rows = [j for j in rows if j.get("project_id") == project_id]
        rows.sort(key=lambda j: j.get("updated_at") or "", reverse=True)
        return [dict(j) for j in rows[:limit]]


jobs = JobManager()
assistant_lock = threading.Lock()
assistant_session: AssistantSession | None = None


def _get_assistant_session() -> AssistantSession:
    global assistant_session
    with assistant_lock:
        if assistant_session is None:
            runner, session_id = create_orchestrator_runner()
            assistant_session = AssistantSession(runner=runner, session_id=session_id)
        return assistant_session


def _dispatch_pending_workflows() -> None:
    pending = get_pending_workflows()
    for req in pending:
        kind = req.get("kind", "workflow")
        pid = req.get("project_id")
        kwargs = req.get("kwargs", {})
        label = req.get("label", kind)

        if kind == "extraction":
            jobs.start_job(kind="extract", label=label, project_id=pid, target=api.extract, kwargs=kwargs)
        elif kind == "projection":
            jobs.start_job(
                kind="project",
                label=label,
                project_id=pid,
                target=api.project,
                kwargs={"space_id": kwargs["space_id"], "project_id": kwargs["project_id"]},
            )
        elif kind == "kg_extraction":
            jobs.start_job(
                kind="knowledge_graph",
                label=label,
                project_id=pid,
                target=api.extract_knowledge_graph,
                kwargs={"project_id": kwargs["project_id"]},
            )
        elif kind == "feedback_review":
            jobs.start_job(
                kind="feedback_review",
                label=label,
                project_id=pid,
                target=api.review_feedback,
                kwargs={"project_id": kwargs["project_id"]},
            )
        elif kind == "projection_review":
            jobs.start_job(
                kind="projection_review",
                label=label,
                project_id=pid,
                target=api.review_projections,
                kwargs={"space_id": kwargs["space_id"], "project_id": kwargs["project_id"]},
            )


class SpaceRef(BaseModel):
    space_id: str


class ProjectionReviewRequest(BaseModel):
    space_id: str
    project_id: str | None = None


class FeedbackResolveRequest(BaseModel):
    status: str
    notes: str = ""


class FeedbackReviewRequest(BaseModel):
    project_id: str | None = None


class GraphReviewRequest(BaseModel):
    mode: str = "auto"
    seed_count: int = 10


class UploadInitResponse(BaseModel):
    upload_id: str


class UploadCompleteRequest(BaseModel):
    upload_id: str


class AssistantChatRequest(BaseModel):
    message: str


class UploadFileItem(BaseModel):
    name: str
    relativePath: str
    uploadPath: str


class UploadProject(BaseModel):
    name: str
    files: list[UploadFileItem]


def _normalize_project_name(name: str, fallback: str = "project") -> str:
    import re

    candidate = (name or "").strip()
    if not candidate:
        candidate = fallback
    candidate = re.sub(r"[^A-Za-z0-9._ -]+", "_", candidate)
    candidate = candidate.strip(" ._")
    return candidate or fallback


def _create_unique_project_dir(project_name: str) -> Path:
    _UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    base_name = _normalize_project_name(project_name)
    candidate = _UPLOAD_ROOT / base_name
    suffix = 2
    while candidate.exists():
        candidate = _UPLOAD_ROOT / f"{base_name}_{suffix}"
        suffix += 1
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


def _next_available_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    idx = 2
    while True:
        candidate = path.with_name(f"{stem}_{idx}{suffix}")
        if not candidate.exists():
            return candidate
        idx += 1


def _run_upload_ingest(payload: list[UploadProject], progress_callback=None) -> dict[str, Any]:
    def emit(msg: str) -> None:
        if progress_callback:
            progress_callback({"message": msg})

    if not payload:
        return {"status": "completed", "message": "No projects provided."}

    upload_id = payload[0].files[0].uploadPath.split("/")[0] if payload and payload[0].files else ""
    temp_root = _UPLOAD_TEMP / upload_id if upload_id else _UPLOAD_TEMP

    total_ingested = 0
    total_dupes = 0
    created: list[str] = []

    try:
        emit(f"Preparing {len(payload)} project(s) for ingest")
        for idx, proj in enumerate(payload, start=1):
            upload_dir = _create_unique_project_dir(proj.name)
            emit(f"Moving files for {upload_dir.name} ({idx}/{len(payload)})")

            for file_info in proj.files:
                src = _safe_child(_UPLOAD_TEMP, file_info.uploadPath)
                rel = _safe_child(upload_dir, file_info.relativePath)
                dest = upload_dir / rel.relative_to(upload_dir)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest = _next_available_path(dest)
                if src.is_file():
                    shutil.move(str(src), str(dest))

            emit(f"Ingesting {upload_dir.name}")
            result = api.ingest(upload_dir)
            total_ingested += int(result.get("ingested", 0) or 0)
            total_dupes += int(result.get("duplicates", 0) or 0)
            created.append(upload_dir.name)
    finally:
        if temp_root.is_dir():
            shutil.rmtree(temp_root, ignore_errors=True)

    return {
        "status": "completed",
        "message": (
            f"Created {len(created)} project(s) · "
            f"{total_ingested} file(s) ingested, {total_dupes} duplicate(s) skipped."
        ),
        "created_projects": created,
        "ingested": total_ingested,
        "duplicates": total_dupes,
    }


app = FastAPI(title="MKB API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/projects")
def list_projects(limit: int = 100):
    return api.list_projects(limit=limit)


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    rows = api.list_projects(limit=500)
    for row in rows:
        if row["project_id"] == project_id:
            return row
    raise HTTPException(status_code=404, detail="Project not found")


@app.get("/api/projects/{project_id}/assets")
def list_project_assets(project_id: str):
    _parse_uuid(project_id, "project_id")
    return api.list_assets(project_id=project_id)


@app.get("/api/projects/{project_id}/processed-assets")
def list_project_processed_assets(project_id: str):
    _parse_uuid(project_id, "project_id")
    return api.list_processed_assets(project_id=project_id)


@app.post("/api/projects/{project_id}/process")
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


@app.post("/api/projects/{project_id}/extract")
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


@app.post("/api/projects/{project_id}/project")
def project_project(project_id: str, body: SpaceRef):
    _parse_uuid(project_id, "project_id")
    _parse_uuid(body.space_id, "space_id")
    job_id = jobs.start_job(
        kind="project",
        label="Project",
        project_id=project_id,
        target=api.project,
        kwargs={"space_id": body.space_id, "project_id": project_id},
    )
    return {"job_id": job_id}


@app.post("/api/projects/{project_id}/kg-extract")
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


@app.get("/api/projects/{project_id}/jobs")
def project_jobs(project_id: str):
    return jobs.list_jobs(project_id=project_id, limit=100)


@app.get("/api/frames")
def list_frames():
    return api.list_frames()


@app.get("/api/frames/{project_id}")
def get_frame(project_id: str):
    frame = api.get_frame(project_id)
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found")
    return frame


@app.get("/api/frames/{project_id}/history")
def get_frame_history(project_id: str):
    return api.get_extraction_history(project_id)


@app.get("/api/spaces")
def list_spaces():
    return api.list_spaces()


@app.get("/api/spaces/{space_id_or_name}")
def get_space(space_id_or_name: str):
    space = api.get_space(space_id_or_name)
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")
    return space


@app.get("/api/projections")
def list_projections(
    limit: int = 100,
    space_id: str | None = None,
    project_id: str | None = None,
    include_data: bool = False,
    newest_only: bool = False,
):
    rows = api.list_projections(
        space_id=space_id,
        project_id=project_id,
        include_data=include_data,
        newest_only=newest_only,
    )
    return rows[:limit]


@app.get("/api/projections/{projection_id}")
def get_projection(projection_id: str):
    row = api.get_projection(projection_id)
    if not row:
        raise HTTPException(status_code=404, detail="Projection not found")
    return row


@app.post("/api/projections/review")
def review_projections(body: ProjectionReviewRequest):
    _parse_uuid(body.space_id, "space_id")
    if body.project_id:
        _parse_uuid(body.project_id, "project_id")
        job_id = jobs.start_job(
            kind="projection_review",
            label="Projection Review",
            project_id=body.project_id,
            target=api.review_projections,
            kwargs={"space_id": body.space_id, "project_id": body.project_id},
        )
    else:
        job_id = jobs.start_job(
            kind="projection_review",
            label="Projection Review",
            target=api.review_projections_all,
            kwargs={"space_id": body.space_id},
        )
    return {"job_id": job_id}


@app.get("/api/feedback")
def list_feedback(limit: int = 100, status: str | None = None, project_id: str | None = None):
    rows = api.list_feedback(project_id=project_id, status=status)
    return rows[:limit]


@app.get("/api/feedback/summary/{project_id}")
def feedback_summary(project_id: str):
    return api.get_feedback_summary(project_id)


@app.post("/api/feedback/{feedback_id}/resolve")
def resolve_feedback(feedback_id: str, body: FeedbackResolveRequest):
    return api.resolve_feedback(feedback_id=feedback_id, status=body.status, notes=body.notes)


def _review_feedback_all(progress_callback=None) -> dict[str, Any]:
    projects = api.list_projects(limit=500)
    results = []
    for p in projects:
        pid = p["project_id"]
        summary = api.get_feedback_summary(pid)
        if int(summary.get("total", 0) or 0) == 0:
            continue
        if progress_callback:
            progress_callback({"message": f"Reviewing feedback for {pid[:8]}"})
        results.append(api.review_feedback(project_id=pid))
    return {"reviewed_projects": len(results), "results": results}


@app.post("/api/feedback/review")
def review_feedback(body: FeedbackReviewRequest):
    if body.project_id:
        _parse_uuid(body.project_id, "project_id")
        job_id = jobs.start_job(
            kind="feedback_review",
            label="Feedback Review",
            project_id=body.project_id,
            target=api.review_feedback,
            kwargs={"project_id": body.project_id},
        )
    else:
        job_id = jobs.start_job(
            kind="feedback_review",
            label="Feedback Review",
            target=_review_feedback_all,
        )
    return {"job_id": job_id}


@app.get("/api/graph")
def get_graph(project_id: str | None = None):
    return api.get_knowledge_graph(project_id=project_id)


@app.get("/api/graph/review-counts")
def get_graph_review_counts():
    return api.get_graph_review_counts()


@app.post("/api/graph/review")
def review_graph(body: GraphReviewRequest):
    job_id = jobs.start_job(
        kind="graph_review",
        label="Graph Review",
        target=api.review_knowledge_graph,
        kwargs={"mode": body.mode, "seed_count": body.seed_count},
    )
    return {"job_id": job_id}


@app.post("/api/graph/clear")
def clear_graph():
    return api.clear_knowledge_graphs()


@app.get("/api/jobs")
def list_jobs(limit: int = 100):
    return jobs.list_jobs(limit=limit)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    row = jobs.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return row


@app.post("/api/upload/init", response_model=UploadInitResponse)
def upload_init():
    upload_id = str(uuid.uuid4())
    (_UPLOAD_TEMP / upload_id).mkdir(parents=True, exist_ok=True)
    return {"upload_id": upload_id}


@app.post("/api/upload/file")
def upload_file(
    upload_id: str = Form(...),
    relative_path: str = Form(...),
    upload_path: str = Form(...),
    file: UploadFile = File(...),
):
    _parse_uuid(upload_id, "upload_id")
    base = _UPLOAD_TEMP / upload_id
    base.mkdir(parents=True, exist_ok=True)
    _safe_child(base, relative_path)
    dest = _safe_child(base, upload_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as fh:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)
    return {"ok": True}


@app.post("/api/upload/complete")
def upload_complete(body: UploadCompleteRequest):
    _parse_uuid(body.upload_id, "upload_id")
    marker = _UPLOAD_TEMP / body.upload_id / ".complete"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("done")
    return {"ok": True}


@app.post("/api/upload/ingest")
def upload_ingest(payload: list[UploadProject]):
    if not payload:
        raise HTTPException(status_code=400, detail="No projects uploaded")
    job_id = jobs.start_job(
        kind="upload",
        label="Upload Ingest",
        project_id="__upload__",
        target=_run_upload_ingest,
        args=(payload,),
    )
    return {"job_id": job_id}


@app.post("/api/assistant/chat")
def assistant_chat(body: AssistantChatRequest):
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    session = _get_assistant_session()

    def _run_chat(progress_callback=None):
        result = send_message(
            runner=session.runner,
            session_id=session.session_id,
            message=message,
            progress_callback=progress_callback,
        )
        _dispatch_pending_workflows()
        return result

    job_id = jobs.start_job(
        kind="orchestrator_chat",
        label="Assistant",
        target=_run_chat,
    )
    return {"job_id": job_id}
