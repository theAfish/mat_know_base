"""
Agent tool functions for knowledge extraction.

These plain functions are registered as google-adk FunctionTools.
They give the LLM agent the ability to *read* batch data (processed
markdown, dataframes, images) and *write* structured knowledge
(entities + relationships) into the knowledge graph.
"""

from __future__ import annotations

import base64
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from mkb.db.engine import SyncSessionLocal
from mkb.db.models import (
    Asset,
    BatchAsset,
    IngestionBatch,
    KnowledgeBaseFrame,
    KnowledgeEdge,
    KnowledgeNode,
    ExtractionStatus,
    ProcessedAsset,
    ProcessingType,
)
from mkb.storage.s3 import download_bytes, object_exists

logger = logging.getLogger(__name__)

EVIDENCE_LEVELS = {
    1: "Level 1: Causal experimental evidence",
    2: "Level 2: Direct experimental observation",
    3: "Level 3: Correlative evidence",
    4: "Level 4: Predicted / inferred",
}


def _normalize_evidence_level(level: str | int | None) -> str:
    """Normalize evidence level into canonical Level 1..4 labels."""
    if isinstance(level, int) and level in EVIDENCE_LEVELS:
        return EVIDENCE_LEVELS[level]
    if isinstance(level, str):
        stripped = level.strip()
        m = re.search(r"\b([1-4])\b", stripped)
        if m:
            return EVIDENCE_LEVELS[int(m.group(1))]
        lowered = stripped.lower()
        if "causal" in lowered:
            return EVIDENCE_LEVELS[1]
        if "direct" in lowered:
            return EVIDENCE_LEVELS[2]
        if "correl" in lowered:
            return EVIDENCE_LEVELS[3]
        if "pred" in lowered or "infer" in lowered:
            return EVIDENCE_LEVELS[4]
    return EVIDENCE_LEVELS[4]


def _default_frame_data() -> dict:
    return {
        "concepts": [],
        "experimental_data": [],
        "statements": [],
        "related_data": [],
    }


def _default_frame_metadata() -> dict:
    return {
        "source_batch_id": None,
        "source_assets": [],
        "source_links": [],
        "extraction_history": [],
        "latest_summary": "",
    }


def _ensure_kb_frame(session, batch_id: uuid.UUID, title: str | None = None) -> KnowledgeBaseFrame:
    frame = session.query(KnowledgeBaseFrame).filter_by(batch_id=batch_id).first()
    if frame:
        if title and not frame.title:
            frame.title = title
        return frame
    frame = KnowledgeBaseFrame(
        frame_id=uuid.uuid4(),
        batch_id=batch_id,
        title=title,
        status="DRAFT",
        frame_data=_default_frame_data(),
        frame_metadata=_default_frame_metadata(),
    )
    session.add(frame)
    session.flush()
    return frame


def _collect_batch_source_assets(session, batch_id: uuid.UUID) -> list[dict]:
    links = session.query(BatchAsset).filter_by(batch_id=batch_id).all()
    asset_ids = [link.asset_id for link in links]
    if not asset_ids:
        return []

    assets = session.query(Asset).filter(Asset.asset_id.in_(asset_ids)).all()
    processed_rows = (
        session.query(ProcessedAsset)
        .filter(ProcessedAsset.asset_id.in_(asset_ids))
        .order_by(ProcessedAsset.created_at.desc())
        .all()
    )
    by_asset: dict[uuid.UUID, list[ProcessedAsset]] = {}
    for row in processed_rows:
        by_asset.setdefault(row.asset_id, []).append(row)

    source_assets = []
    for asset in assets:
        processed = [
            {
                "processing_type": row.processing_type.value,
                "output_format": row.output_format,
                "s3_uri": f"s3://{row.s3_bucket}/{row.s3_key}",
            }
            for row in by_asset.get(asset.asset_id, [])
        ]
        source_assets.append(
            {
                "asset_id": str(asset.asset_id),
                "filename": asset.filename,
                "mime_type": asset.mime_type,
                "raw_s3_uri": f"s3://{asset.s3_bucket}/{asset.s3_key}",
                "processed_outputs": processed,
            }
        )
    return source_assets


def _select_processed_asset(
    session,
    asset_id: uuid.UUID,
    processing_type: ProcessingType,
) -> ProcessedAsset | None:
    """Choose the newest processed row with an existing S3 object when possible."""
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

    # Fallback to newest row even if S3 object is missing; caller can use local fallback.
    return rows[0]


