from __future__ import annotations

from mkb.workflows.review import audit_raw_graph


def _review_flags(row) -> list[dict]:
    flags = list(row.review_flags or [])
    if row.graph:
        seen = {repr(flag) for flag in flags}
        for flag in audit_raw_graph(row.graph):
            key = repr(flag)
            if key not in seen:
                flags.append(flag)
                seen.add(key)
    return flags


def serialize_raw_workflow(row, include_graph: bool) -> dict:
    payload = {
        "extraction_id": str(row.extraction_id),
        "project_id": str(row.project_id),
        "version": row.version,
        "schema_version": row.schema_version,
        "extractor_version": row.extractor_version,
        "model": row.model,
        "status": row.status,
        "record_status": row.record_status,
        "supersedes_extraction_id": (
            str(row.supersedes_extraction_id) if row.supersedes_extraction_id else None
        ),
        "correction_reason": row.correction_reason,
        "correction_author": row.correction_author,
        "correction_details": row.correction_details or {},
        "review_flags": _review_flags(row),
        "provenance": row.provenance or {},
        "error": row.error,
        "extracted_at": row.extracted_at.isoformat() if row.extracted_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "has_checkpoint": bool(row.checkpoint),
        "checkpoint_summary": (row.checkpoint or {}).get("summary"),
        "checkpoint_updated_at": (
            row.checkpoint_updated_at.isoformat() if row.checkpoint_updated_at else None
        ),
        "resumable": row.graph is None and row.status in {"IN_PROGRESS", "FAILED"},
    }
    if include_graph:
        payload["graph"] = row.graph
    elif row.graph:
        payload["node_count"] = len(row.graph.get("nodes", []))
        payload["edge_count"] = len(row.graph.get("edges", []))
    return payload

def serialize_canonical_workflow(row, include_graph: bool) -> dict:
    payload = {
        "canonicalization_id": str(row.canonicalization_id),
        "project_id": str(row.project_id),
        "raw_extraction_id": str(row.raw_extraction_id),
        "version": row.version,
        "schema_version": row.schema_version,
        "canonicalizer_version": row.canonicalizer_version,
        "model": row.model,
        "status": row.status,
        "provenance": row.provenance or {},
        "error": row.error,
        "canonicalized_at": row.canonicalized_at.isoformat() if row.canonicalized_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "has_checkpoint": bool(row.checkpoint),
        "checkpoint_summary": (row.checkpoint or {}).get("summary"),
        "checkpoint_updated_at": (
            row.checkpoint_updated_at.isoformat() if row.checkpoint_updated_at else None
        ),
        "resumable": row.graph is None and row.status in {"IN_PROGRESS", "FAILED"},
    }
    if include_graph:
        payload["graph"] = row.graph
    elif row.graph:
        payload.update(
            node_count=len(row.graph.get("nodes", [])),
            edge_count=len(row.graph.get("edges", [])),
        )
    return payload
