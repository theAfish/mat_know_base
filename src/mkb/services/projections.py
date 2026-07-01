"""Projections API service functions."""

from __future__ import annotations

from mkb.services._api_common import (
    Path,
    SyncSessionLocal,
    init_db,
    uuid,
)


import json


def serialize_projection_payload(projection_id: uuid.UUID, out_path: Path, format: str) -> Path:
    from mkb.db.models import KnowledgeFrame, Projection, Space

    with SyncSessionLocal() as session:
        projection = session.query(Projection).filter_by(projection_id=projection_id).first()
        if not projection:
            raise ValueError(f"Projection {projection_id} not found")
        frame = session.query(KnowledgeFrame).filter_by(frame_id=projection.frame_id).first()
        space = session.query(Space).filter_by(space_id=projection.space_id).first()
        record = {
            "projection_id": str(projection.projection_id),
            "space": {
                "space_id": str(projection.space_id),
                "name": space.name if space else None,
                "purpose": getattr(space, "purpose", None) if space else None,
                "version": projection.space_version,
            },
            "frame_id": str(projection.frame_id) if projection.frame_id else None,
            "project_id": str(frame.project_id) if frame else None,
            "status": projection.status.value,
            "source_type": getattr(projection, "source_type", "frame"),
            "extracted_at": projection.extracted_at.isoformat() if projection.extracted_at else None,
            "agent_notes": projection.agent_notes,
            "data": projection.data or {},
        }

    fmt = (format or "yaml").strip().lower()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False, default=str))
    else:
        import yaml

        out_path.write_text(yaml.safe_dump(record, sort_keys=False, allow_unicode=True))
    return out_path


def project(
    space_id: str | uuid.UUID,
    frame_id: str | uuid.UUID | None = None,
    project_id: str | uuid.UUID | None = None,
    model: str | None = None,
    verbose: bool = False,
    progress_callback=None,
    source_type: str = "frame",
) -> dict:
    """Run projection on one or more frames using a space definition.

    Args:
        source_type: ``"frame"`` (default) to project from the curated knowledge
            frame, or ``"markdown"`` to project directly from the processed
            Markdown of the project's papers (no extraction step required).

    If frame_id given, project that specific frame.
    If project_id given, find or auto-create the frame for that project.
    """
    from mkb.agents.projection import run_projection
    from mkb.db.models import KnowledgeFrame

    init_db()
    sid = uuid.UUID(str(space_id))
    source_kind = (source_type or "frame").strip().lower()

    if frame_id:
        fid = uuid.UUID(str(frame_id))
        return run_projection(
            sid, fid, model=model, verbose=verbose,
            progress_callback=progress_callback, source_type=source_kind,
        )

    if project_id:
        pid = uuid.UUID(str(project_id))
        if source_kind == "markdown":
            # Frame may not exist yet; the agent runner will create one.
            return run_projection(
                sid, None, model=model, verbose=verbose,
                progress_callback=progress_callback,
                source_type=source_kind, project_id=pid,
            )
        with SyncSessionLocal() as session:
            frame = session.query(KnowledgeFrame).filter_by(project_id=pid).first()
            if not frame:
                return {"error": f"No frame for project {project_id}"}
            fid = frame.frame_id
        return run_projection(
            sid, fid, model=model, verbose=verbose,
            progress_callback=progress_callback, source_type=source_kind,
        )

    return {"error": "Must specify frame_id or project_id"}

def project_all(
    space_id: str | uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
    source_type: str = "frame",
) -> dict:
    """Run projection on all completed frames (or all projects) using a space."""
    from mkb.agents.projection import run_projection_all

    init_db()
    sid = uuid.UUID(str(space_id))
    return run_projection_all(sid, model=model, verbose=verbose, source_type=source_type)

