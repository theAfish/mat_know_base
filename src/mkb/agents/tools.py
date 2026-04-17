"""
Agent tool functions for knowledge extraction.

Reading tools let the LLM agent explore project data (processed markdown,
dataframes, images). Writing tools let it save a structured knowledge
frame for the project.
"""

from __future__ import annotations

import base64
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from mkb.config import settings
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import (
    Asset,
    FrameStatus,
    KnowledgeFrame,
    ProcessedAsset,
    ProcessingType,
    ProjectAsset,
    ResearchProject,
)
from mkb.storage.s3 import download_bytes, object_exists

logger = logging.getLogger(__name__)


def _select_processed_asset(
    session,
    asset_id: uuid.UUID,
    processing_type: ProcessingType,
) -> ProcessedAsset | None:
    rows = (
        session.query(ProcessedAsset)
        .filter_by(asset_id=asset_id, processing_type=processing_type)
        .order_by(ProcessedAsset.created_at.desc())
        .all()
    )
    if not rows:
        return None
    for row in rows:
        if object_exists(row.s3_bucket, row.s3_key):
            return row
    return rows[0]


def _read_processed_bytes(pa: ProcessedAsset) -> bytes:
    try:
        return download_bytes(pa.s3_bucket, pa.s3_key)
    except Exception:
        metadata = pa.conversion_metadata or {}
        local_dir = metadata.get("local_dir")
        primary_relpath = metadata.get("primary_relpath")
        if local_dir and primary_relpath:
            local_path = Path(local_dir) / primary_relpath
            if local_path.exists():
                return local_path.read_bytes()
        raise


# =====================================================================
# Reading tools
# =====================================================================


def list_project_files(project_id: str) -> list[dict]:
    """List every file in a research project with its processing status."""
    pid = uuid.UUID(project_id)
    with SyncSessionLocal() as session:
        links = session.query(ProjectAsset).filter_by(project_id=pid).all()
        asset_ids = [l.asset_id for l in links]
        if not asset_ids:
            return []
        assets = session.query(Asset).filter(Asset.asset_id.in_(asset_ids)).all()
        result = []
        for a in assets:
            pa = session.query(ProcessedAsset).filter_by(asset_id=a.asset_id).first()
            result.append({
                "asset_id": str(a.asset_id),
                "filename": a.filename,
                "mime_type": a.mime_type,
                "size_bytes": a.size_bytes,
                "processing_type": pa.processing_type.value if pa else None,
                "has_processed_output": pa is not None,
            })
        return result


def read_processed_markdown(asset_id: str) -> str:
    """Read the full processed Markdown content for a given asset."""
    aid = uuid.UUID(asset_id)
    with SyncSessionLocal() as session:
        pa = _select_processed_asset(session, aid, ProcessingType.MARKDOWN)
        if not pa:
            return f"No processed markdown found for asset {asset_id}."
        try:
            data = _read_processed_bytes(pa)
        except Exception as exc:
            return f"Cannot read markdown for asset {asset_id}: {exc}"
        return data.decode("utf-8", errors="replace")


def read_markdown_section(asset_id: str, section_heading: str) -> str:
    """Read a specific section from a processed Markdown file."""
    full_md = read_processed_markdown(asset_id)
    if full_md.startswith("No processed markdown"):
        return full_md

    lines = full_md.splitlines(keepends=True)
    pattern = re.compile(r"^(#{1,6})\s+" + re.escape(section_heading), re.IGNORECASE)

    start_idx = None
    start_level = 0
    for i, line in enumerate(lines):
        m = pattern.match(line)
        if m:
            start_idx = i
            start_level = len(m.group(1))
            break

    if start_idx is None:
        for i, line in enumerate(lines):
            m2 = re.match(r"^(#{1,6})\s+(.+)", line)
            if m2 and section_heading.lower() in m2.group(2).lower():
                start_idx = i
                start_level = len(m2.group(1))
                break

    if start_idx is None:
        return (
            f"Section '{section_heading}' not found. "
            f"Available headings: {_list_headings(lines)}"
        )

    end_idx = len(lines)
    for j in range(start_idx + 1, len(lines)):
        m3 = re.match(r"^(#{1,6})\s+", lines[j])
        if m3 and len(m3.group(1)) <= start_level:
            end_idx = j
            break

    return "".join(lines[start_idx:end_idx])


