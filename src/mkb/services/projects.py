"""Projects API service functions."""

from __future__ import annotations

from mkb.services._api_common import (
    SyncSessionLocal,
    init_db,
    logger,
    uuid,
)


def serialize_group(group, project_count: int) -> dict:
    return {
        "group_id": str(group.group_id),
        "name": group.name,
        "description": group.description,
        "color": group.color,
        "display_order": group.display_order,
        "project_count": project_count,
        "created_at": group.created_at.isoformat() if group.created_at else None,
        "updated_at": group.updated_at.isoformat() if group.updated_at else None,
    }


def rename_project(
    project_id: str | uuid.UUID,
    label: str,
    *,
    user_initiated: bool = True,
) -> dict:
    """Rename a research project.

    When ``user_initiated`` is True (the default), records
    ``metadata_["user_named"] = True`` so that the automatic post-extraction
    rename will skip this project. Callers that want to perform an automatic
    rename (e.g. from an extracted paper title) should pass
    ``user_initiated=False`` to leave that flag alone.
    """
    from mkb.db.models import ResearchProject

    pid = uuid.UUID(str(project_id))
    cleaned = (label or "").strip()
    if not cleaned:
        return {"error": "label must not be empty"}

    with SyncSessionLocal() as session:
        project = session.query(ResearchProject).filter_by(project_id=pid).first()
        if not project:
            return {"error": f"Project {project_id} not found"}
        project.label = cleaned
        if user_initiated:
            meta = dict(project.metadata_ or {})
            meta["user_named"] = True
            project.metadata_ = meta
        session.commit()
        return {
            "project_id": str(project.project_id),
            "label": project.label,
            "user_named": bool((project.metadata_ or {}).get("user_named")),
        }

def list_projects(limit: int = 50) -> list[dict]:
    """List research projects."""
    from collections import defaultdict

    from mkb.db.models import CanonicalWorkflow, KnowledgeFrame, ProcessedAsset, ProjectAsset, RawWorkflowExtraction, ResearchProject

    init_db()
    with SyncSessionLocal() as session:
        projects = (
            session.query(ResearchProject)
            .order_by(ResearchProject.created_at.desc())
            .limit(limit)
            .all()
        )
        if not projects:
            return []

        project_ids = [p.project_id for p in projects]

        # Bulk-fetch asset links for all queried projects
        all_links = session.query(ProjectAsset).filter(ProjectAsset.project_id.in_(project_ids)).all()
        project_to_asset_ids: dict = defaultdict(list)
        for link in all_links:
            project_to_asset_ids[link.project_id].append(link.asset_id)

        # Bulk-fetch which assets have at least one ProcessedAsset record
        all_asset_ids = [link.asset_id for link in all_links]
        if all_asset_ids:
            processed_ids = {
                row.asset_id
                for row in session.query(ProcessedAsset.asset_id)
                .filter(ProcessedAsset.asset_id.in_(all_asset_ids))
                .distinct()
                .all()
            }
        else:
            processed_ids = set()

        # Bulk-fetch frames
        frames = session.query(KnowledgeFrame).filter(KnowledgeFrame.project_id.in_(project_ids)).all()
        frame_by_project = {f.project_id: f for f in frames}
        workflow_rows = (
            session.query(RawWorkflowExtraction)
            .filter(RawWorkflowExtraction.project_id.in_(project_ids))
            .order_by(RawWorkflowExtraction.version.desc())
            .all()
        )
        workflow_by_project = {}
        for workflow in workflow_rows:
            workflow_by_project.setdefault(workflow.project_id, workflow)
            current = workflow_by_project[workflow.project_id]
            current_is_valid = (
                current.status == "COMPLETED"
                and current.record_status in {"active", "needs_review"}
            )
            if not current_is_valid and workflow.status == "COMPLETED" and workflow.record_status in {"active", "needs_review"}:
                workflow_by_project[workflow.project_id] = workflow
        canonical_rows = (
            session.query(CanonicalWorkflow)
            .filter(CanonicalWorkflow.project_id.in_(project_ids))
            .order_by(CanonicalWorkflow.version.desc()).all()
        )
        canonical_by_project = {}
        for canonical in canonical_rows:
            canonical_by_project.setdefault(canonical.project_id, canonical)

        result = []
        for p in projects:
            asset_ids_for_project = project_to_asset_ids[p.project_id]
            total = len(asset_ids_for_project)
            processed_count = sum(1 for aid in asset_ids_for_project if aid in processed_ids)
            if total == 0:
                processing_status = "NO_ASSETS"
            elif processed_count == 0:
                processing_status = "UNPROCESSED"
            elif processed_count < total:
                processing_status = "PARTIAL"
            else:
                processing_status = "PROCESSED"

            frame = frame_by_project.get(p.project_id)
            workflow = workflow_by_project.get(p.project_id)
            canonical = canonical_by_project.get(p.project_id)
            result.append({
                "project_id": str(p.project_id),
                "label": p.label,
                "source_path": p.source_path,
                "file_count": p.file_count,
                "asset_count": total,
                "processing_status": processing_status,
                "frame_status": frame.status.value if frame else "NO_FRAME",
                "workflow_status": workflow.status if workflow else "NO_WORKFLOW",
                "workflow_version": workflow.version if workflow else None,
                "canonical_workflow_status": canonical.status if canonical else "NO_CANONICAL_WORKFLOW",
                "canonical_workflow_version": canonical.version if canonical else None,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "duplicate_of": (p.metadata_ or {}).get("duplicate_of"),
                "group_id": str(p.group_id) if p.group_id else None,
            })
        return result


