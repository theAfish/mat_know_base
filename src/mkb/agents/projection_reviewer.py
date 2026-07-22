"""
Projection reviewer agent — consolidates and corrects projection data
through multi-agent review.

Orchestrates the review process: reads all projections, compares against
knowledge frame and source material, delegates to fixer sub-agent when
needed, picks the best projection, updates it in-place, and soft-deletes
the rest.
"""

from __future__ import annotations

import logging
import json
import uuid
from typing import Any

from google.adk.agents import Agent

from mkb.agents._utils import create_llm, sync_agent_run
from mkb.agents.prompts.projection_review import default_review_prompt_for
from mkb.agents.runner import AgentRunner
from mkb.agents.runtime import AgentRuntime
from mkb.agents.tools.reading import reading_tools
from mkb.agents.tools.projection_review import projection_review_tools
from mkb.agents.tools.projection_review import save_reviewed_projection_patch
from mkb.agents.tools.review_search import (
    get_projection_review_search_tools,
)
from mkb.db.models import (
    KnowledgeFrame,
    Projection,
    ProjectionStatus,
    Space,
)
from mkb.spaces.registry import resolve_post_processor
from mkb.post_processors.registry import run_script as run_post_processor_script
from mkb.skills.registry import skill_instruction_block

logger = logging.getLogger(__name__)

APP_NAME = "mkb_projection_reviewer"

def _compact_followup_context(value: Any, *, max_chars: int = 8000) -> str:
    text = json.dumps(value, ensure_ascii=True, default=str) if not isinstance(value, str) else value
    return text if len(text) <= max_chars else text[: max_chars - 3] + "..."


def build_projection_reviewer_agent(
    runtime: AgentRuntime,
    model: str | None = None,
    purpose: str | None = None,
    custom_prompt: str | None = None,
    tool_groups: list[str] | None = None,
    skill_ids: list[str] | None = None,
    output_columns: list[dict] | None = None,
) -> Agent:
    """Create a projection reviewer agent.

    Prompt selection order:
    1. ``custom_prompt`` — a per-space override from ``Space.review_prompt``.
    2. The default prompt for the space ``purpose`` (tabular / qa / skill /
       freeform), via :func:`default_review_prompt_for`.
    """
    llm = create_llm(model)
    purpose_key = (purpose or "tabular_database").lower()
    if custom_prompt and custom_prompt.strip():
        instruction = custom_prompt
        agent_name = f"projection_reviewer_{purpose_key}_custom"
    else:
        instruction = default_review_prompt_for(purpose_key)
        agent_name = (
            "projection_reviewer_qa"
            if purpose_key == "qa_benchmark"
            else f"projection_reviewer_{purpose_key}"
            if purpose_key in {"skill_cards", "freeform"}
            else "projection_reviewer"
        )
    skill_block = skill_instruction_block(skill_ids)
    if skill_block:
        instruction = f"{instruction.rstrip()}\n\n{skill_block}"
    output_column_block = _post_processor_output_column_block(output_columns)
    if output_column_block:
        instruction = f"{instruction.rstrip()}\n\n{output_column_block}"
    groups = [str(group).strip().lower() for group in (tool_groups or ["reading"]) if str(group).strip()]
    optional_tools = []
    if "reading" in groups:
        optional_tools.extend(reading_tools(runtime))
    search_names = [group for group in groups if group in {"web", "uniprot", "crossref", "ncbi"}]
    if search_names:
        optional_tools.extend(get_projection_review_search_tools(search_names))
    return Agent(
        name=agent_name,
        model=llm,
        instruction=instruction,
        tools=projection_review_tools(runtime) + optional_tools,
    )


def _post_processor_output_column_block(output_columns: list[dict] | None) -> str:
    columns = []
    for raw_column in output_columns or []:
        if not isinstance(raw_column, dict):
            continue
        name = str(raw_column.get("name") or "").strip()
        if not name:
            continue
        description = str(raw_column.get("description") or "").strip()
        columns.append((name, description))
    if not columns:
        return (
            "# Post-Processor Output Columns\n\n"
            "This post-processor has no user-declared output columns. Do not "
            "add new columns or keys outside the current extraction schema; "
            "only correct, normalize, merge, or remove existing projection data."
        )

    column_lines = "\n".join(
        f"- `{name}`: {description}" if description else f"- `{name}`"
        for name, description in columns
    )
    return (
        "# Post-Processor Output Columns\n\n"
        "The user has explicitly allowed this post-processor to add ONLY these "
        "new output columns/keys when they are needed by the post-processing "
        "task:\n"
        f"{column_lines}\n\n"
        "Do not invent alternate names, duplicate variants, or similar "
        "columns. Reuse these exact names across review runs. Leave an allowed "
        "column empty/null when the value cannot be verified from the source "
        "or enabled lookup tools."
    )


