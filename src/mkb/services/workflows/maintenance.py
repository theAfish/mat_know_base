from __future__ import annotations

from mkb.services._api_common import (
    SyncSessionLocal,
    datetime,
    init_db,
    timezone,
    uuid,
)

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
