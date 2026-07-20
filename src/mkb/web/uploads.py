"""Upload/archive helpers shared by API routes and compatibility wrappers."""

from __future__ import annotations

import shutil
import tarfile
import zipfile
from pathlib import Path
from typing import Any, BinaryIO, Callable, Protocol

from pydantic import BaseModel

from mkb.web._helpers import _safe_child


class UploadBudgetExceeded(ValueError):
    """A streamed upload or archive exceeded a configured hard limit."""


def _mb(value: int) -> int:
    return max(1, int(value)) * 1024 * 1024


def validate_upload_path(value: str, *, max_depth: int | None = None) -> Path:
    """Validate a client/archive path before any directories are created."""
    from mkb.config import settings

    raw = (value or "").replace("\\", "/")
    path = Path(raw)
    depth_limit = max_depth or settings.upload_max_path_depth
    if (
        not raw
        or raw.startswith("/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise UploadBudgetExceeded("Upload path must be a safe relative path")
    if len(path.parts) > depth_limit:
        raise UploadBudgetExceeded(f"Upload path exceeds maximum depth of {depth_limit}")
    return path


def copy_stream_bounded(
    source: BinaryIO,
    target: BinaryIO,
    *,
    max_bytes: int,
    total_remaining: int | None = None,
) -> int:
    """Copy a stream while enforcing limits before writing each chunk."""
    written = 0
    allowed = min(max_bytes, total_remaining) if total_remaining is not None else max_bytes
    if allowed < 0:
        raise UploadBudgetExceeded("Upload session has exhausted its byte budget")
    while True:
        chunk = source.read(min(1024 * 1024, allowed - written + 1))
        if not chunk:
            return written
        written += len(chunk)
        if written > allowed:
            raise UploadBudgetExceeded("Upload exceeds configured byte budget")
        target.write(chunk)


def write_stream_bounded(
    target: Path,
    source: BinaryIO,
    *,
    max_bytes: int,
    total_remaining: int | None = None,
) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("wb") as output:
            return copy_stream_bounded(
                source,
                output,
                max_bytes=max_bytes,
                total_remaining=total_remaining,
            )
    except Exception:
        target.unlink(missing_ok=True)
        raise


def directory_usage(root: Path) -> tuple[int, int]:
    files = [path for path in root.rglob("*") if path.is_file()] if root.is_dir() else []
    return len(files), sum(path.stat().st_size for path in files)


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
    from mkb.config import settings

    dest_root = dest_dir.resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    expanded = 0
    seen: set[str] = set()
    max_members = settings.archive_max_members
    max_member_bytes = _mb(settings.archive_max_member_mb)
    max_expanded_bytes = _mb(settings.archive_max_expanded_mb)
    max_ratio = settings.archive_max_compression_ratio
    max_depth = settings.upload_max_path_depth
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
                try:
                    rel = validate_upload_path(member_name, max_depth=max_depth)
                except UploadBudgetExceeded:
                    continue
                normalized = rel.as_posix()
                if normalized in seen:
                    raise UploadBudgetExceeded(f"Archive contains duplicate member: {normalized}")
                seen.add(normalized)
                if member.file_size > max_member_bytes:
                    raise UploadBudgetExceeded(f"Archive member is too large: {normalized}")
                if member.file_size / max(member.compress_size, 1) > max_ratio:
                    raise UploadBudgetExceeded(
                        f"Archive member has excessive compression ratio: {normalized}"
                    )
                expanded += member.file_size
                if expanded > max_expanded_bytes:
                    raise UploadBudgetExceeded("Archive expanded size exceeds configured budget")
                mode = member.external_attr >> 16
                if mode and (mode & 0o170000) not in {0, 0o100000}:
                    continue
                count += 1
                if count > max_members:
                    raise UploadBudgetExceeded("Archive contains too many members")
                target = (dest_dir / member_name).resolve()
                try:
                    target.relative_to(dest_root)
                except ValueError:
                    continue
                target = next_available_path(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with zf.open(member) as src, target.open("wb") as out:
                        copy_stream_bounded(src, out, max_bytes=max_member_bytes)
                except Exception:
                    target.unlink(missing_ok=True)
                    raise
        return count

    with tarfile.open(archive_path, "r:*") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            member_name = member.name.replace("\\", "/")
            if not member_name:
                continue
            if member_name.startswith("__MACOSX/") or member_name.endswith("/.DS_Store"):
                continue
            try:
                rel = validate_upload_path(member_name, max_depth=max_depth)
            except UploadBudgetExceeded:
                continue
            normalized = rel.as_posix()
            if normalized in seen:
                raise UploadBudgetExceeded(f"Archive contains duplicate member: {normalized}")
            seen.add(normalized)
            count += 1
            if count > max_members:
                raise UploadBudgetExceeded("Archive contains too many members")
            if member.size > max_member_bytes:
                raise UploadBudgetExceeded(f"Archive member is too large: {normalized}")
            expanded += member.size
            if expanded > max_expanded_bytes:
                raise UploadBudgetExceeded("Archive expanded size exceeds configured budget")
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
            try:
                with target.open("wb") as out:
                    copy_stream_bounded(src, out, max_bytes=max_member_bytes)
            except Exception:
                target.unlink(missing_ok=True)
                raise
    if expanded / max(archive_path.stat().st_size, 1) > max_ratio:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise UploadBudgetExceeded("Archive has excessive compression ratio")
    return count


def expand_temp_dir(
    temp_root: Path,
    emit: Callable[[str], None] | None = None,
    extract_archive: Callable[[Path, Path], int] = safe_extract_archive,
) -> dict[str, Any]:
    extracted: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    total_expanded = 0

    if not temp_root.is_dir():
        return {"files": [], "extracted": extracted, "failed": failed}

    from mkb.config import settings

    for _ in range(settings.archive_max_nesting):
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
            except (zipfile.BadZipFile, tarfile.TarError, OSError, UploadBudgetExceeded) as exc:
                shutil.rmtree(extract_target, ignore_errors=True)
                failed.append({"archive": str(archive.relative_to(temp_root)), "error": str(exc)})
                continue
            extracted_bytes = sum(
                path.stat().st_size for path in extract_target.rglob("*") if path.is_file()
            )
            if total_expanded + extracted_bytes > _mb(settings.archive_max_expanded_mb):
                shutil.rmtree(extract_target, ignore_errors=True)
                failed.append({
                    "archive": str(archive.relative_to(temp_root)),
                    "error": "Archive session expanded size exceeds configured budget",
                })
                continue
            total_expanded += extracted_bytes
            extracted.append({"archive": str(archive.relative_to(temp_root)), "count": count})
            try:
                archive.unlink()
            except OSError:
                pass

    remaining_archives = [
        path for path in temp_root.rglob("*") if path.is_file() and is_archive(path.name)
    ]
    for archive in remaining_archives:
        rel = str(archive.relative_to(temp_root))
        if not any(item["archive"] == rel for item in failed):
            failed.append({"archive": rel, "error": "Archive nesting limit exceeded"})

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
