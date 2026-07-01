"""Shared imports and small helpers for API service modules."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mkb.config import settings
from mkb.db.engine import SyncSessionLocal, init_db

logger = logging.getLogger(__name__)

__all__ = [
    "Any",
    "Path",
    "SyncSessionLocal",
    "datetime",
    "init_db",
    "logger",
    "settings",
    "timezone",
    "uuid",
    "_choose_asset_for_manual_output",
    "_inspect_manual_processed_dir",
    "_matches_search_tokens",
    "_normalize_search_query",
    "_sha256_bytes",
]


def _sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()

def _inspect_manual_processed_dir(
    processed_dir: str | Path,
    primary_file: str | None = None,
) -> dict:
    """Inspect a handmade processed-output directory and describe its bundle."""
    root = Path(processed_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Processed directory not found: {root}")

    files = sorted(p for p in root.rglob("*") if p.is_file())
    if not files:
        raise FileNotFoundError(f"No files found in processed directory: {root}")

    if primary_file:
        primary_path = (root / primary_file).resolve()
        if not primary_path.is_file():
            raise FileNotFoundError(f"Primary file not found: {primary_path}")
    else:
        def _priority(path: Path) -> tuple[int, str]:
            suffix = path.suffix.lower()
            if suffix in {".md", ".markdown"}:
                rank = 0
            elif suffix in {".parquet", ".csv", ".tsv"}:
                rank = 1
            elif suffix == ".json":
                rank = 2
            elif suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                rank = 4
            else:
                rank = 3
            return rank, path.relative_to(root).as_posix()

        primary_path = sorted(files, key=_priority)[0]

    primary_relpath = primary_path.relative_to(root).as_posix()
    primary_bytes = primary_path.read_bytes()

    ext = primary_path.suffix.lower()
    if ext in {".md", ".markdown", ".txt"}:
        processing_type = "MARKDOWN"
    elif ext in {".parquet", ".csv", ".tsv", ".json"}:
        processing_type = "DATAFRAME"
    elif ext in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        processing_type = "IMAGE"
    else:
        processing_type = "MARKDOWN"

    artifact_files = sorted(
        p.relative_to(root).as_posix()
        for p in files
        if p != primary_path
    )

    bundle_hash = hashlib.sha256()
    bundle_hash.update(primary_bytes)
    for relpath in artifact_files:
        data = (root / relpath).read_bytes()
        bundle_hash.update(relpath.encode("utf-8"))
        bundle_hash.update(_sha256_bytes(data).encode("utf-8"))

    from mkb.db.models import ProcessingType

    return {
        "local_dir": str(root),
        "primary_name": primary_path.name,
        "primary_relpath": primary_relpath,
        "processing_type": ProcessingType(processing_type),
        "output_format": primary_path.suffix.lstrip(".") or "bin",
        "artifact_files": artifact_files,
        "size_bytes": len(primary_bytes),
        "sha256": bundle_hash.hexdigest(),
    }

def _choose_asset_for_manual_output(assets: list, primary_name: str | None = None):
    """Choose the most likely raw asset for a handmade processed bundle."""
    if not assets:
        return None
    if not primary_name:
        return assets[0]

    primary_stem = Path(primary_name).stem.lower()
    for asset in assets:
        if Path(asset.filename).stem.lower() == primary_stem:
            return asset

    for asset in assets:
        asset_stem = Path(asset.filename).stem.lower()
        if primary_stem in asset_stem or asset_stem in primary_stem:
            return asset

    return assets[0]

def _normalize_search_query(query: str) -> list[str]:
    """Split a free-text query into non-empty keyword tokens."""
    return [token.strip() for token in query.split() if token.strip()]

def _matches_search_tokens(*values: Any, tokens: list[str]) -> bool:
    """Return True when every token is present in at least one candidate value."""
    haystacks = [str(value).lower() for value in values if value]
    if not tokens:
        return True
    return all(any(token in haystack for haystack in haystacks) for token in tokens)


# ── Lifecycle ────────────────────────────────────────────────────
