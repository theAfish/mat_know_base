"""FastAPI application entry point for the MKB web layer."""
from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from mkb import api
from mkb.logging_setup import setup_logging

# Ensure logging is configured even when uvicorn imports this module directly
# (e.g. ``uvicorn mkb.web.api_server:app``) without going through ``mkb.cli``.
setup_logging()

from mkb.web._helpers import _parse_uuid, _safe_child, require_service_result, start_web_job_action  # noqa: E402
from mkb.web._state import (  # noqa: E402  (re-exported for back-compat tests)
    AssistantSession,
    JobManager,
    _dispatch_pending_workflows,
    _get_assistant_session,
    _now_iso,
    assistant_lock,
    assistant_session,
    jobs,
)
from mkb.web import uploads as upload_impl  # noqa: E402


# ── Upload compatibility wrappers ───────────────────────────────────────────

_UPLOAD_TEMP = Path("data/uploads/_temp")
_UPLOAD_ROOT = Path("data/uploads")


UploadCompleteRequest = upload_impl.UploadCompleteRequest
UploadExpandFile = upload_impl.UploadExpandFile
UploadExpandRequest = upload_impl.UploadExpandRequest
UploadExpandResponse = upload_impl.UploadExpandResponse
UploadFileItem = upload_impl.UploadFileItem
UploadInitResponse = upload_impl.UploadInitResponse
UploadProject = upload_impl.UploadProject


def _normalize_project_name(name: str, fallback: str = "project") -> str:
    return upload_impl.normalize_project_name(name, fallback)


def _create_unique_project_dir(project_name: str) -> Path:
    return upload_impl.create_unique_project_dir(project_name, _UPLOAD_ROOT)


def _next_available_path(path: Path) -> Path:
    return upload_impl.next_available_path(path)


def _is_archive(name: str) -> bool:
    return upload_impl.is_archive(name)


def _strip_archive_ext(name: str) -> str:
    return upload_impl.strip_archive_ext(name)


def _safe_extract_archive(archive_path: Path, dest_dir: Path) -> int:
    return upload_impl.safe_extract_archive(archive_path, dest_dir)


def _expand_temp_dir(temp_root: Path, emit=None) -> dict[str, Any]:
    return upload_impl.expand_temp_dir(
        temp_root,
        emit=emit,
        extract_archive=_safe_extract_archive,
    )


def _run_upload_ingest(payload: list[UploadProject], progress_callback=None) -> dict[str, Any]:
    return upload_impl.run_upload_ingest(
        payload,
        upload_temp=_UPLOAD_TEMP,
        create_project_dir=_create_unique_project_dir,
        api_module=api,
        progress_callback=progress_callback,
    )


# ── App + middleware ────────────────────────────────────────────────────────

app = FastAPI(title="MKB API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from mkb.web.routers import (  # noqa: E402
    assistant,
    feedback,
    frames,
    graph,
    health,
    jobs as jobs_router,
    projections,
    projects,
    settings as settings_router,
    skills,
    spaces,
)

for _r in (
    health,
    projects,
    frames,
    spaces,
    skills,
    projections,
    feedback,
    graph,
    jobs_router,
    assistant,
    settings_router,
):
    app.include_router(_r.router)


# ── Upload routes (must stay here so tests can monkeypatch this module) ─────


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
    job_id = start_web_job_action(
        jobs,
        "upload_ingest",
        job_project_id="__upload__",
        payload=payload,
        ingest_func=_run_upload_ingest,
    )
    return {"job_id": job_id}


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
        return require_service_result(result)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


__all__ = [
    "app",
    # Re-exported for back-compat (tests + external imports)
    "AssistantSession",
    "JobManager",
    "UploadCompleteRequest",
    "UploadExpandFile",
    "UploadExpandRequest",
    "UploadExpandResponse",
    "UploadFileItem",
    "UploadInitResponse",
    "UploadProject",
    "_UPLOAD_ROOT",
    "_UPLOAD_TEMP",
    "_create_unique_project_dir",
    "_dispatch_pending_workflows",
    "_expand_temp_dir",
    "_get_assistant_session",
    "_now_iso",
    "_parse_uuid",
    "_run_upload_ingest",
    "_safe_child",
    "_safe_extract_archive",
    "api",
    "assistant_lock",
    "assistant_session",
    "jobs",
]