def list_markdown_headings(asset_id: str) -> list[str]:
    """List all headings in a processed Markdown file."""
    full_md = read_processed_markdown(asset_id)
    if full_md.startswith("No processed markdown"):
        return [full_md]
    return _list_headings(full_md.splitlines())


def _list_headings(lines: list[str]) -> list[str]:
    headings = []
    for line in lines:
        m = re.match(r"^(#{1,6})\s+(.+)", line)
        if m:
            headings.append(f"{'#' * len(m.group(1))} {m.group(2).strip()}")
    return headings


def read_raw_text(asset_id: str) -> str:
    """Read the raw text content of an asset directly from object storage."""
    aid = uuid.UUID(asset_id)
    with SyncSessionLocal() as session:
        asset = session.query(Asset).filter_by(asset_id=aid).first()
        if not asset:
            return f"Asset {asset_id} not found."
        if not (
            asset.mime_type.startswith("text/")
            or asset.mime_type in ("application/json", "application/xml")
        ):
            return f"Asset is binary ({asset.mime_type}). Use a processed output instead."
        data = download_bytes(asset.s3_bucket, asset.s3_key)
        return data.decode("utf-8", errors="replace")


def read_dataframe_summary(asset_id: str) -> str:
    """Read a summary of a processed dataframe (Parquet) for an asset."""
    aid = uuid.UUID(asset_id)
    with SyncSessionLocal() as session:
        pa = _select_processed_asset(session, aid, ProcessingType.DATAFRAME)
        if not pa:
            return f"No processed dataframe found for asset {asset_id}."
        try:
            data = _read_processed_bytes(pa)
        except Exception as exc:
            return f"Cannot read dataframe for asset {asset_id}: {exc}"

    import io
    df = pd.read_parquet(io.BytesIO(data))
    parts = [
        f"Shape: {df.shape[0]} rows × {df.shape[1]} columns",
        f"\nColumns and dtypes:\n{df.dtypes.to_string()}",
        f"\nFirst 5 rows:\n{df.head().to_string()}",
    ]
    if df.shape[1] <= 30:
        parts.append(f"\nStatistics:\n{df.describe(include='all').to_string()}")
    return "\n".join(parts)


def read_dataframe_rows(asset_id: str, start_row: int = 0, end_row: int = 20) -> str:
    """Read specific rows from a processed dataframe (capped at 100)."""
    end_row = min(end_row, start_row + 100)
    aid = uuid.UUID(asset_id)
    with SyncSessionLocal() as session:
        pa = _select_processed_asset(session, aid, ProcessingType.DATAFRAME)
        if not pa:
            return f"No processed dataframe found for asset {asset_id}."
        try:
            data = _read_processed_bytes(pa)
        except Exception as exc:
            return f"Cannot read dataframe for asset {asset_id}: {exc}"

    import io
    df = pd.read_parquet(io.BytesIO(data))
    subset = df.iloc[start_row:end_row]
    return f"Rows {start_row}–{min(end_row, len(df))} of {len(df)}:\n{subset.to_string()}"


def read_image_metadata(asset_id: str) -> str:
    """Read the processed image metadata JSON for an image asset."""
    aid = uuid.UUID(asset_id)
    with SyncSessionLocal() as session:
        pa = _select_processed_asset(session, aid, ProcessingType.IMAGE)
        if not pa:
            return f"No processed image metadata found for asset {asset_id}."
        try:
            data = _read_processed_bytes(pa)
        except Exception as exc:
            return f"Cannot read image metadata for asset {asset_id}: {exc}"
        return data.decode("utf-8", errors="replace")


def get_image_base64(asset_id: str) -> dict:
    """Get the raw image bytes as a base64-encoded string."""
    aid = uuid.UUID(asset_id)
    with SyncSessionLocal() as session:
        asset = session.query(Asset).filter_by(asset_id=aid).first()
        if not asset:
            return {"error": f"Asset {asset_id} not found."}
        if not asset.mime_type.startswith("image/"):
            return {"error": f"Asset is not an image ({asset.mime_type})."}
        data = download_bytes(asset.s3_bucket, asset.s3_key)
        return {
            "mime_type": asset.mime_type,
            "base64_data": base64.b64encode(data).decode("ascii"),
            "filename": asset.filename,
        }


