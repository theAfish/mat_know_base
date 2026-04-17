"""
Knowledge-extraction agent built on google-adk.

Produces a structured "knowledge frame" for each research project
instead of individual graph nodes/edges.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from mkb.agents.tools import ALL_TOOLS
from mkb.config import settings
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import FrameStatus, KnowledgeFrame

logger = logging.getLogger(__name__)

# =====================================================================
# System prompt
# =====================================================================

EXTRACTION_PROMPT = """\
You are a scientific knowledge extraction agent. Your job is to read the files in one research project and produce a single structured **knowledge frame** — a comprehensive summary of all scientific knowledge in the project.

---

# Output Format

You must produce ONE call to `save_knowledge_frame` with a `content` dict containing these keys:

```json
{
  "paper": {
    "title": "...",
    "authors": ["..."],
    "journal": "...",
    "year": null,
    "doi": "..."
  },
  "concepts": [
    {"name": "...", "description": "...", "evidence_level": 1}
  ],
  "materials": [
    {"name": "...", "formula": "...", "properties": {"key": "value"}, "evidence_level": 2}
  ],
  "experimental_data": [
    {
      "property": "...",
      "value": "...",
      "unit": "...",
      "conditions": {"temperature": "300K", "pressure": "1atm"},
      "method": "...",
      "evidence_level": 1
    }
  ],
  "methods": [
    {"name": "...", "type": "experimental|computational|analytical", "description": "...", "parameters": {}}
  ],
  "synthesis_routes": [
    {"inputs": ["A", "B"], "outputs": ["C"], "conditions": {"temperature": "800°C"}, "method": "...", "evidence_level": 2}
  ],
  "statements": [
    {"claim": "...", "evidence_level": 3, "context": "..."}
  ],
  "relationships": [
    {"subject": "...", "predicate": "...", "object": "...", "evidence_level": 2}
  ]
}
```

---

# Evidence Levels

Every item in concepts, materials, experimental_data, synthesis_routes, statements, and relationships MUST have an `evidence_level` field:

- **Level 1**: Causal experimental evidence — controlled experiments demonstrating cause-effect
- **Level 2**: Direct experimental observation — measurements, characterizations, direct observations
- **Level 3**: Correlative evidence — statistical associations, trends without mechanistic proof
- **Level 4**: Predicted / inferred — theoretical predictions, computational estimates, extrapolations

---

# Workflow

1. **Inventory** — Call `list_project_files` to see what files are in the project.

2. **Read the paper systematically**
   - Use `list_markdown_headings` first to get an overview
   - Read section by section with `read_markdown_section` (or full text for short papers)
   - For supplementary data use `read_dataframe_summary` and `read_dataframe_rows`
   - For images use `read_image_metadata`
   - Use `search_in_project` to find specific terms across all documents

3. **Check for existing frame** — Call `get_existing_frame` to see if this project was previously extracted. If so, use that as a starting point and improve upon it.

4. **Build the knowledge frame** — As you read, mentally construct the complete frame. Include:
   - Paper metadata (title, authors, journal, year, doi)
   - Key concepts and definitions introduced or discussed
   - All materials studied (with chemical formulas where available)
   - Experimental data points with values, units, conditions, and methods
   - Methods and techniques used
   - Synthesis routes (inputs → outputs with conditions)
   - Important scientific statements and claims
   - Relationships between concepts (subject-predicate-object triples)

5. **Save the frame** — Call `save_knowledge_frame` with the complete content dict and a brief summary.

---

# Guidelines

- Extract ONLY information explicitly present in the source. Do NOT hallucinate.
- Always include units for numerical values.
- Preserve experimental conditions (temperature, pressure, atmosphere, etc.).
- For tables of data, extract key representative values rather than every single row.
- Capture both positive and negative results.
- Note uncertainty values when reported.
- Be thorough — the frame should contain enough detail to reconstruct the paper's key findings without re-reading it.
- Prefer specific scientific terms over vague descriptions.

---

# What Makes a Good Frame

