"""Registry and storage helpers for user-uploaded skills."""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path
from typing import BinaryIO

from mkb.config import settings
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import CustomSkill
from mkb.web.uploads import safe_extract_archive, write_stream_bounded

SKILLS_ROOT = Path("data/skills")
MAX_SKILL_MD_CHARS = 80_000


def _slugify(value: str, fallback: str = "skill") -> str:
    text = (value or fallback).strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return slug or fallback


def _safe_relpath(value: str) -> Path | None:
    raw = (value or "").replace("\\", "/").lstrip("/")
    if not raw or raw.endswith("/"):
        return None
    path = Path(raw)
    if any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path


def _extract_title(skill_md: str, fallback: str) -> str:
    for line in skill_md.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped[2:].strip()
            if title:
                return title[:255]
    return fallback[:255]


def _extract_description(skill_md: str) -> str | None:
    for line in skill_md.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped[:1000]
    return None


def _unique_slug(session, base: str, existing_id: uuid.UUID | None = None) -> str:
    slug = base
    suffix = 2
    while True:
        existing = session.query(CustomSkill).filter_by(slug=slug).first()
        if not existing or (existing_id and existing.skill_id == existing_id):
            return slug
        slug = f"{base}_{suffix}"
        suffix += 1


def _root_with_skill_md(root: Path) -> Path:
    direct = root / "SKILL.md"
    if direct.is_file():
        return root

    children = [path for path in root.iterdir() if path.is_dir()]
    files = [path for path in root.iterdir() if path.is_file()]
    if len(children) == 1 and not files and (children[0] / "SKILL.md").is_file():
        return children[0]

    raise ValueError("Skill upload must contain a SKILL.md file at the skill root.")


def _write_stream(target: Path, stream: BinaryIO, *, total_remaining: int | None = None) -> int:
    return write_stream_bounded(
        target,
        stream,
        max_bytes=settings.upload_max_file_mb * 1024 * 1024,
        total_remaining=total_remaining,
    )


def _safe_extract_zip(zip_path: Path, dest_dir: Path) -> int:
    return safe_extract_archive(zip_path, dest_dir)