def search_in_project(project_id: str, query: str) -> list[dict]:
    """Search for a text pattern across all processed Markdown files in a project."""
    pid = uuid.UUID(project_id)
    query_lower = query.lower()

    with SyncSessionLocal() as session:
        links = session.query(ProjectAsset).filter_by(project_id=pid).all()
        asset_ids = [l.asset_id for l in links]
        if not asset_ids:
            return []

        processed = (
            session.query(ProcessedAsset)
            .filter(
                ProcessedAsset.asset_id.in_(asset_ids),
                ProcessedAsset.processing_type == ProcessingType.MARKDOWN,
            )
            .all()
        )

        results = []
        for pa in processed:
            asset = session.query(Asset).filter_by(asset_id=pa.asset_id).first()
            try:
                data = _read_processed_bytes(pa)
                text = data.decode("utf-8", errors="replace")
            except Exception:
                continue

            all_lines = text.splitlines()
            snippets = []
            for i, line in enumerate(all_lines):
                if query_lower in line.lower():
                    start = max(0, i - 1)
                    end = min(len(all_lines), i + 2)
                    snippets.append("\n".join(all_lines[start:end]))
                    if len(snippets) >= 5:
                        break

            if snippets:
                results.append({
                    "asset_id": str(pa.asset_id),
                    "filename": asset.filename if asset else "unknown",
                    "matches": snippets,
                })
        return results


# =====================================================================
# Knowledge frame tools
# =====================================================================


def save_knowledge_frame(
    project_id: str,
    content: dict,
    summary: str = "",
) -> dict:
    """Save or update a knowledge frame for a research project.

    content should be a dict with keys like: paper, concepts, materials,
    experimental_data, methods, synthesis_routes, statements, relationships.
    Each item in the lists should have an evidence_level (1-4):
      1 = Causal experimental evidence
      2 = Direct experimental observation
      3 = Correlative evidence
      4 = Predicted / inferred

    This also marks the frame as COMPLETED.
    """
    pid = uuid.UUID(project_id)
    now = datetime.now(timezone.utc)

    with SyncSessionLocal() as session:
        project = session.query(ResearchProject).filter_by(project_id=pid).first()
        if not project:
            return {"error": f"Project {project_id} not found."}

        links = session.query(ProjectAsset).filter_by(project_id=pid).all()
        asset_ids = [str(l.asset_id) for l in links]
        source_meta = {
            "project_label": project.label,
            "source_path": project.source_path,
            "asset_ids": asset_ids,
            "project_id": project_id,
        }

        existing = session.query(KnowledgeFrame).filter_by(project_id=pid).first()
        if existing:
            existing.content = content
            existing.extraction_summary = summary
            existing.status = FrameStatus.COMPLETED
            existing.extracted_at = now
            existing.source_metadata = source_meta
            existing.times_checked = existing.times_checked + 1
            session.commit()
            return {"frame_id": str(existing.frame_id), "status": "updated"}

        frame = KnowledgeFrame(
            frame_id=uuid.uuid4(),
            project_id=pid,
            status=FrameStatus.COMPLETED,
            content=content,
            extraction_summary=summary,
            times_checked=1,
            extracted_at=now,
            source_metadata=source_meta,
        )
        session.add(frame)
        session.commit()
        return {"frame_id": str(frame.frame_id), "status": "created"}


def get_existing_frame(project_id: str) -> dict:
    """Get the existing knowledge frame for a project, if any.

    Returns the frame content and metadata, or an indication that none exists.
    Useful for re-extraction to see what was previously extracted.
    """
    pid = uuid.UUID(project_id)
    with SyncSessionLocal() as session:
        frame = session.query(KnowledgeFrame).filter_by(project_id=pid).first()
        if not frame:
            return {"exists": False}
        return {
            "exists": True,
            "frame_id": str(frame.frame_id),
            "status": frame.status.value,
            "content": frame.content,
            "extraction_summary": frame.extraction_summary,
            "times_checked": frame.times_checked,
            "extracted_at": frame.extracted_at.isoformat() if frame.extracted_at else None,
        }


# =====================================================================
# All tools for agent registration
# =====================================================================

ALL_TOOLS = [
    # Reading
    list_project_files,
    read_processed_markdown,
    read_markdown_section,
    list_markdown_headings,
    read_raw_text,
    read_dataframe_summary,
    read_dataframe_rows,
    read_image_metadata,
    get_image_base64,
    search_in_project,
    # Writing
    save_knowledge_frame,
    get_existing_frame,
]
