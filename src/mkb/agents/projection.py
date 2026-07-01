"""
Projection agent — extracts structured data from knowledge frames
according to a Space definition.

Unlike the KB extraction agent which reads raw files, the projection
agent reads knowledge frame content and produces structured output
per a domain-specific schema.
"""

from __future__ import annotations

import logging
import uuid

from google.adk.agents import Agent

from mkb.agents._utils import JobCancelled, SpaceConfig, create_llm, sync_agent_run
from mkb.agents.prompts.projection import build_projection_prompt
from mkb.agents.runner import AgentRunner
from mkb.agents.tools.projection import PROJECTION_TOOLS, write_projection_trace
from mkb.agents.tools.vision import VISION_TOOLS
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import (
    FrameStatus,
    KnowledgeFrame,
    Projection,
    ProjectionStatus,
    Space,
)

logger = logging.getLogger(__name__)

APP_NAME = "mkb_projection"


def build_projection_agent(
    space: Space | SpaceConfig,
    model: str | None = None,
    source_type: str = "frame",
    source_id: str | None = None,
    project_id: str | None = None,
) -> Agent:
    """Create a projection agent configured for a specific space."""
    prompt = build_projection_prompt(
        domain=space.domain,
        system_prompt=space.system_prompt,
        extraction_schema=space.extraction_schema,
        field_descriptions=space.field_descriptions,
        purpose=getattr(space, "purpose", None),
        source_type=source_type,
        source_id=source_id,
        project_id=project_id,
    )

    llm = create_llm(model)
    return Agent(
        name="projection_agent",
        model=llm,
        instruction=prompt,
        tools=PROJECTION_TOOLS + VISION_TOOLS,
    )


async def _run_projection_async(
    space_id: uuid.UUID,
    frame_id: uuid.UUID | None = None,
    model: str | None = None,
    verbose: bool = False,
    progress_callback=None,
    source_type: str = "frame",
    project_id: uuid.UUID | None = None,
) -> dict:
    """Run projection on a single frame using a space definition.

    When ``source_type == "frame"`` (default) the projection agent reads
    the curated knowledge frame; ``frame_id`` is required.

    When ``source_type == "markdown"`` the projection agent reads the
    project's processed Markdown directly (no curated frame needed).
    ``project_id`` is required; a placeholder frame will be created for
    the project if one does not yet exist (so the DB linkage is preserved).
    """

    source_kind = (source_type or "frame").strip().lower()
    if source_kind not in {"frame", "markdown"}:
        return {"status": "error", "message": f"Unknown source_type: {source_type}"}

    def _emit(message: str, **extra) -> None:
        if progress_callback:
            progress_callback({"message": message, **extra})

    with SyncSessionLocal() as db:
        space = db.query(Space).filter_by(space_id=space_id).first()
        if not space:
            return {"status": "error", "message": f"Space {space_id} not found"}

        # Resolve / create frame depending on source mode.
        if source_kind == "markdown":
            if project_id is None and frame_id is None:
                return {"status": "error", "message": "project_id required for source_type=markdown"}
            if project_id is None:
                f = db.query(KnowledgeFrame).filter_by(frame_id=frame_id).first()
                if not f:
                    return {"status": "error", "message": f"Frame {frame_id} not found"}
                project_id = f.project_id
            frame = db.query(KnowledgeFrame).filter_by(project_id=project_id).first()
            if not frame:
                frame = KnowledgeFrame(
                    frame_id=uuid.uuid4(),
                    project_id=project_id,
                    status=FrameStatus.PENDING,
                    content={},
                )
                db.add(frame)
                db.flush()
            frame_id = frame.frame_id
            resolved_project_id = project_id
        else:
            if frame_id is None:
                return {"status": "error", "message": "frame_id required for source_type=frame"}
            frame = db.query(KnowledgeFrame).filter_by(frame_id=frame_id).first()
            if not frame:
                return {"status": "error", "message": f"Frame {frame_id} not found"}
            if frame.status != FrameStatus.COMPLETED:
                return {"status": "error", "message": f"Frame is not completed (status: {frame.status.value})"}
            resolved_project_id = frame.project_id

        # Always create a fresh projection record so history is preserved
        projection = Projection(
            projection_id=uuid.uuid4(),
            space_id=space_id,
            frame_id=frame_id,
            status=ProjectionStatus.IN_PROGRESS,
            space_version=space.version,
            source_type=source_kind,
        )
        db.add(projection)
        db.commit()

        projection_id = projection.projection_id
        space_name = space.name
        write_projection_trace(
            event="projection_run_started",
            projection_id=str(projection_id),
            frame_id=str(frame_id),
            details={
                "space_id": str(space_id),
                "space_name": space_name,
                "space_version": space.version,
                "model": model,
                "source_type": source_kind,
                "project_id": str(resolved_project_id),
            },
        )

        # Capture space attributes before session closes
        space_cfg = SpaceConfig(
            domain=space.domain,
            system_prompt=space.system_prompt,
            extraction_schema=space.extraction_schema,
            field_descriptions=space.field_descriptions,
            purpose=getattr(space, "purpose", None),
        )

    source_id = (
        str(resolved_project_id) if source_kind == "markdown" else str(frame_id)
    )
    agent = build_projection_agent(
        space_cfg, model, source_type=source_kind, source_id=source_id,
        project_id=str(resolved_project_id),
    )
    runner = AgentRunner(agent=agent, app_name=APP_NAME)

    session_id = f"projection_{projection_id}"
    await runner.create_session(session_id)

    if source_kind == "markdown":
        message = (
            f"Extract structured data for project {resolved_project_id} "
            f"using the '{space_name}' space schema, reading directly from "
            f"the project's processed Markdown. "
            f"The projection ID is {projection_id}. "
            f"Start by calling list_project_markdown_files(project_id=\"{resolved_project_id}\") "
            f"to plan your reads, then walk the files section by section "
            f"(read_markdown_section / read_project_markdown_file). Seed the "
            f"projection once with save_projection, then use update_projection "
            f"to append items as you go — do not re-emit the full payload."
        )
    else:
        message = (
            f"Extract structured data from frame {frame_id} "
            f"(project {resolved_project_id}) "
            f"using the '{space_name}' space schema. "
            f"The projection ID is {projection_id}. "
            f"Start by reading the frame content, then extract "
            f"data according to the schema. Seed the projection once with "
            f"save_projection and use update_projection for additional batches "
            f"so you avoid re-emitting the full payload. "
            f"If you encounter image references (``![](images/...)``) in the "
            f"frame content that may contain schema-relevant data, use the "
            f"vision tools with project_id=\"{resolved_project_id}\"."
        )

    try:
        _emit(f"Projection started for {space_name} (source={source_kind})", stage="setup")
        result = await runner.run(
            session_id=session_id,
            message=message,
            verbose=verbose,
            progress_callback=progress_callback,
        )
    except JobCancelled:
        # Revert the projection to PENDING so it can be re-run cleanly.
        with SyncSessionLocal() as db:
            proj = db.query(Projection).filter_by(projection_id=projection_id).first()
            if proj and proj.status == ProjectionStatus.IN_PROGRESS:
                proj.status = ProjectionStatus.PENDING
                db.commit()
        logger.info("Projection %s cancelled — reverted to PENDING", projection_id)
        raise

    if not result.success:
        with SyncSessionLocal() as db:
            proj = db.query(Projection).filter_by(projection_id=projection_id).first()
            if proj:
                proj.status = ProjectionStatus.FAILED
                proj.agent_notes = result.error
                db.commit()
        write_projection_trace(
            event="projection_run_failed",
            projection_id=str(projection_id),
            frame_id=str(frame_id),
            details={"error": result.error},
        )
        return {
            "status": "error",
            "projection_id": str(projection_id),
            "message": result.error,
        }

    # Check result
    with SyncSessionLocal() as db:
        proj = db.query(Projection).filter_by(projection_id=projection_id).first()
        status = proj.status.value if proj else "unknown"

    write_projection_trace(
        event="projection_run_finished",
        projection_id=str(projection_id),
        frame_id=str(frame_id),
        details={
            "status": status,
            "agent_summary_preview": (result.final_text or "")[:1200],
        },
    )

    return {
        "status": "completed" if status == "COMPLETED" else status,
        "projection_id": str(projection_id),
        "space_name": space_name,
        "frame_id": str(frame_id),
        "agent_summary": result.final_text,
    }


