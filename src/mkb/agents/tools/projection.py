"""
Projection tools for the projection agent.

These tools let the projection agent read knowledge frame content,
save projection results, interact live with the extraction agent
for clarification, and flag fundamental pipeline issues as feedback.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

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

_TRACE_DIR = Path(
    os.getenv("MKB_AGENT_TRACE_DIR", str(Path(tempfile.gettempdir()) / "mkb_agent_logs"))
)

DEFAULT_FRAME_CONTENT_MAX_CHARS = 30000
DEFAULT_FRAME_CONTENT_MAX_LIST_ITEMS = 80
DEFAULT_FRAME_CONTENT_MAX_DICT_ITEMS = 120
DEFAULT_FRAME_CONTENT_MAX_STRING_CHARS = 1200

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


def _extract_json_from_text(value: str):
    """Best-effort parse for JSON payloads (raw or fenced)."""
    text = (value or "").strip()
    if not text:
        return None

    try:
        return json.loads(text)
    except Exception:
        pass

    # LLMs sometimes embed literal control characters (newlines, tabs) inside
    # JSON string values, which strict mode rejects.  Try again permissively.
    try:
        return json.loads(text, strict=False)
    except Exception:
        pass

    if "```" in text:
        start = text.find("```")
        end = text.rfind("```")
        if start != -1 and end != -1 and end > start:
            block = text[start + 3:end].strip()
            if block.startswith("json"):
                block = block[4:].strip()
            try:
                return json.loads(block)
            except Exception:
                return None
    return None


def _safe_json_preview(value, max_chars: int = 1200) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def write_projection_trace(
    *,
    event: str,
    projection_id: str | None = None,
    frame_id: str | None = None,
    details: dict | None = None,
) -> None:
    """Append projection-agent trace events to local JSONL in /tmp.

    One file per projection (`projection_<id>.jsonl`). If projection id is not
    available, events are grouped under frame (`frame_<id>.jsonl`).
    """
    try:
        _TRACE_DIR.mkdir(parents=True, exist_ok=True)
        if projection_id:
            fname = f"projection_{projection_id}.jsonl"
        elif frame_id:
            fname = f"frame_{frame_id}.jsonl"
        else:
            fname = "projection_unknown.jsonl"

        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "projection_id": projection_id,
            "frame_id": frame_id,
            "details": details or {},
        }
        with (_TRACE_DIR / fname).open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        logger.debug("projection trace write failed: %s", exc)


def _single_list_key_from_schema(extraction_schema: dict | None) -> str | None:
    if not isinstance(extraction_schema, dict) or not extraction_schema:
        return None

    list_keys = [
        key
        for key, node in extraction_schema.items()
        if isinstance(node, dict) and str(node.get("type", "")).strip().lower() == "list"
    ]
    if len(list_keys) == 1:
        return str(list_keys[0])
    return None


def _coerce_projection_payload(data, extraction_schema: dict | None):
    """Coerce common malformed payload shapes into a mapping.

    Returns:
        tuple[payload_dict | None, warning | None]
    """
    if isinstance(data, dict):
        return data, None

    single_list_key = _single_list_key_from_schema(extraction_schema)

    if isinstance(data, str):
        parsed = _extract_json_from_text(data)
        if parsed is not None:
            coerced, warning = _coerce_projection_payload(parsed, extraction_schema)
            if coerced is not None:
                return coerced, warning or "Coerced string payload to JSON mapping."

    if isinstance(data, list) and single_list_key:
        return {single_list_key: data}, (
            f"Coerced list payload into mapping key '{single_list_key}'."
        )

    if isinstance(data, dict) and single_list_key and single_list_key not in data:
        schema_node = extraction_schema.get(single_list_key) if isinstance(extraction_schema, dict) else None
        item_schema = schema_node.get("item_schema") if isinstance(schema_node, dict) else None
        if isinstance(item_schema, dict) and set(data.keys()).issubset(set(item_schema.keys())):
            return {single_list_key: [data]}, (
                f"Coerced single-item mapping into list key '{single_list_key}'."
            )

    return None, "Projection payload was not a JSON object and could not be coerced."


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


def _trim_string(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0:
        return "", bool(text)
    if len(text) <= max_chars:
        return text, False
    if max_chars <= 3:
        return text[:max_chars], True
    return text[: max_chars - 3] + "...", True


def _compact_json_payload(
    value,
    *,
    max_chars: int,
    max_list_items: int,
    max_dict_items: int,
    max_string_chars: int,
):
    """Compact a JSON-like object while preserving broad structure."""

    truncated = False

    def _walk(node):
        nonlocal truncated
        if isinstance(node, str):
            text, did_trim = _trim_string(node, max_string_chars)
            truncated = truncated or did_trim
            return text
        if isinstance(node, list):
            if len(node) > max_list_items:
                truncated = True
            return [_walk(item) for item in node[:max_list_items]]
        if isinstance(node, dict):
            items = list(node.items())
            if len(items) > max_dict_items:
                truncated = True
            compacted = {}
            for key, val in items[:max_dict_items]:
                compacted[key] = _walk(val)
            return compacted
        return node

    compact = _walk(value)

    try:
        encoded = json.dumps(compact, ensure_ascii=True)
    except Exception:
        return compact, True

    if len(encoded) <= max_chars:
        return compact, truncated

    # Second-pass hard trim if serialized payload is still too large.
    truncated = True
    compact2 = _walk(value if isinstance(value, dict) else {"value": value})
    encoded2 = json.dumps(compact2, ensure_ascii=True)
    if len(encoded2) <= max_chars:
        return compact2 if isinstance(value, dict) else compact2.get("value"), truncated

    # Last-resort: return a compact textual preview in shape-preserving envelope.
    preview, _ = _trim_string(encoded2, max_chars)
    if isinstance(value, dict):
        return {"_preview": preview}, True
    return {"_preview": preview}, True


def get_frame_content(
    frame_id: str,
    max_chars: int = DEFAULT_FRAME_CONTENT_MAX_CHARS,
    max_list_items: int = DEFAULT_FRAME_CONTENT_MAX_LIST_ITEMS,
    max_dict_items: int = DEFAULT_FRAME_CONTENT_MAX_DICT_ITEMS,
    max_string_chars: int = DEFAULT_FRAME_CONTENT_MAX_STRING_CHARS,
) -> dict:
    """Read the knowledge frame content for projection.

    Returns a compacted frame content payload bounded for model-safe tool responses.
    """
    fid = parse_uuidish(frame_id)
    if not fid:
        return {"error": invalid_identifier_message("frame_id", frame_id)}

    with SyncSessionLocal() as session:
        frame = session.query(KnowledgeFrame).filter_by(frame_id=fid).first()
        if not frame:
            return {"error": f"Frame {frame_id} not found."}

        write_projection_trace(
            event="get_frame_content",
            frame_id=str(frame.frame_id),
            details={
                "project_id": str(frame.project_id),
                "status": frame.status.value,
                "limits": {
                    "max_chars": max_chars,
                    "max_list_items": max_list_items,
                    "max_dict_items": max_dict_items,
                    "max_string_chars": max_string_chars,
                },
            },
        )

        safe_max_chars = max(2000, min(int(max_chars), 120000))
        safe_list_items = max(10, min(int(max_list_items), 300))
        safe_dict_items = max(20, min(int(max_dict_items), 400))
        safe_string_chars = max(200, min(int(max_string_chars), 5000))

        compact_content, content_truncated = _compact_json_payload(
            frame.content or {},
            max_chars=safe_max_chars,
            max_list_items=safe_list_items,
            max_dict_items=safe_dict_items,
            max_string_chars=safe_string_chars,
        )
        compact_annotations, ann_truncated = _compact_json_payload(
            frame.agent_annotations or {},
            max_chars=max(1000, min(safe_max_chars // 3, 40000)),
            max_list_items=safe_list_items,
            max_dict_items=safe_dict_items,
            max_string_chars=safe_string_chars,
        )

        extraction_summary, summary_truncated = _trim_string(
            str(frame.extraction_summary or ""),
            max(500, min(safe_string_chars * 2, 10000)),
        )

        return {
            "frame_id": str(frame.frame_id),
            "project_id": str(frame.project_id),
            "status": frame.status.value,
            "content": compact_content,
            "extraction_summary": extraction_summary,
            "agent_annotations": compact_annotations,
            "content_truncated": bool(content_truncated or ann_truncated or summary_truncated),
            "content_limits": {
                "max_chars": safe_max_chars,
                "max_list_items": safe_list_items,
                "max_dict_items": safe_dict_items,
                "max_string_chars": safe_string_chars,
            },
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

        schema = space.extraction_schema if space else {}
        write_projection_trace(
            event="save_projection_called",
            projection_id=str(projection.projection_id),
            frame_id=str(projection.frame_id),
            details={
                "data_type": type(data).__name__,
                "data_preview": _safe_json_preview(data),
                "validation_notes_preview": (validation_notes or "")[:300],
                "agent_notes_preview": (agent_notes or "")[:300],
            },
        )

        coerced_data, coercion_warning = _coerce_projection_payload(data, schema)

        if coerced_data is None:
            projection.status = ProjectionStatus.FAILED
            projection.agent_notes = (
                f"save_projection rejected invalid payload type: {type(data).__name__}"
            )
            projection.validation_result = {
                "warnings": [coercion_warning],
                "notes": validation_notes,
            }
            session.commit()
            logger.warning(
                "Projection %s save_projection rejected payload type=%s",
                projection_id,
                type(data).__name__,
            )
            write_projection_trace(
                event="save_projection_rejected",
                projection_id=str(projection.projection_id),
                frame_id=str(projection.frame_id),
                details={
                    "data_type": type(data).__name__,
                    "error": coercion_warning,
                },
            )
            return {
                "error": coercion_warning,
                "projection_id": str(projection.projection_id),
            }

        normalized_data, validation_result = normalize_projection_data(
            coerced_data,
            schema,
        )

        if coercion_warning:
            existing_warnings = list(validation_result.get("warnings", []))
            existing_warnings.append(coercion_warning)
            validation_result = {
                **validation_result,
                "warnings": existing_warnings,
            }

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

        qa_pairs = normalized_data.get("qa_pairs") if isinstance(normalized_data, dict) else None
        questions = normalized_data.get("questions") if isinstance(normalized_data, dict) else None
        write_projection_trace(
            event="save_projection_committed",
            projection_id=str(projection.projection_id),
            frame_id=str(projection.frame_id),
            details={
                "status": projection.status.value,
                "qa_pairs_len": len(qa_pairs) if isinstance(qa_pairs, list) else None,
                "questions_len": len(questions) if isinstance(questions, list) else None,
                "validation_warnings": (validation_result or {}).get("warnings"),
            },
        )

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
    write_projection_trace(
        event="request_frame_clarification",
        projection_id=str(pid),
        frame_id=str(frame_id),
        details={
            "field": field,
            "question_preview": (question or "")[:300],
            "context_preview": (context or "")[:300],
        },
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

        write_projection_trace(
            event="flag_for_feedback",
            projection_id=str(pid),
            frame_id=str(frame.frame_id),
            details={
                "feedback_id": str(feedback.feedback_id),
                "issue": issue,
                "field": field,
                "question_preview": (question or "")[:300],
            },
        )

        return {"feedback_id": str(feedback.feedback_id), "status": "created"}


def get_project_markdown(
    project_id: str,
    max_chars: int = 40000,
    max_files: int = 10,
) -> dict:
    """Read concatenated Markdown of all processed assets for a project.

    NOTE: For non-trivial projects prefer the per-file workflow
    (`list_project_markdown_files` + `read_project_markdown_file` /
    `read_markdown_section`). This bulk tool silently truncates and is kept
    only as a convenience for very small projects.

    Args:
        project_id: Project (paper bundle) whose processed Markdown to read.
        max_chars: Hard cap on the combined Markdown size.
        max_files: Hard cap on number of processed files to include.

    Returns:
        Dict with combined ``markdown``, list of ``files`` included,
        and a ``truncated`` flag.
    """
    pid = parse_uuidish(project_id)
    if not pid:
        return {"error": invalid_identifier_message("project_id", project_id)}

    from mkb.db.models import Asset, ProcessedAsset, ProcessingType, ProjectAsset
    from mkb.storage.s3 import download_bytes

    safe_max_chars = max(2000, min(int(max_chars), 400000))
    safe_max_files = max(1, min(int(max_files), 100))

    with SyncSessionLocal() as session:
        links = session.query(ProjectAsset).filter_by(project_id=pid).all()
        asset_ids = [link.asset_id for link in links]
        if not asset_ids:
            return {
                "project_id": str(pid),
                "markdown": "",
                "files": [],
                "truncated": False,
                "error": "No assets linked to this project.",
            }

        rows = (
            session.query(ProcessedAsset, Asset)
            .join(Asset, Asset.asset_id == ProcessedAsset.asset_id)
            .filter(ProcessedAsset.asset_id.in_(asset_ids))
            .filter(ProcessedAsset.processing_type == ProcessingType.MARKDOWN)
            .order_by(ProcessedAsset.created_at.asc())
            .all()
        )

    if not rows:
        return {
            "project_id": str(pid),
            "markdown": "",
            "files": [],
            "truncated": False,
            "error": (
                "No processed-markdown assets for this project. "
                "Run the processing step first."
            ),
        }

    chunks: list[str] = []
    included: list[dict] = []
    used = 0
    truncated = False

    for processed, asset in rows[:safe_max_files]:
        if used >= safe_max_chars:
            truncated = True
            break
        try:
            data = download_bytes(processed.s3_bucket, processed.s3_key)
            text = data.decode("utf-8", errors="replace")
        except Exception as exc:
            logger.warning(
                "get_project_markdown: failed to fetch %s/%s: %s",
                processed.s3_bucket, processed.s3_key, exc,
            )
            continue

        header = f"\n\n<!-- ── FILE: {asset.filename} ── -->\n\n"
        remaining = safe_max_chars - used - len(header)
        if remaining <= 0:
            truncated = True
            break
        body = text if len(text) <= remaining else (text[: remaining - 3] + "...")
        if len(text) > remaining:
            truncated = True
        chunks.append(header + body)
        used += len(header) + len(body)
        included.append({
            "filename": asset.filename,
            "processed_asset_id": str(processed.processed_asset_id),
            "size_chars": len(text),
        })

    if len(rows) > safe_max_files:
        truncated = True

    write_projection_trace(
        event="get_project_markdown",
        details={
            "project_id": str(pid),
            "files_included": len(included),
            "total_chars": used,
            "truncated": truncated,
        },
    )

    return {
        "project_id": str(pid),
        "markdown": "".join(chunks),
        "files": included,
        "file_count": len(included),
        "total_files": len(rows),
        "truncated": truncated,
        "limits": {"max_chars": safe_max_chars, "max_files": safe_max_files},
    }


def mark_projection_not_relevant(
    projection_id: str,
    reason: str = "",
) -> dict:
    """Mark a projection as NOT_RELEVANT and stop extraction.

    Call this when the source content (knowledge frame or Markdown) clearly
    does not contain any data relevant to the space's domain.  For example,
    a biomedical space applied to a paper about Si phase-transition physics
    should be marked not-relevant rather than returning an empty extraction.

    Do NOT call this just because some schema fields are missing — use
    ``save_projection`` with null values for absent fields instead.  Only
    call this when the paper's subject is entirely outside the space domain.

    Args:
        projection_id: The projection record to mark.
        reason: Brief explanation of why the paper is not relevant to the domain.

    Returns:
        Dict with ``projection_id`` and ``status`` = ``"not_relevant"``.
    """
    pid = parse_uuidish(projection_id)
    if not pid:
        return {"error": invalid_identifier_message("projection_id", projection_id)}

    with SyncSessionLocal() as session:
        projection = session.query(Projection).filter_by(projection_id=pid).first()
        if not projection:
            return {"error": f"Projection {projection_id} not found."}

        projection.status = ProjectionStatus.NOT_RELEVANT
        projection.agent_notes = reason or "Paper is not relevant to this space's domain."
        projection.extracted_at = datetime.now(timezone.utc)
        session.commit()

    write_projection_trace(
        event="projection_not_relevant",
        projection_id=str(pid),
        details={"reason": reason},
    )
    logger.info("Projection %s marked NOT_RELEVANT: %s", projection_id, reason)
    return {"projection_id": str(pid), "status": "not_relevant"}


# =====================================================================
# Incremental project-markdown reading (preferred over bulk dump)
# =====================================================================


def list_project_markdown_files(project_id: str) -> dict:
    """List every processed-Markdown file attached to a project.

    Returns one entry per file with its asset_id, filename, total character
    length, and the count of Markdown headings. Use this to plan paged reads
    via `read_project_markdown_file` / `read_markdown_section` instead of
    pulling everything at once with `get_project_markdown`.
    """
    pid = parse_uuidish(project_id)
    if not pid:
        return {"error": invalid_identifier_message("project_id", project_id)}

    from mkb.db.models import Asset, ProcessedAsset, ProcessingType, ProjectAsset
    from mkb.storage.s3 import download_bytes

    with SyncSessionLocal() as session:
        links = session.query(ProjectAsset).filter_by(project_id=pid).all()
        asset_ids = [link.asset_id for link in links]
        if not asset_ids:
            return {"project_id": str(pid), "files": []}

        rows = (
            session.query(ProcessedAsset, Asset)
            .join(Asset, Asset.asset_id == ProcessedAsset.asset_id)
            .filter(ProcessedAsset.asset_id.in_(asset_ids))
            .filter(ProcessedAsset.processing_type == ProcessingType.MARKDOWN)
            .order_by(ProcessedAsset.created_at.asc())
            .all()
        )

    files: list[dict] = []
    for processed, asset in rows:
        entry = {
            "asset_id": str(asset.asset_id),
            "processed_asset_id": str(processed.processed_asset_id),
            "filename": asset.filename,
            "total_chars": None,
            "heading_count": None,
        }
        try:
            data = download_bytes(processed.s3_bucket, processed.s3_key)
            text = data.decode("utf-8", errors="replace")
            entry["total_chars"] = len(text)
            entry["heading_count"] = sum(
                1 for line in text.splitlines()
                if line.lstrip().startswith("#")
            )
        except Exception as exc:
            entry["error"] = f"unreadable: {exc}"
        files.append(entry)

    return {"project_id": str(pid), "files": files, "file_count": len(files)}


_PROJECT_MD_CHUNK = 80_000


def read_project_markdown_file(
    asset_id: str,
    start_char: int = 0,
) -> str:
    """Read a single processed-Markdown file for projection, with paging.

    Returns up to 80,000 characters starting at `start_char`. If the file is
    longer, a trailing `[TRUNCATED …]` banner reports the next `start_char`
    to use. Prefer this (or `read_markdown_section`) over `get_project_markdown`
    for any non-trivial project — it lets you scope context to one file at a
    time and only read what you actually need.
    """
    # Reuse the extraction reading tool to avoid duplicating logic.
    from mkb.agents.tools.reading import read_processed_markdown

    return read_processed_markdown(asset_id, start_char=start_char)


# =====================================================================
# Incremental projection writes
# =====================================================================


def update_projection(
    projection_id: str,
    additions: dict | str | None = None,
    modifications: list | str | None = None,
    removals: list | str | None = None,
    agent_notes: str = "",
) -> dict:
    """Apply incremental edits to an existing projection's `data` payload.

    Mirrors `update_knowledge_frame`. Use this **instead of resubmitting the
    full payload via `save_projection`** when you are extracting per-section
    or per-source-file and want to append rows as you go.

    Args:
        projection_id: The projection to update. Must already exist (created
            by the projection runner) — typically you call `save_projection`
            once to establish the top-level shape, then `update_projection`
            for subsequent batches.
        additions: Dict mapping a list-typed top-level key to items to
            append. Example: ``{"records": [{...}, {...}]}``.
        modifications: List of ``{"key": str, "index": int, "changes": dict}``
            entries (only valid when the item at that index is a dict).
        removals: List of ``{"key": str, "index": int, "reason": str}``
            entries. Indices refer to the current list state.
        agent_notes: Optional notes; appended to existing ``agent_notes``.

    Returns:
        Dict with ``projection_id``, ``status``, and ``changes_made`` counts.
        Does NOT change the projection status (already COMPLETED stays
        COMPLETED, IN_PROGRESS stays IN_PROGRESS).
    """
    pid = parse_uuidish(projection_id)
    if not pid:
        return {"error": invalid_identifier_message("projection_id", projection_id)}

    if isinstance(additions, str):
        parsed = _extract_json_from_text(additions)
        additions = parsed if isinstance(parsed, dict) else None
    if isinstance(modifications, str):
        parsed = _extract_json_from_text(modifications)
        modifications = parsed if isinstance(parsed, list) else None
    if isinstance(removals, str):
        parsed = _extract_json_from_text(removals)
        removals = parsed if isinstance(parsed, list) else None

    now = datetime.now(timezone.utc)

    with SyncSessionLocal() as session:
        projection = session.query(Projection).filter_by(projection_id=pid).first()
        if not projection:
            return {"error": f"Projection {projection_id} not found."}

        frame = session.query(KnowledgeFrame).filter_by(frame_id=projection.frame_id).first()
        source_project_id = str(frame.project_id) if frame else None

        data = copy.deepcopy(projection.data) if isinstance(projection.data, dict) else {}
        changes_made = {"additions": 0, "modifications": 0, "removals": 0}

        # Additions
        if isinstance(additions, dict):
            for key, items in additions.items():
                if not isinstance(items, list):
                    items = [items]
                if source_project_id:
                    items = _inject_source_project_references(items, source_project_id)
                if key not in data or not isinstance(data[key], list):
                    data[key] = []
                data[key].extend(items)
                changes_made["additions"] += len(items)

        # Removals (descending index per key to avoid shifting)
        if isinstance(removals, list):
            sorted_removals = sorted(
                removals, key=lambda r: r.get("index", 0) if isinstance(r, dict) else 0,
                reverse=True,
            )
            for removal in sorted_removals:
                if not isinstance(removal, dict):
                    continue
                key = removal.get("key")
                idx = removal.get("index")
                if (
                    key and key in data and isinstance(data[key], list)
                    and isinstance(idx, int) and 0 <= idx < len(data[key])
                ):
                    data[key].pop(idx)
                    changes_made["removals"] += 1

        # Modifications
        if isinstance(modifications, list):
            for mod in modifications:
                if not isinstance(mod, dict):
                    continue
                key = mod.get("key")
                idx = mod.get("index")
                changes = mod.get("changes", {})
                if (
                    key and key in data and isinstance(data[key], list)
                    and isinstance(idx, int) and 0 <= idx < len(data[key])
                    and isinstance(data[key][idx], dict)
                    and isinstance(changes, dict)
                ):
                    data[key][idx].update(changes)
                    changes_made["modifications"] += 1

        projection.data = data
        projection.extracted_at = now
        if agent_notes:
            existing_notes = projection.agent_notes or ""
            projection.agent_notes = (
                f"{existing_notes}\n{agent_notes}".strip()
                if existing_notes else agent_notes
            )
        session.commit()

        write_projection_trace(
            event="update_projection",
            projection_id=str(pid),
            frame_id=str(projection.frame_id),
            details={"changes_made": changes_made},
        )

        return {
            "projection_id": str(pid),
            "status": projection.status.value,
            "changes_made": changes_made,
        }


PROJECTION_TOOLS = [
    get_frame_content,
    get_project_markdown,
    list_project_markdown_files,
    read_project_markdown_file,
    save_projection,
    update_projection,
    mark_projection_not_relevant,
    request_frame_clarification,
    flag_for_feedback,
]
