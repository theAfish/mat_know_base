"""Project naming and collision rules shared by all ingestion surfaces."""

from __future__ import annotations

import re
from pathlib import Path


def normalize_project_name(name: str, fallback: str = "project") -> str:
    candidate = (name or "").strip() or fallback
    candidate = re.sub(r"[^A-Za-z0-9._ -]+", "_", candidate).strip(" ._")
    return candidate or "project"


def create_unique_project_dir(upload_root: Path, project_name: str) -> Path:
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
    suffix = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{suffix}{path.suffix}")
        if not candidate.exists():
            return candidate
        suffix += 1


def safe_relative_path(raw: str | Path, base: Path) -> Path:
    path = Path(raw)
    if path.is_absolute():
        raise ValueError(f"Absolute path rejected: {raw!r}")
    try:
        (base / path).resolve().relative_to(base.resolve())
    except ValueError as exc:
        raise ValueError(f"Path traversal rejected: {raw!r}") from exc
    return path
