"""FastAPI application entry point for the MKB web layer."""
from __future__ import annotations

import logging
import shutil
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from mkb.config import settings
from mkb.logging_setup import setup_logging

# Ensure logging is configured even when uvicorn imports this module directly
# (e.g. ``uvicorn mkb.web.api_server:app``) without going through ``mkb.cli``.
setup_logging()

from mkb.web._helpers import _parse_uuid, _safe_child, require_service_result  # noqa: E402
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
from mkb.web.security import ApiSecurityMiddleware  # noqa: E402
from mkb.web.request_context import RequestContextMiddleware  # noqa: E402


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
    from mkb.web.dependencies import get_knowledge_base

    return upload_impl.run_upload_ingest(
        payload,
        upload_temp=_UPLOAD_TEMP,
        create_project_dir=_create_unique_project_dir,
        api_module=get_knowledge_base(),
        progress_callback=progress_callback,
    )


# ── App + middleware ────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    from mkb.runtime_settings import get_setting
    from mkb.web.dependencies import close_knowledge_base, get_knowledge_base

    for warning in settings.validate_startup(log_level=get_setting("log_level")):
        logger.warning("UNSAFE LOCAL OVERRIDE: %s", warning)
    knowledge_base = get_knowledge_base()
    interrupted = knowledge_base.jobs.recover_interrupted()
    if interrupted:
        logger.warning("Marked %d background job(s) interrupted after restart", interrupted)
    logger.info(
        "Deployment boundary active (mode=%s, host=%s, cors=%s)",
        settings.deployment_mode.value,
        settings.api_host,
        settings.cors_origins,
    )
    try:
        yield
    finally:
        close_knowledge_base()


app = FastAPI(title="MKB API", version="0.1.0", lifespan=_lifespan)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(ApiSecurityMiddleware, settings=settings)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Accept", "Authorization", "Content-Type", "X-Request-ID"],
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
    post_processor_scripts,
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
    post_processor_scripts,
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
    try:
        upload_impl.validate_upload_path(relative_path)
        upload_impl.validate_upload_path(upload_path)
        dest = _safe_child(base, upload_path)
        file_count, used_bytes = upload_impl.directory_usage(base)
        if dest.exists():
            raise upload_impl.UploadBudgetExceeded("Duplicate upload path")
        if file_count >= settings.upload_max_files:
            raise upload_impl.UploadBudgetExceeded("Upload session contains too many files")
        upload_impl.write_stream_bounded(
            dest,
            file.file,
            max_bytes=settings.upload_max_file_mb * 1024 * 1024,
            total_remaining=settings.upload_max_total_mb * 1024 * 1024 - used_bytes,
        )
    except upload_impl.UploadBudgetExceeded as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
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
    from mkb.web.dependencies import get_knowledge_base

    job = get_knowledge_base().jobs.submit_action(
        "upload_ingest",
        job_project_id="__upload__",
        payload=payload,
        ingest_func=_run_upload_ingest,
    )
    return {"job_id": str(job.id)}


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
    if len(files) > settings.upload_max_files:
        raise HTTPException(status_code=413, detail="Too many files uploaded")
    if relative_paths is not None and len(relative_paths) != len(files):
        raise HTTPException(
            status_code=400,
            detail="relative_paths length must match files",
        )

    tmp_dir = Path(tempfile.mkdtemp(prefix="mkb_proc_upload_"))
    try:
        total_written = 0
        for idx, upload in enumerate(files):
            rel = (relative_paths[idx] if relative_paths else None) or upload.filename
            if not rel:
                continue
            try:
                upload_impl.validate_upload_path(rel)
                dest = _safe_child(tmp_dir, rel)
                if dest.exists():
                    raise upload_impl.UploadBudgetExceeded("Duplicate upload path")
                total_written += upload_impl.write_stream_bounded(
                    dest,
                    upload.file,
                    max_bytes=settings.upload_max_file_mb * 1024 * 1024,
                    total_remaining=(
                        settings.upload_max_total_mb * 1024 * 1024 - total_written
                    ),
                )
            except upload_impl.UploadBudgetExceeded as exc:
                raise HTTPException(status_code=413, detail=str(exc)) from exc

        try:
            from mkb.web.dependencies import get_knowledge_base

            result = get_knowledge_base().materials.library.link_processed(
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
    "assistant_lock",
    "assistant_session",
    "jobs",
]