def _read_processed_bytes(pa: ProcessedAsset) -> bytes:
    """Read processed bytes from S3, falling back to local mirror if needed."""
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
# Reading tools — let the agent explore the batch data
# =====================================================================


def list_batch_files(batch_id: str) -> list[dict]:
    """List every file in an ingestion batch with its processing status.

    Returns a list of dicts with keys: asset_id, filename, mime_type,
    size_bytes, processing_type, has_processed_output.
    """
    bid = uuid.UUID(batch_id)
    with SyncSessionLocal() as session:
        links = session.query(BatchAsset).filter_by(batch_id=bid).all()
        asset_ids = [link.asset_id for link in links]
        if not asset_ids:
            return []

        assets = session.query(Asset).filter(Asset.asset_id.in_(asset_ids)).all()
        result = []
        for a in assets:
            pa = (
                session.query(ProcessedAsset)
                .filter_by(asset_id=a.asset_id)
                .first()
            )
            result.append(
                {
                    "asset_id": str(a.asset_id),
                    "filename": a.filename,
                    "mime_type": a.mime_type,
                    "size_bytes": a.size_bytes,
                    "processing_type": pa.processing_type.value if pa else None,
                    "has_processed_output": pa is not None,
                }
            )
        return result


def read_processed_markdown(asset_id: str) -> str:
    """Read the full processed Markdown content for a given asset.

    Works for PDFs, DOCX, and plain-text assets that were converted to
    Markdown during the processing pipeline.  Returns the Markdown string
    or an error message if not found.
    """
    aid = uuid.UUID(asset_id)
    with SyncSessionLocal() as session:
        pa = _select_processed_asset(session, aid, ProcessingType.MARKDOWN)
        if not pa:
            return f"No processed markdown found for asset {asset_id}."

        try:
            data = _read_processed_bytes(pa)
        except Exception as exc:
            return f"Processed markdown exists but cannot be read for asset {asset_id}: {exc}"
        return data.decode("utf-8", errors="replace")


def read_markdown_section(asset_id: str, section_heading: str) -> str:
    """Read a specific section from a processed Markdown file.

    Searches for a heading matching `section_heading` (case-insensitive)
    and returns everything from that heading to the next heading of same
    or higher level.  Useful for large papers where you only need one
    section (e.g. "Methods", "Results").
    """
    full_md = read_processed_markdown(asset_id)
    if full_md.startswith("No processed markdown"):
        return full_md

    lines = full_md.splitlines(keepends=True)
    pattern = re.compile(
        r"^(#{1,6})\s+" + re.escape(section_heading),
        re.IGNORECASE,
    )

    start_idx = None
    start_level = 0
    for i, line in enumerate(lines):
        m = pattern.match(line)
        if m:
            start_idx = i
            start_level = len(m.group(1))
            break

    if start_idx is None:
        # Try fuzzy match: heading contains the query as substring
        for i, line in enumerate(lines):
            m2 = re.match(r"^(#{1,6})\s+(.+)", line)
            if m2 and section_heading.lower() in m2.group(2).lower():
                start_idx = i
                start_level = len(m2.group(1))
                break

    if start_idx is None:
        return (
            f"Section '{section_heading}' not found.  "
            f"Available headings: {_list_headings(lines)}"
        )

    # Collect lines until next heading of same or higher level
    end_idx = len(lines)
    for j in range(start_idx + 1, len(lines)):
        m3 = re.match(r"^(#{1,6})\s+", lines[j])
        if m3 and len(m3.group(1)) <= start_level:
            end_idx = j
            break

    return "".join(lines[start_idx:end_idx])


def list_markdown_headings(asset_id: str) -> list[str]:
    """List all headings in a processed Markdown file.

    Useful for deciding which section to read in detail.
    """
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
    """Read the raw text content of an asset directly from object storage.

    Only useful for text-based assets (text/plain, text/markdown, etc.).
    Binary files will return a warning instead of garbage.
    """
    aid = uuid.UUID(asset_id)
    with SyncSessionLocal() as session:
        asset = session.query(Asset).filter_by(asset_id=aid).first()
        if not asset:
            return f"Asset {asset_id} not found."

        if not (
            asset.mime_type.startswith("text/")
            or asset.mime_type in ("application/json", "application/xml")
        ):
            return (
                f"Asset is binary ({asset.mime_type}). Use a processed "
                "output instead, or use get_image_base64 for images."
            )

        data = download_bytes(asset.s3_bucket, asset.s3_key)
        return data.decode("utf-8", errors="replace")