def _format_output_column_names(processor: dict) -> str:
    names = [
        str(column.get("name")).strip()
        for column in (processor.get("output_columns") or [])
        if isinstance(column, dict) and str(column.get("name") or "").strip()
    ]
    return ", ".join(names) if names else "none"


def _processor_runtime(space: Space, reviewer_id: str | None = None) -> tuple[str | None, str | None, list[str], list[str], dict]:
    processor = resolve_post_processor(space, reviewer_id)
    prompt = processor.get("prompt") or getattr(space, "review_prompt", None)
    groups = processor.get("tool_groups") or ["reading"]
    skill_ids = [str(skill_id).strip() for skill_id in (processor.get("skill_ids") or []) if str(skill_id).strip()]
    return (
        str(processor.get("id") or "default"),
        prompt,
        [str(group).strip().lower() for group in groups],
        skill_ids,
        processor,
    )


def _post_processor_script_payload(
    space: Space,
    project_id: uuid.UUID,
    frame_id: uuid.UUID,
    projections: list[Projection],
) -> dict:
    return {
        "space": {
            "space_id": str(space.space_id),
            "name": space.name,
            "purpose": getattr(space, "purpose", None),
            "extraction_schema": space.extraction_schema,
        },
        "project": {
            "project_id": str(project_id),
            "frame_id": str(frame_id),
            "projection_count": len(projections),
        },
        "projections": [
            {
                "projection_id": str(projection.projection_id),
                "data": projection.data,
                "space_version": projection.space_version,
                "status": projection.status.value,
            }
            for projection in projections
        ],
    }


def _apply_script_patch(script_decision: dict, *, runtime: AgentRuntime) -> dict | None:
    patch = script_decision.get("patch")
    if not patch:
        return None
    result = save_reviewed_projection_patch(
        str(patch["winning_projection_id"]),
        patch["updates"],
        str(patch.get("review_notes") or "Post-processor script applied."),
        runtime=runtime,
    )
    if result.get("error"):
        raise ValueError(f"Post-processor script patch was not saved: {result['error']}")
    return result


