"""
Knowledge frame tools for the LLM agent.

Includes tools for saving, retrieving, and updating knowledge frames.
"""

from __future__ import annotations

import copy
import json
import logging
import uuid
from datetime import datetime, timezone

from mkb.agents.tools._ids import invalid_identifier_message, parse_uuidish
from mkb.agents.runtime import AgentRuntime, bind_tools
from mkb.db.models import (
    FrameStatus,
    KnowledgeFrame,
    ProjectAsset,
    ResearchProject,
)

logger = logging.getLogger(__name__)


# =====================================================================
# Validation
# =====================================================================


def _validate_frame_content(content: dict) -> list[str]:
    """Return list of validation warnings (not errors — don't block saves)."""
    warnings = []
    if "paper" not in content:
        warnings.append("Missing 'paper' key")
    if "domain" not in content:
        warnings.append("Missing 'domain' key")
    for key, value in content.items():
        if key in ("paper", "domain"):
            continue
        if not isinstance(value, list):
            warnings.append(f"Key '{key}' should be a list, got {type(value).__name__}")
    return warnings


# =====================================================================
# Knowledge frame tools
# =====================================================================


def _derive_project_label(content: dict) -> str | None:
    """Pick a human-friendly project label from extracted frame content.

    Prefers the paper title, falls back to the DOI, and returns ``None`` when
    neither is present. The returned label is trimmed and length-capped so it
    stays usable in lists and UI.
    """
    paper = (content or {}).get("paper") or {}
    title = paper.get("title")
    if isinstance(title, str):
        candidate = title.strip()
        if candidate:
            return candidate[:200]
    doi = paper.get("doi")
    if isinstance(doi, str):
        candidate = doi.strip()
        if candidate:
            return candidate[:200]
    return None


def _maybe_auto_rename_project(session, project: ResearchProject, content: dict) -> None:
    """Auto-rename ``project`` based on extracted content, unless user-named.

    No-ops when the project's ``metadata_["user_named"]`` flag is set, when no
    usable label can be derived, or when the derived label matches the current
    one.
    """
    meta = project.metadata_ or {}
    if meta.get("user_named"):
        return
    new_label = _derive_project_label(content)
    if not new_label or new_label == project.label:
        return
    previous = project.label
    project.label = new_label
    updated_meta = dict(meta)
    updated_meta["auto_renamed_from"] = previous
    project.metadata_ = updated_meta
    logger.info(
        "Auto-renamed project %s: %r -> %r", project.project_id, previous, new_label,
    )


def save_knowledge_frame(
    project_id: str,
    content: dict,
    summary: str = "",
    *,
    runtime: AgentRuntime,
) -> dict:
    """Save or update a knowledge frame for a research project.

    content should be a dict with required keys:
      - "paper": {title, authors, journal, year, doi}
      - "domain": string identifying the research domain

    All other keys are free-form — the agent decides the structure based
    on the paper content. Each additional key should map to a list of dicts,
    and every item should have an evidence_level (1-4):
      1 = Causal experimental evidence
      2 = Direct experimental observation
      3 = Correlative evidence
      4 = Predicted / inferred

    This also marks the frame as COMPLETED.
    """
    pid = parse_uuidish(project_id)
    if not pid:
        return {"error": invalid_identifier_message("project_id", project_id)}

    now = datetime.now(timezone.utc)

    # Handle content passed as JSON string
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return {"error": "content must be a dict or valid JSON string"}

    # Validate and log warnings
    warnings = _validate_frame_content(content)
    for w in warnings:
        logger.warning("Frame validation: %s (project %s)", w, project_id)

    with runtime.database.session() as session:
        project = session.query(ResearchProject).filter_by(project_id=pid).first()
        if not project:
            return {"error": f"Project {project_id} not found."}

        links = session.query(ProjectAsset).filter_by(project_id=pid).all()
        asset_ids = [str(link.asset_id) for link in links]
        source_meta = {
            "project_label": project.label,
            "source_path": project.source_path,
            "asset_ids": asset_ids,
            "project_id": project_id,
        }

        existing = session.query(KnowledgeFrame).filter_by(project_id=pid).first()
        if existing:
            existing.content = content
            existing.extraction_summary = summary
            existing.status = FrameStatus.COMPLETED
            existing.extracted_at = now
            existing.source_metadata = source_meta
            existing.times_checked = existing.times_checked + 1
            existing.extraction_version = (existing.extraction_version or 0) + 1
            _maybe_auto_rename_project(session, project, content)
            session.commit()
            return {"frame_id": str(existing.frame_id), "status": "updated"}

        frame = KnowledgeFrame(
            frame_id=uuid.uuid4(),
            project_id=pid,
            status=FrameStatus.COMPLETED,
            content=content,
            extraction_summary=summary,
            times_checked=1,
            extraction_version=1,
            extracted_at=now,
            source_metadata=source_meta,
        )
        session.add(frame)
        _maybe_auto_rename_project(session, project, content)
        session.commit()
        return {"frame_id": str(frame.frame_id), "status": "created"}


