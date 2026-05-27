from __future__ import annotations

import ctypes
import queue
import shutil
import tarfile
import tempfile
import threading
import uuid
import io
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from mkb import api
from mkb.agents.orchestrator import create_orchestrator_runner, send_message
from mkb.agents.tools.orchestrator_tools import get_pending_workflows
from mkb.config import settings


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


class JobCancelled(BaseException):
    """Raised in a worker thread to cancel a running job."""


class JobManager:
    def __init__(self, max_concurrent: int | None = None) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._queues: dict[str, queue.Queue] = {}
        self._lock = threading.Lock()
        self._cancelled: set[str] = set()
        self._threads: dict[str, int] = {}  # job_id -> thread ident
        limit = max_concurrent if max_concurrent is not None else settings.max_concurrent_jobs
        self._semaphore = threading.Semaphore(max(1, limit))

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
                "status": "QUEUED",
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
            self._semaphore.acquire()
            try:
                if job_id in self._cancelled:
                    q.put({"type": "cancelled"})
                    return
                with self._lock:
                    self._threads[job_id] = threading.current_thread().ident  # type: ignore[assignment]
                q.put({"type": "running"})
                q.put({"type": "progress", "message": f"Started {label.lower()}"})
                result = target(*worker_args, **worker_kwargs)
                q.put({"type": "done", "result": result})
            except JobCancelled:
                q.put({"type": "cancelled"})
            except Exception as exc:  # noqa: BLE001
                q.put({"type": "error", "error": str(exc)})
            finally:
                self._semaphore.release()
                with self._lock:
                    self._threads.pop(job_id, None)

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
                    if et == "running":
                        job["status"] = "RUNNING"
                        job["current_message"] = "Running"
                    elif et == "progress":
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
                    elif et == "cancelled":
                        job["status"] = "CANCELLED"
                        job["current_message"] = "Cancelled"
                        self._queues.pop(job_id, None)
                    job["updated_at"] = _now_iso()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        self._drain()
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def cancel_job(self, job_id: str) -> bool:
        """Request cancellation of a QUEUED or RUNNING job.

        Returns True if the job was found and a cancellation was initiated.
        QUEUED jobs are marked CANCELLED immediately; RUNNING jobs receive an
        async exception via ctypes so the worker thread can clean up.
        """
        self._drain()
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            status = job.get("status")
            if status not in ("QUEUED", "RUNNING"):
                return False
            self._cancelled.add(job_id)
            if status == "QUEUED":
                # Thread is blocked on semaphore — mark immediately so the UI
                # sees the change; the runner will handle cleanup on start.
                job["status"] = "CANCELLED"
                job["current_message"] = "Cancelled"
                job["updated_at"] = _now_iso()
                self._queues.pop(job_id, None)
            thread_id = self._threads.get(job_id)

        if thread_id is not None:
            # Best-effort: raise JobCancelled in the worker thread.
            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(thread_id),
                ctypes.py_object(JobCancelled),
            )
        return True

    def list_jobs(self, *, limit: int = 100, project_id: str | None = None) -> list[dict[str, Any]]:
        self._drain()
        with self._lock:
            rows = list(self._jobs.values())
        if project_id is not None:
            rows = [j for j in rows if j.get("project_id") == project_id]
        _active = {"QUEUED", "RUNNING"}
        rows.sort(
            key=lambda j: (
                0 if j.get("status") in _active else 1,
                j.get("updated_at") or "",
            ),
            reverse=False,
        )
        # active jobs first (ascending order within active), then completed desc
        active = [j for j in rows if j.get("status") in _active]
        inactive = sorted(
            [j for j in rows if j.get("status") not in _active],
            key=lambda j: j.get("updated_at") or "",
            reverse=True,
        )
        rows = active + inactive
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
            proj_kwargs = {
                "space_id": kwargs["space_id"],
                "project_id": kwargs["project_id"],
            }
            if "source_type" in kwargs:
                proj_kwargs["source_type"] = kwargs["source_type"]
            jobs.start_job(
                kind="project",
                label=label,
                project_id=pid,
                target=api.project,
                kwargs=proj_kwargs,
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


class ProjectionRunRequest(BaseModel):
    space_id: str
    source_type: str = "frame"  # "frame" | "markdown"


class SpaceCreateRequest(BaseModel):
    name: str
    domain: str
    extraction_schema: dict
    system_prompt: str
    field_descriptions: dict
    description: str | None = None
    purpose: str = "tabular_database"


class SpaceUpdateRequest(BaseModel):
    name: str | None = None
    domain: str | None = None
    description: str | None = None
    purpose: str | None = None
    extraction_schema: dict | None = None
    system_prompt: str | None = None
    field_descriptions: dict | None = None


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


class UploadExpandRequest(BaseModel):
    upload_id: str


class UploadExpandFile(BaseModel):
    uploadPath: str
    size: int


class UploadExpandResponse(BaseModel):
    files: list[UploadExpandFile]
    extracted: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []


class AssistantChatRequest(BaseModel):
    message: str


class UploadFileItem(BaseModel):
    name: str
    relativePath: str
    uploadPath: str


class UploadProject(BaseModel):
    name: str
    upload_id: str
    files: list[UploadFileItem]
    # True (default) means ``name`` was auto-generated (e.g. folder basename
    # from the upload-preview grouping) and may be overwritten later by an
    # auto-rename from extraction. False means the user explicitly typed/edited
    # the name and it should be preserved.
    name_auto: bool = True


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


_ARCHIVE_SUFFIXES = (
    ".zip",
    ".tar",
    ".tar.gz", ".tgz",
    ".tar.bz2", ".tbz2", ".tbz",
    ".tar.xz", ".txz",
)


def _is_archive(name: str) -> bool:
    lower = name.lower()
    return any(lower.endswith(ext) for ext in _ARCHIVE_SUFFIXES)


def _strip_archive_ext(name: str) -> str:
    lower = name.lower()
    for ext in _ARCHIVE_SUFFIXES:
        if lower.endswith(ext):
            return name[: -len(ext)]
    return name


def _safe_extract_archive(archive_path: Path, dest_dir: Path) -> int:
    """Extract an archive into ``dest_dir`` safely.

    - Rejects entries whose resolved path would escape ``dest_dir`` (zip-slip).
    - Skips symlinks/hardlinks and non-regular entries.
    - Resolves collisions via ``_next_available_path``.
    Returns the number of regular files extracted.
    """
    dest_root = dest_dir.resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    name = archive_path.name.lower()

    if name.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                member_name = member.filename.replace("\\", "/")
                if not member_name or member_name.endswith("/"):
                    continue
                # Skip macOS metadata noise
                if member_name.startswith("__MACOSX/") or "/.DS_Store" in member_name or member_name.endswith("/.DS_Store"):
                    continue
                target = (dest_dir / member_name).resolve()
                try:
                    target.relative_to(dest_root)
                except ValueError:
                    continue
                target = _next_available_path(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, target.open("wb") as out:
                    shutil.copyfileobj(src, out)
                count += 1
        return count

    # tar family (auto-detects compression with "r:*")
    with tarfile.open(archive_path, "r:*") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                # skip directories, symlinks, hardlinks, devices, fifos
                continue
            member_name = member.name.replace("\\", "/").lstrip("/")
            if not member_name:
                continue
            if member_name.startswith("__MACOSX/") or member_name.endswith("/.DS_Store"):
                continue
            target = (dest_dir / member_name).resolve()
            try:
                target.relative_to(dest_root)
            except ValueError:
                continue
            src = tf.extractfile(member)
            if src is None:
                continue
            target = _next_available_path(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as out:
                shutil.copyfileobj(src, out)
            count += 1
    return count


def _expand_temp_dir(temp_root: Path, emit=None) -> dict[str, Any]:
    """Expand archives in ``temp_root`` and return the resulting file tree.

    For each archive found anywhere in ``temp_root`` we extract it into a
    sibling directory (named after the archive without its suffix) and
    delete the archive file. Returns a dict with::

        {
            "files": [{"uploadPath": str, "size": int}, ...],
            "extracted": [{"archive": str, "count": int}, ...],
            "failed":    [{"archive": str, "error": str}, ...],
        }

    ``uploadPath`` values are relative to ``temp_root`` and use forward
    slashes so they can round-trip through the JSON API.
    """
    def _emit(msg: str) -> None:
        if emit:
            emit(msg)

    extracted: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    if not temp_root.is_dir():
        return {"files": [], "extracted": extracted, "failed": failed}

    # Repeatedly scan so nested archives (an archive that contains another
    # archive) get expanded too. Bound the loop to a safe max depth.
    for _ in range(8):
        archives = [
            p for p in temp_root.rglob("*")
            if p.is_file() and _is_archive(p.name)
        ]
        if not archives:
            break
        for archive in archives:
            extract_target = archive.parent / _strip_archive_ext(archive.name)
            if extract_target.exists():
                extract_target = _next_available_path(extract_target)
            _emit(f"Extracting {archive.relative_to(temp_root)}")
            try:
                n = _safe_extract_archive(archive, extract_target)
            except (zipfile.BadZipFile, tarfile.TarError, OSError) as exc:
                failed.append({
                    "archive": str(archive.relative_to(temp_root)),
                    "error": str(exc),
                })
                continue
            extracted.append({
                "archive": str(archive.relative_to(temp_root)),
                "count": n,
            })
            try:
                archive.unlink()
            except OSError:
                pass

    # Build the resulting file tree
    files: list[dict[str, Any]] = []
    root_resolved = temp_root.resolve()
    for p in sorted(temp_root.rglob("*")):
        if not p.is_file():
            continue
        try:
            rel = p.resolve().relative_to(root_resolved)
        except ValueError:
            continue
        files.append({
            "uploadPath": rel.as_posix(),
            "size": p.stat().st_size,
        })
    return {"files": files, "extracted": extracted, "failed": failed}


def _run_upload_ingest(payload: list[UploadProject], progress_callback=None) -> dict[str, Any]:
    def emit(msg: str) -> None:
        if progress_callback:
            progress_callback({"message": msg})

    if not payload:
        return {"status": "completed", "message": "No projects provided."}

    upload_id = str(payload[0].upload_id) if payload else ""
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
                src = _safe_child(temp_root, file_info.uploadPath)
                if not src.is_file():
                    continue
                rel_full = _safe_child(upload_dir, file_info.relativePath)
                dest = upload_dir / rel_full.relative_to(upload_dir.resolve())
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest = _next_available_path(dest)
                shutil.move(str(src), str(dest))

            emit(f"Ingesting {upload_dir.name}")
            result = api.ingest(
                upload_dir,
                label=proj.name if not proj.name_auto else None,
                user_named=not proj.name_auto,
            )
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


class ProjectUpdateRequest(BaseModel):
    label: str


@app.patch("/api/projects/{project_id}")
def update_project(project_id: str, body: ProjectUpdateRequest):
    _parse_uuid(project_id, "project_id")
    result = api.rename_project(project_id, body.label, user_initiated=True)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


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


@app.post("/api/spaces")
def create_space(body: SpaceCreateRequest):
    result = api.create_space(
        name=body.name,
        domain=body.domain,
        extraction_schema=body.extraction_schema,
        system_prompt=body.system_prompt,
        field_descriptions=body.field_descriptions,
        description=body.description,
        purpose=body.purpose,
    )
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.put("/api/spaces/{space_id}")
def update_space(space_id: str, body: SpaceUpdateRequest):
    _parse_uuid(space_id, "space_id")
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = api.update_space(space_id, **changes)
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.delete("/api/spaces/{space_id}")
def delete_space(space_id: str):
    _parse_uuid(space_id, "space_id")
    result = api.delete_space(space_id)
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


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


@app.delete("/api/projections/{projection_id}", status_code=204)
def delete_projection(projection_id: str):
    found = api.delete_projection(projection_id)
    if not found:
        raise HTTPException(status_code=404, detail="Projection not found")


@app.get("/api/projections/{projection_id}/export")
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
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
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

        # Multi-file: zip the export dir
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


@app.get("/api/spaces/{space_id_or_name}/export")
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
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
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


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    ok = jobs.cancel_job(job_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Job not found or not cancellable")
    return {"ok": True}


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


@app.post("/api/upload/expand", response_model=UploadExpandResponse)
def upload_expand(body: UploadExpandRequest):
    """Extract any archives in the session's temp dir and return the file tree.

    Safe to call multiple times — archives that have already been removed
    simply don't appear in the next pass. ``.complete`` and other dot files
    are filtered out of the returned tree.
    """
    _parse_uuid(body.upload_id, "upload_id")
    temp_root = _UPLOAD_TEMP / body.upload_id
    result = _expand_temp_dir(temp_root)
    # Hide internal marker files (e.g. ".complete") from the UI tree.
    result["files"] = [
        f for f in result["files"]
        if not Path(f["uploadPath"]).name.startswith(".")
    ]
    return result


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


# ─── Runtime Settings ────────────────────────────────────────────────────────

class SettingsUpdateRequest(BaseModel):
    pdf_backend: str | None = None
    mineru_api_base: str | None = None
    mineru_api_token: str | None = None
    mineru_api_model_version: str | None = None
    mineru_api_language: str | None = None
    mineru_api_enable_ocr: bool | None = None
    mineru_api_enable_formula: bool | None = None
    mineru_api_enable_table: bool | None = None
    mineru_api_timeout: int | None = None


@app.get("/api/settings")
def get_settings_endpoint():
    from mkb import runtime_settings

    return runtime_settings.public_view()


@app.put("/api/settings")
def update_settings_endpoint(body: SettingsUpdateRequest):
    from mkb import runtime_settings

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        result = runtime_settings.update_settings(updates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return runtime_settings.public_view(result)


# ─── Per-asset manual processed upload ──────────────────────────────────────

@app.post("/api/assets/{asset_id}/processed-upload")
def upload_processed_for_asset(
    asset_id: str,
    files: list[UploadFile] = File(...),
    relative_paths: list[str] | None = Form(default=None),
    primary_file: str | None = Form(default=None),
    processing_type: str | None = Form(default=None),
    output_format: str | None = Form(default=None),
):
    """Attach user-provided processed output (e.g. a hand-edited .md plus
    images/) to an existing raw asset.

    The request is multipart/form-data with one or more `files` and optional
    parallel `relative_paths` entries so callers can preserve nested layout
    such as ``images/figure1.png``.
    """
    _parse_uuid(asset_id, "asset_id")
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    if relative_paths is not None and len(relative_paths) != len(files):
        raise HTTPException(
            status_code=400,
            detail="relative_paths length must match files",
        )

    tmp_dir = Path(tempfile.mkdtemp(prefix="mkb_proc_upload_"))
    try:
        for idx, upload in enumerate(files):
            rel = (relative_paths[idx] if relative_paths else None) or upload.filename
            if not rel:
                continue
            dest = _safe_child(tmp_dir, rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as fh:
                while True:
                    chunk = upload.file.read(1024 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)

        try:
            result = api.link_manual_processed_data(
                processed_dir=tmp_dir,
                asset_id=asset_id,
                primary_file=primary_file,
                processing_type=processing_type,
                output_format=output_format,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