# ── Project groups ─────────────────────────────────────────────

def _serialize_group(g, project_count: int) -> dict:
    return serialize_group(g, project_count)

def list_project_groups() -> list[dict]:
    """List all project groups with project counts."""
    from sqlalchemy import func as sa_func

    from mkb.db.models import ProjectGroup, ResearchProject

    init_db()
    with SyncSessionLocal() as session:
        groups = (
            session.query(ProjectGroup)
            .order_by(ProjectGroup.display_order, ProjectGroup.created_at)
            .all()
        )
        counts = dict(
            session.query(ResearchProject.group_id, sa_func.count())
            .filter(ResearchProject.group_id.isnot(None))
            .group_by(ResearchProject.group_id)
            .all()
        )
        return [_serialize_group(g, counts.get(g.group_id, 0)) for g in groups]

def create_project_group(
    name: str,
    *,
    description: str | None = None,
    color: str | None = None,
    display_order: int | None = None,
) -> dict:
    from mkb.db.models import ProjectGroup

    cleaned = (name or "").strip()
    if not cleaned:
        return {"error": "name must not be empty"}

    init_db()
    with SyncSessionLocal() as session:
        if display_order is None:
            current_max = (
                session.query(ProjectGroup)
                .order_by(ProjectGroup.display_order.desc())
                .first()
            )
            display_order = (current_max.display_order + 1) if current_max else 0
        group = ProjectGroup(
            name=cleaned,
            description=(description or None),
            color=(color or None),
            display_order=int(display_order),
        )
        session.add(group)
        session.commit()
        session.refresh(group)
        return _serialize_group(group, 0)

def update_project_group(
    group_id: str | uuid.UUID,
    *,
    name: str | None = None,
    description: str | None = None,
    color: str | None = None,
    display_order: int | None = None,
) -> dict:
    from sqlalchemy import func as sa_func

    from mkb.db.models import ProjectGroup, ResearchProject

    gid = uuid.UUID(str(group_id))
    init_db()
    with SyncSessionLocal() as session:
        group = session.query(ProjectGroup).filter_by(group_id=gid).first()
        if not group:
            return {"error": f"Group {group_id} not found"}
        if name is not None:
            cleaned = name.strip()
            if not cleaned:
                return {"error": "name must not be empty"}
            group.name = cleaned
        if description is not None:
            group.description = description.strip() or None
        if color is not None:
            group.color = color.strip() or None
        if display_order is not None:
            group.display_order = int(display_order)
        session.commit()
        session.refresh(group)
        count = (
            session.query(sa_func.count())
            .select_from(ResearchProject)
            .filter(ResearchProject.group_id == gid)
            .scalar()
        ) or 0
        return _serialize_group(group, int(count))

