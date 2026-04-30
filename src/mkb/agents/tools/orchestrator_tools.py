"""
Tools for the orchestrator agent.

Provides two categories:
- Status/inspection tools: read-only DB queries via mkb.api
- Action tools: queue background workflow jobs for the assistant UI to dispatch

The workflow queue is a module-level thread-safe Queue. The assistant page
drains it on each Streamlit rerun and starts proper start_job background jobs.
"""

from __future__ import annotations

import json
import logging
import queue as _queue
import uuid

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Workflow queue — shared between this module and the assistant UI page
# ---------------------------------------------------------------------------

_workflow_queue: _queue.Queue = _queue.Queue()


def get_pending_workflows() -> list[dict]:
    """Drain and return all pending workflow requests (called by the UI page)."""
    pending: list[dict] = []
    while True:
        try:
            pending.append(_workflow_queue.get_nowait())
        except _queue.Empty:
            break
    return pending


# ---------------------------------------------------------------------------
# Status / inspection tools
# ---------------------------------------------------------------------------


def list_projects() -> dict:
    """List all research projects with their label, asset count, and frame status.

    Returns a dict with a 'projects' list. Each item has project_id, label,
    asset_count, frame_status, and created_at.
    """
    from mkb import api

    try:
        projects = api.list_projects(limit=200)
        return {"projects": projects, "count": len(projects)}
    except Exception as exc:
        logger.error("list_projects failed: %s", exc)
        return {"error": str(exc)}


def get_project_details(project_id: str) -> dict:
    """Get full details for a single project: assets, frame status, and projections.

    Args:
        project_id: UUID string of the project.
    """
    from mkb import api

    try:
        projects = api.list_projects(limit=200)
        project = next((p for p in projects if p["project_id"] == project_id), None)
        if not project:
            return {"error": f"Project {project_id} not found"}

        assets = api.list_assets(project_id=project_id)
        frame = api.get_frame(project_id)
        projections = api.list_projections(project_id=project_id)

        frame_summary = None
        if frame:
            content = frame.get("content") or {}
            frame_summary = {
                "status": frame.get("status"),
                "extracted_at": frame.get("extracted_at"),
                "times_checked": frame.get("times_checked"),
                "top_level_keys": list(content.keys()) if isinstance(content, dict) else [],
                "extraction_summary": frame.get("extraction_summary"),
            }

        return {
            "project_id": project_id,
            "label": project.get("label"),
            "source_path": project.get("source_path"),
            "asset_count": len(assets),
            "assets": [
                {"filename": a.get("filename"), "status": a.get("status"), "mime_type": a.get("mime_type")}
                for a in assets[:20]
            ],
            "frame": frame_summary,
            "projections": [
                {
                    "projection_id": p.get("projection_id"),
                    "space_id": p.get("space_id"),
                    "status": p.get("status"),
                    "times_reviewed": p.get("times_reviewed"),
                }
                for p in projections[:10]
            ],
        }
    except Exception as exc:
        logger.error("get_project_details failed: %s", exc)
        return {"error": str(exc)}


def list_spaces() -> dict:
    """List all available extraction spaces (domain schemas used for projection).

    Returns a dict with a 'spaces' list. Each item has space_id, name, and domain.
    """
    from mkb import api

    try:
        spaces = api.list_spaces()
        return {
            "spaces": [
                {
                    "space_id": s.get("space_id"),
                    "name": s.get("name"),
                    "domain": s.get("domain"),
                    "field_count": len(s.get("extraction_schema", {}).get("fields", s.get("extraction_schema", {}))) if s.get("extraction_schema") else 0,
                }
                for s in spaces
            ],
            "count": len(spaces),
        }
    except Exception as exc:
        logger.error("list_spaces failed: %s", exc)
        return {"error": str(exc)}


def get_knowledge_frame(project_id: str) -> dict:
    """Read the knowledge frame for a project, including its content.

    Args:
        project_id: UUID string of the project.
    """
    from mkb import api

    try:
        frame = api.get_frame(project_id)
        if not frame:
            return {"error": f"No knowledge frame found for project {project_id}"}
        # Return full frame but truncate large content fields for readability
        content = frame.get("content") or {}
        if isinstance(content, dict):
            truncated_content = {}
            for k, v in content.items():
                if isinstance(v, list) and len(v) > 5:
                    truncated_content[k] = v[:5] + [f"... ({len(v) - 5} more items)"]
                elif isinstance(v, str) and len(v) > 500:
                    truncated_content[k] = v[:500] + "..."
                else:
                    truncated_content[k] = v
        else:
            truncated_content = content
        return {
            "frame_id": frame.get("frame_id"),
            "status": frame.get("status"),
            "extracted_at": frame.get("extracted_at"),
            "times_checked": frame.get("times_checked"),
            "extraction_summary": frame.get("extraction_summary"),
            "content": truncated_content,
        }
    except Exception as exc:
        logger.error("get_knowledge_frame failed: %s", exc)
        return {"error": str(exc)}


def list_projections(project_id: str) -> dict:
    """List all projections for a project across all spaces.

    Args:
        project_id: UUID string of the project.
    """
    from mkb import api

    try:
        projections = api.list_projections(project_id=project_id)
        spaces = {s["space_id"]: s["name"] for s in api.list_spaces()}
        return {
            "projections": [
                {
                    "projection_id": p.get("projection_id"),
                    "space_id": p.get("space_id"),
                    "space_name": spaces.get(str(p.get("space_id")), "unknown"),
                    "status": p.get("status"),
                    "times_reviewed": p.get("times_reviewed"),
                    "extracted_at": p.get("extracted_at"),
                }
                for p in projections
            ],
            "count": len(projections),
        }
    except Exception as exc:
        logger.error("list_projections failed: %s", exc)
        return {"error": str(exc)}


