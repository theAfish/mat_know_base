"""
Projection tools for the projection agent.

These tools let the projection agent read knowledge frame content,
save projection results, interact live with the extraction agent
for clarification, and flag fundamental pipeline issues as feedback.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from mkb.agents.tools._ids import invalid_identifier_message, parse_uuidish
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import (
    Feedback,
    FeedbackStatus,
    KnowledgeFrame,
    Projection,
    ProjectionStatus,
    Space,
)
from mkb.spaces.schema_utils import normalize_projection_data

logger = logging.getLogger(__name__)

_CORE_STUDY_TRUE_MARKERS = {
    "core",
    "focus",
    "investigated",
    "lead",
    "main",
    "primary",
    "study",
    "target",
}
_CORE_STUDY_FALSE_MARKERS = {
    "background",
    "benchmark",
    "comparison",
    "complementary",
    "control",
    "reference",
    "supplementary",
    "supporting",
    "test",
    "testing",
    "validation",
}


def _coerce_core_study_flag(value) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "core", "primary", "main"}:
            return True
        if lowered in {"false", "no", "0", "control", "comparison", "supplementary"}:
            return False
    return None


def _infer_core_study_data(mapping: dict) -> bool | None:
    explicit_value = _coerce_core_study_flag(mapping.get("is_core_study_data"))
    if explicit_value is not None:
        return explicit_value

    for key in ("experimental_role", "role", "data_role", "study_role", "item_role"):
        value = mapping.get(key)
        if value in (None, ""):
            continue
        normalized = str(value).lower().replace("_", " ").replace("-", " ")
        if any(marker in normalized for marker in _CORE_STUDY_FALSE_MARKERS):
            return False
        if any(marker in normalized for marker in _CORE_STUDY_TRUE_MARKERS):
            return True

    return None


def _inject_source_project_references(data, source_project_id: str, *, _from_list: bool = False):
    """Recursively attach source-project metadata to extracted records."""
    if isinstance(data, list):
        return [
            _inject_source_project_references(item, source_project_id, _from_list=True)
            for item in data
        ]

    if isinstance(data, dict):
        enriched = {
            key: _inject_source_project_references(value, source_project_id)
            for key, value in data.items()
        }

        lower_keys = {str(key).lower() for key in enriched}
        scalar_fields = [
            key for key, value in enriched.items()
            if not isinstance(value, (dict, list))
        ]
        if scalar_fields and "source_project_id" not in lower_keys:
            enriched["source_project_id"] = source_project_id

        if _from_list and scalar_fields:
            inferred_flag = _infer_core_study_data(enriched)
            enriched["is_core_study_data"] = True if inferred_flag is None else inferred_flag

        return enriched

    return data


def get_frame_content(frame_id: str) -> dict:
    """Read the knowledge frame content for projection.

    Returns the full frame content dict, or an error if not found.
    """
    fid = parse_uuidish(frame_id)
    if not fid:
        return {"error": invalid_identifier_message("frame_id", frame_id)}

    with SyncSessionLocal() as session:
        frame = session.query(KnowledgeFrame).filter_by(frame_id=fid).first()
        if not frame:
            return {"error": f"Frame {frame_id} not found."}
        return {
            "frame_id": str(frame.frame_id),
            "project_id": str(frame.project_id),
            "status": frame.status.value,
            "content": frame.content or {},
            "extraction_summary": frame.extraction_summary,
            "agent_annotations": frame.agent_annotations or {},
        }


def save_projection(
    projection_id: str,
    data: dict,
    validation_notes: str = "",
    agent_notes: str = "",
) -> dict:
    """Save extracted projection data.

    Args:
        projection_id: The projection record to update.
        data: The structured data extracted per the space schema.
        validation_notes: Notes about data validation.
        agent_notes: Agent's confidence assessment and observations.

    Returns:
        Dict with projection_id and status.
    """
    pid = parse_uuidish(projection_id)
    if not pid:
        return {"error": invalid_identifier_message("projection_id", projection_id)}

    now = datetime.now(timezone.utc)

    with SyncSessionLocal() as session:
        projection = session.query(Projection).filter_by(projection_id=pid).first()
        if not projection:
            return {"error": f"Projection {projection_id} not found."}

        frame = session.query(KnowledgeFrame).filter_by(frame_id=projection.frame_id).first()
        space = session.query(Space).filter_by(space_id=projection.space_id).first()

        normalized_data, validation_result = normalize_projection_data(
            data,
            space.extraction_schema if space else {},
        )

        source_project_id = str(frame.project_id) if frame else None
        if source_project_id:
            normalized_data = _inject_source_project_references(normalized_data, source_project_id)

        if validation_notes:
            validation_result = {
                **validation_result,
                "notes": validation_notes,
            }

        projection.data = normalized_data
        projection.validation_result = validation_result or None
        projection.agent_notes = agent_notes
        projection.status = ProjectionStatus.COMPLETED
        projection.extracted_at = now
        session.commit()

        return {"projection_id": str(projection.projection_id), "status": "completed"}


def request_frame_clarification(
    projection_id: str,
    question: str,
    context: str = "",
    field: str = "",
) -> dict:
    """Ask the extraction agent to clarify or update the knowledge frame in real time.

    Use this when the knowledge frame is missing data, contains an ambiguous
    entry, or needs more detail on a specific aspect so that the projection
    can proceed accurately.  This directly invokes the KB extraction agent:
    it reads the source files and applies targeted updates to the frame before
    returning control to the projection agent.

    Do NOT use this for fundamental pipeline/architecture issues — use
    ``flag_for_feedback`` for those instead.

    Args:
        projection_id: The current projection's ID (used to locate the frame).
        question: Specific question or clarification request for the extraction agent.
        context: Relevant excerpt from the knowledge frame that is unclear (optional).
        field: The schema field or section path where clarification is needed (optional).

    Returns:
        Dict with keys:
          - ``updated`` (bool): whether the frame was modified.
          - ``clarification_summary`` (str): extraction agent's explanation.
          - ``updated_frame_content`` (dict | None): the current frame content
            after the clarification run (read it back via ``get_frame_content``
            to continue projection with the latest data).
    """
    pid = parse_uuidish(projection_id)
    if not pid:
        return {"error": invalid_identifier_message("projection_id", projection_id)}

    with SyncSessionLocal() as session:
        projection = session.query(Projection).filter_by(projection_id=pid).first()
        if not projection:
            return {"error": f"Projection {projection_id} not found."}

        frame = session.query(KnowledgeFrame).filter_by(frame_id=projection.frame_id).first()
        if not frame:
            return {"error": "Associated frame not found."}

        frame_id = frame.frame_id
        project_id = frame.project_id

    # Import here to avoid circular imports at module load time.
    from mkb.agents.clarification import run_clarification_in_thread

    logger.info(
        "Projection %s requesting clarification from extraction agent: %s",
        projection_id,
        question[:120],
    )

    result = run_clarification_in_thread(
        project_id=project_id,
        frame_id=frame_id,
        question=question,
        context=context,
        field=field,
    )

    # Record the clarification in the frame's agent_annotations so future
    # projection/extraction runs skip re-asking the same question.
    now = datetime.now(timezone.utc)
    annotation = {
        "question": question,
        "field": field or None,
        "summary": result.get("clarification_summary") or "",
        "frame_updated": result.get("updated", False),
        "resolved_at": now.isoformat(),
    }
    with SyncSessionLocal() as session:
        frame = session.query(KnowledgeFrame).filter_by(frame_id=frame_id).first()
        if frame:
            annotations = dict(frame.agent_annotations or {})
            clarifications = list(annotations.get("clarifications", []))
            clarifications.append(annotation)
            annotations["clarifications"] = clarifications
            frame.agent_annotations = annotations
            session.commit()

    return result


def flag_for_feedback(
    projection_id: str,
    field: str,
    issue: str,
    question: str,
    context: str = "",
) -> dict:
    """Record a fundamental pipeline or architectural issue encountered during projection.

    Use this **only** for issues that reflect problems with the system design,
    the extraction pipeline, or recurring structural deficiencies in how
    knowledge frames are built — not for ordinary missing or ambiguous data.
    For the latter, call ``request_frame_clarification`` instead so the
    extraction agent can resolve it immediately.

    Typical cases for feedback:
    - A schema field category is systematically absent from all frames
      (possible domain gap in the extraction prompt).
    - Evidence-level assignment is consistently wrong across multiple frames
      (likely a prompt or guideline issue).
    - A whole class of experimental data is never extracted (extraction
      architecture gap).

    Args:
        projection_id: The projection encountering the issue.
        field: The field path where the issue was found (e.g., "catalysts[2].selectivity").
        issue: Category (missing_data, ambiguous_data, inconsistency, wrong_evidence_level, other).
        question: Description of the architectural or pipeline concern.
        context: Relevant excerpt from the knowledge frame.

    Returns:
        Dict with feedback_id.
    """
    pid = parse_uuidish(projection_id)
    if not pid:
        return {"error": invalid_identifier_message("projection_id", projection_id)}

    with SyncSessionLocal() as session:
        projection = session.query(Projection).filter_by(projection_id=pid).first()
        if not projection:
            return {"error": f"Projection {projection_id} not found."}

        # Get the frame to find project_id
        frame = session.query(KnowledgeFrame).filter_by(frame_id=projection.frame_id).first()
        if not frame:
            return {"error": "Associated frame not found."}

        feedback = Feedback(
            feedback_id=uuid.uuid4(),
            source_projection_id=pid,
            source_agent="projection_agent",
            target_frame_id=frame.frame_id,
            target_project_id=frame.project_id,
            category=issue,
            field_path=field,
            question=question,
            context=context,
            status=FeedbackStatus.OPEN,
        )
        session.add(feedback)

        # Mark projection as needing feedback if not already completed
        if projection.status != ProjectionStatus.COMPLETED:
            projection.status = ProjectionStatus.NEEDS_FEEDBACK

        session.commit()

        return {"feedback_id": str(feedback.feedback_id), "status": "created"}


PROJECTION_TOOLS = [
    get_frame_content,
    save_projection,
    request_frame_clarification,
    flag_for_feedback,
]