def get_projection(projection_id: str | uuid.UUID) -> dict | None:
    """Get a projection by ID."""
    from mkb.db.models import KnowledgeFrame, Projection, Space
    from mkb.spaces.schema_utils import normalize_projection_data

    init_db()
    pid = uuid.UUID(str(projection_id))
    with SyncSessionLocal() as session:
        proj = session.query(Projection).filter_by(projection_id=pid).first()
        if not proj:
            return None
        frame = session.query(KnowledgeFrame).filter_by(frame_id=proj.frame_id).first()
        space = session.query(Space).filter_by(space_id=proj.space_id).first()
        normalized_data, normalized_validation = normalize_projection_data(
            proj.data or {},
            space.extraction_schema if space else {},
        )

        validation_result = proj.validation_result or {}
        if normalized_validation:
            validation_result = {
                **normalized_validation,
                **validation_result,
            }

        return {
            "projection_id": str(proj.projection_id),
            "space_id": str(proj.space_id),
            "frame_id": str(proj.frame_id),
            "project_id": str(frame.project_id) if frame else None,
            "status": proj.status.value,
            "data": normalized_data,
            "validation_result": validation_result or None,
            "agent_notes": proj.agent_notes,
            "extracted_at": proj.extracted_at.isoformat() if proj.extracted_at else None,
            "space_version": proj.space_version,
            "source_type": getattr(proj, "source_type", "frame"),
            "times_reviewed": proj.times_reviewed,
            "review_notes": proj.review_notes,
            "reviewed_at": proj.reviewed_at.isoformat() if proj.reviewed_at else None,
            "created_at": proj.created_at.isoformat() if proj.created_at else None,
        }

def delete_projection(projection_id: str | uuid.UUID) -> bool:
    """Soft-delete a projection by setting deleted_at.  Returns True if found."""
    from datetime import datetime, timezone

    from mkb.db.models import Projection

    init_db()
    pid = uuid.UUID(str(projection_id))
    with SyncSessionLocal() as session:
        proj = session.query(Projection).filter_by(projection_id=pid).first()
        if not proj:
            return False
        proj.deleted_at = datetime.now(timezone.utc)
        session.commit()
        return True

def list_projections(
    space_id: str | uuid.UUID | None = None,
    frame_id: str | uuid.UUID | None = None,
    project_id: str | uuid.UUID | None = None,
    include_data: bool = False,
    newest_only: bool = False,
    include_history: bool = False,
) -> list[dict]:
    """List projections, optionally filtered by space, frame, or project.

    Args:
        include_history: When False (default), superseded projections (those
            replaced by a tracked review) are hidden. When True, the full
            history is returned, including ``superseded_by_id`` /
            ``supersedes_ids`` pointers so callers can rebuild the chain.
    """
    from mkb.db.models import KnowledgeFrame, Projection, Space
    from mkb.spaces.schema_utils import normalize_projection_data

    init_db()
    with SyncSessionLocal() as session:
        q = (
            session.query(Projection, KnowledgeFrame.project_id)
            .outerjoin(KnowledgeFrame, Projection.frame_id == KnowledgeFrame.frame_id)
            .filter(Projection.deleted_at.is_(None))
            .order_by(Projection.created_at.desc(), Projection.extracted_at.desc())
        )
        if not include_history:
            q = q.filter(Projection.superseded_by_id.is_(None))
        if space_id:
            q = q.filter(Projection.space_id == uuid.UUID(str(space_id)))
        if frame_id:
            q = q.filter(Projection.frame_id == uuid.UUID(str(frame_id)))
        if project_id:
            q = q.filter(KnowledgeFrame.project_id == uuid.UUID(str(project_id)))
        projections = q.all()

        results = []
        seen_keys: set[tuple[str, str]] = set()
        for projection, projection_project_id in projections:
            project_value = str(projection_project_id) if projection_project_id else None
            dedupe_key = (str(projection.space_id), project_value or str(projection.frame_id))
            if newest_only and dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)

            item = {
                "projection_id": str(projection.projection_id),
                "space_id": str(projection.space_id),
                "frame_id": str(projection.frame_id),
                "project_id": project_value,
                "status": projection.status.value,
                "agent_notes": projection.agent_notes,
                "extracted_at": projection.extracted_at.isoformat() if projection.extracted_at else None,
                "created_at": projection.created_at.isoformat() if projection.created_at else None,
                "space_version": projection.space_version,
                "source_type": getattr(projection, "source_type", "frame"),
                "times_reviewed": projection.times_reviewed,
                "review_notes": projection.review_notes,
                "reviewed_at": projection.reviewed_at.isoformat() if projection.reviewed_at else None,
                "superseded_by_id": (
                    str(projection.superseded_by_id)
                    if getattr(projection, "superseded_by_id", None)
                    else None
                ),
                "supersedes_ids": getattr(projection, "supersedes_ids", None),
            }
            if include_data:
                space = session.query(Space).filter_by(space_id=projection.space_id).first()
                normalized_data, _ = normalize_projection_data(
                    projection.data or {},
                    space.extraction_schema if space else {},
                )
                item["data"] = normalized_data
            results.append(item)

        return results


# ── Projection Exports ──────────────────────────────────────────