def get_existing_frame(project_id: str, *, runtime: AgentRuntime) -> dict:
    """Get the existing knowledge frame for a project, if any.

    Returns the frame content and metadata, or an indication that none exists.
    Useful for re-extraction to see what was previously extracted.
    """
    pid = parse_uuidish(project_id)
    if not pid:
        return {"exists": False, "error": invalid_identifier_message("project_id", project_id)}

    with runtime.database.session() as session:
        frame = session.query(KnowledgeFrame).filter_by(project_id=pid).first()
        if not frame:
            return {"exists": False}
        return {
            "exists": True,
            "frame_id": str(frame.frame_id),
            "status": frame.status.value,
            "content": frame.content,
            "extraction_summary": frame.extraction_summary,
            "times_checked": frame.times_checked,
            "extraction_version": frame.extraction_version,
            "extracted_at": frame.extracted_at.isoformat() if frame.extracted_at else None,
            "agent_annotations": frame.agent_annotations or {},
        }


def update_knowledge_frame(
    project_id: str,
    additions: dict | None = None,
    modifications: list | None = None,
    removals: list | None = None,
    review_notes: str = "",
    *,
    runtime: AgentRuntime,
) -> dict:
    """Apply incremental updates to an existing knowledge frame.

    Args:
        project_id: The project whose frame to update.
        additions: Dict mapping keys to lists of items to append.
            Example: {"materials": [{"name": "...", "evidence_level": 2}]}
        modifications: List of modifications, each with:
            {"key": "materials", "index": 0, "changes": {"formula": "NaCl"}}
        removals: List of removals, each with:
            {"key": "materials", "index": 0, "reason": "duplicate entry"}
        review_notes: Agent's notes about what was changed and why.

    Returns:
        Dict with frame_id, status, and summary of changes made.
    """
    pid = parse_uuidish(project_id)
    if not pid:
        return {"error": invalid_identifier_message("project_id", project_id)}

    now = datetime.now(timezone.utc)

    # Handle args passed as JSON strings
    if isinstance(additions, str):
        try:
            additions = json.loads(additions)
        except (json.JSONDecodeError, TypeError):
            additions = None
    if isinstance(modifications, str):
        try:
            modifications = json.loads(modifications)
        except (json.JSONDecodeError, TypeError):
            modifications = None
    if isinstance(removals, str):
        try:
            removals = json.loads(removals)
        except (json.JSONDecodeError, TypeError):
            removals = None

    with runtime.database.session() as session:
        frame = session.query(KnowledgeFrame).filter_by(project_id=pid).first()
        if not frame:
            return {"error": f"No frame found for project {project_id}."}
        if not frame.content:
            return {"error": "Frame has no content to update."}

        content = copy.deepcopy(frame.content)
        changes_made = {"additions": 0, "modifications": 0, "removals": 0}

        # Apply additions
        if additions:
            for key, items in additions.items():
                if not isinstance(items, list):
                    items = [items]
                if key not in content:
                    content[key] = []
                if isinstance(content[key], list):
                    content[key].extend(items)
                    changes_made["additions"] += len(items)

        # Apply removals (process before modifications, in reverse index order)
        if removals:
            # Sort by index descending to avoid index shifting
            sorted_removals = sorted(removals, key=lambda r: r.get("index", 0), reverse=True)
            for removal in sorted_removals:
                key = removal.get("key")
                idx = removal.get("index")
                if key and key in content and isinstance(content[key], list):
                    if idx is not None and 0 <= idx < len(content[key]):
                        content[key].pop(idx)
                        changes_made["removals"] += 1

        # Apply modifications
        if modifications:
            for mod in modifications:
                key = mod.get("key")
                idx = mod.get("index")
                changes = mod.get("changes", {})
                if key and key in content and isinstance(content[key], list):
                    if idx is not None and 0 <= idx < len(content[key]):
                        if isinstance(content[key][idx], dict):
                            content[key][idx].update(changes)
                            changes_made["modifications"] += 1

        frame.content = content
        frame.extraction_summary = review_notes or frame.extraction_summary
        frame.extracted_at = now
        frame.times_checked = frame.times_checked + 1
        frame.extraction_version = (frame.extraction_version or 0) + 1
        session.commit()

        # Save extraction pass record
        from mkb.db.models import ExtractionPass
        pass_record = ExtractionPass(
            pass_id=uuid.uuid4(),
            frame_id=frame.frame_id,
            pass_number=frame.extraction_version,
            pass_type="review",
            content_snapshot=content,
            changes_made=changes_made,
            agent_notes=review_notes,
        )
        session.add(pass_record)
        session.commit()

        return {
            "frame_id": str(frame.frame_id),
            "status": "updated",
            "changes_made": changes_made,
            "extraction_version": frame.extraction_version,
        }


FRAME_OPERATIONS = [
    save_knowledge_frame,
    get_existing_frame,
    update_knowledge_frame,
]


def frame_tools(runtime: AgentRuntime):
    """Return frame tools bound to one client-owned database."""

    return bind_tools(FRAME_OPERATIONS, runtime)


# Compatibility export for callers that have not yet been converted to the
# per-client factory.  It deliberately contains unbound operations: registering
# these directly is rejected by ADK rather than silently selecting a process
# global database.
FRAME_TOOLS = FRAME_OPERATIONS
