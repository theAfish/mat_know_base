"""Knowledge graph extraction agent.

This agent mirrors projection-agent orchestration but writes concept-only
graph projections into one global space shared by all domains.
"""

from __future__ import annotations

import logging
import uuid

from google.adk.agents import Agent

from mkb.agents._utils import create_llm, sync_agent_run
from mkb.agents.prompts.knowledge_graph import KNOWLEDGE_GRAPH_PROMPT
from mkb.agents.runner import AgentRunner
from mkb.agents.tools.knowledge_graph import KNOWLEDGE_GRAPH_TOOLS
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import FrameStatus, KnowledgeFrame, Projection, ProjectionStatus
from mkb.knowledge_graph import clear_knowledge_graph_projections, ensure_global_kg_space

logger = logging.getLogger(__name__)

APP_NAME = "mkb_knowledge_graph"


def build_knowledge_graph_agent(model: str | None = None) -> Agent:
    """Create the concept-graph extraction agent."""
    llm = create_llm(model)
    return Agent(
        name="knowledge_graph_agent",
        model=llm,
        instruction=KNOWLEDGE_GRAPH_PROMPT,
        tools=KNOWLEDGE_GRAPH_TOOLS,
    )


async def _run_knowledge_graph_async(
    frame_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
    clear_existing: bool = True,
    progress_callback=None,
) -> dict:
    """Run knowledge graph extraction for one completed frame."""

    def _emit(message: str, **extra) -> None:
        if progress_callback:
            progress_callback({"message": message, **extra})

    global_space = ensure_global_kg_space()

    with SyncSessionLocal() as db:
        frame = db.query(KnowledgeFrame).filter_by(frame_id=frame_id).first()
        if not frame:
            return {"status": "error", "message": f"Frame {frame_id} not found"}
        if frame.status != FrameStatus.COMPLETED:
            return {"status": "error", "message": f"Frame is not completed (status: {frame.status.value})"}

        if clear_existing:
            clear_knowledge_graph_projections(frame_id=frame_id, include_legacy_spaces=True)

        projection = Projection(
            projection_id=uuid.uuid4(),
            space_id=global_space.space_id,
            frame_id=frame_id,
            status=ProjectionStatus.IN_PROGRESS,
            space_version=global_space.version,
        )
        db.add(projection)
        db.commit()
        projection_id = projection.projection_id
        project_id = frame.project_id
        _emit("Knowledge graph extraction started", stage="setup")

    agent = build_knowledge_graph_agent(model)
    runner = AgentRunner(agent=agent, app_name=APP_NAME)
    session_id = f"kg_{projection_id}"
    await runner.create_session(session_id)

    message = (
        f"Build a concept-only knowledge graph for frame {frame_id} "
        f"(project {project_id}) in projection {projection_id}. "
        "Use global graph snapshot tools to avoid redundant concepts and relations. "
        "Store details as references, not extra nodes."
    )

    result = await runner.run(
        session_id=session_id,
        message=message,
        verbose=verbose,
        progress_callback=progress_callback,
    )
    if not result.success:
        with SyncSessionLocal() as db:
            proj = db.query(Projection).filter_by(projection_id=projection_id).first()
            if proj:
                proj.status = ProjectionStatus.FAILED
                proj.agent_notes = result.error
                db.commit()
        return {
            "status": "error",
            "projection_id": str(projection_id),
            "message": result.error,
        }

    with SyncSessionLocal() as db:
        proj = db.query(Projection).filter_by(projection_id=projection_id).first()
        status = proj.status.value if proj else "unknown"

    return {
        "status": "completed" if status == "COMPLETED" else status,
        "projection_id": str(projection_id),
        "space_id": str(global_space.space_id),
        "frame_id": str(frame_id),
        "project_id": str(project_id),
        "agent_summary": result.final_text,
    }


@sync_agent_run
async def run_knowledge_graph(
    frame_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
    clear_existing: bool = True,
    progress_callback=None,
) -> dict:
    """Run knowledge graph extraction for a single frame."""
    return await _run_knowledge_graph_async(
        frame_id,
        model,
        verbose,
        clear_existing,
        progress_callback=progress_callback,
    )


@sync_agent_run
async def run_knowledge_graph_all(
    model: str | None = None,
    verbose: bool = False,
    clear_existing: bool = True,
) -> dict:
    """Run knowledge graph extraction for all completed frames."""
    with SyncSessionLocal() as db:
        frame_ids = [
            row.frame_id
            for row in db.query(KnowledgeFrame).filter_by(status=FrameStatus.COMPLETED).all()
        ]

    results = []
    for fid in frame_ids:
        logger.info("Extracting concept graph for frame %s ...", fid)
        result = await _run_knowledge_graph_async(
            fid,
            model=model,
            verbose=verbose,
            clear_existing=clear_existing,
        )
        results.append(result)
        logger.info("  -> %s", result.get("status", "unknown"))

    return {
        "total_frames": len(results),
        "completed": sum(1 for r in results if r.get("status") == "completed"),
        "failed": sum(1 for r in results if r.get("status") == "error"),
        "results": results,
    }