def _serialize_projection_payload(
    projection_id: uuid.UUID,
    out_path: Path,
    format: str,
) -> Path:
    """Generic single-projection dump (used for non-qa_benchmark spaces)."""
    return serialize_projection_payload(projection_id, out_path, format)

def export_projection(
    projection_id: str | uuid.UUID,
    out_dir: str | Path,
    format: str = "yaml",
    overwrite: bool = False,
) -> dict:
    """Export a single projection to disk.

    For ``qa_benchmark`` spaces, delegates to the mat_agent_bench exporter
    (one YAML per question under ``<out_dir>/<capability>/<id>.yaml``).
    For all other purposes, writes a single ``<projection_id>.<ext>`` file
    containing the projection payload + metadata.
    """
    from mkb.db.models import Projection, Space
    from mkb.spaces.export_qa_bench import (
        QABenchExportError,
        export_projection_to_yaml,
    )

    init_db()
    pid = uuid.UUID(str(projection_id))
    out_root = Path(out_dir)
    fmt = (format or "yaml").strip().lower()
    if fmt not in {"yaml", "json"}:
        raise ValueError(f"Unsupported export format: {format}")

    with SyncSessionLocal() as session:
        proj = session.query(Projection).filter_by(projection_id=pid).first()
        if not proj:
            return {"error": f"Projection {projection_id} not found"}
        space = session.query(Space).filter_by(space_id=proj.space_id).first()
        purpose = getattr(space, "purpose", None) if space else None

    if purpose == "qa_benchmark" and fmt == "yaml":
        try:
            return export_projection_to_yaml(pid, out_root, overwrite=overwrite)
        except QABenchExportError as e:
            return {"error": str(e)}

    out_path = out_root / f"{pid}.{fmt}"
    if out_path.exists() and not overwrite:
        return {"error": f"{out_path} already exists (pass overwrite=True)"}
    _serialize_projection_payload(pid, out_path, fmt)
    return {"files": [str(out_path)], "skipped": [], "warnings": []}

def export_space_projections(
    space_id_or_name: str | uuid.UUID,
    out_dir: str | Path,
    format: str = "yaml",
    overwrite: bool = False,
    newest_only: bool = True,
) -> dict:
    """Export every projection belonging to a space.

    For ``qa_benchmark`` spaces and ``format='yaml'`` this delegates to the
    aggregated mat_agent_bench exporter. Otherwise one file is written per
    projection: ``<out_dir>/<projection_id>.<ext>``.
    """
    from mkb.db.models import Projection, ProjectionStatus, Space
    from mkb.spaces.export_qa_bench import (
        QABenchExportError,
        export_space_to_yaml,
    )

    init_db()
    out_root = Path(out_dir)
    fmt = (format or "yaml").strip().lower()
    if fmt not in {"yaml", "json"}:
        raise ValueError(f"Unsupported export format: {format}")

    with SyncSessionLocal() as session:
        try:
            sid = uuid.UUID(str(space_id_or_name))
            space = session.query(Space).filter_by(space_id=sid).first()
        except (ValueError, AttributeError):
            space = session.query(Space).filter_by(name=str(space_id_or_name)).first()
        if not space:
            return {"error": f"Space {space_id_or_name} not found"}
        purpose = getattr(space, "purpose", None)
        space_id = space.space_id

    if purpose == "qa_benchmark" and fmt == "yaml":
        try:
            return export_space_to_yaml(space_id, out_root, overwrite=overwrite)
        except QABenchExportError as e:
            return {"error": str(e)}

    # Generic per-projection dump
    with SyncSessionLocal() as session:
        q = (
            session.query(Projection)
            .filter(Projection.space_id == space_id)
            .filter(Projection.deleted_at.is_(None))
            .filter(Projection.status == ProjectionStatus.COMPLETED)
            .order_by(Projection.created_at.desc())
        )
        projections = q.all()
        if newest_only:
            seen: set[str] = set()
            unique = []
            for p in projections:
                key = str(p.frame_id)
                if key in seen:
                    continue
                seen.add(key)
                unique.append(p)
            projections = unique
        ids = [p.projection_id for p in projections]

    written: list[str] = []
    skipped: list[dict] = []
    out_root.mkdir(parents=True, exist_ok=True)
    for pid in ids:
        out_path = out_root / f"{pid}.{fmt}"
        if out_path.exists() and not overwrite:
            skipped.append({"id": str(pid), "reason": "exists"})
            continue
        _serialize_projection_payload(pid, out_path, fmt)
        written.append(str(out_path))

    return {"files": written, "skipped": skipped, "warnings": []}


# ── Knowledge Graphs ────────────────────────────────────────────