async def _run_review_async(
    space_id: uuid.UUID,
    project_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
    progress_callback=None,
    reviewer_id: str | None = None,
    runtime: AgentRuntime | None = None,
) -> dict:
    """Run projection review on a single project for a given space."""

    if runtime is None:
        raise ValueError("Projection review requires an explicit AgentRuntime")
    with runtime.database.session() as db:
        space = db.query(Space).filter_by(space_id=space_id).first()
        if not space:
            return {"status": "error", "message": f"Space {space_id} not found"}

        frame = db.query(KnowledgeFrame).filter_by(project_id=project_id).first()
        if not frame:
            return {"status": "error", "message": f"No frame found for project {project_id}"}

        # Count non-deleted completed projections and detect stale schema versions
        projections_for_count = (
            db.query(Projection)
            .filter_by(space_id=space_id, frame_id=frame.frame_id)
            .filter(
                Projection.status.in_(
                    [ProjectionStatus.COMPLETED, ProjectionStatus.REVIEWED]
                )
            )
            .filter(Projection.deleted_at.is_(None))
            .filter(Projection.superseded_by_id.is_(None))
            .order_by(Projection.created_at.desc())
            .all()
        )
        projection_count = len(projections_for_count)
        if projection_count == 0:
            return {
                "status": "error",
                "message": f"No completed projections for space {space_id} and project {project_id}",
            }

        current_space_version = space.version
        stale_versions = [
            p.space_version
            for p in projections_for_count
            if p.space_version < current_space_version
        ]
        space_name = space.name
        space_purpose = getattr(space, "purpose", None)
        selected_reviewer_id, processor_prompt, tool_groups, skill_ids, processor = _processor_runtime(space, reviewer_id)

        if progress_callback:
            progress_callback({
                "message": f"Running post-processor script for project {str(project_id)[:8]}",
                "stage": "post_processor_script",
            })
        try:
            script_decision = run_post_processor_script(
                processor.get("script"),
                _post_processor_script_payload(space, project_id, frame.frame_id, projections_for_count),
            )
        except ValueError as exc:
            return {
                "status": "error",
                "space_id": str(space_id),
                "project_id": str(project_id),
                "reviewer_id": selected_reviewer_id,
                "message": f"Post-processor script failed: {exc}",
            }

    try:
        script_save = _apply_script_patch(script_decision, runtime=runtime)
    except ValueError as exc:
        return {
            "status": "error",
            "space_id": str(space_id),
            "project_id": str(project_id),
            "reviewer_id": selected_reviewer_id,
            "message": str(exc),
        }

    if not script_decision["run_agent"]:
        if progress_callback:
            progress_callback({
                "message": "Post-processor completed; agent review was not needed.",
                "stage": "post_processor_complete",
            })
        return {
            "status": "completed" if script_save else "skipped",
            "space_id": str(space_id),
            "space_name": space_name,
            "project_id": str(project_id),
            "reviewer_id": selected_reviewer_id,
            "reviewer_name": processor.get("name"),
            "message": "Post-processor script determined that agent review is not needed.",
            "script": script_decision["script"],
            "script_context": script_decision["context"],
            "script_save": script_save,
        }

    if progress_callback:
        progress_callback({
            "message": "Post-processor found unresolved records; starting agent review.",
            "stage": "agent_fallback",
        })
    agent = build_projection_reviewer_agent(
        runtime, model,
        purpose=space_purpose,
        custom_prompt=processor_prompt,
        tool_groups=tool_groups,
        skill_ids=skill_ids,
        output_columns=processor.get("output_columns"),
    )
    runner = AgentRunner(agent=agent, app_name=APP_NAME)

    session_id = f"review_proj_{space_id}_{project_id}_{uuid.uuid4().hex[:8]}"
    await runner.create_session(session_id)

    schema_change_hint = ""
    if stale_versions:
        schema_change_hint = (
            f" IMPORTANT: The space template has been updated — "
            f"the current schema version is v{current_space_version}, but "
            f"{len(stale_versions)} of the projection run(s) were extracted "
            f"with an older version (v{min(stale_versions)}). "
            f"The extraction_schema returned by get_all_projections_for_review "
            f"is the CURRENT (authoritative) schema. When reviewing, "
            f"revise the projection data to conform to the new schema: "
            f"populate any new or renamed fields that are present in the current "
            f"schema but missing from old projections, and remove or remap fields "
            f"that no longer appear in the current schema."
        )

    message = (
        f"Review all projections for space {space_id} ('{space_name}') "
        f"and project {project_id}. "
        f"There are {projection_count} completed projection run(s) to review."
        f"{schema_change_hint} "
        f"Start by loading all projections and the knowledge frame, "
        f"then systematically verify the data, pick the best projection "
        f"as the winner, merge corrections, and save it."
    )
    if tool_groups:
        message += (
            f" Use the selected post-processor '{processor.get('name')}' "
            f"(id={selected_reviewer_id}) with these tool groups: "
            f"{', '.join(tool_groups)}."
        )
    if skill_ids:
        message += f" Apply attached skill IDs: {', '.join(skill_ids)}."
    if processor.get("output_columns"):
        names = ", ".join(
            str(column.get("name"))
            for column in processor.get("output_columns", [])
            if isinstance(column, dict) and column.get("name")
        )
        message += f" Allowed post-processor output columns: {names}."
    if script_decision["context"] is not None:
        message += f" Script pre-check context: {_compact_followup_context(script_decision['context'])}."

    result = await runner.run(
        session_id=session_id,
        message=message,
        verbose=verbose,
        progress_callback=progress_callback,
    )

    if not result.success:
        return {
            "status": "error",
            "space_id": str(space_id),
            "project_id": str(project_id),
            "message": result.error,
        }

    # Check result — look for a REVIEWED projection
    with runtime.database.session() as db:
        reviewed = (
            db.query(Projection)
            .filter_by(space_id=space_id, status=ProjectionStatus.REVIEWED)
            .join(KnowledgeFrame, Projection.frame_id == KnowledgeFrame.frame_id)
            .filter(KnowledgeFrame.project_id == project_id)
            .filter(Projection.deleted_at.is_(None))
            .filter(Projection.superseded_by_id.is_(None))
            .order_by(Projection.reviewed_at.desc().nullslast())
            .first()
        )
        projection_id = str(reviewed.projection_id) if reviewed else None
        status = reviewed.status.value if reviewed else "unknown"

    return {
        "status": "completed" if status == "REVIEWED" else status,
        "projection_id": projection_id,
        "space_id": str(space_id),
        "space_name": space_name,
        "reviewer_id": selected_reviewer_id,
        "reviewer_name": processor.get("name"),
        "project_id": str(project_id),
        "projections_reviewed": projection_count,
        "agent_summary": result.final_text,
        "script": script_decision["script"],
        "script_context": script_decision["context"],
        "script_save": script_save,
    }


