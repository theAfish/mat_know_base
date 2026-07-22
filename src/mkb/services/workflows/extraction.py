from __future__ import annotations

from mkb.services._api_common import datetime, timezone, uuid
from mkb.agents.runtime import AgentRuntime
from mkb.ports import Database, ObjectStore
from mkb.services.workflows.serialization import serialize_raw_workflow

def extract_raw_workflow(project_id: str | uuid.UUID, model: str | None = None, verbose: bool = False, progress_callback=None, *, database: Database, object_store: ObjectStore | None = None) -> dict:
    """Append a new faithful raw-workflow extraction version for a project."""
    from mkb.agents.workflow_extraction import run_workflow_extraction

    readiness = get_raw_workflow_extraction_readiness(project_id, database=database)
    if not readiness.get("ready"):
        return {
            "status": "error",
            "message": readiness.get("message") or "Project is not ready for workflow extraction",
        }
    return run_workflow_extraction(
        uuid.UUID(str(project_id)), model=model, verbose=verbose,
        progress_callback=progress_callback,
        runtime=AgentRuntime(database, object_store),
    )

def get_raw_workflow_extraction_readiness(project_id: str | uuid.UUID, *, database: Database) -> dict:
    """Check whether a project has readable sources for raw workflow extraction."""
    from mkb.db.models import (
        ProcessedAsset,
        ProcessingType,
        ProjectAsset,
        RawWorkflowExtraction,
        ResearchProject,
    )

    pid = uuid.UUID(str(project_id))
    with database.session() as session:
        project = session.query(ResearchProject).filter_by(project_id=pid).first()
        if not project:
            return {"ready": False, "message": f"Project {pid} not found"}

        rows = (
            session.query(ProcessedAsset.asset_id)
            .join(ProjectAsset, ProjectAsset.asset_id == ProcessedAsset.asset_id)
            .filter(
                ProjectAsset.project_id == pid,
                ProcessedAsset.processing_type == ProcessingType.MARKDOWN,
            )
            .distinct()
            .all()
        )
        asset_ids = [str(row.asset_id) for row in rows]
        if not asset_ids:
            return {
                "ready": False,
                "message": (
                    "Workflow extraction requires processed Markdown, but this project has no readable "
                    "processed Markdown files yet. Run Process first and confirm Markdown outputs exist."
                ),
            }

        unfinished = (
            session.query(RawWorkflowExtraction)
            .filter(
                RawWorkflowExtraction.project_id == pid,
                RawWorkflowExtraction.graph.is_(None),
                RawWorkflowExtraction.status.in_(("IN_PROGRESS", "FAILED")),
            )
            .order_by(RawWorkflowExtraction.version.desc())
            .first()
        )
        return {
            "ready": True,
            "project_id": str(pid),
            "readable_asset_ids": asset_ids,
            "resume_extraction_id": str(unfinished.extraction_id) if unfinished else None,
            "resume_version": unfinished.version if unfinished else None,
            "has_checkpoint": bool(unfinished and unfinished.checkpoint),
        }

def list_raw_workflows(project_id: str | uuid.UUID, include_graph: bool = False, *, database: Database) -> list[dict]:
    """List append-only raw workflow versions, newest first."""
    from mkb.db.models import RawWorkflowExtraction

    pid = uuid.UUID(str(project_id))
    with database.session() as session:
        rows = (
            session.query(RawWorkflowExtraction)
            .filter(RawWorkflowExtraction.project_id == pid)
            .order_by(RawWorkflowExtraction.version.desc())
            .all()
        )
        return [_serialize_raw_workflow(row, include_graph=include_graph) for row in rows]

def get_raw_workflow(project_id: str | uuid.UUID, version: int | None = None, *, database: Database) -> dict | None:
    """Get the latest completed raw workflow, or a specific version."""
    from mkb.db.models import RawWorkflowExtraction

    pid = uuid.UUID(str(project_id))
    with database.session() as session:
        query = session.query(RawWorkflowExtraction).filter(RawWorkflowExtraction.project_id == pid)
        if version is None:
            query = query.filter(
                RawWorkflowExtraction.status == "COMPLETED",
                RawWorkflowExtraction.record_status.in_(("active", "needs_review")),
            ).order_by(RawWorkflowExtraction.version.desc())
        else:
            query = query.filter(RawWorkflowExtraction.version == version)
        row = query.first()
        return _serialize_raw_workflow(row, include_graph=True) if row else None

