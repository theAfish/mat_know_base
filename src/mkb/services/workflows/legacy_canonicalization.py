"""Compatibility service functions for retired canonical workflow records."""

from __future__ import annotations

from mkb.services._api_common import uuid
from mkb.agents.runtime import AgentRuntime
from mkb.ports import Database, ObjectStore
from mkb.services.workflows.serialization import serialize_canonical_workflow

def delete_canonical_workflow_version(project_id: str | uuid.UUID, version: int, *, database: Database) -> dict:
    """Delete one canonical workflow version and its derived indexes/tasks."""
    from mkb.db.models import CanonicalWorkflow, WorkflowIndexEntry, WorkflowMaintenanceTask

    pid = uuid.UUID(str(project_id))
    with database.session() as session:
        row = (
            session.query(CanonicalWorkflow)
            .filter(
                CanonicalWorkflow.project_id == pid,
                CanonicalWorkflow.version == version,
            )
            .first()
        )
        if not row:
            return {"error": "Canonical workflow version not found"}

        canonicalization_id = row.canonicalization_id
        session.query(WorkflowIndexEntry).filter(
            WorkflowIndexEntry.canonicalization_id == canonicalization_id
        ).delete(synchronize_session=False)
        session.query(WorkflowMaintenanceTask).filter(
            WorkflowMaintenanceTask.project_id == pid,
            WorkflowMaintenanceTask.source_canonicalization_id == canonicalization_id,
        ).delete(synchronize_session=False)
        session.delete(row)
        session.commit()
        return {
            "status": "deleted",
            "project_id": str(pid),
            "version": version,
            "canonicalization_id": str(canonicalization_id),
        }

def canonicalize_workflow(project_id: str | uuid.UUID, raw_extraction_id: str | uuid.UUID | None = None, model: str | None = None, verbose: bool = False, progress_callback=None, *, database: Database, object_store: ObjectStore | None = None) -> dict:
    """Create an append-only canonical view from a valid raw workflow."""
    from mkb.agents.workflow_canonicalization import run_workflow_canonicalization

    return run_workflow_canonicalization(
        uuid.UUID(str(project_id)),
        uuid.UUID(str(raw_extraction_id)) if raw_extraction_id else None,
        model=model, verbose=verbose, progress_callback=progress_callback,
        runtime=AgentRuntime(database, object_store),
    )

def list_canonical_workflows(project_id: str | uuid.UUID, include_graph: bool = False, *, database: Database) -> list[dict]:
    from mkb.db.models import CanonicalWorkflow

    pid = uuid.UUID(str(project_id))
    with database.session() as session:
        rows = session.query(CanonicalWorkflow).filter_by(project_id=pid).order_by(CanonicalWorkflow.version.desc()).all()
        return [_serialize_canonical_workflow(row, include_graph) for row in rows]

def get_canonical_workflow(project_id: str | uuid.UUID, version: int | None = None, *, database: Database) -> dict | None:
    from mkb.db.models import CanonicalWorkflow

    pid = uuid.UUID(str(project_id))
    with database.session() as session:
        query = session.query(CanonicalWorkflow).filter(CanonicalWorkflow.project_id == pid)
        query = (
            query.filter(CanonicalWorkflow.status == "COMPLETED").order_by(CanonicalWorkflow.version.desc())
            if version is None else query.filter(CanonicalWorkflow.version == version)
        )
        row = query.first()
        return _serialize_canonical_workflow(row, True) if row else None

def _serialize_canonical_workflow(row, include_graph: bool) -> dict:
    return serialize_canonical_workflow(row, include_graph)
