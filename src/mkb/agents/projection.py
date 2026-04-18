"""
Projection agent — extracts structured data from knowledge frames
according to a Space definition.

Unlike the KB extraction agent which reads raw files, the projection
agent reads knowledge frame content and produces structured output
per a domain-specific schema.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm

from mkb.agents.prompts.projection import build_projection_prompt
from mkb.agents.runner import AgentRunner
from mkb.agents.tools.projection import PROJECTION_TOOLS
from mkb.config import settings
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


def build_projection_agent(space: Space, model: str | None = None) -> Agent:
    """Create a projection agent configured for a specific space."""
    if settings.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    if settings.openai_api_base:
        os.environ.setdefault("OPENAI_API_BASE", settings.openai_api_base)

    prompt = build_projection_prompt(
        domain=space.domain,
        system_prompt=space.system_prompt,
        extraction_schema=space.extraction_schema,
        field_descriptions=space.field_descriptions,
    )

    llm = LiteLlm(model=model or settings.extraction_model)
    return Agent(
        name="projection_agent",
        model=llm,
        instruction=prompt,
        tools=PROJECTION_TOOLS,
    )


async def _run_projection_async(
    space_id: uuid.UUID,
    frame_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
) -> dict:
    """Run projection on a single frame using a space definition."""

    with SyncSessionLocal() as db:
        space = db.query(Space).filter_by(space_id=space_id).first()
        if not space:
            return {"status": "error", "message": f"Space {space_id} not found"}

        frame = db.query(KnowledgeFrame).filter_by(frame_id=frame_id).first()
        if not frame:
            return {"status": "error", "message": f"Frame {frame_id} not found"}
        if frame.status != FrameStatus.COMPLETED:
            return {"status": "error", "message": f"Frame is not completed (status: {frame.status.value})"}

        # Create or get projection record
        projection = db.query(Projection).filter_by(
            space_id=space_id, frame_id=frame_id
        ).first()
        if not projection:
            projection = Projection(
                projection_id=uuid.uuid4(),
                space_id=space_id,
                frame_id=frame_id,
                status=ProjectionStatus.IN_PROGRESS,
                space_version=space.version,
            )
            db.add(projection)
        else:
            projection.status = ProjectionStatus.IN_PROGRESS
            projection.space_version = space.version
        db.commit()

        projection_id = projection.projection_id
        space_name = space.name

        # Need to access space attributes before session closes
        space_domain = space.domain
        space_system_prompt = space.system_prompt
        space_extraction_schema = space.extraction_schema
        space_field_descriptions = space.field_descriptions

    # Build agent with space-specific prompt
    # Reconstruct a minimal space-like object for the agent builder
    class SpaceProxy:
        pass
    sp = SpaceProxy()
    sp.domain = space_domain
    sp.system_prompt = space_system_prompt
    sp.extraction_schema = space_extraction_schema
    sp.field_descriptions = space_field_descriptions

    agent = build_projection_agent(sp, model)
    runner = AgentRunner(agent=agent, app_name=APP_NAME)

    session_id = f"projection_{projection_id}"
    await runner.create_session(session_id)

    message = (
        f"Extract structured data from frame {frame_id} "
        f"using the '{space_name}' space schema. "
        f"The projection ID is {projection_id}. "
        f"Start by reading the frame content, then extract "
        f"data according to the schema."
    )

    result = await runner.run(
        session_id=session_id,
        message=message,
        verbose=verbose,
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

    # Check result
    with SyncSessionLocal() as db:
        proj = db.query(Projection).filter_by(projection_id=projection_id).first()
        status = proj.status.value if proj else "unknown"

    return {
        "status": "completed" if status == "COMPLETED" else status,
        "projection_id": str(projection_id),
        "space_name": space_name,
        "frame_id": str(frame_id),
        "agent_summary": result.final_text,
    }


def run_projection(
    space_id: uuid.UUID,
    frame_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
) -> dict:
    """Synchronous wrapper — run projection on one frame."""
    return asyncio.run(_run_projection_async(space_id, frame_id, model, verbose))


def run_projection_all(
    space_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
) -> dict:
    """Run projection on all completed frames that don't have a projection for this space."""
    sid = space_id

    with SyncSessionLocal() as db:
        space = db.query(Space).filter_by(space_id=sid).first()
        if not space:
            return {"status": "error", "message": f"Space {sid} not found"}

        # Find completed frames without a projection for this space
        existing_frame_ids = [
            p.frame_id for p in
            db.query(Projection.frame_id).filter_by(space_id=sid).all()
        ]
        q = db.query(KnowledgeFrame).filter_by(status=FrameStatus.COMPLETED)
        if existing_frame_ids:
            q = q.filter(~KnowledgeFrame.frame_id.in_(existing_frame_ids))
        frames = q.all()
        frame_ids = [f.frame_id for f in frames]

    results = []
    for fid in frame_ids:
        logger.info("Projecting frame %s with space %s ...", fid, sid)
        result = run_projection(sid, fid, model=model, verbose=verbose)
        results.append(result)
        logger.info("  → %s", result.get("status", "unknown"))

    return {
        "total_frames": len(results),
        "completed": sum(1 for r in results if r["status"] == "completed"),
        "failed": sum(1 for r in results if r["status"] == "error"),
        "results": results,
    }