def delete_raw_workflow_version(project_id: str | uuid.UUID, version: int, *, database: Database) -> dict:
    """Delete one raw workflow version when no canonical version depends on it."""
    from mkb.db.models import CanonicalWorkflow, RawWorkflowExtraction

    pid = uuid.UUID(str(project_id))
    with database.session() as session:
        row = (
            session.query(RawWorkflowExtraction)
            .filter(
                RawWorkflowExtraction.project_id == pid,
                RawWorkflowExtraction.version == version,
            )
            .first()
        )
        if not row:
            return {"error": "Raw workflow version not found"}
        dependent_canonical = (
            session.query(CanonicalWorkflow)
            .filter(CanonicalWorkflow.raw_extraction_id == row.extraction_id)
            .order_by(CanonicalWorkflow.version.desc())
            .first()
        )
        if dependent_canonical:
            return {
                "error": (
                    f"Raw workflow v{version} cannot be deleted because canonical workflow "
                    f"v{dependent_canonical.version} still depends on it"
                )
            }

        extraction_id = row.extraction_id
        session.delete(row)
        session.commit()
        return {
            "status": "deleted",
            "project_id": str(pid),
            "version": version,
            "extraction_id": str(extraction_id),
        }

def _serialize_raw_workflow(row, include_graph: bool) -> dict:
    return serialize_raw_workflow(row, include_graph)

def review_raw_workflow(extraction_id: str | uuid.UUID, *, status: str | None = None, author: str = "system", database: Database) -> dict:
    """Run automatic checks and optionally set a manual lifecycle status."""
    from mkb.db.models import RawWorkflowExtraction
    from mkb.workflows.review import VALID_RECORD_STATUSES, audit_raw_graph

    eid = uuid.UUID(str(extraction_id))
    if status is not None and status not in VALID_RECORD_STATUSES:
        return {"error": f"Invalid record status: {status}"}
    with database.session() as session:
        row = session.query(RawWorkflowExtraction).filter_by(extraction_id=eid).first()
        if not row or not row.graph:
            return {"error": "Completed raw workflow not found"}
        later = session.query(RawWorkflowExtraction).filter(
            RawWorkflowExtraction.project_id == row.project_id,
            RawWorkflowExtraction.version > row.version,
            RawWorkflowExtraction.status == "COMPLETED",
        ).order_by(RawWorkflowExtraction.version.desc()).first()
        flags = audit_raw_graph(row.graph, later_graph=later.graph if later else None)
        row.review_flags = flags
        if status:
            row.record_status = status
        elif flags and row.record_status == "active":
            row.record_status = "needs_review"
        row.provenance = {**(row.provenance or {}), "last_reviewed_by": author}
        session.commit()
        return _serialize_raw_workflow(row, include_graph=False)

def correct_raw_workflow(
    extraction_id: str | uuid.UUID, graph: dict, *, reason: str, author: str,
    affected_nodes: list[str] | None = None, affected_edges: list[str] | None = None,
    evidence: str,
    database: Database,
) -> dict:
    """Create a corrected immutable version and supersede the source version."""
    from sqlalchemy import func
    from mkb.db.models import RawWorkflowExtraction
    from mkb.workflows.review import audit_raw_graph, correction_metadata, rebase_graph

    eid = uuid.UUID(str(extraction_id))
    new_id = uuid.uuid4()
    details = correction_metadata(reason, author, affected_nodes or [], affected_edges or [], evidence)
    with database.session() as session:
        source = session.query(RawWorkflowExtraction).filter_by(extraction_id=eid).first()
        if not source or source.status != "COMPLETED":
            return {"error": "Completed source workflow not found"}
        corrected = dict(graph)
        corrected["paper_id"] = str(source.project_id)
        corrected["schema_version"] = source.schema_version
        try:
            corrected = rebase_graph(corrected, new_id)
        except Exception as exc:
            return {"error": f"Corrected graph validation failed: {exc}"}
        version = (session.query(func.max(RawWorkflowExtraction.version)).filter_by(project_id=source.project_id).scalar() or 0) + 1
        flags = audit_raw_graph(corrected)
        row = RawWorkflowExtraction(
            extraction_id=new_id, project_id=source.project_id, version=version,
            schema_version=source.schema_version, extractor_version=source.extractor_version,
            model=source.model, status="COMPLETED",
            record_status="needs_review" if flags else "active",
            supersedes_extraction_id=source.extraction_id, graph=corrected,
            correction_reason=reason, correction_author=author,
            correction_details=details, review_flags=flags,
            provenance={**(source.provenance or {}), "correction_evidence": evidence},
            extracted_at=datetime.now(timezone.utc),
        )
        source.record_status = "superseded"
        session.add(row)
        session.commit()
        return _serialize_raw_workflow(row, include_graph=True)
