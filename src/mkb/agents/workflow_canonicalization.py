"""Phase-2 raw-to-canonical workflow agent."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from google.adk.agents import Agent
from sqlalchemy import func

from mkb.agents._utils import create_llm, sync_agent_run
from mkb.agents.prompts.workflow_canonicalization import WORKFLOW_CANONICALIZER_PROMPT
from mkb.agents.runner import AgentRunner
from mkb.agents.tools.workflow_canonicalization import CANONICALIZATION_TOOLS
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import CanonicalWorkflow, RawWorkflowExtraction, ResearchProject
from mkb.workflows.schema_library import CANONICALIZER_VERSION, CANONICAL_SCHEMA_VERSION

APP_NAME = "mkb_workflow_canonicalization"


def build_workflow_canonicalizer(model: str | None = None) -> Agent:
    return Agent(
        name="workflow_canonicalizer", model=create_llm(model),
        instruction=WORKFLOW_CANONICALIZER_PROMPT, tools=CANONICALIZATION_TOOLS,
    )


async def _run_async(project_id: uuid.UUID, raw_extraction_id: uuid.UUID | None, model: str | None, verbose: bool, progress_callback=None) -> dict:
    if model is None:
        from mkb.runtime_settings import get_setting

        model = get_setting("extraction_model")
    with SyncSessionLocal() as db:
        if not db.query(ResearchProject).filter_by(project_id=project_id).first():
            return {"status": "error", "message": "Project not found"}
        raw_query = db.query(RawWorkflowExtraction).filter(
            RawWorkflowExtraction.project_id == project_id,
            RawWorkflowExtraction.status == "COMPLETED",
            RawWorkflowExtraction.record_status.in_(["active", "needs_review"]),
        )
        raw = (
            raw_query.filter(RawWorkflowExtraction.extraction_id == raw_extraction_id).first()
            if raw_extraction_id else raw_query.order_by(RawWorkflowExtraction.version.desc()).first()
        )
        if not raw:
            return {"status": "error", "message": "No valid completed raw workflow to canonicalize"}
        version = int(
            db.query(func.coalesce(func.max(CanonicalWorkflow.version), 0))
            .filter(CanonicalWorkflow.project_id == project_id).scalar()
        ) + 1
        cid = uuid.uuid4()
        db.add(CanonicalWorkflow(
            canonicalization_id=cid, project_id=project_id,
            raw_extraction_id=raw.extraction_id, version=version,
            schema_version=CANONICAL_SCHEMA_VERSION,
            canonicalizer_version=CANONICALIZER_VERSION, model=model,
            provenance={"raw_extraction_id": str(raw.extraction_id), "raw_version": raw.version},
        ))
        db.commit()
    if progress_callback:
        progress_callback({"message": "Workflow canonicalization started", "stage": "setup"})

    runner = AgentRunner(agent=build_workflow_canonicalizer(model), app_name=APP_NAME)
    await runner.create_session(f"canonical_{cid}")
    result = await runner.run(
        session_id=f"canonical_{cid}",
        message=f"Canonicalize workflow using canonicalization_id {cid}. Load its context and save the result.",
        verbose=verbose, progress_callback=progress_callback,
    )
    with SyncSessionLocal() as db:
        row = db.query(CanonicalWorkflow).filter_by(canonicalization_id=cid).first()
        if row and row.status == "COMPLETED":
            return {"status": "completed", "canonicalization_id": str(cid), "version": version}
        if row:
            row.status = "FAILED"
            row.error = result.error or "Agent finished without saving"
            row.canonicalized_at = datetime.now(timezone.utc)
            db.commit()
        return {"status": "error", "canonicalization_id": str(cid), "message": result.error or "No canonical graph saved"}


@sync_agent_run
async def run_workflow_canonicalization(project_id: uuid.UUID, raw_extraction_id: uuid.UUID | None = None, model: str | None = None, verbose: bool = False, progress_callback=None) -> dict:
    return await _run_async(project_id, raw_extraction_id, model, verbose, progress_callback)
