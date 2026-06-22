"""Phase-1 faithful, append-only paper workflow extractor."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from google.adk.agents import Agent
from sqlalchemy import func

from mkb.agents._utils import create_llm, sync_agent_run
from mkb.agents.prompts.workflow_extraction import WORKFLOW_EXTRACTOR_PROMPT
from mkb.agents.runner import AgentRunner
from mkb.agents.tools.reading import READING_TOOLS
from mkb.agents.tools.workflows import WORKFLOW_EXTRACTION_TOOLS
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import ProjectAsset, RawWorkflowExtraction, ResearchProject
from mkb.workflows.contract import EXTRACTOR_VERSION, RAW_WORKFLOW_SCHEMA_VERSION

logger = logging.getLogger(__name__)
APP_NAME = "mkb_raw_workflow"


def build_workflow_extractor(model: str | None = None) -> Agent:
    return Agent(
        name="raw_workflow_extractor",
        model=create_llm(model),
        instruction=WORKFLOW_EXTRACTOR_PROMPT,
        tools=READING_TOOLS + WORKFLOW_EXTRACTION_TOOLS,
    )


async def _run_async(project_id: uuid.UUID, model: str | None, verbose: bool, progress_callback=None) -> dict:
    def emit(message: str, **extra) -> None:
        if progress_callback:
            progress_callback({"message": message, **extra})

    if model is None:
        from mkb.runtime_settings import get_setting

        model = get_setting("extraction_model")

    with SyncSessionLocal() as db:
        project = db.query(ResearchProject).filter_by(project_id=project_id).first()
        if not project:
            return {"status": "error", "message": f"Project {project_id} not found"}
        next_version = int(
            db.query(func.coalesce(func.max(RawWorkflowExtraction.version), 0))
            .filter(RawWorkflowExtraction.project_id == project_id).scalar()
        ) + 1
        eid = uuid.uuid4()
        asset_ids = [str(row.asset_id) for row in db.query(ProjectAsset).filter_by(project_id=project_id).all()]
        row = RawWorkflowExtraction(
            extraction_id=eid,
            project_id=project_id,
            version=next_version,
            schema_version=RAW_WORKFLOW_SCHEMA_VERSION,
            extractor_version=EXTRACTOR_VERSION,
            model=model,
            status="IN_PROGRESS",
            provenance={
                "paper_id": str(project_id),
                "available_asset_ids": asset_ids,
                "prompt_version": EXTRACTOR_VERSION,
            },
        )
        db.add(row)
        db.commit()
    emit("Raw workflow extraction started", stage="setup")

    runner = AgentRunner(agent=build_workflow_extractor(model), app_name=APP_NAME)
    session_id = f"raw_workflow_{eid}"
    await runner.create_session(session_id)
    result = await runner.run(
        session_id=session_id,
        message=(
            f"Extract the raw workflow for paper_id/project_id {project_id}. "
            f"The append-only extraction_id is {eid}. Read project {project_id} and save version {next_version}."
        ),
        verbose=verbose,
        progress_callback=progress_callback,
    )

    with SyncSessionLocal() as db:
        row = db.query(RawWorkflowExtraction).filter_by(extraction_id=eid).first()
        if row and row.status == "COMPLETED":
            return {
                "status": "completed", "extraction_id": str(eid),
                "project_id": str(project_id), "version": next_version,
            }
        if row:
            row.status = "FAILED"
            row.error = result.error or "Agent finished without saving a graph"
            row.extracted_at = datetime.now(timezone.utc)
            db.commit()
        return {"status": "error", "extraction_id": str(eid), "message": result.error or "No graph was saved"}


@sync_agent_run
async def run_workflow_extraction(project_id: uuid.UUID, model: str | None = None, verbose: bool = False, progress_callback=None) -> dict:
    return await _run_async(project_id, model, verbose, progress_callback)
