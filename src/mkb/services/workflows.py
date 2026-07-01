"""Workflows API service functions."""

from __future__ import annotations

from mkb.services._api_common import (
    SyncSessionLocal,
    datetime,
    init_db,
    timezone,
    uuid,
)


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
        "review_flags": row.review_flags or [],
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


def extract_raw_workflow(project_id: str | uuid.UUID, model: str | None = None, verbose: bool = False, progress_callback=None) -> dict:
    """Append a new faithful raw-workflow extraction version for a project."""
    from mkb.agents.workflow_extraction import run_workflow_extraction

    readiness = get_raw_workflow_extraction_readiness(project_id)
    if not readiness.get("ready"):
        return {
            "status": "error",
            "message": readiness.get("message") or "Project is not ready for workflow extraction",
        }
    init_db()
    return run_workflow_extraction(
        uuid.UUID(str(project_id)), model=model, verbose=verbose,
        progress_callback=progress_callback,
    )

def get_raw_workflow_extraction_readiness(project_id: str | uuid.UUID) -> dict:
    """Check whether a project has readable sources for raw workflow extraction."""
    from mkb.db.models import (
        ProcessedAsset,
        ProcessingType,
        ProjectAsset,
        RawWorkflowExtraction,
        ResearchProject,
    )

    pid = uuid.UUID(str(project_id))
    init_db()
    with SyncSessionLocal() as session:
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

def list_raw_workflows(project_id: str | uuid.UUID, include_graph: bool = False) -> list[dict]:
    """List append-only raw workflow versions, newest first."""
    from mkb.db.models import RawWorkflowExtraction

    pid = uuid.UUID(str(project_id))
    init_db()
    with SyncSessionLocal() as session:
        rows = (
            session.query(RawWorkflowExtraction)
            .filter(RawWorkflowExtraction.project_id == pid)
            .order_by(RawWorkflowExtraction.version.desc())
            .all()
        )
        return [_serialize_raw_workflow(row, include_graph=include_graph) for row in rows]

def get_raw_workflow(project_id: str | uuid.UUID, version: int | None = None) -> dict | None:
    """Get the latest completed raw workflow, or a specific version."""
    from mkb.db.models import RawWorkflowExtraction

    pid = uuid.UUID(str(project_id))
    init_db()
    with SyncSessionLocal() as session:
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

def delete_raw_workflow_version(project_id: str | uuid.UUID, version: int) -> dict:
    """Delete one raw workflow version when no canonical version depends on it."""
    from mkb.db.models import CanonicalWorkflow, RawWorkflowExtraction

    pid = uuid.UUID(str(project_id))
    init_db()
    with SyncSessionLocal() as session:
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

def delete_canonical_workflow_version(project_id: str | uuid.UUID, version: int) -> dict:
    """Delete one canonical workflow version and its derived indexes/tasks."""
    from mkb.db.models import CanonicalWorkflow, WorkflowIndexEntry, WorkflowMaintenanceTask

    pid = uuid.UUID(str(project_id))
    init_db()
    with SyncSessionLocal() as session:
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

def _serialize_raw_workflow(row, include_graph: bool) -> dict:
    return serialize_raw_workflow(row, include_graph)

def review_raw_workflow(extraction_id: str | uuid.UUID, *, status: str | None = None, author: str = "system") -> dict:
    """Run automatic checks and optionally set a manual lifecycle status."""
    from mkb.db.models import RawWorkflowExtraction
    from mkb.workflows.review import VALID_RECORD_STATUSES, audit_raw_graph

    init_db()
    eid = uuid.UUID(str(extraction_id))
    if status is not None and status not in VALID_RECORD_STATUSES:
        return {"error": f"Invalid record status: {status}"}
    with SyncSessionLocal() as session:
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
) -> dict:
    """Create a corrected immutable version and supersede the source version."""
    from sqlalchemy import func
    from mkb.db.models import RawWorkflowExtraction
    from mkb.workflows.review import audit_raw_graph, correction_metadata, rebase_graph

    init_db()
    eid = uuid.UUID(str(extraction_id))
    new_id = uuid.uuid4()
    details = correction_metadata(reason, author, affected_nodes or [], affected_edges or [], evidence)
    with SyncSessionLocal() as session:
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