def read_dataframe_summary(asset_id: str) -> str:
    """Read a summary of a processed dataframe (Parquet) for an asset.

    Returns column names, dtypes, row count, and basic statistics.
    """
    aid = uuid.UUID(asset_id)
    with SyncSessionLocal() as session:
        pa = _select_processed_asset(session, aid, ProcessingType.DATAFRAME)
        if not pa:
            return f"No processed dataframe found for asset {asset_id}."

        try:
            data = _read_processed_bytes(pa)
        except Exception as exc:
            return f"Processed dataframe exists but cannot be read for asset {asset_id}: {exc}"

    import io

    df = pd.read_parquet(io.BytesIO(data))

    parts = [
        f"Shape: {df.shape[0]} rows × {df.shape[1]} columns",
        f"\nColumns and dtypes:\n{df.dtypes.to_string()}",
        f"\nFirst 5 rows:\n{df.head().to_string()}",
    ]
    # Include describe only for small-ish frames
    if df.shape[1] <= 30:
        parts.append(f"\nStatistics:\n{df.describe(include='all').to_string()}")

    return "\n".join(parts)


def read_dataframe_rows(
    asset_id: str, start_row: int = 0, end_row: int = 20
) -> str:
    """Read specific rows from a processed dataframe.

    Returns the requested rows as a formatted string table.
    Capped at 100 rows per call to avoid overwhelming context.
    """
    end_row = min(end_row, start_row + 100)
    aid = uuid.UUID(asset_id)
    with SyncSessionLocal() as session:
        pa = _select_processed_asset(session, aid, ProcessingType.DATAFRAME)
        if not pa:
            return f"No processed dataframe found for asset {asset_id}."

        try:
            data = _read_processed_bytes(pa)
        except Exception as exc:
            return f"Processed dataframe exists but cannot be read for asset {asset_id}: {exc}"

    import io

    df = pd.read_parquet(io.BytesIO(data))
    subset = df.iloc[start_row:end_row]
    return (
        f"Rows {start_row}–{min(end_row, len(df))} of {len(df)}:\n"
        f"{subset.to_string()}"
    )


def read_image_metadata(asset_id: str) -> str:
    """Read the processed image metadata JSON for an image asset.

    Returns dimensions, format, mode, file size, and any OCR text if
    available.
    """
    aid = uuid.UUID(asset_id)
    with SyncSessionLocal() as session:
        pa = _select_processed_asset(session, aid, ProcessingType.IMAGE)
        if not pa:
            return f"No processed image metadata found for asset {asset_id}."

        try:
            data = _read_processed_bytes(pa)
        except Exception as exc:
            return f"Processed image metadata exists but cannot be read for asset {asset_id}: {exc}"
        return data.decode("utf-8", errors="replace")


def get_image_base64(asset_id: str) -> dict:
    """Get the raw image bytes as a base64-encoded string.

    Returns a dict with keys: mime_type, base64_data, filename.
    Useful if a multimodal model needs to visually inspect a figure.
    """
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


def search_in_batch(batch_id: str, query: str) -> list[dict]:
    """Search for a text pattern across all processed Markdown files in a batch.

    Returns a list of matches with asset_id, filename, and matching
    context snippets (up to 5 per file).
    """
    bid = uuid.UUID(batch_id)
    query_lower = query.lower()

    with SyncSessionLocal() as session:
        links = session.query(BatchAsset).filter_by(batch_id=bid).all()
        asset_ids = [link.asset_id for link in links]
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
                results.append(
                    {
                        "asset_id": str(pa.asset_id),
                        "filename": asset.filename if asset else "unknown",
                        "matches": snippets,
                    }
                )

        return results


# =====================================================================
# Knowledge-writing tools — let the agent build the knowledge graph
# =====================================================================


def initialize_knowledge_frame(batch_id: str, title: str | None = None) -> dict:
    """Initialize or refresh a frame for one research package (batch)."""
    bid = uuid.UUID(batch_id)
    now = datetime.now(timezone.utc).isoformat()

    with SyncSessionLocal() as session:
        batch = session.query(IngestionBatch).filter_by(batch_id=bid).first()
        if not batch:
            return {"status": "error", "message": f"Batch {batch_id} not found"}

        frame = _ensure_kb_frame(session, bid, title=title or batch.label)
        meta = dict(frame.frame_metadata or _default_frame_metadata())
        source_assets = _collect_batch_source_assets(session, bid)
        meta["source_batch_id"] = str(bid)
        meta["source_assets"] = source_assets
        meta["source_links"] = [asset["raw_s3_uri"] for asset in source_assets]
        meta["initialized_at"] = now
        frame.frame_metadata = meta
        frame.status = "DRAFT"
        session.commit()

        return {
            "status": "ok",
            "frame_id": str(frame.frame_id),
            "batch_id": str(bid),
            "source_assets": len(source_assets),
        }