def delete_project_group(group_id: str | uuid.UUID) -> dict:
    """Delete a group. Projects in it are unassigned (group_id set to NULL)."""
    from mkb.db.models import ProjectGroup, ResearchProject

    gid = uuid.UUID(str(group_id))
    init_db()
    with SyncSessionLocal() as session:
        group = session.query(ProjectGroup).filter_by(group_id=gid).first()
        if not group:
            return {"error": f"Group {group_id} not found"}
        unassigned = (
            session.query(ResearchProject)
            .filter(ResearchProject.group_id == gid)
            .update({ResearchProject.group_id: None}, synchronize_session=False)
        )
        session.delete(group)
        session.commit()
        return {"group_id": str(gid), "deleted": True, "unassigned_projects": int(unassigned)}

def delete_project(
    project_id: str | uuid.UUID,
    *,
    delete_s3_objects: bool = True,
) -> dict:
    """Hard-delete a research project and all data exclusively owned by it.

    Cascade:
    - ``ProjectAsset`` links for this project are removed.
    - ``Asset`` / ``ProcessedAsset`` / ``ProcessingLog`` records are removed
      only when the asset is **not** linked to any other project (i.e. not
      shared).  When ``delete_s3_objects`` is True the corresponding S3
      objects are removed before the DB records.
    - ``KnowledgeFrame`` owned by this project is deleted, along with its
      ``ExtractionPass``, all ``Projection`` rows (hard delete), and all
      ``Feedback`` rows whose ``target_frame_id`` / ``target_project_id``
      match.
    - The ``ResearchProject`` record itself is deleted last.

    Returns a summary dict or ``{"error": ...}`` when the project is not found.
    """
    from mkb.db.models import (
        Asset,
        CanonicalWorkflow,
        ExtractionPass,
        Feedback,
        KnowledgeFrame,
        ProcessedAsset,
        ProcessingLog,
        ProjectAsset,
        Projection,
        RawWorkflowExtraction,
        ResearchProject,
        WorkflowIndexEntry,
        WorkflowMaintenanceTask,
    )
    from mkb.storage.s3 import delete_object

    pid = uuid.UUID(str(project_id))
    init_db()

    with SyncSessionLocal() as session:
        project = session.query(ResearchProject).filter_by(project_id=pid).first()
        if not project:
            return {"error": f"Project {project_id} not found"}

        # ── Collect asset IDs linked to this project ──────────────────────
        own_links = session.query(ProjectAsset).filter_by(project_id=pid).all()
        own_asset_ids = [lnk.asset_id for lnk in own_links]

        # Determine which of those assets are shared with other projects
        shared_asset_ids: set[uuid.UUID] = set()
        if own_asset_ids:
            other_links = (
                session.query(ProjectAsset.asset_id)
                .filter(
                    ProjectAsset.asset_id.in_(own_asset_ids),
                    ProjectAsset.project_id != pid,
                )
                .distinct()
                .all()
            )
            shared_asset_ids = {row.asset_id for row in other_links}

        exclusive_asset_ids = [a for a in own_asset_ids if a not in shared_asset_ids]

        # ── Remove S3 objects and DB records for exclusive assets ─────────
        deleted_assets = 0
        deleted_processed = 0
        deleted_s3_objects = 0

        if exclusive_asset_ids:
            # ProcessedAsset rows (and their S3 objects)
            processed_rows = (
                session.query(ProcessedAsset)
                .filter(ProcessedAsset.asset_id.in_(exclusive_asset_ids))
                .all()
            )
            for pa in processed_rows:
                if delete_s3_objects:
                    try:
                        delete_object(pa.s3_bucket, pa.s3_key)
                        deleted_s3_objects += 1
                    except Exception:
                        logger.warning(
                            "Failed to delete S3 object %s/%s", pa.s3_bucket, pa.s3_key
                        )
                session.delete(pa)
            deleted_processed = len(processed_rows)

            # ProcessingLog rows
            session.query(ProcessingLog).filter(
                ProcessingLog.asset_id.in_(exclusive_asset_ids)
            ).delete(synchronize_session=False)

            # Raw asset S3 objects + Asset rows
            raw_assets = (
                session.query(Asset)
                .filter(Asset.asset_id.in_(exclusive_asset_ids))
                .all()
            )
            for asset in raw_assets:
                if delete_s3_objects:
                    try:
                        delete_object(asset.s3_bucket, asset.s3_key)
                        deleted_s3_objects += 1
                    except Exception:
                        logger.warning(
                            "Failed to delete S3 object %s/%s", asset.s3_bucket, asset.s3_key
                        )
                session.delete(asset)
            deleted_assets = len(raw_assets)

        # ── Remove ProjectAsset links (including shared ones) ─────────────
        for lnk in own_links:
            session.delete(lnk)

        # ── Knowledge frame + dependents ──────────────────────────────────
        frame = session.query(KnowledgeFrame).filter_by(project_id=pid).first()
        deleted_projections = 0
        deleted_passes = 0
        deleted_feedback = 0

        if frame:
            fid = frame.frame_id

            # Projections (hard delete)
            deleted_projections = (
                session.query(Projection)
                .filter(Projection.frame_id == fid)
                .delete(synchronize_session=False)
            )

            # ExtractionPass rows
            deleted_passes = (
                session.query(ExtractionPass)
                .filter(ExtractionPass.frame_id == fid)
                .delete(synchronize_session=False)
            )

            # Feedback rows tied to this frame
            deleted_feedback = (
                session.query(Feedback)
                .filter(Feedback.target_frame_id == fid)
                .delete(synchronize_session=False)
            )

            session.delete(frame)

        # Also remove any feedback rows referencing the project but a different
        # (or NULL) frame (defensive clean-up).
        extra_feedback = (
            session.query(Feedback)
            .filter(Feedback.target_project_id == pid)
            .delete(synchronize_session=False)
        )
        deleted_feedback += extra_feedback

        deleted_workflows = (
            session.query(RawWorkflowExtraction)
            .filter(RawWorkflowExtraction.project_id == pid)
            .delete(synchronize_session=False)
        )
        deleted_canonical_workflows = (
            session.query(CanonicalWorkflow)
            .filter(CanonicalWorkflow.project_id == pid)
            .delete(synchronize_session=False)
        )
        session.query(WorkflowIndexEntry).filter(
            WorkflowIndexEntry.project_id == pid
        ).delete(synchronize_session=False)
        session.query(WorkflowMaintenanceTask).filter(
            WorkflowMaintenanceTask.project_id == pid
        ).delete(synchronize_session=False)

        # ── Delete the project itself ─────────────────────────────────────
        session.delete(project)
        session.commit()

    return {
        "project_id": str(pid),
        "deleted": True,
        "deleted_assets": deleted_assets,
        "shared_assets_kept": len(shared_asset_ids),
        "deleted_processed_assets": deleted_processed,
        "deleted_s3_objects": deleted_s3_objects,
        "deleted_projections": deleted_projections,
        "deleted_extraction_passes": deleted_passes,
        "deleted_feedback": deleted_feedback,
        "deleted_workflow_versions": deleted_workflows,
        "deleted_canonical_workflow_versions": deleted_canonical_workflows,
    }


# ── Raw workflow graphs ───────────────────────────────────────

def assign_projects_to_group(
    project_ids: list[str | uuid.UUID],
    group_id: str | uuid.UUID | None,
) -> dict:
    """Assign multiple projects to a group, or to no group when ``group_id`` is None."""
    from mkb.db.models import ProjectGroup, ResearchProject

    if not project_ids:
        return {"updated": 0, "group_id": None}

    pids = [uuid.UUID(str(p)) for p in project_ids]
    gid = uuid.UUID(str(group_id)) if group_id else None

    init_db()
    with SyncSessionLocal() as session:
        if gid is not None:
            group = session.query(ProjectGroup).filter_by(group_id=gid).first()
            if not group:
                return {"error": f"Group {group_id} not found"}
        updated = (
            session.query(ResearchProject)
            .filter(ResearchProject.project_id.in_(pids))
            .update({ResearchProject.group_id: gid}, synchronize_session=False)
        )
        session.commit()
        return {"updated": int(updated), "group_id": str(gid) if gid else None}