@sync_agent_run
async def run_projection_review(
    space_id: uuid.UUID,
    project_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
    progress_callback=None,
    reviewer_id: str | None = None,
    runtime: AgentRuntime | None = None,
) -> dict:
    """Run projection review on one project."""
    return await _run_review_async(
        space_id,
        project_id,
        model=model,
        verbose=verbose,
        progress_callback=progress_callback,
        reviewer_id=reviewer_id,
        runtime=runtime,
    )


@sync_agent_run
async def run_projection_review_followup(
    space_id: uuid.UUID,
    project_id: uuid.UUID,
    message: str,
    previous_job: dict | None = None,
    model: str | None = None,
    verbose: bool = False,
    progress_callback=None,
    reviewer_id: str | None = None,
    runtime: AgentRuntime | None = None,
) -> dict:
    """Run a follow-up reviewer turn after a projection review job.

    This creates a fresh reviewer agent with the same space prompt and tools,
    gives it the previous job result/events as context, and lets it answer or
    make additional projection edits using the normal review save tools.
    """
    cleaned_message = (message or "").strip()
    if not cleaned_message:
        return {"status": "error", "message": "Follow-up message is required."}

    if runtime is None:
        raise ValueError("Projection review requires an explicit AgentRuntime")
    with runtime.database.session() as db:
        space = db.query(Space).filter_by(space_id=space_id).first()
        if not space:
            return {"status": "error", "message": f"Space {space_id} not found"}
        frame = db.query(KnowledgeFrame).filter_by(project_id=project_id).first()
        if not frame:
            return {"status": "error", "message": f"No frame found for project {project_id}"}
        live_count = (
            db.query(Projection)
            .filter_by(space_id=space_id, frame_id=frame.frame_id)
            .filter(
                Projection.status.in_(
                    [ProjectionStatus.COMPLETED, ProjectionStatus.REVIEWED]
                )
            )
            .filter(Projection.deleted_at.is_(None))
            .filter(Projection.superseded_by_id.is_(None))
            .count()
        )
        if live_count == 0:
            return {
                "status": "error",
                "message": f"No active projections for space {space_id} and project {project_id}",
            }

        space_name = space.name
        space_purpose = getattr(space, "purpose", None)
        selected_reviewer_id, processor_prompt, tool_groups, skill_ids, processor = _processor_runtime(space, reviewer_id)

    agent = build_projection_reviewer_agent(
        runtime, model,
        purpose=space_purpose,
        custom_prompt=processor_prompt,
        tool_groups=tool_groups,
        skill_ids=skill_ids,
        output_columns=processor.get("output_columns"),
    )
    runner = AgentRunner(agent=agent, app_name=APP_NAME)
    session_id = f"review_followup_{space_id}_{project_id}_{uuid.uuid4().hex[:8]}"
    await runner.create_session(session_id)

    prior_result = (previous_job or {}).get("result")
    prior_events = (previous_job or {}).get("events") or []
    compact_events = [
        {
            "stage": event.get("stage"),
            "tool": event.get("tool"),
            "message": event.get("message"),
            "payload": event.get("payload"),
        }
        for event in prior_events[-20:]
        if isinstance(event, dict)
    ]
    followup_prompt = (
        f"You are continuing a completed projection review for space {space_id} "
        f"('{space_name}') and project {project_id}.\n\n"
        f"Selected post-processor: {processor.get('name')} "
        f"(id={selected_reviewer_id}); tool groups: {', '.join(tool_groups)}; "
        f"skill IDs: {', '.join(skill_ids) if skill_ids else 'none'}; "
        f"allowed output columns: {_format_output_column_names(processor)}.\n\n"
        f"User follow-up request:\n{cleaned_message}\n\n"
        f"Previous review job result, if available:\n"
        f"{_compact_followup_context(prior_result)}\n\n"
        f"Recent previous review events, if available:\n"
        f"{_compact_followup_context(compact_events)}\n\n"
        "Start by calling get_all_projections_for_review for this space and "
        "project so you operate on the current live projection. Use "
        "get_frame_for_review and your available tools as needed. If the user "
        "asks you to fill, correct, or write projection data and you verify a "
        "small number of values, prefer save_reviewed_projection_patch with "
        "path/value updates. After saving, inspect change_summary. If "
        "change_summary.data_changed is false, say clearly that no projection "
        "data was changed. Do not treat review notes as a data update."
    )
    result = await runner.run(
        session_id=session_id,
        message=followup_prompt,
        verbose=verbose,
        progress_callback=progress_callback,
    )
    if not result.success:
        return {
            "status": "error",
            "space_id": str(space_id),
            "project_id": str(project_id),
            "message": result.error,
        }
    return {
        "status": "completed",
        "mode": "followup",
        "space_id": str(space_id),
        "space_name": space_name,
        "reviewer_id": selected_reviewer_id,
        "reviewer_name": processor.get("name"),
        "project_id": str(project_id),
        "agent_summary": result.final_text,
    }