def add_knowledge_frame_items(
    batch_id: str,
    section: str,
    items: list[dict],
    evidence_level: str | int | None = None,
    source_asset_id: str | None = None,
) -> dict:
    """Add extracted items into one frame section.

    Valid sections: concepts, experimental_data, statements, related_data.
    """
    bid = uuid.UUID(batch_id)
    normalized_section = section.strip().lower()
    if normalized_section not in _default_frame_data():
        return {
            "status": "error",
            "message": f"Unsupported section '{section}'. Use one of: concepts, experimental_data, statements, related_data",
        }
    if not isinstance(items, list) or not all(isinstance(it, dict) for it in items):
        return {"status": "error", "message": "items must be a list of objects"}

    ev = _normalize_evidence_level(evidence_level)
    now = datetime.now(timezone.utc).isoformat()
    src_asset = None
    if source_asset_id:
        try:
            src_asset = str(uuid.UUID(source_asset_id))
        except ValueError:
            return {
                "status": "error",
                "message": f"Invalid source_asset_id UUID: {source_asset_id}",
            }

    with SyncSessionLocal() as session:
        frame = _ensure_kb_frame(session, bid)
        frame_data = dict(frame.frame_data or _default_frame_data())
        bucket = list(frame_data.get(normalized_section) or [])
        for item in items:
            payload = dict(item)
            payload.setdefault("evidence_level", ev)
            payload.setdefault("source_batch_id", str(bid))
            if src_asset:
                payload.setdefault("source_asset_id", src_asset)
            payload.setdefault("recorded_at", now)
            bucket.append(payload)
        frame_data[normalized_section] = bucket
        frame.frame_data = frame_data
        frame.status = "DRAFT"
        session.commit()
        return {
            "status": "ok",
            "frame_id": str(frame.frame_id),
            "section": normalized_section,
            "items_added": len(items),
            "section_total": len(bucket),
        }


def mark_knowledge_frame_checked(batch_id: str, summary: str = "") -> str:
    """Finalize one batch frame and mark extraction completion metadata."""
    bid = uuid.UUID(batch_id)
    now = datetime.now(timezone.utc)

    with SyncSessionLocal() as session:
        batch = session.query(IngestionBatch).filter_by(batch_id=bid).first()
        if not batch:
            return f"Batch {batch_id} not found."

        frame = _ensure_kb_frame(session, bid, title=batch.label)
        frame.check_count = (frame.check_count or 0) + 1
        frame.status = "COMPLETED"
        if not frame.first_extracted_at:
            frame.first_extracted_at = now
        frame.last_extracted_at = now

        meta = dict(frame.frame_metadata or _default_frame_metadata())
        history = list(meta.get("extraction_history") or [])
        history.append(
            {
                "checked_at": now.isoformat(),
                "summary": summary,
                "check_count": frame.check_count,
            }
        )
        meta["extraction_history"] = history[-20:]
        meta["latest_summary"] = summary
        frame.frame_metadata = meta

        batch.extraction_status = ExtractionStatus.COMPLETED
        batch_meta = dict(batch.extraction_metadata or {})
        batch_meta["summary"] = summary
        batch_meta["frame_id"] = str(frame.frame_id)
        batch_meta["frame_checked_count"] = frame.check_count
        batch_meta["frame_last_extracted_at"] = now.isoformat()
        batch.extraction_metadata = batch_meta

        session.commit()
        return f"Knowledge frame for batch {batch_id} marked as COMPLETED."


