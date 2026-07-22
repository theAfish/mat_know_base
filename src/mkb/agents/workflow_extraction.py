"""Phase-1 faithful, append-only paper workflow extractor."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from google.adk.agents import Agent
from sqlalchemy import func

from mkb.agents._utils import create_llm, run_async_sync
from mkb.agents.prompts.workflow_extraction import WORKFLOW_EXTRACTOR_PROMPT
from mkb.agents.runner import AgentRunner
from mkb.agents.runtime import AgentRuntime
from mkb.agents.tools.reading import reading_tools
from mkb.agents.tools.workflows import workflow_extraction_tools
from mkb.db.models import ProjectAsset, RawWorkflowExtraction, ResearchProject
from mkb.workflows.contract import EXTRACTOR_VERSION, RAW_WORKFLOW_SCHEMA_VERSION

logger = logging.getLogger(__name__)
APP_NAME = "mkb_raw_workflow"


def build_workflow_extractor(
    runtime: AgentRuntime,
    model: str | None = None,
) -> Agent:
    return Agent(
        name="raw_workflow_extractor",
        model=create_llm(model),
        instruction=WORKFLOW_EXTRACTOR_PROMPT,
        tools=reading_tools(runtime) + workflow_extraction_tools(runtime),
    )


async def _run_async(
    project_id: uuid.UUID,
    model: str | None,
    verbose: bool,
    progress_callback=None,
    reextraction_request: dict | None = None,
    runtime: AgentRuntime | None = None,
) -> dict:
    def emit(message: str, **extra) -> None:
        if progress_callback:
            progress_callback({"message": message, **extra})

    from mkb.runtime_settings import get_setting

    if model is None:
        model = get_setting("extraction_model")
    reextraction_metadata = (
        {key: value for key, value in reextraction_request.items() if key != "baseline_graph"}
        if reextraction_request else None
    )

    if runtime is None:
        raise ValueError("Workflow extraction requires an explicit AgentRuntime")
    with runtime.database.session() as db:
        project = db.query(ResearchProject).filter_by(project_id=project_id).first()
        if not project:
            return {"status": "error", "message": f"Project {project_id} not found"}

        asset_ids = [str(row.asset_id) for row in db.query(ProjectAsset).filter_by(project_id=project_id).all()]
        row = None if reextraction_request else (
            db.query(RawWorkflowExtraction)
            .filter(
                RawWorkflowExtraction.project_id == project_id,
                RawWorkflowExtraction.graph.is_(None),
                RawWorkflowExtraction.status.in_(("IN_PROGRESS", "FAILED")),
            )
            .order_by(RawWorkflowExtraction.version.desc())
            .first()
        )

        resumed = row is not None
        if row is None:
            next_version = int(
                db.query(func.coalesce(func.max(RawWorkflowExtraction.version), 0))
                .filter(RawWorkflowExtraction.project_id == project_id).scalar()
            ) + 1
            eid = uuid.uuid4()
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
                    "resume_count": 0,
                    **({"reextraction": reextraction_metadata} if reextraction_metadata else {}),
                },
            )
            if reextraction_request and reextraction_request.get("baseline_graph"):
                row.checkpoint = {
                    "summary": (
                        "Re-extraction baseline. Preserve valid information outside the requested "
                        "scope, re-check the requested scope, and assign all nodes/edges new IDs."
                    ),
                    "graph": reextraction_request["baseline_graph"],
                }
                row.checkpoint_updated_at = datetime.now(timezone.utc)
            db.add(row)
        else:
            eid = row.extraction_id
            next_version = row.version
            if model is None:
                model = row.model or get_setting("extraction_model")
            row.status = "IN_PROGRESS"
            row.error = None
            row.model = model
            row.provenance = {
                **(row.provenance or {}),
                "paper_id": str(project_id),
                "available_asset_ids": asset_ids,
                "prompt_version": EXTRACTOR_VERSION,
                "resume_count": int((row.provenance or {}).get("resume_count", 0)) + 1,
                "last_resumed_at": datetime.now(timezone.utc).isoformat(),
            }
        db.commit()
        checkpoint = row.checkpoint or {}

    emit(
        "Raw workflow extraction resumed" if resumed else "Raw workflow extraction started",
        stage="setup",
        extraction_id=str(eid),
        version=next_version,
    )

    runner = AgentRunner(agent=build_workflow_extractor(runtime, model), app_name=APP_NAME)
    session_id = f"raw_workflow_{eid}"
    await runner.create_session(session_id)
    checkpoint_summary = str(checkpoint.get("summary") or "").strip()
    resume_message = (
        "Resume the existing unfinished extraction version. "
        "Call get_raw_workflow_checkpoint first to inspect the saved checkpoint before continuing."
        if resumed
        else "Start a new extraction version."
    )
    if checkpoint_summary:
        resume_message += f" Latest checkpoint summary: {checkpoint_summary[:1200]}"
    if reextraction_request:
        scope = reextraction_request.get("scope", {"type": "full"})
        resume_message += (
            f" This is an approved re-extraction because {reextraction_request.get('reason')}. "
            f"Scope: {scope}. Inspect the baseline checkpoint, preserve supported content outside "
            "a partial scope, and produce a complete new immutable graph with new IDs."
        )
    result = await runner.run(
        session_id=session_id,
        message=(
            f"Extract the raw workflow for paper_id/project_id {project_id}. "
            f"The append-only extraction_id is {eid}. Read project {project_id} and save version {next_version}. "
            f"{resume_message}"
        ),
        verbose=verbose,
        progress_callback=progress_callback,
        max_retries=int(get_setting("agent_retry_count")),
    )

    with runtime.database.session() as db:
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


def run_workflow_extraction(
    project_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
    progress_callback=None,
    reextraction_request: dict | None = None,
    runtime: AgentRuntime | None = None,
) -> dict:
    return run_async_sync(
        _run_async(
            project_id,
            model,
            verbose,
            progress_callback,
            reextraction_request,
            runtime=runtime,
        )
    )