def canonicalize_workflow(project_id: str | uuid.UUID, raw_extraction_id: str | uuid.UUID | None = None, model: str | None = None, verbose: bool = False, progress_callback=None) -> dict:
    """Create an append-only canonical view from a valid raw workflow."""
    from mkb.agents.workflow_canonicalization import run_workflow_canonicalization

    init_db()
    return run_workflow_canonicalization(
        uuid.UUID(str(project_id)),
        uuid.UUID(str(raw_extraction_id)) if raw_extraction_id else None,
        model=model, verbose=verbose, progress_callback=progress_callback,
    )

def list_canonical_workflows(project_id: str | uuid.UUID, include_graph: bool = False) -> list[dict]:
    from mkb.db.models import CanonicalWorkflow

    pid = uuid.UUID(str(project_id))
    init_db()
    with SyncSessionLocal() as session:
        rows = session.query(CanonicalWorkflow).filter_by(project_id=pid).order_by(CanonicalWorkflow.version.desc()).all()
        return [_serialize_canonical_workflow(row, include_graph) for row in rows]

def get_canonical_workflow(project_id: str | uuid.UUID, version: int | None = None) -> dict | None:
    from mkb.db.models import CanonicalWorkflow

    pid = uuid.UUID(str(project_id))
    init_db()
    with SyncSessionLocal() as session:
        query = session.query(CanonicalWorkflow).filter(CanonicalWorkflow.project_id == pid)
        query = (
            query.filter(CanonicalWorkflow.status == "COMPLETED").order_by(CanonicalWorkflow.version.desc())
            if version is None else query.filter(CanonicalWorkflow.version == version)
        )
        row = query.first()
        return _serialize_canonical_workflow(row, True) if row else None

def _serialize_canonical_workflow(row, include_graph: bool) -> dict:
    return serialize_canonical_workflow(row, include_graph)

def curate_workflow_schema(*, min_support: int = 2, author: str = "schema-curator/1.0") -> list[dict]:
    """Analyze accumulated workflows and persist new evidence-backed proposals."""
    from mkb.db.models import CanonicalWorkflow, RawWorkflowExtraction, SchemaProposal, SchemaProposalRevision, WorkflowSchemaVersion
    from mkb.workflows.curator import analyze_canonical_workflows
    from mkb.workflows.schema_library import get_schema_library_payload

    init_db()
    with SyncSessionLocal() as session:
        current = session.query(WorkflowSchemaVersion).filter_by(status="active").order_by(WorkflowSchemaVersion.version.desc()).first()
        if not current:
            current = WorkflowSchemaVersion(version=1, name="workflow-schema/1.0", payload=get_schema_library_payload(), created_by="seed")
            session.add(current)
            session.flush()
        rows = session.query(CanonicalWorkflow).filter_by(status="COMPLETED").all()
        workflows = []
        for row in rows:
            raw = session.query(RawWorkflowExtraction).filter_by(extraction_id=row.raw_extraction_id).first()
            workflows.append({"canonicalization_id": str(row.canonicalization_id), "graph": row.graph, "raw_graph": raw.graph if raw else {}})
        generated = analyze_canonical_workflows(workflows, min_support=min_support)
        results = []
        for item in generated:
            duplicate = session.query(SchemaProposal).filter(
                SchemaProposal.status.in_(("pending", "revision_requested")),
                SchemaProposal.proposal_type == item["proposal_type"],
                SchemaProposal.payload == item["payload"],
            ).first()
            if duplicate:
                continue
            rationale = (
                f"Deterministic discovery signal: {item.get('analysis', {}).get('signal', 'unknown')} "
                f"with support from {len(item.get('evidence_workflow_ids', []))} workflows."
            )
            proposal = SchemaProposal(
                **item, rationale=rationale,
                base_schema_version=current.name, created_by=author,
            )
            session.add(proposal)
            session.flush()
            session.add(SchemaProposalRevision(
                proposal_id=proposal.proposal_id, revision_number=1,
                payload=proposal.payload,
                evidence_workflow_ids=proposal.evidence_workflow_ids,
                analysis=proposal.analysis, rationale=proposal.rationale,
                author=author,
                author_type="agent" if "agent" in author else "system",
                change_note="Initial proposal draft",
                validation_errors=[],
            ))
            results.append({**item, "proposal_id": str(proposal.proposal_id), "status": "pending"})
        session.commit()
        return results