@sync_agent_run
async def run_projection(
    space_id: uuid.UUID,
    frame_id: uuid.UUID | None = None,
    model: str | None = None,
    verbose: bool = False,
    progress_callback=None,
    source_type: str = "frame",
    project_id: uuid.UUID | None = None,
) -> dict:
    """Run projection on one frame."""
    return await _run_projection_async(
        space_id,
        frame_id,
        model,
        verbose,
        progress_callback=progress_callback,
        source_type=source_type,
        project_id=project_id,
    )


@sync_agent_run
async def run_projection_all(
    space_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
    source_type: str = "frame",
) -> dict:
    """Run projection on all completed frames (or all projects) using a space."""
    sid = space_id
    source_kind = (source_type or "frame").strip().lower()

    with SyncSessionLocal() as db:
        space = db.query(Space).filter_by(space_id=sid).first()
        if not space:
            return {"status": "error", "message": f"Space {sid} not found"}

        if source_kind == "markdown":
            # Project against every project that has at least one MARKDOWN processed asset.
            from mkb.db.models import (
                Asset,
                ProcessedAsset,
                ProcessingType,
                ProjectAsset,
                ResearchProject,
            )

            project_rows = (
                db.query(ResearchProject.project_id)
                .join(ProjectAsset, ProjectAsset.project_id == ResearchProject.project_id)
                .join(Asset, Asset.asset_id == ProjectAsset.asset_id)
                .join(ProcessedAsset, ProcessedAsset.asset_id == Asset.asset_id)
                .filter(ProcessedAsset.processing_type == ProcessingType.MARKDOWN)
                .distinct()
                .all()
            )
            targets = [(None, pid) for (pid,) in project_rows]
        else:
            frames = db.query(KnowledgeFrame).filter_by(status=FrameStatus.COMPLETED).all()
            targets = [(f.frame_id, None) for f in frames]

    results = []
    for fid, pid in targets:
        logger.info(
            "Projecting %s %s with space %s ...",
            "project" if source_kind == "markdown" else "frame",
            pid or fid, sid,
        )
        result = await _run_projection_async(
            sid, fid, model=model, verbose=verbose,
            source_type=source_kind, project_id=pid,
        )
        results.append(result)
        logger.info("  → %s", result.get("status", "unknown"))

    return {
        "total": len(results),
        "completed": sum(1 for r in results if r["status"] == "completed"),
        "failed": sum(1 for r in results if r["status"] == "error"),
        "results": results,
    }