@sync_agent_run
async def run_projection_review_all(
    space_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
    progress_callback=None,
    project_ids: list[uuid.UUID] | None = None,
    reviewer_id: str | None = None,
    runtime: AgentRuntime | None = None,
) -> dict:
    """Run projection review on projects in a space (separate sessions).

    When ``project_ids`` is provided, only those projects are reviewed;
    otherwise every project with at least one live completed projection
    in the space is reviewed.
    """
    sid = space_id

    if runtime is None:
        raise ValueError("Projection review requires an explicit AgentRuntime")
    with runtime.database.session() as db:
        space = db.query(Space).filter_by(space_id=sid).first()
        if not space:
            return {"status": "error", "message": f"Space {sid} not found"}

        if project_ids:
            # Resolve frame_ids for the requested projects in this space
            requested = list(project_ids)
            frames = (
                db.query(KnowledgeFrame)
                .filter(KnowledgeFrame.project_id.in_(requested))
                .all()
            )
            frame_by_pid = {f.project_id: f.frame_id for f in frames}
            # Keep only those that have a non-deleted completed projection
            live_frames = (
                db.query(Projection.frame_id)
                .filter_by(space_id=sid)
                .filter(
                    Projection.status.in_(
                        [ProjectionStatus.COMPLETED, ProjectionStatus.REVIEWED]
                    )
                )
                .filter(Projection.deleted_at.is_(None))
                .filter(Projection.superseded_by_id.is_(None))
                .filter(Projection.frame_id.in_(list(frame_by_pid.values())))
                .distinct()
                .all()
            )
            live_frame_ids = {row[0] for row in live_frames}
            project_id_list = [
                pid for pid in requested if frame_by_pid.get(pid) in live_frame_ids
            ]
            if not project_id_list:
                return {
                    "status": "error",
                    "message": "None of the requested projects have completed projections in this space",
                }
        else:
            projections = (
                db.query(Projection.frame_id)
                .filter_by(space_id=sid)
                .filter(
                    Projection.status.in_(
                        [ProjectionStatus.COMPLETED, ProjectionStatus.REVIEWED]
                    )
                )
                .filter(Projection.deleted_at.is_(None))
                .filter(Projection.superseded_by_id.is_(None))
                .distinct()
                .all()
            )
            frame_ids = [p[0] for p in projections]
            if not frame_ids:
                return {"status": "error", "message": "No completed projections in this space"}
            frames = (
                db.query(KnowledgeFrame)
                .filter(KnowledgeFrame.frame_id.in_(frame_ids))
                .all()
            )
            project_id_list = [f.project_id for f in frames]

    results = []
    total = len(project_id_list)
    for idx, pid in enumerate(project_id_list, start=1):
        logger.info("Reviewing projections for project %s in space %s ...", pid, sid)
        if progress_callback:
            progress_callback({
                "message": f"Reviewing project {idx}/{total} ({str(pid)[:8]})",
            })
        result = await _run_review_async(
            sid,
            pid,
            model=model,
            verbose=verbose,
            progress_callback=progress_callback,
            reviewer_id=reviewer_id,
            runtime=runtime,
        )
        results.append(result)
        logger.info("  -> %s", result.get("status", "unknown"))

    return {
        "total_projects": len(results),
        "completed": sum(1 for r in results if r["status"] == "completed"),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "failed": sum(1 for r in results if r["status"] == "error"),
        "results": results,
    }