def _finalize_skill(staging_root: Path, *, source_type: str, fallback_name: str) -> dict:
    skill_root = _root_with_skill_md(staging_root)
    skill_md = (skill_root / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    if not skill_md.strip():
        raise ValueError("SKILL.md must not be empty.")
    if len(skill_md) > MAX_SKILL_MD_CHARS:
        raise ValueError("SKILL.md is too large.")

    name = _extract_title(skill_md, fallback_name)
    description = _extract_description(skill_md)
    files = sorted(path for path in skill_root.rglob("*") if path.is_file())
    if len(files) > settings.upload_max_files:
        raise ValueError("Skill contains too many files.")
    if sum(path.stat().st_size for path in files) > settings.upload_max_total_mb * 1024 * 1024:
        raise ValueError("Skill exceeds the total upload byte budget.")
    skill_id = uuid.uuid4()

    SKILLS_ROOT.mkdir(parents=True, exist_ok=True)
    with SyncSessionLocal() as session:
        slug = _unique_slug(session, _slugify(name))
        final_root = SKILLS_ROOT / f"{slug}_{skill_id.hex[:8]}"
        shutil.move(str(skill_root), final_root)

        skill = CustomSkill(
            skill_id=skill_id,
            name=name,
            slug=slug,
            description=description,
            source_type=source_type,
            storage_path=str(final_root),
            skill_md=skill_md,
            file_count=len(files),
            metadata_={
                "files": [
                    path.relative_to(skill_root).as_posix()
                    for path in files
                ],
            },
        )
        session.add(skill)
        session.commit()
        return _skill_to_dict(skill)


def create_skill_from_single_file(filename: str, stream: BinaryIO) -> dict:
    if Path(filename or "").name.lower() != "skill.md":
        raise ValueError("Single-file skill uploads must be named SKILL.md.")
    staging = SKILLS_ROOT / "_staging" / uuid.uuid4().hex
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        _write_stream(staging / "SKILL.md", stream)
        return _finalize_skill(staging, source_type="file", fallback_name="Uploaded skill")
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def create_skill_from_zip(filename: str, stream: BinaryIO) -> dict:
    if not (filename or "").lower().endswith(".zip"):
        raise ValueError("Archive skill uploads must be .zip files.")
    staging = SKILLS_ROOT / "_staging" / uuid.uuid4().hex
    archive = staging / "upload.zip"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        _write_stream(archive, stream)
        extract_root = staging / "extract"
        extracted = _safe_extract_zip(archive, extract_root)
        if extracted == 0:
            raise ValueError("Zip did not contain any regular files.")
        return _finalize_skill(
            extract_root,
            source_type="zip",
            fallback_name=Path(filename).stem or "Uploaded skill",
        )
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def create_skill_from_files(files: list[tuple[str, BinaryIO]]) -> dict:
    if not files:
        raise ValueError("Folder skill upload must include at least one file.")
    if len(files) > settings.upload_max_files:
        raise ValueError("Folder skill upload contains too many files.")
    staging = SKILLS_ROOT / "_staging" / uuid.uuid4().hex
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        top_name = "Uploaded skill"
        total_written = 0
        for relname, stream in files:
            rel = _safe_relpath(relname)
            if rel is None:
                continue
            if top_name == "Uploaded skill" and rel.parts:
                top_name = rel.parts[0]
            total_written += _write_stream(
                staging / rel,
                stream,
                total_remaining=settings.upload_max_total_mb * 1024 * 1024 - total_written,
            )
        return _finalize_skill(staging, source_type="folder", fallback_name=top_name)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _skill_to_dict(skill: CustomSkill, *, include_content: bool = False) -> dict:
    payload = {
        "skill_id": str(skill.skill_id),
        "name": skill.name,
        "slug": skill.slug,
        "description": skill.description,
        "source_type": skill.source_type,
        "file_count": skill.file_count,
        "metadata": skill.metadata_ or {},
        "created_at": skill.created_at.isoformat() if skill.created_at else None,
        "updated_at": skill.updated_at.isoformat() if skill.updated_at else None,
    }
    if include_content:
        payload["skill_md"] = skill.skill_md
        payload["storage_path"] = skill.storage_path
    return payload


def list_skills() -> list[dict]:
    with SyncSessionLocal() as session:
        skills = session.query(CustomSkill).order_by(CustomSkill.name).all()
        return [_skill_to_dict(skill) for skill in skills]


def get_skill(skill_id_or_slug: str, *, include_content: bool = False) -> dict | None:
    with SyncSessionLocal() as session:
        try:
            sid = uuid.UUID(str(skill_id_or_slug))
            skill = session.query(CustomSkill).filter_by(skill_id=sid).first()
        except ValueError:
            skill = session.query(CustomSkill).filter_by(slug=skill_id_or_slug).first()
        return _skill_to_dict(skill, include_content=include_content) if skill else None


def delete_skill(skill_id: str | uuid.UUID) -> dict:
    sid = uuid.UUID(str(skill_id))
    with SyncSessionLocal() as session:
        skill = session.query(CustomSkill).filter_by(skill_id=sid).first()
        if not skill:
            return {"error": f"Skill {skill_id} not found."}
        root = Path(skill.storage_path)
        name = skill.name
        session.delete(skill)
        session.commit()
    if root.exists() and root.is_dir():
        shutil.rmtree(root, ignore_errors=True)
    return {"ok": True, "deleted": name}


def skill_instruction_block(skill_ids: list[str] | None) -> str:
    if not skill_ids:
        return ""
    blocks: list[str] = []
    with SyncSessionLocal() as session:
        for raw_id in skill_ids:
            try:
                sid = uuid.UUID(str(raw_id))
                skill = session.query(CustomSkill).filter_by(skill_id=sid).first()
            except ValueError:
                skill = session.query(CustomSkill).filter_by(slug=str(raw_id)).first()
            if not skill:
                continue
            blocks.append(
                f"## {skill.name} ({skill.slug})\n"
                f"Source folder: {skill.storage_path}\n\n"
                f"{skill.skill_md.strip()}"
            )
    if not blocks:
        return ""
    return (
        "\n\nAttached user skills. Follow these SKILL.md instructions when relevant. "
        "If a skill references files in its source folder, inspect them only through available reading tools.\n\n"
        + "\n\n---\n\n".join(blocks)
    )
