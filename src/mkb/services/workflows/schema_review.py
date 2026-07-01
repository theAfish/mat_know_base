from __future__ import annotations

from mkb.services._api_common import (
    SyncSessionLocal,
    datetime,
    init_db,
    timezone,
    uuid,
)

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
