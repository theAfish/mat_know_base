"""Upload/archive helpers shared by API routes and compatibility wrappers."""

from __future__ import annotations

import shutil
import tarfile
import zipfile
from pathlib import Path
from typing import Any, Callable, Protocol

from pydantic import BaseModel

from mkb.web._helpers import _safe_child


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
    name_auto: bool = True


class IngestApi(Protocol):
    def ingest(self, path: Path, **kwargs): ...


_ARCHIVE_SUFFIXES = (
    ".zip",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tbz2",
    ".tbz",
    ".tar.xz",
    ".txz",
)


def normalize_project_name(name: str, fallback: str = "project") -> str:
    import re

    candidate = (name or "").strip()
    if not candidate:
        candidate = fallback
    candidate = re.sub(r"[^A-Za-z0-9._ -]+", "_", candidate)
    candidate = candidate.strip(" ._")
    return candidate or fallback


def create_unique_project_dir(project_name: str, upload_root: Path) -> Path:
    upload_root.mkdir(parents=True, exist_ok=True)
    base_name = normalize_project_name(project_name)
    candidate = upload_root / base_name
    suffix = 2
    while candidate.exists():
        candidate = upload_root / f"{base_name}_{suffix}"
        suffix += 1
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


def next_available_path(path: Path) -> Path:
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


def is_archive(name: str) -> bool:
    lower = name.lower()
    return any(lower.endswith(ext) for ext in _ARCHIVE_SUFFIXES)


def strip_archive_ext(name: str) -> str:
    lower = name.lower()
    for ext in _ARCHIVE_SUFFIXES:
        if lower.endswith(ext):
            return name[: -len(ext)]
    return name


def safe_extract_archive(archive_path: Path, dest_dir: Path) -> int:
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
                if (
                    member_name.startswith("__MACOSX/")
                    or "/.DS_Store" in member_name
                    or member_name.endswith("/.DS_Store")
                ):
                    continue
                target = (dest_dir / member_name).resolve()
                try:
                    target.relative_to(dest_root)
                except ValueError:
                    continue
                target = next_available_path(target)
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
            target = next_available_path(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as out:
                shutil.copyfileobj(src, out)
            count += 1
    return count


def expand_temp_dir(
    temp_root: Path,
    emit: Callable[[str], None] | None = None,
    extract_archive: Callable[[Path, Path], int] = safe_extract_archive,
) -> dict[str, Any]:
    extracted: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    if not temp_root.is_dir():
        return {"files": [], "extracted": extracted, "failed": failed}

    for _ in range(8):
        archives = [p for p in temp_root.rglob("*") if p.is_file() and is_archive(p.name)]
        if not archives:
            break
        for archive in archives:
            extract_target = archive.parent / strip_archive_ext(archive.name)
            if extract_target.exists():
                extract_target = next_available_path(extract_target)
            if emit:
                emit(f"Extracting {archive.relative_to(temp_root)}")
            try:
                count = extract_archive(archive, extract_target)
            except (zipfile.BadZipFile, tarfile.TarError, OSError) as exc:
                failed.append({"archive": str(archive.relative_to(temp_root)), "error": str(exc)})
                continue
            extracted.append({"archive": str(archive.relative_to(temp_root)), "count": count})
            try:
                archive.unlink()
            except OSError:
                pass

    files: list[dict[str, Any]] = []
    root_resolved = temp_root.resolve()
    for path in sorted(temp_root.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel = path.resolve().relative_to(root_resolved)
        except ValueError:
            continue
        files.append({"uploadPath": rel.as_posix(), "size": path.stat().st_size})
    return {"files": files, "extracted": extracted, "failed": failed}


def run_upload_ingest(
    payload: list[UploadProject],
    *,
    upload_temp: Path,
    create_project_dir: Callable[[str], Path],
    api_module: IngestApi,
    progress_callback=None,
) -> dict[str, Any]:
    def emit(msg: str) -> None:
        if progress_callback:
            progress_callback({"message": msg})

    if not payload:
        return {"status": "completed", "message": "No projects provided."}

    upload_id = str(payload[0].upload_id) if payload else ""
    temp_root = upload_temp / upload_id if upload_id else upload_temp

    total_ingested = 0
    total_dupes = 0
    created: list[str] = []
    reused = 0

    try:
        emit(f"Preparing {len(payload)} project(s) for ingest")
        for idx, project in enumerate(payload, start=1):
            upload_dir = create_project_dir(project.name)
            emit(f"Moving files for {upload_dir.name} ({idx}/{len(payload)})")

            for file_info in project.files:
                src = _safe_child(temp_root, file_info.uploadPath)
                if not src.is_file():
                    continue
                rel_full = _safe_child(upload_dir, file_info.relativePath)
                dest = upload_dir / rel_full.relative_to(upload_dir.resolve())
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest = next_available_path(dest)
                shutil.move(str(src), str(dest))

            emit(f"Ingesting {upload_dir.name}")
            result = api_module.ingest(
                upload_dir,
                label=project.name if not project.name_auto else None,
                user_named=not project.name_auto,
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

