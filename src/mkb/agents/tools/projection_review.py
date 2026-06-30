"""
Projection review tools for the projection reviewer agent.

These tools let the reviewer agent read all projections for a project,
access knowledge frames, save the winning projection (updating it in-place
and soft-deleting the rest), and delegate re-extraction to a fixer sub-agent.
"""

from __future__ import annotations

import logging
import uuid
from copy import deepcopy
from datetime import datetime, timezone

from mkb.agents.tools._ids import invalid_identifier_message, parse_uuidish
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import (
    KnowledgeFrame,
    Projection,
    ProjectionStatus,
    Space,
)
from mkb.spaces.schema_utils import normalize_projection_data

logger = logging.getLogger(__name__)


def _is_empty_value(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _summarize_data_changes(before, after, path: str = "") -> dict:
    changed_paths: list[str] = []
    sequence_filled_paths: list[str] = []
    sequence_changed_paths: list[str] = []

    def walk(old, new, current_path: str) -> None:
        if old == new:
            return
        key_name = current_path.split(".")[-1].lower()
        if not isinstance(old, (dict, list)) and not isinstance(new, (dict, list)):
            changed_paths.append(current_path or "$")
            if "sequence" in key_name:
                sequence_changed_paths.append(current_path or "$")
                if _is_empty_value(old) and not _is_empty_value(new):
                    sequence_filled_paths.append(current_path or "$")
            return
        if isinstance(old, dict) and isinstance(new, dict):
            for key in sorted(set(old) | set(new)):
                next_path = f"{current_path}.{key}" if current_path else str(key)
                walk(old.get(key), new.get(key), next_path)
            return
        if isinstance(old, list) and isinstance(new, list):
            for idx in range(max(len(old), len(new))):
                old_item = old[idx] if idx < len(old) else None
                new_item = new[idx] if idx < len(new) else None
                next_path = f"{current_path}[{idx}]" if current_path else f"[{idx}]"
                walk(old_item, new_item, next_path)
            return
        changed_paths.append(current_path or "$")
        if "sequence" in key_name:
            sequence_changed_paths.append(current_path or "$")
            if _is_empty_value(old) and not _is_empty_value(new):
                sequence_filled_paths.append(current_path or "$")

    walk(before, after, path)
    return {
        "changed_count": len(changed_paths),
        "data_changed": bool(changed_paths),
        "changed_paths": changed_paths[:40],
        "truncated": len(changed_paths) > 40,
        "sequence_changed_count": len(sequence_changed_paths),
        "sequence_filled": bool(sequence_filled_paths),
        "sequence_filled_count": len(sequence_filled_paths),
        "sequence_changed_paths": sequence_changed_paths[:20],
        "sequence_filled_paths": sequence_filled_paths[:20],
    }


def _parse_patch_path(path: str) -> list[str | int]:
    parts: list[str | int] = []
    token = ""
    i = 0
    while i < len(path):
        char = path[i]
        if char == ".":
            if token:
                parts.append(token)
                token = ""
            i += 1
            continue
        if char == "[":
            if token:
                parts.append(token)
                token = ""
            close = path.find("]", i)
            if close < 0:
                raise ValueError(f"Invalid path {path!r}: missing closing bracket")
            index_text = path[i + 1:close].strip()
            if not index_text.isdigit():
                raise ValueError(f"Invalid path {path!r}: list index must be a non-negative integer")
            parts.append(int(index_text))
            i = close + 1
            continue
        token += char
        i += 1
    if token:
        parts.append(token)
    if not parts:
        raise ValueError("Patch path cannot be empty")
    return parts


def _set_patch_value(data, path: str, value) -> None:
    parts = _parse_patch_path(path)
    current = data
    for part in parts[:-1]:
        if isinstance(part, int):
            if not isinstance(current, list):
                raise ValueError(f"Path {path!r} expected a list before index {part}")
            if part >= len(current):
                raise ValueError(f"Path {path!r} index {part} is out of range")
            current = current[part]
            continue
        if not isinstance(current, dict):
            raise ValueError(f"Path {path!r} expected an object before key {part!r}")
        if part not in current or current[part] is None:
            current[part] = {}
        current = current[part]

    last = parts[-1]
    if isinstance(last, int):
        if not isinstance(current, list):
            raise ValueError(f"Path {path!r} expected a list before index {last}")
        if last >= len(current):
            raise ValueError(f"Path {path!r} index {last} is out of range")
        current[last] = value
        return
    if not isinstance(current, dict):
        raise ValueError(f"Path {path!r} expected an object before key {last!r}")
    current[last] = value


def _apply_projection_review_save(
    session,
    winner: Projection,
    data: dict,
    review_notes: str,
    now: datetime,
) -> dict:
    space = session.query(Space).filter_by(space_id=winner.space_id).first()
    if not space:
        return {"error": f"Space {winner.space_id} not found."}

    # Normalize the data against the space schema
    before_data = winner.data or {}
    normalized_data, validation_result = normalize_projection_data(
        data,
        space.extraction_schema,
    )

    # Inject source_project_id references
    frame = session.query(KnowledgeFrame).filter_by(frame_id=winner.frame_id).first()
    if frame:
        from mkb.agents.tools.projection import _inject_source_project_references
        normalized_data = _inject_source_project_references(
            normalized_data, str(frame.project_id)
        )

    change_summary = _summarize_data_changes(before_data, normalized_data)

    trackable = bool(getattr(space, "review_trackable", True))

    # All other live, non-superseded projections for the same space+frame
    siblings = (
        session.query(Projection)
        .filter_by(space_id=winner.space_id, frame_id=winner.frame_id)
        .filter(Projection.projection_id != winner.projection_id)
        .filter(Projection.deleted_at.is_(None))
        .filter(Projection.superseded_by_id.is_(None))
        .all()
    )

    if not trackable:
        # ── Legacy: in-place update + soft-delete losers ──
        winner.data = normalized_data
        winner.validation_result = validation_result or None
        winner.review_notes = review_notes
        winner.status = ProjectionStatus.REVIEWED
        winner.times_reviewed = winner.times_reviewed + 1
        winner.reviewed_at = now

        for other in siblings:
            other.deleted_at = now

        session.commit()
        return {
            "projection_id": str(winner.projection_id),
            "status": "reviewed",
            "trackable": False,
            "times_reviewed": winner.times_reviewed,
            "soft_deleted_count": len(siblings),
            "change_summary": change_summary,
        }

    # ── Trackable: create a new REVIEWED projection that supersedes
    #    the winner + every other live sibling. Older rows stay
    #    visible via ``include_history``.
    supersedes = [str(winner.projection_id)] + [str(s.projection_id) for s in siblings]
    reviewed = Projection(
        projection_id=uuid.uuid4(),
        space_id=winner.space_id,
        frame_id=winner.frame_id,
        source_type=winner.source_type,
        status=ProjectionStatus.REVIEWED,
        data=normalized_data,
        validation_result=validation_result or None,
        agent_notes=None,
        extracted_at=winner.extracted_at,
        space_version=winner.space_version,
        times_reviewed=(winner.times_reviewed or 0) + 1,
        review_notes=review_notes,
        reviewed_at=now,
        supersedes_ids=supersedes,
    )
    session.add(reviewed)
    # ``session.flush()`` so reviewed.projection_id is available for
    # the supersession pointers (Postgres assigns from python default).
    session.flush()

    winner.superseded_by_id = reviewed.projection_id
    for s in siblings:
        s.superseded_by_id = reviewed.projection_id

    session.commit()
    return {
        "projection_id": str(reviewed.projection_id),
        "status": "reviewed",
        "trackable": True,
        "times_reviewed": reviewed.times_reviewed,
        "supersedes_count": len(supersedes),
        "change_summary": change_summary,
    }


def get_all_projections_for_review(space_id: str, project_id: str) -> dict:
    """Get all projection data for a space+project combination.

    Returns all non-deleted projection runs (grouped by timestamp) so the
    reviewer can compare, pick the best, and merge corrections.

    Args:
        space_id: The space to filter projections by.
        project_id: The project to filter projections by.

    Returns:
        Dict with space info, frame info, and list of all projections with data.
    """
    sid = parse_uuidish(space_id)
    if not sid:
        return {"error": invalid_identifier_message("space_id", space_id)}
    pid = parse_uuidish(project_id)
    if not pid:
        return {"error": invalid_identifier_message("project_id", project_id)}

    with SyncSessionLocal() as session:
        space = session.query(Space).filter_by(space_id=sid).first()
        if not space:
            return {"error": f"Space {space_id} not found."}

        frame = session.query(KnowledgeFrame).filter_by(project_id=pid).first()
        if not frame:
            return {"error": f"No knowledge frame found for project {project_id}."}

        projections = (
            session.query(Projection)
            .filter_by(space_id=sid, frame_id=frame.frame_id)
            .filter(Projection.deleted_at.is_(None))
            .filter(Projection.superseded_by_id.is_(None))
            .filter(
                Projection.status.in_(
                    [ProjectionStatus.COMPLETED, ProjectionStatus.REVIEWED]
                )
            )
            .order_by(Projection.created_at.desc())
            .all()
        )

        if not projections:
            return {
                "error": f"No projections found for space {space_id} and project {project_id}.",
            }

        current_space_version = space.version
        projection_list = []
        for proj in projections:
            normalized_data, validation = normalize_projection_data(
                proj.data or {},
                space.extraction_schema,
            )
            schema_outdated = proj.space_version < current_space_version
            projection_list.append({
                "projection_id": str(proj.projection_id),
                "status": proj.status.value,
                "data": normalized_data,
                "validation_result": validation,
                "agent_notes": proj.agent_notes,
                "space_version": proj.space_version,
                "schema_outdated": schema_outdated,
                "times_reviewed": proj.times_reviewed,
                "extracted_at": proj.extracted_at.isoformat() if proj.extracted_at else None,
                "created_at": proj.created_at.isoformat() if proj.created_at else None,
            })

        any_schema_outdated = any(p["schema_outdated"] for p in projection_list)
        return {
            "space_id": str(sid),
            "space_name": space.name,
            "project_id": str(pid),
            "frame_id": str(frame.frame_id),
            "current_space_version": current_space_version,
            "any_schema_outdated": any_schema_outdated,
            "extraction_schema": space.extraction_schema,
            "total_projections": len(projection_list),
            "projections": projection_list,
        }


def get_frame_for_review(project_id: str) -> dict:
    """Get the knowledge frame content for cross-referencing during review.

    Args:
        project_id: The project whose frame to retrieve.

    Returns:
        Dict with frame content and metadata.
    """
    pid = parse_uuidish(project_id)
    if not pid:
        return {"error": invalid_identifier_message("project_id", project_id)}

    with SyncSessionLocal() as session:
        frame = session.query(KnowledgeFrame).filter_by(project_id=pid).first()
        if not frame:
            return {"error": f"No knowledge frame found for project {project_id}."}
        return {
            "frame_id": str(frame.frame_id),
            "project_id": str(frame.project_id),
            "status": frame.status.value,
            "content": frame.content or {},
            "extraction_summary": frame.extraction_summary,
            "extraction_version": frame.extraction_version,
        }


def save_reviewed_projection(
    winning_projection_id: str,
    data: dict,
    review_notes: str = "",
) -> dict:
    """Save the reviewed projection.

    Behaviour depends on the owning space's ``review_trackable`` flag:

    * **trackable=True (default)** — preserves history. The winner and all
      other live projections for the same ``(space_id, frame_id)`` are
      marked with ``superseded_by_id`` pointing to a NEW ``REVIEWED``
      projection row that carries the corrected, consolidated data. The
      new row records the ids it consolidated in ``supersedes_ids``.
      Earlier projections remain queryable (e.g. ``include_history=true``).

    * **trackable=False** — legacy behaviour. The winner is updated
      in-place (status → REVIEWED), and every other live projection for
      the same ``(space_id, frame_id)`` is soft-deleted via ``deleted_at``.
      No history is kept.

    Args:
        winning_projection_id: The projection ID chosen as the winner.
        data: The corrected, consolidated projection data.
        review_notes: Reviewer's summary of corrections and decisions made.
    """
    wid = parse_uuidish(winning_projection_id)
    if not wid:
        return {"error": invalid_identifier_message("winning_projection_id", winning_projection_id)}

    now = datetime.now(timezone.utc)

    with SyncSessionLocal() as session:
        winner = session.query(Projection).filter_by(projection_id=wid).first()
        if not winner:
            return {"error": f"Projection {winning_projection_id} not found."}

        return _apply_projection_review_save(session, winner, data, review_notes, now)


def save_reviewed_projection_patch(
    winning_projection_id: str,
    updates: list[dict],
    review_notes: str = "",
) -> dict:
    """Save a reviewed projection by applying only the changed fields.

    Use this instead of ``save_reviewed_projection`` when the winner is
    mostly correct and only a few values need correction or filling. The
    tool loads the winner's current data, applies each update, then performs
    the same schema normalization, validation, review history handling, and
    loser cleanup as the full-save tool.

    If you verified an external value for an empty schema field (for example
    by search or database lookup), use this tool to save that value. Merely
    mentioning the verified value in review notes does not update the
    projection data.

    ``updates`` is a list of objects:

    * ``path``: field path using dot/bracket notation, for example
      ``templates[3].sequence`` or ``summary.references[0].doi``.
    * ``value``: replacement value for that field.

    Existing list indexes must already exist. This tool is for small field
    edits, not inserting/removing/reordering array items. For major merges,
    duplicate removal, or array reshaping, use ``save_reviewed_projection``.
    """
    wid = parse_uuidish(winning_projection_id)
    if not wid:
        return {"error": invalid_identifier_message("winning_projection_id", winning_projection_id)}
    if not isinstance(updates, list) or not updates:
        return {"error": "updates must be a non-empty list of {path, value} objects."}

    now = datetime.now(timezone.utc)

    with SyncSessionLocal() as session:
        winner = session.query(Projection).filter_by(projection_id=wid).first()
        if not winner:
            return {"error": f"Projection {winning_projection_id} not found."}

        patched_data = deepcopy(winner.data or {})
        applied_paths: list[str] = []
        for update in updates:
            if not isinstance(update, dict):
                return {"error": "Each update must be an object with path and value."}
            path = update.get("path")
            if not isinstance(path, str) or not path.strip():
                return {"error": "Each update must include a non-empty string path."}
            try:
                _set_patch_value(patched_data, path.strip(), update.get("value"))
            except ValueError as exc:
                return {"error": str(exc), "path": path}
            applied_paths.append(path.strip())

        result = _apply_projection_review_save(
            session,
            winner,
            patched_data,
            review_notes,
            now,
        )
        if "error" not in result:
            result["save_mode"] = "patch"
            result["applied_update_count"] = len(applied_paths)
            result["applied_update_paths"] = applied_paths[:40]
            result["applied_update_paths_truncated"] = len(applied_paths) > 40
        return result


def request_re_extraction(
    project_id: str,
    fields: str,
    context: str = "",
) -> dict:
    """Request the fixer sub-agent to re-examine specific fields against source data.

    Delegates to a projection fixer agent that reads the raw processed files
    and knowledge frame to verify and correct specific data points.

    Args:
        project_id: The project to re-examine.
        fields: Description of which fields to re-check and what seems wrong.
            Example: "catalysts[0].surface_area_m2_g is 500 but Table 2 shows 250"
        context: Additional context about what the reviewer found problematic.

    Returns:
        Dict with the fixer agent's corrections and findings.
    """
    pid = parse_uuidish(project_id)
    if not pid:
        return {"error": invalid_identifier_message("project_id", project_id)}

    try:
        from mkb.agents.projection_fixer import run_fixer
        result = run_fixer(
            project_id=pid,
            fields=fields,
            context=context,
        )
        return result
    except Exception as exc:
        logger.error("Fixer sub-agent failed: %s", exc)
        return {
            "error": f"Re-extraction failed: {exc}",
            "fields": fields,
        }


PROJECTION_REVIEW_TOOLS = [
    get_all_projections_for_review,
    get_frame_for_review,
    save_reviewed_projection,
    save_reviewed_projection_patch,
    request_re_extraction,
]
