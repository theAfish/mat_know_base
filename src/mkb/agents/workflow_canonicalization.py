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
from mkb.workflows.schema_library import CANONICALIZER_VERSION, get_schema_library_payload

APP_NAME = "mkb_workflow_canonicalization"


def canonicalization_call_budget(raw_node_count: int, raw_edge_count: int) -> int:
    """Scale the ADK call budget for large graphs while retaining a hard cap."""
    return min(240, max(80, 40 + raw_node_count * 3 + raw_edge_count))


def build_workflow_canonicalizer(model: str | None = None) -> Agent:
    return Agent(
        name="workflow_canonicalizer", model=create_llm(model),
        instruction=WORKFLOW_CANONICALIZER_PROMPT, tools=CANONICALIZATION_TOOLS,
    )


async def _run_async(project_id: uuid.UUID, raw_extraction_id: uuid.UUID | None, model: str | None, verbose: bool, progress_callback=None, recanonicalization_reason: str | None = None, target_schema_version: str | None = None) -> dict:
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
        row = None if recanonicalization_reason else (
            db.query(CanonicalWorkflow)
            .filter(
                CanonicalWorkflow.project_id == project_id,
                CanonicalWorkflow.raw_extraction_id == raw.extraction_id,
                CanonicalWorkflow.graph.is_(None),
                CanonicalWorkflow.status.in_(("IN_PROGRESS", "FAILED")),
            )
            .order_by(CanonicalWorkflow.version.desc())
            .first()
        )
        resumed = row is not None
        if row is None:
            version = int(
                db.query(func.coalesce(func.max(CanonicalWorkflow.version), 0))
                .filter(CanonicalWorkflow.project_id == project_id).scalar()
            ) + 1
            cid = uuid.uuid4()
            row = CanonicalWorkflow(
                canonicalization_id=cid, project_id=project_id,
                raw_extraction_id=raw.extraction_id, version=version,
                schema_version=get_schema_library_payload(target_schema_version)["schema_version"],
                canonicalizer_version=CANONICALIZER_VERSION, model=model,
                provenance={
                    "raw_extraction_id": str(raw.extraction_id),
                    "raw_version": raw.version,
                    "resume_count": 0,
                    **({"recanonicalization_reason": recanonicalization_reason} if recanonicalization_reason else {}),
                },
            )
            db.add(row)
        else:
            cid = row.canonicalization_id
            version = row.version
            row.status = "IN_PROGRESS"
            row.error = None
            row.model = model
            row.provenance = {
                **(row.provenance or {}),
                "raw_extraction_id": str(raw.extraction_id),
                "raw_version": raw.version,
                "resume_count": int((row.provenance or {}).get("resume_count", 0)) + 1,
                "last_resumed_at": datetime.now(timezone.utc).isoformat(),
            }
        db.commit()
        checkpoint = row.checkpoint or {}
        raw_node_count = len((raw.graph or {}).get("nodes", []))
        raw_edge_count = len((raw.graph or {}).get("edges", []))
    if progress_callback:
        progress_callback({
            "message": "Workflow canonicalization resumed" if resumed else "Workflow canonicalization started",
            "stage": "setup",
        })

    max_llm_calls = canonicalization_call_budget(raw_node_count, raw_edge_count)
    runner = AgentRunner(
        agent=build_workflow_canonicalizer(model),
        app_name=APP_NAME,
        max_llm_calls=max_llm_calls,
    )
    await runner.create_session(f"canonical_{cid}")
    checkpoint_summary = str(checkpoint.get("summary") or "").strip()
    resume_message = (
        "Resume the existing unfinished canonicalization version. "
        "Call get_canonical_workflow_checkpoint first to inspect the saved checkpoint before continuing."
        if resumed
        else "Start a new canonicalization version."
    )
    if checkpoint_summary:
        resume_message += f" Latest checkpoint summary: {checkpoint_summary[:1200]}"
    result = await runner.run(
        session_id=f"canonical_{cid}",
        message=(
            f"Canonicalize workflow using canonicalization_id {cid}. "
            f"Work from raw_extraction_id {raw.extraction_id} and save version {version}. "
            f"Your call budget is {max_llm_calls}; use batch draft tools to stay efficient. "
            f"{resume_message}"
        ),
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
async def run_workflow_canonicalization(project_id: uuid.UUID, raw_extraction_id: uuid.UUID | None = None, model: str | None = None, verbose: bool = False, progress_callback=None, recanonicalization_reason: str | None = None, target_schema_version: str | None = None) -> dict:
    return await _run_async(project_id, raw_extraction_id, model, verbose, progress_callback, recanonicalization_reason, target_schema_version)