def create_entity(
    entity_type: str,
    label: str,
    properties: dict | None = None,
    source_asset_id: str | None = None,
    source_batch_id: str | None = None,
) -> dict:
    """Create a knowledge-graph entity (node).

    entity_type examples: Material, Property, Method, Parameter, Author,
    Institution, Measurement, Device, ChemicalFormula, CrystalStructure.

    Properties is a free-form dict for domain-specific attributes
    (e.g. {"value": "4e-12", "unit": "A", "temperature": "300K"}).

    Returns the created node_id and whether a duplicate was found.
    """
    with SyncSessionLocal() as session:
        # De-duplicate: same type + label + batch → reuse node
        q = session.query(KnowledgeNode).filter_by(
            entity_type=entity_type, label=label
        )
        if source_batch_id:
            q = q.filter_by(source_batch_id=uuid.UUID(source_batch_id))

        existing = q.first()
        if existing:
            # Merge properties
            if properties:
                merged = dict(existing.properties or {})
                merged.update(properties)
                existing.properties = merged
                session.commit()
            return {
                "node_id": str(existing.node_id),
                "status": "existing_updated" if properties else "already_exists",
            }

        node = KnowledgeNode(
            node_id=uuid.uuid4(),
            entity_type=entity_type,
            label=label,
            properties=properties or {},
            source_asset_id=uuid.UUID(source_asset_id) if source_asset_id else None,
            source_batch_id=uuid.UUID(source_batch_id) if source_batch_id else None,
        )
        session.add(node)
        session.commit()
        return {"node_id": str(node.node_id), "status": "created"}


def create_relationship(
    source_node_id: str,
    target_node_id: str,
    relation_type: str,
    properties: dict | None = None,
) -> dict:
    """Create a directed relationship (edge) between two knowledge nodes.

    relation_type examples: HAS_PROPERTY, MEASURED, SIMULATED_BY,
    CONTAINS_ELEMENT, HAS_STRUCTURE, STUDIED_IN, AUTHORED_BY,
    AFFILIATED_WITH, CITES, EXHIBITS, FABRICATED_WITH, CHARACTERIZED_BY.

    Returns the edge_id.
    """
    sid = uuid.UUID(source_node_id)
    tid = uuid.UUID(target_node_id)

    with SyncSessionLocal() as session:
        # De-duplicate: same source + target + relation → reuse edge
        existing = (
            session.query(KnowledgeEdge)
            .filter_by(
                source_node_id=sid,
                target_node_id=tid,
                relation_type=relation_type,
            )
            .first()
        )
        if existing:
            if properties:
                merged = dict(existing.properties or {})
                merged.update(properties)
                existing.properties = merged
                session.commit()
            return {
                "edge_id": str(existing.edge_id),
                "status": "existing_updated" if properties else "already_exists",
            }

        edge = KnowledgeEdge(
            edge_id=uuid.uuid4(),
            source_node_id=sid,
            target_node_id=tid,
            relation_type=relation_type,
            properties=properties or {},
        )
        session.add(edge)
        session.commit()
        return {"edge_id": str(edge.edge_id), "status": "created"}


def find_existing_entities(
    entity_type: str | None = None,
    label_contains: str | None = None,
    batch_id: str | None = None,
) -> list[dict]:
    """Search for existing knowledge-graph entities.

    Useful for checking whether an entity already exists before creating
    duplicates, or for finding node_ids to link relationships to.
    All filter parameters are optional and combine with AND logic.
    """
    with SyncSessionLocal() as session:
        q = session.query(KnowledgeNode)
        if entity_type:
            q = q.filter_by(entity_type=entity_type)
        if label_contains:
            q = q.filter(KnowledgeNode.label.ilike(f"%{label_contains}%"))
        if batch_id:
            q = q.filter_by(source_batch_id=uuid.UUID(batch_id))

        nodes = q.limit(50).all()
        return [
            {
                "node_id": str(n.node_id),
                "entity_type": n.entity_type,
                "label": n.label,
                "properties": n.properties,
            }
            for n in nodes
        ]


def mark_batch_extracted(batch_id: str, summary: str = "") -> str:
    """Backward-compatible wrapper around mark_knowledge_frame_checked.

    Marks the batch frame as checked/completed, updates extraction counters
    and timestamps, and syncs extraction metadata on the ingestion batch.
    """
    return mark_knowledge_frame_checked(batch_id=batch_id, summary=summary)


# =====================================================================
# Convenience: collect all tools into a list for agent registration
# =====================================================================

ALL_TOOLS = [
    list_batch_files,
    read_processed_markdown,
    read_markdown_section,
    list_markdown_headings,
    read_raw_text,
    read_dataframe_summary,
    read_dataframe_rows,
    read_image_metadata,
    get_image_base64,
    search_in_batch,
    initialize_knowledge_frame,
    add_knowledge_frame_items,
    mark_knowledge_frame_checked,
    create_entity,
    create_relationship,
    find_existing_entities,
    mark_batch_extracted,
]
