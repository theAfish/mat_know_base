"""Shared response helpers for previewable asset content."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote


def inline_headers(filename: str) -> dict[str, str]:
    safe_name = filename.replace('"', "'").replace("\r", "").replace("\n", "")
    ascii_name = safe_name.encode("ascii", "ignore").decode("ascii") or "document"
    return {
        "Content-Disposition": (
            f'inline; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(safe_name)}'
        ),
        "X-Content-Type-Options": "nosniff",
    }


def asset_media_type(filename: str, mime_type: str | None = None) -> str | None:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf" or mime_type == "application/pdf":
        return "application/pdf"
    if suffix in {".md", ".markdown"} or mime_type in {"text/markdown", "text/x-markdown"}:
        return "text/markdown"
    return None

