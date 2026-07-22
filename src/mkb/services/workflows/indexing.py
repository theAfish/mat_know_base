from __future__ import annotations

from mkb.services._api_common import uuid
from mkb.ports import Database

def rebuild_workflow_indexes(project_id: str | uuid.UUID | None = None, *, database: Database) -> dict:
    from mkb.db.models import CanonicalWorkflow, RawWorkflowExtraction, WorkflowIndexEntry
    from mkb.workflows.indexing import build_index_entries
    from mkb.workflows.schema_library import get_schema_library_payload

    with database.session() as session:
        query = session.query(CanonicalWorkflow).filter_by(status="COMPLETED")
        if project_id:
            query = query.filter_by(project_id=uuid.UUID(str(project_id)))
        rows = query.all()
        ids = [row.canonicalization_id for row in rows]
        if ids:
            session.query(WorkflowIndexEntry).filter(WorkflowIndexEntry.canonicalization_id.in_(ids)).delete(synchronize_session=False)
        count = 0
        for row in rows:
            raw = session.query(RawWorkflowExtraction).filter_by(extraction_id=row.raw_extraction_id).first()
            schema = get_schema_library_payload(row.schema_version)
            for entry in build_index_entries(row.graph or {}, raw.graph if raw else {}, schema):
                session.add(WorkflowIndexEntry(canonicalization_id=row.canonicalization_id, project_id=row.project_id, **entry))
                count += 1
        session.commit()
        return {"workflows_indexed": len(rows), "entries_created": count}

def search_canonical_workflows(source: str | None = None, operation: str | None = None, target: str | None = None, mode: str = "strict", limit: int = 100, *, database: Database) -> list[dict]:
    """Search persisted workflow indexes and return evidence-rich explanations."""
    from mkb.db.models import CanonicalWorkflow, WorkflowIndexEntry
    from mkb.workflows.indexing import QUERY_MODES, match_index_entry, normalize

    legacy_modes = {"exact": "strict", "relaxed": "alias-expanded", "expanded": "granularity-expanded", "summarized": "granularity-expanded"}
    mode = legacy_modes.get(mode, mode)
    if mode not in QUERY_MODES:
        raise ValueError(f"Unsupported query mode: {mode}")
    results = []
    seen_paths = set()
    with database.session() as session:
        completed = session.query(CanonicalWorkflow).filter_by(status="COMPLETED").order_by(CanonicalWorkflow.project_id, CanonicalWorkflow.version.desc()).all()
        latest = {}
        for row in completed:
            latest.setdefault(row.project_id, row)
        rows_by_id = {row.canonicalization_id: row for row in latest.values()}
        entry_query = session.query(WorkflowIndexEntry).filter(
            WorkflowIndexEntry.canonicalization_id.in_(rows_by_id)
        ) if rows_by_id else None
        if entry_query is not None and mode in {"strict", "alias-expanded", "evidence-required"}:
            entry_query = entry_query.filter(WorkflowIndexEntry.index_type == "direct")
            if source:
                entry_query = entry_query.filter(WorkflowIndexEntry.source_label == normalize(source))
            if target:
                entry_query = entry_query.filter(WorkflowIndexEntry.target_label == normalize(target))
            if operation and mode in {"strict", "evidence-required"}:
                entry_query = entry_query.filter(WorkflowIndexEntry.operation_label == normalize(operation))
        entries = entry_query.all() if entry_query is not None else []
        for entry in entries:
            data = {column.name: getattr(entry, column.name) for column in WorkflowIndexEntry.__table__.columns}
            matched, explanation = match_index_entry(data, source=source, operation=operation, target=target, mode=mode)
            if not matched:
                continue
            result_key = (entry.canonicalization_id, tuple(entry.path_node_ids))
            if result_key in seen_paths:
                continue
            seen_paths.add(result_key)
            canonical = rows_by_id[entry.canonicalization_id]
            graph_nodes = {node["node_id"]: node for node in (canonical.graph or {}).get("nodes", [])}
            results.append({
                "project_id": str(entry.project_id),
                "canonicalization_id": str(entry.canonicalization_id),
                "version": canonical.version, "mode": mode,
                "path": [graph_nodes[node_id] for node_id in entry.path_node_ids if node_id in graph_nodes],
                "explanation": explanation,
            })
            if len(results) >= limit:
                break
    return results