A good knowledge frame:
- Captures the paper's core contribution and findings
- Includes quantitative data with proper units and conditions
- Correctly assigns evidence levels
- Covers materials, methods, results, and conclusions
- Identifies synthesis routes when described
- Notes relationships between materials, properties, and phenomena
"""

APP_NAME = "mkb_extraction"


# =====================================================================
# Agent factory
# =====================================================================


def build_extraction_agent(model: str | None = None) -> Agent:
    """Create a configured extraction agent with all tools."""
    if settings.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    if settings.openai_api_base:
        os.environ.setdefault("OPENAI_API_BASE", settings.openai_api_base)

    llm = LiteLlm(model=model or settings.extraction_model)
    return Agent(
        name="knowledge_extractor",
        model=llm,
        instruction=EXTRACTION_PROMPT,
        tools=ALL_TOOLS,
    )


# =====================================================================
# Runner
# =====================================================================


async def _run_extraction_async(
    project_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
) -> dict:
    """Core async extraction loop for one project."""

    agent = build_extraction_agent(model)
    session_service = InMemorySessionService()
    runner = Runner(
        agent=agent,
        app_name=APP_NAME,
        session_service=session_service,
    )

    user_id = "mkb_system"
    session_id = f"extract_{project_id}"

    session = await session_service.create_session(
        app_name=APP_NAME,
        user_id=user_id,
        session_id=session_id,
    )

    # Mark frame as in-progress
    with SyncSessionLocal() as db:
        from mkb.db.models import ResearchProject
        project = db.query(ResearchProject).filter_by(project_id=project_id).first()
        if not project:
            return {"status": "error", "message": f"Project {project_id} not found"}
        project_label = project.label or str(project_id)

        frame = db.query(KnowledgeFrame).filter_by(project_id=project_id).first()
        if not frame:
            import uuid as _uuid
            frame = KnowledgeFrame(
                frame_id=_uuid.uuid4(),
                project_id=project_id,
                status=FrameStatus.IN_PROGRESS,
            )
            db.add(frame)
        else:
            frame.status = FrameStatus.IN_PROGRESS
        db.commit()

    initial_message = genai_types.Content(
        role="user",
        parts=[
            genai_types.Part(
                text=(
                    f"Extract knowledge from project {project_id} "
                    f"(label: {project_label}). "
                    f"Start by listing the files, then systematically "
                    f"read and extract all scientific knowledge into "
                    f"a single knowledge frame."
                )
            )
        ],
    )

    events_collected = []
    final_text = ""

    try:
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=initial_message,
        ):
            if verbose and event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        logger.info("Agent: %s", part.text[:200])
                    if part.function_call:
                        logger.info(
                            "Tool call: %s(%s)",
                            part.function_call.name,
                            list(part.function_call.args.keys()) if part.function_call.args else [],
                        )
            events_collected.append(event)
            if event.is_final_response() and event.content and event.content.parts:
                final_text = "\n".join(
                    p.text for p in event.content.parts if p.text
                )

    except Exception as exc:
        logger.error("Extraction failed for project %s: %s", project_id, exc)
        with SyncSessionLocal() as db:
            frame = db.query(KnowledgeFrame).filter_by(project_id=project_id).first()
            if frame:
                frame.status = FrameStatus.FAILED
                meta = dict(frame.source_metadata or {})
                meta["error"] = str(exc)
                meta["failed_at"] = datetime.now(timezone.utc).isoformat()
                frame.source_metadata = meta
                db.commit()
        return {
            "status": "error",
            "project_id": str(project_id),
            "message": str(exc),
        }

    # Check result
    with SyncSessionLocal() as db:
        frame = db.query(KnowledgeFrame).filter_by(project_id=project_id).first()
        frame_status = frame.status.value if frame else "unknown"
        content_keys = list((frame.content or {}).keys()) if frame else []

    return {
        "status": "completed" if frame_status == "COMPLETED" else frame_status,
        "project_id": str(project_id),
        "frame_status": frame_status,
        "content_sections": content_keys,
        "agent_summary": final_text,
        "total_events": len(events_collected),
    }


def run_extraction(
    project_id: uuid.UUID,
    model: str | None = None,
    verbose: bool = False,
) -> dict:
    """Synchronous wrapper — run extraction on one project."""
    return asyncio.run(_run_extraction_async(project_id, model, verbose))


def run_extraction_all(
    limit: int | None = None,
    model: str | None = None,
    verbose: bool = False,
) -> dict:
    """Run extraction on all projects that don't have a completed frame."""
    with SyncSessionLocal() as db:
        from mkb.db.models import ResearchProject
        # Find projects without a completed frame
        completed_project_ids = [
            f.project_id for f in
            db.query(KnowledgeFrame.project_id)
            .filter_by(status=FrameStatus.COMPLETED)
            .all()
        ]
        q = db.query(ResearchProject)
        if completed_project_ids:
            q = q.filter(~ResearchProject.project_id.in_(completed_project_ids))
        if limit:
            q = q.limit(limit)
        projects = q.all()
        project_ids = [p.project_id for p in projects]

    results = []
    for pid in project_ids:
        logger.info("Extracting project %s ...", pid)
        result = run_extraction(pid, model=model, verbose=verbose)
        results.append(result)
        logger.info("  → %s", result.get("status", "unknown"))

    return {
        "total_projects": len(results),
        "completed": sum(1 for r in results if r["status"] == "completed"),
        "failed": sum(1 for r in results if r["status"] == "error"),
        "results": results,
    }