def list_schema_proposals(status: str | None = "pending") -> list[dict]:
    from sqlalchemy import func
    from mkb.db.models import SchemaProposal, SchemaProposalRevision

    init_db()
    with SyncSessionLocal() as session:
        query = session.query(SchemaProposal)
        if status:
            query = query.filter_by(status=status)
        rows = query.order_by(SchemaProposal.created_at.desc()).all()
        revision_counts = dict(
            session.query(
                SchemaProposalRevision.proposal_id,
                func.count(SchemaProposalRevision.revision_id),
            ).group_by(SchemaProposalRevision.proposal_id).all()
        )
        return [{
            "proposal_id": str(row.proposal_id), "proposal_type": row.proposal_type,
            "status": row.status, "payload": row.payload,
            "evidence_workflow_ids": row.evidence_workflow_ids, "analysis": row.analysis,
            "base_schema_version": row.base_schema_version, "created_by": row.created_by,
            "rationale": row.rationale,
            "reviewer_notes": row.reviewer_notes,
            "validation_errors": (row.analysis or {}).get("validation_errors", []),
            "revision_count": int(revision_counts.get(row.proposal_id, 0)),
            "reviewed_by": row.reviewed_by,
            "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        } for row in rows]

def get_schema_proposal_revisions(proposal_id: str | uuid.UUID) -> list[dict]:
    from mkb.db.models import SchemaProposalRevision

    pid = uuid.UUID(str(proposal_id))
    init_db()
    with SyncSessionLocal() as session:
        rows = session.query(SchemaProposalRevision).filter_by(proposal_id=pid).order_by(
            SchemaProposalRevision.revision_number.desc()
        ).all()
        return [{
            "revision_id": str(row.revision_id),
            "revision_number": row.revision_number,
            "payload": row.payload,
            "evidence_workflow_ids": row.evidence_workflow_ids,
            "analysis": row.analysis,
            "rationale": row.rationale,
            "author": row.author,
            "author_type": row.author_type,
            "change_note": row.change_note,
            "validation_errors": row.validation_errors,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        } for row in rows]

def edit_schema_proposal(
    proposal_id: str | uuid.UUID, *, payload: dict,
    evidence_workflow_ids: list[str], rationale: str,
    editor: str, change_note: str,
) -> dict:
    """Save an attributed proposal draft revision and revalidate it."""
    from sqlalchemy import func
    from mkb.db.models import (
        CanonicalWorkflow, RawWorkflowExtraction, SchemaProposal, SchemaProposalRevision,
        WorkflowSchemaVersion,
    )
    from mkb.workflows.curator import validate_proposal

    pid = uuid.UUID(str(proposal_id))
    if not editor.strip() or not change_note.strip():
        return {"error": "editor and change_note are required"}
    try:
        evidence_uuids = [uuid.UUID(value) for value in evidence_workflow_ids]
    except (TypeError, ValueError, AttributeError):
        return {"error": "evidence_workflow_ids must contain canonicalization UUIDs"}
    init_db()
    with SyncSessionLocal() as session:
        row = session.query(SchemaProposal).filter_by(proposal_id=pid).first()
        if not row or row.status not in {"pending", "revision_requested"}:
            return {"error": "Only pending or revision-requested proposals can be edited"}
        current = session.query(WorkflowSchemaVersion).filter_by(status="active").order_by(
            WorkflowSchemaVersion.version.desc()
        ).first()
        if not current:
            return {"error": "Active schema library not found"}
        known_evidence = {
            str(value) for (value,) in session.query(CanonicalWorkflow.canonicalization_id).filter(
                CanonicalWorkflow.canonicalization_id.in_(evidence_uuids)
            ).all()
        } if evidence_workflow_ids else set()
        if evidence_workflow_ids:
            known_evidence.update({
                str(value) for (value,) in session.query(RawWorkflowExtraction.extraction_id).filter(
                    RawWorkflowExtraction.extraction_id.in_(evidence_uuids)
                ).all()
            })
        errors = validate_proposal(
            row.proposal_type, payload, evidence_workflow_ids, current.payload,
        )
        missing = sorted(set(evidence_workflow_ids) - known_evidence)
        if missing:
            errors.append(f"unknown evidence workflows: {', '.join(missing)}")
        row.payload = payload
        row.evidence_workflow_ids = evidence_workflow_ids
        row.rationale = rationale.strip()
        row.base_schema_version = current.name
        row.analysis = {**(row.analysis or {}), "validation_errors": errors}
        row.status = "pending" if not errors else "revision_requested"
        revision_number = int(
            session.query(func.coalesce(func.max(SchemaProposalRevision.revision_number), 0))
            .filter_by(proposal_id=pid).scalar()
        ) + 1
        session.add(SchemaProposalRevision(
            proposal_id=pid, revision_number=revision_number,
            payload=payload, evidence_workflow_ids=evidence_workflow_ids,
            analysis=row.analysis, rationale=row.rationale,
            author=editor.strip(), author_type="human",
            change_note=change_note.strip(), validation_errors=errors,
        ))
        session.commit()
        return {
            "proposal_id": str(pid), "status": row.status,
            "revision_number": revision_number, "validation_errors": errors,
        }

def get_workflow_schema_status() -> dict:
    """Return global schema and curator queue summary for the frontend."""
    from sqlalchemy import func
    from mkb.db.models import SchemaProposal, WorkflowMaintenanceTask, WorkflowSchemaVersion
    from mkb.workflows.schema_library import get_schema_library_payload

    init_db()
    with SyncSessionLocal() as session:
        active = session.query(WorkflowSchemaVersion).filter_by(status="active").order_by(
            WorkflowSchemaVersion.version.desc()
        ).first()
        payload = active.payload if active else get_schema_library_payload()
        proposal_counts = dict(
            session.query(SchemaProposal.status, func.count(SchemaProposal.proposal_id))
            .group_by(SchemaProposal.status).all()
        )
        pending_recanonicalizations = session.query(func.count(WorkflowMaintenanceTask.task_id)).filter(
            WorkflowMaintenanceTask.task_type == "recanonicalize",
            WorkflowMaintenanceTask.status == "pending",
        ).scalar() or 0
        return {
            "schema_version": active.name if active else payload["schema_version"],
            "version_number": active.version if active else 1,
            "status": active.status if active else "seed",
            "change_summary": active.change_summary if active else "Built-in seed schema",
            "created_by": active.created_by if active else "system",
            "created_at": active.created_at.isoformat() if active and active.created_at else None,
            "object_schema_count": len(payload.get("object_schemas", {})),
            "operation_template_count": len(payload.get("operation_templates", {})),
            "card_count": len(payload.get("cards", {})),
            "granularity_relation_count": len(payload.get("granularity_relations", [])),
            "proposal_counts": proposal_counts,
            "pending_recanonicalizations": int(pending_recanonicalizations),
        }

def review_schema_proposal(
    proposal_id: str | uuid.UUID, *, approve: bool | None = None,
    reviewer: str, decision: str | None = None, notes: str = "",
) -> dict:
    """Validate and approve/reject a proposal; approval creates a schema snapshot."""
    from sqlalchemy import func
    from mkb.db.models import (
        CanonicalWorkflow, RawWorkflowExtraction, SchemaProposal, SchemaProposalRevision,
        WorkflowMaintenanceTask, WorkflowSchemaVersion,
    )
    from mkb.workflows.curator import apply_proposal, validate_proposal
    from mkb.workflows.maintenance import recanonicalization_reason_for_proposal

    decision = decision or ("approve" if approve else "reject")
    if decision not in {"approve", "reject", "request_revision"}:
        return {"error": f"Unsupported review decision: {decision}"}
    if not reviewer.strip():
        return {"error": "reviewer is required"}
    if decision == "request_revision" and not notes.strip():
        return {"error": "Revision requests require reviewer notes"}
    init_db()
    with SyncSessionLocal() as session:
        row = session.query(SchemaProposal).filter_by(proposal_id=uuid.UUID(str(proposal_id))).first()
        if not row or row.status not in {"pending", "revision_requested"}:
            return {"error": "Reviewable proposal not found"}
        current = session.query(WorkflowSchemaVersion).filter_by(status="active").order_by(WorkflowSchemaVersion.version.desc()).first()
        if not current:
            return {"error": "Schema library is not initialized; run the curator first"}
        errors = validate_proposal(row.proposal_type, row.payload, row.evidence_workflow_ids, current.payload)
        known_evidence = {
            str(value) for (value,) in session.query(CanonicalWorkflow.canonicalization_id).filter(
                CanonicalWorkflow.canonicalization_id.in_([
                    uuid.UUID(value) for value in row.evidence_workflow_ids
                ])
            ).all()
        } if row.evidence_workflow_ids else set()
        if row.evidence_workflow_ids:
            known_evidence.update({
                str(value) for (value,) in session.query(RawWorkflowExtraction.extraction_id).filter(
                    RawWorkflowExtraction.extraction_id.in_([
                        uuid.UUID(value) for value in row.evidence_workflow_ids
                    ])
                ).all()
            })
        missing = sorted(set(row.evidence_workflow_ids) - known_evidence)
        if missing:
            errors.append(f"unknown evidence workflows: {', '.join(missing)}")
        rebased_from = None
        if decision == "approve" and row.base_schema_version != current.name:
            rebased_from = row.base_schema_version
            row.base_schema_version = current.name
            row.analysis = {
                **(row.analysis or {}),
                "rebased_from_schema": rebased_from,
                "rebased_to_schema": current.name,
            }
        if decision == "approve" and errors:
            return {"error": "Schema validation failed", "details": errors}
        row.reviewed_by = reviewer.strip()
        row.reviewer_notes = notes.strip() or None
        row.reviewed_at = datetime.now(timezone.utc)
        revision_number = int(
            session.query(func.coalesce(func.max(SchemaProposalRevision.revision_number), 0))
            .filter_by(proposal_id=row.proposal_id).scalar()
        ) + 1
        session.add(SchemaProposalRevision(
            proposal_id=row.proposal_id, revision_number=revision_number,
            payload=row.payload, evidence_workflow_ids=row.evidence_workflow_ids,
            analysis=row.analysis, rationale=row.rationale,
            author=reviewer.strip(), author_type="human",
            change_note=(
                f"Automatically rebased {rebased_from} to {current.name}. "
                if rebased_from else ""
            ) + f"Review decision: {decision}. {notes.strip()}".strip(),
            validation_errors=errors,
        ))
        if decision in {"reject", "request_revision"}:
            row.status = "rejected" if decision == "reject" else "revision_requested"
            session.commit()
            return {
                "proposal_id": str(row.proposal_id), "status": row.status,
                "revision_number": revision_number,
            }
        next_version = current.version + 1
        next_name = f"workflow-schema/1.{next_version - 1}"
        base_payload = {**current.payload, "schema_version": next_name}
        payload = apply_proposal(base_payload, row.proposal_type, row.payload)
        current.status = "superseded"
        session.add(WorkflowSchemaVersion(
            version=next_version, name=next_name, payload=payload,
            change_summary=f"Applied proposal {row.proposal_id}: {row.proposal_type}",
            created_by=reviewer,
        ))
        row.status = "approved"
        affected = 0
        queues_created = 0
        queues_updated = 0
        duplicate_queues_removed = 0
        completed = session.query(CanonicalWorkflow).filter_by(status="COMPLETED").order_by(
            CanonicalWorkflow.project_id, CanonicalWorkflow.version.desc()
        ).all()
        latest_by_project = {}
        for canonical in completed:
            latest_by_project.setdefault(canonical.project_id, canonical)
        # Every immutable schema snapshot has a new version. Even when a
        # proposal directly cites only a subset, each latest project view is
        # queued so its canonical graph can explicitly target that version.
        for canonical in latest_by_project.values():
            canonical.provenance = {
                **(canonical.provenance or {}),
                "recanonicalization_required": True,
                "target_schema_version": next_name,
            }
            pending_tasks = session.query(WorkflowMaintenanceTask).filter_by(
                project_id=canonical.project_id,
                task_type="recanonicalize",
                status="pending",
            ).order_by(WorkflowMaintenanceTask.created_at).all()
            proposal_ids = [str(row.proposal_id)]
            if pending_tasks:
                task = pending_tasks[0]
                previous_ids = (task.scope or {}).get("schema_proposal_ids", [])
                task.scope = {
                    **(task.scope or {}),
                    "schema_proposal_ids": list(dict.fromkeys([
                        *previous_ids, *proposal_ids,
                    ])),
                }
                task.reason = "schema_version_changed"
                task.source_raw_extraction_id = canonical.raw_extraction_id
                task.source_canonicalization_id = canonical.canonicalization_id
                task.target_schema_version = next_name
                task.requested_by = reviewer.strip()
                for duplicate in pending_tasks[1:]:
                    session.delete(duplicate)
                    duplicate_queues_removed += 1
                queues_updated += 1
            else:
                session.add(WorkflowMaintenanceTask(
                    project_id=canonical.project_id,
                    task_type="recanonicalize",
                    reason=recanonicalization_reason_for_proposal(row.proposal_type),
                    source_raw_extraction_id=canonical.raw_extraction_id,
                    source_canonicalization_id=canonical.canonicalization_id,
                    target_schema_version=next_name,
                    requested_by=reviewer.strip(),
                    scope={"schema_proposal_ids": proposal_ids},
                ))
                queues_created += 1
            affected += 1
        session.commit()
        return {
            "proposal_id": str(row.proposal_id), "status": "approved",
            "schema_version": next_name,
            "rebased_from_schema": rebased_from,
            "recanonicalization_scheduled": affected,
            "queues_created": queues_created,
            "queues_updated": queues_updated,
            "duplicate_queues_removed": duplicate_queues_removed,
        }

def schedule_workflow_reextraction(project_id: str | uuid.UUID, *, reason: str, requested_by: str, scope: dict | None = None, raw_extraction_id: str | uuid.UUID | None = None) -> dict:
    """Queue an approved full or partial re-extraction request."""
    from mkb.db.models import RawWorkflowExtraction, WorkflowMaintenanceTask
    from mkb.workflows.maintenance import validate_reextraction_request

    pid = uuid.UUID(str(project_id))
    validated_scope = validate_reextraction_request(reason, scope)
    init_db()
    with SyncSessionLocal() as session:
        query = session.query(RawWorkflowExtraction).filter(
            RawWorkflowExtraction.project_id == pid,
            RawWorkflowExtraction.status == "COMPLETED",
            RawWorkflowExtraction.record_status.in_(("active", "needs_review")),
        )
        raw = query.filter_by(extraction_id=uuid.UUID(str(raw_extraction_id))).first() if raw_extraction_id else query.order_by(RawWorkflowExtraction.version.desc()).first()
        if not raw:
            return {"error": "No valid raw workflow is available for re-extraction"}
        task = WorkflowMaintenanceTask(
            project_id=pid, task_type="reextract", reason=reason,
            source_raw_extraction_id=raw.extraction_id, scope=validated_scope,
            requested_by=requested_by,
        )
        session.add(task)
        session.commit()
        return {"task_id": str(task.task_id), "status": task.status, "task_type": task.task_type, "scope": task.scope}

def schedule_workflow_recanonicalization(project_id: str | uuid.UUID, *, reason: str = "manual_request", requested_by: str, raw_extraction_id: str | uuid.UUID | None = None, target_schema_version: str | None = None) -> dict:
    from mkb.db.models import RawWorkflowExtraction, WorkflowMaintenanceTask
    from mkb.workflows.maintenance import RECANONICALIZATION_REASONS
    from mkb.workflows.schema_library import get_schema_library_payload

    if reason not in RECANONICALIZATION_REASONS:
        raise ValueError(f"Unsupported recanonicalization reason: {reason}")
    pid = uuid.UUID(str(project_id))
    init_db()
    with SyncSessionLocal() as session:
        query = session.query(RawWorkflowExtraction).filter(
            RawWorkflowExtraction.project_id == pid,
            RawWorkflowExtraction.status == "COMPLETED",
            RawWorkflowExtraction.record_status.in_(("active", "needs_review")),
        )
        raw = query.filter_by(extraction_id=uuid.UUID(str(raw_extraction_id))).first() if raw_extraction_id else query.order_by(RawWorkflowExtraction.version.desc()).first()
        if not raw:
            return {"error": "No valid raw workflow is available for canonicalization"}
        task = WorkflowMaintenanceTask(
            project_id=pid, task_type="recanonicalize", reason=reason,
            source_raw_extraction_id=raw.extraction_id,
            target_schema_version=target_schema_version or get_schema_library_payload()["schema_version"],
            requested_by=requested_by,
        )
        session.add(task)
        session.commit()
        return {"task_id": str(task.task_id), "status": task.status, "task_type": task.task_type}

def list_workflow_maintenance_tasks(*, status: str | None = None, project_id: str | uuid.UUID | None = None) -> list[dict]:
    from mkb.db.models import WorkflowMaintenanceTask

    init_db()
    with SyncSessionLocal() as session:
        query = session.query(WorkflowMaintenanceTask)
        if status:
            query = query.filter_by(status=status)
        if project_id:
            query = query.filter_by(project_id=uuid.UUID(str(project_id)))
        return [{
            "task_id": str(row.task_id), "project_id": str(row.project_id),
            "task_type": row.task_type, "reason": row.reason, "scope": row.scope,
            "status": row.status, "target_schema_version": row.target_schema_version,
            "result": row.result, "error": row.error,
        } for row in query.order_by(WorkflowMaintenanceTask.created_at.desc()).all()]

def run_workflow_maintenance_task(task_id: str | uuid.UUID, *, model: str | None = None, verbose: bool = False, progress_callback=None) -> dict:
    """Execute one queued task, retaining both raw and canonical history."""
    from mkb.agents.workflow_canonicalization import run_workflow_canonicalization
    from mkb.agents.workflow_extraction import run_workflow_extraction
    from mkb.db.models import RawWorkflowExtraction, WorkflowMaintenanceTask

    tid = uuid.UUID(str(task_id))
    init_db()
    with SyncSessionLocal() as session:
        task = session.query(WorkflowMaintenanceTask).filter_by(task_id=tid).first()
        if not task or task.status not in {"pending", "failed"}:
            return {"error": "Pending or failed maintenance task not found"}
        task.status = "running"
        task.started_at = datetime.now(timezone.utc)
        project_id, task_type, reason = task.project_id, task.task_type, task.reason
        source_raw_id, scope = task.source_raw_extraction_id, task.scope
        target_schema_version = task.target_schema_version
        raw = session.query(RawWorkflowExtraction).filter_by(extraction_id=source_raw_id).first()
        baseline = raw.graph if raw else None
        session.commit()
    try:
        if task_type == "reextract":
            extraction = run_workflow_extraction(
                project_id, model=model, verbose=verbose, progress_callback=progress_callback,
                reextraction_request={
                    "reason": reason, "scope": scope,
                    "source_raw_extraction_id": str(source_raw_id),
                    "baseline_graph": baseline,
                },
            )
            if extraction.get("status") != "completed":
                raise RuntimeError(extraction.get("message") or "Re-extraction failed")
            canonical = run_workflow_canonicalization(
                project_id, uuid.UUID(extraction["extraction_id"]), model=model,
                verbose=verbose, progress_callback=progress_callback,
                recanonicalization_reason="raw_version_changed",
            )
            result = {"extraction": extraction, "canonicalization": canonical}
        else:
            result = run_workflow_canonicalization(
                project_id, source_raw_id, model=model, verbose=verbose,
                progress_callback=progress_callback, recanonicalization_reason=reason,
                target_schema_version=target_schema_version,
            )
        successful = result.get("status") == "completed" or result.get("canonicalization", {}).get("status") == "completed"
        if not successful:
            raise RuntimeError(result.get("message") or "Workflow maintenance failed")
    except Exception as exc:
        with SyncSessionLocal() as session:
            task = session.query(WorkflowMaintenanceTask).filter_by(task_id=tid).first()
            task.status, task.error, task.completed_at = "failed", str(exc), datetime.now(timezone.utc)
            session.commit()
        return {"task_id": str(tid), "status": "failed", "error": str(exc)}
    with SyncSessionLocal() as session:
        task = session.query(WorkflowMaintenanceTask).filter_by(task_id=tid).first()
        task.status, task.result, task.completed_at = "completed", result, datetime.now(timezone.utc)
        session.commit()
    return {"task_id": str(tid), "status": "completed", "result": result}

def run_pending_recanonicalizations(
    *, model: str | None = None, verbose: bool = False, progress_callback=None,
) -> dict:
    """Run all currently pending recanonicalizations as one global batch job."""
    from mkb.db.models import WorkflowMaintenanceTask

    init_db()
    with SyncSessionLocal() as session:
        rows = session.query(WorkflowMaintenanceTask).filter_by(
                task_type="recanonicalize", status="pending",
            ).order_by(WorkflowMaintenanceTask.created_at.desc()).all()
        latest_by_project = {}
        duplicates = []
        for row in rows:
            if row.project_id in latest_by_project:
                duplicates.append((row, latest_by_project[row.project_id]))
            else:
                latest_by_project[row.project_id] = row
        for duplicate, retained in duplicates:
            duplicate.status = "superseded"
            duplicate.result = {
                **(duplicate.result or {}),
                "superseded_by_task_id": str(retained.task_id),
            }
        session.commit()
        task_ids = [row.task_id for row in latest_by_project.values()]
    results = []
    completed = 0
    failed = 0
    for index, task_id in enumerate(task_ids, 1):
        if progress_callback:
            progress_callback({
                "stage": "recanonicalization_batch",
                "message": f"Recanonicalizing project workflow {index}/{len(task_ids)}",
            })
        result = run_workflow_maintenance_task(
            task_id, model=model, verbose=verbose,
            progress_callback=progress_callback,
        )
        results.append(result)
        if result.get("status") == "completed":
            completed += 1
        else:
            failed += 1
    return {
        "status": "completed" if failed == 0 else "completed_with_errors",
        "task_count": len(task_ids), "completed": completed, "failed": failed,
        "duplicate_tasks_coalesced": len(duplicates),
        "results": results,
    }

def rebuild_workflow_indexes(project_id: str | uuid.UUID | None = None) -> dict:
    from mkb.db.models import CanonicalWorkflow, RawWorkflowExtraction, WorkflowIndexEntry
    from mkb.workflows.indexing import build_index_entries
    from mkb.workflows.schema_library import get_schema_library_payload

    init_db()
    with SyncSessionLocal() as session:
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

def search_canonical_workflows(source: str | None = None, operation: str | None = None, target: str | None = None, mode: str = "strict", limit: int = 100) -> list[dict]:
    """Search persisted workflow indexes and return evidence-rich explanations."""
    from mkb.db.models import CanonicalWorkflow, WorkflowIndexEntry
    from mkb.workflows.indexing import QUERY_MODES, match_index_entry, normalize

    legacy_modes = {"exact": "strict", "relaxed": "alias-expanded", "expanded": "granularity-expanded", "summarized": "granularity-expanded"}
    mode = legacy_modes.get(mode, mode)
    if mode not in QUERY_MODES:
        raise ValueError(f"Unsupported query mode: {mode}")
    init_db()
    results = []
    seen_paths = set()
    with SyncSessionLocal() as session:
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