def get_open_feedback(project_id: str) -> dict:
    """Get unresolved feedback items for a project.

    Args:
        project_id: UUID string of the project.
    """
    from mkb import api

    try:
        all_feedback = api.list_feedback(project_id=project_id)
        open_items = [f for f in all_feedback if f.get("status") in ("OPEN", "open")]
        return {
            "open_count": len(open_items),
            "total_count": len(all_feedback),
            "open_feedback": [
                {
                    "feedback_id": f.get("feedback_id"),
                    "category": f.get("category"),
                    "question": f.get("question"),
                    "status": f.get("status"),
                }
                for f in open_items[:20]
            ],
        }
    except Exception as exc:
        logger.error("get_open_feedback failed: %s", exc)
        return {"error": str(exc)}


def get_system_overview() -> dict:
    """Get a high-level overview of the entire system: project counts, frame statuses, etc."""
    from mkb import api

    try:
        projects = api.list_projects(limit=500)
        spaces = api.list_spaces()
        feedback = api.list_feedback()

        frame_status_counts: dict[str, int] = {}
        for p in projects:
            s = p.get("frame_status", "NO_FRAME")
            frame_status_counts[s] = frame_status_counts.get(s, 0) + 1

        open_feedback = sum(1 for f in feedback if f.get("status") in ("OPEN", "open"))

        return {
            "total_projects": len(projects),
            "total_spaces": len(spaces),
            "total_feedback": len(feedback),
            "open_feedback": open_feedback,
            "frame_status_breakdown": frame_status_counts,
            "spaces": [{"name": s.get("name"), "domain": s.get("domain")} for s in spaces],
        }
    except Exception as exc:
        logger.error("get_system_overview failed: %s", exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Action tools — push workflow requests to the queue
# ---------------------------------------------------------------------------


def trigger_extraction(project_id: str, max_passes: int = 1) -> dict:
    """Queue a knowledge extraction job for the given project.

    This reads all processed files for the project and builds or updates its
    knowledge frame using an LLM extraction agent.

    Args:
        project_id: UUID string of the project to extract.
        max_passes: Number of extraction passes (1 = initial only, 2+ includes review).
    """
    _workflow_queue.put({
        "kind": "extraction",
        "project_id": project_id,
        "kwargs": {"project_id": project_id, "max_passes": max_passes},
        "label": f"Extraction · {project_id[:8]}",
    })
    return {
        "status": "queued",
        "message": f"Extraction queued for project {project_id} ({max_passes} pass(es)). Monitor progress in the sidebar.",
    }


def trigger_projection(project_id: str, space_id: str) -> dict:
    """Queue a projection job: map the project's knowledge frame onto a Space schema.

    The project must have a completed knowledge frame before projection can run.

    Args:
        project_id: UUID string of the project.
        space_id: UUID string of the Space to project onto.
    """
    _workflow_queue.put({
        "kind": "projection",
        "project_id": project_id,
        "kwargs": {"project_id": project_id, "space_id": space_id},
        "label": f"Projection · {project_id[:8]}",
    })
    return {
        "status": "queued",
        "message": f"Projection queued for project {project_id} onto space {space_id}. Monitor progress in the sidebar.",
    }


def trigger_knowledge_graph_extraction(project_id: str) -> dict:
    """Queue a knowledge graph extraction job for a project.

    Extracts concepts and relations from the project's knowledge frame and adds
    them to the global knowledge graph.

    Args:
        project_id: UUID string of the project.
    """
    _workflow_queue.put({
        "kind": "kg_extraction",
        "project_id": project_id,
        "kwargs": {"project_id": project_id},
        "label": f"KG Extraction · {project_id[:8]}",
    })
    return {
        "status": "queued",
        "message": f"Knowledge graph extraction queued for project {project_id}. Monitor progress in the sidebar.",
    }


def trigger_feedback_review(project_id: str) -> dict:
    """Queue a feedback review job for a project.

    The feedback reviewer agent reads open feedback items and updates the
    knowledge frame to incorporate them.

    Args:
        project_id: UUID string of the project.
    """
    _workflow_queue.put({
        "kind": "feedback_review",
        "project_id": project_id,
        "kwargs": {"project_id": project_id},
        "label": f"Feedback Review · {project_id[:8]}",
    })
    return {
        "status": "queued",
        "message": f"Feedback review queued for project {project_id}. Monitor progress in the sidebar.",
    }


def trigger_projection_review(project_id: str, space_id: str) -> dict:
    """Queue a projection review job for a project + space.

    The projection reviewer consolidates and quality-checks all projection runs
    for a project against a space, correcting errors by cross-referencing source material.

    Args:
        project_id: UUID string of the project.
        space_id: UUID string of the Space.
    """
    _workflow_queue.put({
        "kind": "projection_review",
        "project_id": project_id,
        "kwargs": {"project_id": project_id, "space_id": space_id},
        "label": f"Projection Review · {project_id[:8]}",
    })
    return {
        "status": "queued",
        "message": f"Projection review queued for project {project_id}, space {space_id}. Monitor progress in the sidebar.",
    }


# ---------------------------------------------------------------------------
# Tool list for the agent
# ---------------------------------------------------------------------------

ORCHESTRATOR_TOOLS = [
    list_projects,
    get_project_details,
    list_spaces,
    get_knowledge_frame,
    list_projections,
    get_open_feedback,
    get_system_overview,
    trigger_extraction,
    trigger_projection,
    trigger_knowledge_graph_extraction,
    trigger_feedback_review,
    trigger_projection_review,
]
