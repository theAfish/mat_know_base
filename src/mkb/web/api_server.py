"""FastAPI application entry point for the MKB web layer.

Most route logic lives in ``mkb.web.routers.*``. The upload cluster
(temp dirs, archive extraction, ``_run_upload_ingest`` and the
``/api/upload/*`` routes) intentionally stays inline because tests in
``tests/test_api_upload_ingest.py`` monkeypatch module attributes here.
"""
from __future__ import annotations

import shutil
import tarfile
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from mkb import api
from mkb.logging_setup import setup_logging

# Ensure logging is configured even when uvicorn imports this module directly
# (e.g. ``uvicorn mkb.web.api_server:app``) without going through ``mkb.cli``.
setup_logging()

from mkb.web._helpers import _parse_uuid, _safe_child  # noqa: E402
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


# ── Upload cluster (kept inline for test monkeypatch compatibility) ─────────

_UPLOAD_TEMP = Path("data/uploads/_temp")
_UPLOAD_ROOT = Path("data/uploads")


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

    with tarfile.open(archive_path, "r:*") as tf:
        for member in tf.getmembers():
            if not member.isfile():
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
    """Expand archives in ``temp_root`` and return the resulting file tree."""
    def _emit(msg: str) -> None:
        if emit:
            emit(msg)

    extracted: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    if not temp_root.is_dir():
        return {"files": [], "extracted": extracted, "failed": failed}

    # Bounded loop so nested archives (an archive that contains another
    # archive) eventually fully unpack.
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
    reused = 0

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
            if result.get("project_reused"):
                reused += 1
                shutil.rmtree(upload_dir, ignore_errors=True)
            else:
                created.append(upload_dir.name)
    finally:
        if temp_root.is_dir():
            shutil.rmtree(temp_root, ignore_errors=True)

    return {
        "status": "completed",
        "message": (
            f"Created {len(created)} project(s), reused {reused} existing project(s) · "
            f"{total_ingested} file(s) ingested, {total_dupes} duplicate(s) skipped."
        ),
        "created_projects": created,
        "reused_projects": reused,
        "ingested": total_ingested,
        "duplicates": total_dupes,
    }


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
    spaces,
)

for _r in (
    health,
    projects,
    frames,
    spaces,
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
    job_id = jobs.start_job(
        kind="upload",
        label="Upload Ingest",
        project_id="__upload__",
        target=_run_upload_ingest,
        args=(payload,),
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
        return result
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
