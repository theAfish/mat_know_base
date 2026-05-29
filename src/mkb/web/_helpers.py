"""Small reusable web-layer helpers (input validation, path safety)."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import HTTPException


def _parse_uuid(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}: {value!r}") from exc


def _safe_child(base: Path, rel: str) -> Path:
    """Resolve ``rel`` under ``base``, rejecting absolute paths and zip-slip."""
    p = Path(rel)
    if p.is_absolute():
        raise HTTPException(status_code=400, detail="Absolute path rejected")
    full = (base / p).resolve()
    try:
        full.relative_to(base.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path traversal rejected") from exc
    return full