@sync_agent_run
async def run_projection_review_session(
    space_id: uuid.UUID,
    project_ids: list[uuid.UUID],
    model: str | None = None,
    verbose: bool = False,
    progress_callback=None,
    reviewer_id: str | None = None,
    runtime: AgentRuntime | None = None,
) -> dict:
    """Run a SINGLE reviewer session that handles every selected project.

    The same agent context (and conversation/session) reviews each project
    in turn. The agent is instructed to call ``save_reviewed_projection``
    after each project before moving on to the next. This is useful when
    cross-project context (e.g. shared schemas, recurring patterns) helps
    the reviewer produce more consistent decisions.
    """
    sid = space_id

    if runtime is None:
        raise ValueError("Projection review requires an explicit AgentRuntime")
    with runtime.database.session() as db:
        space = db.query(Space).filter_by(space_id=sid).first()
        if not space:
            return {"status": "error", "message": f"Space {sid} not found"}
        selected_reviewer_id, processor_prompt, tool_groups, skill_ids, processor = _processor_runtime(space, reviewer_id)

        # Filter to projects that actually have live completed projections
        frames = (
            db.query(KnowledgeFrame)
            .filter(KnowledgeFrame.project_id.in_(list(project_ids)))
            .all()
        )
        frame_by_pid = {f.project_id: f.frame_id for f in frames}
        if not frame_by_pid:
            return {"status": "error", "message": "No knowledge frames found for selected projects"}

        per_project_counts: dict[uuid.UUID, int] = {}
        script_skips: dict[uuid.UUID, dict] = {}
        script_contexts: dict[uuid.UUID, Any] = {}
        for pid in project_ids:
            fid = frame_by_pid.get(pid)
            if not fid:
                continue
            projections_for_project = (
                db.query(Projection)
                .filter_by(space_id=sid, frame_id=fid)
                .filter(
                    Projection.status.in_(
                        [ProjectionStatus.COMPLETED, ProjectionStatus.REVIEWED]
                    )
                )
                .filter(Projection.deleted_at.is_(None))
                .filter(Projection.superseded_by_id.is_(None))
                .order_by(Projection.created_at.desc())
                .all()
            )
            if not projections_for_project:
                continue
            try:
                script_decision = run_post_processor_script(
                    processor.get("script"),
                    _post_processor_script_payload(space, pid, fid, projections_for_project),
                )
            except ValueError as exc:
                return {
                    "status": "error",
                    "space_id": str(sid),
                    "project_id": str(pid),
                    "reviewer_id": selected_reviewer_id,
                    "message": f"Post-processor script failed: {exc}",
                }
            try:
                script_save = _apply_script_patch(script_decision, runtime=runtime)
            except ValueError as exc:
                return {
                    "status": "error",
                    "space_id": str(sid),
                    "project_id": str(pid),
                    "reviewer_id": selected_reviewer_id,
                    "message": str(exc),
                }
            if script_decision["run_agent"]:
                per_project_counts[pid] = len(projections_for_project)
                script_contexts[pid] = script_decision["context"]
            else:
                script_skips[pid] = {**script_decision, "script_save": script_save}

        if not per_project_counts:
            if script_skips:
                return {
                    "mode": "session",
                    "space_id": str(sid),
                    "space_name": space.name,
                    "reviewer_id": selected_reviewer_id,
                    "reviewer_name": processor.get("name"),
                    "total_projects": len(script_skips),
                    "completed": sum(1 for decision in script_skips.values() if decision["script_save"]),
                    "skipped": sum(1 for decision in script_skips.values() if not decision["script_save"]),
                    "results": [
                        {
                            "project_id": str(pid),
                            "status": "completed" if decision["script_save"] else "skipped",
                            "message": "Post-processor script completed without agent review.",
                            "script": decision["script"],
                            "script_context": decision["context"],
                            "script_save": decision["script_save"],
                        }
                        for pid, decision in script_skips.items()
                    ],
                }
            return {
                "status": "error",
                "message": "None of the selected projects have completed projections in this space",
            }

        space_name = space.name
        space_purpose = getattr(space, "purpose", None)

    agent = build_projection_reviewer_agent(
        runtime, model,
        purpose=space_purpose,
        custom_prompt=processor_prompt,
        tool_groups=tool_groups,
        skill_ids=skill_ids,
        output_columns=processor.get("output_columns"),
    )
    runner = AgentRunner(agent=agent, app_name=APP_NAME)

    session_id = f"review_sess_{sid}_{uuid.uuid4().hex[:8]}"
    await runner.create_session(session_id)

    project_lines = "\n".join(
        f"  {i+1}. project_id={pid} ({n} completed projection run(s))"
        + (f"; script context: {_compact_followup_context(script_contexts[pid])}" if script_contexts.get(pid) is not None else "")
        for i, (pid, n) in enumerate(per_project_counts.items())
    )
    message = (
        f"You are running a SINGLE consolidated review session over "
        f"{len(per_project_counts)} project(s) in space {sid} ('{space_name}').\n\n"
        f"Selected post-processor: {processor.get('name')} "
        f"(id={selected_reviewer_id}); tool groups: {', '.join(tool_groups)}; "
        f"skill IDs: {', '.join(skill_ids) if skill_ids else 'none'}; "
        f"allowed output columns: {_format_output_column_names(processor)}.\n\n"
        f"Projects to review (one at a time, in order):\n{project_lines}\n\n"
        f"For EACH project, in order:\n"
        f"  1. Call get_all_projections_for_review(space_id, project_id) to "
        f"load that project's projection runs.\n"
        f"  2. Call get_frame_for_review(project_id) to load its knowledge frame.\n"
        f"  3. Apply your usual review process and call "
        f"save_reviewed_projection(winning_projection_id, data, review_notes) "
        f"for that project BEFORE moving on to the next.\n"
        f"  4. Carry over any patterns / standards you established from earlier "
        f"projects to keep decisions consistent across the batch.\n\n"
        f"Do not skip any project. Report a brief per-project summary at the end."
    )

    if progress_callback:
        progress_callback({
            "message": f"Reviewing {len(per_project_counts)} project(s) in one session",
        })

    result = await runner.run(
        session_id=session_id,
        message=message,
        verbose=verbose,
        progress_callback=progress_callback,
    )

    if not result.success:
        return {
            "status": "error",
            "space_id": str(sid),
            "project_ids": [str(p) for p in per_project_counts.keys()],
            "message": result.error,
        }

    # Tally reviewed projections per project after the run
    summary: list[dict] = []
    with runtime.database.session() as db:
        for pid in per_project_counts.keys():
            fid = frame_by_pid.get(pid)
            reviewed = (
                db.query(Projection)
                .filter_by(space_id=sid, frame_id=fid, status=ProjectionStatus.REVIEWED)
                .filter(Projection.deleted_at.is_(None))
                .filter(Projection.superseded_by_id.is_(None))
                .order_by(Projection.reviewed_at.desc().nullslast())
                .first()
            )
            summary.append({
                "project_id": str(pid),
                "status": "completed" if reviewed else "unknown",
                "projection_id": str(reviewed.projection_id) if reviewed else None,
            })
    summary.extend(
        {
            "project_id": str(pid),
            "status": "completed" if decision["script_save"] else "skipped",
            "projection_id": None,
            "message": "Post-processor script completed without agent review.",
            "script": decision["script"],
            "script_context": decision["context"],
            "script_save": decision["script_save"],
        }
        for pid, decision in script_skips.items()
    )

    return {
        "mode": "session",
        "space_id": str(sid),
        "space_name": space_name,
        "reviewer_id": selected_reviewer_id,
        "reviewer_name": processor.get("name"),
        "total_projects": len(summary),
        "completed": sum(1 for s in summary if s["status"] == "completed"),
        "skipped": sum(1 for decision in script_skips.values() if not decision["script_save"]),
        "results": summary,
        "agent_summary": result.final_text,
    }
