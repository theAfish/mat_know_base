"""Graphs API service functions."""

from __future__ import annotations

from mkb.services._api_common import (
    SyncSessionLocal,
    init_db,
    uuid,
)


def clear_knowledge_graphs(
    project_id: str | uuid.UUID | None = None,
    remove_legacy_frame_sections: bool = True,
) -> dict:
    """Delete (soft-delete) old KG projections and optionally purge legacy frame graph sections."""
    from mkb.knowledge_graph import clear_knowledge_graph_projections, purge_legacy_graph_sections

    init_db()
    pid = uuid.UUID(str(project_id)) if project_id else None
    deleted = clear_knowledge_graph_projections(project_id=pid, include_legacy_spaces=True)

    purged = {"updated_frames": 0, "project_id": str(pid) if pid else None}
    if remove_legacy_frame_sections:
        purged = purge_legacy_graph_sections(project_id=pid)

    return {
        "deleted_projections": deleted,
        "purged_legacy_frame_sections": purged,
    }

def extract_knowledge_graph(
    project_id: str | uuid.UUID | None = None,
    frame_id: str | uuid.UUID | None = None,
    model: str | None = None,
    verbose: bool = False,
    clear_existing: bool = True,
    clear_legacy_frame_sections: bool = True,
    progress_callback=None,
) -> dict:
    """Run concept-graph extraction using one global cross-domain space.

    If frame_id is provided, extract for that frame.
    If project_id is provided, resolve that project's frame and extract.
    Otherwise run for all completed frames.
    """
    from mkb.agents.knowledge_graph import run_knowledge_graph, run_knowledge_graph_all
    from mkb.db.models import KnowledgeFrame
    from mkb.knowledge_graph import ensure_global_kg_space_id, purge_legacy_graph_sections

    init_db()

    if clear_legacy_frame_sections:
        if project_id:
            purge_legacy_graph_sections(project_id=uuid.UUID(str(project_id)))
        else:
            purge_legacy_graph_sections()

    if frame_id is not None:
        fid = uuid.UUID(str(frame_id))
        result = run_knowledge_graph(
            fid,
            model=model,
            verbose=verbose,
            clear_existing=clear_existing,
            progress_callback=progress_callback,
        )
    elif project_id is not None:
        pid = uuid.UUID(str(project_id))
        with SyncSessionLocal() as session:
            frame = session.query(KnowledgeFrame).filter_by(project_id=pid).first()
            if not frame:
                return {"error": f"No frame for project {project_id}"}
            fid = frame.frame_id
        result = run_knowledge_graph(
            fid,
            model=model,
            verbose=verbose,
            clear_existing=clear_existing,
            progress_callback=progress_callback,
        )
    else:
        result = run_knowledge_graph_all(model=model, verbose=verbose, clear_existing=clear_existing)

    return {
        "global_space_id": str(ensure_global_kg_space_id()),
        **result,
    }

def get_knowledge_graph(
    project_id: str | uuid.UUID | None = None,
) -> dict:
    """Get the merged concept graph from the singleton global KG space."""
    from mkb.agents.tools.knowledge_graph import normalize_knowledge_graph_payload
    from mkb.db.models import KnowledgeFrame, Projection, ProjectionStatus
    from mkb.knowledge_graph import ensure_global_kg_space_id

    init_db()
    sid = ensure_global_kg_space_id()

    with SyncSessionLocal() as session:
        q = (
            session.query(Projection)
            .filter(Projection.space_id == sid)
            .filter(Projection.deleted_at.is_(None))
            .filter(Projection.status.in_([ProjectionStatus.COMPLETED, ProjectionStatus.REVIEWED]))
        )
        if project_id:
            pid = uuid.UUID(str(project_id))
            q = q.join(KnowledgeFrame, Projection.frame_id == KnowledgeFrame.frame_id)
            q = q.filter(KnowledgeFrame.project_id == pid)
        rows = q.all()

    aggregate = {"concepts": [], "relations": []}
    for row in rows:
        payload, _ = normalize_knowledge_graph_payload(row.data or {})
        aggregate["concepts"].extend(payload["concepts"])
        aggregate["relations"].extend(payload["relations"])

    merged, validation = normalize_knowledge_graph_payload(aggregate)
    return {
        "space_id": str(sid),
        "projection_count": len(rows),
        "graph": merged,
        "validation": validation,
        "project_id": str(project_id) if project_id else None,
    }


# ── Feedback ─────────────────────────────────────────────────────

def review_knowledge_graph(
    mode: str = "auto",
    model: str | None = None,
    verbose: bool = False,
    seed_count: int = 10,
    progress_callback=None,
) -> dict:
    """Run the graph review agent to deduplicate and clean the knowledge graph.

    Two modes:
    - "global": analyzes relation name distributions, standardizes naming, merges
      duplicate or synonymous concept nodes across the entire graph.
    - "local": selects the least-reviewed concepts as starting points, explores
      their neighborhoods, verifies against source frames, and fixes local issues.
    - "auto" (default): randomly picks global or local each time.

    After each run, the times_examined and times_modified counters on each visited
    graph element are incremented in the graph_element_reviews table.

    Args:
        mode: "global", "local", or "auto".
        model: LLM model override.
        verbose: Enable verbose logging.
        seed_count: Number of starting concepts for local mode.
    """
    from mkb.agents.graph_review import run_graph_review

    init_db()
    return run_graph_review(mode=mode, model=model, verbose=verbose, seed_count=seed_count, progress_callback=progress_callback)

def get_graph_review_counts(space_id: str | uuid.UUID | None = None) -> dict:
    """Return review counts (times_examined, times_modified) per graph element.

    Returns a dict with two sub-dicts keyed by normalized element key:
    - ``concepts``: mapping of normalized concept label → {times_examined, times_modified}
    - ``relations``: mapping of "src||rel||tgt" → {times_examined, times_modified}
    """
    from mkb.db.models import GraphElementReview
    from mkb.knowledge_graph import ensure_global_kg_space_id

    init_db()
    sid = uuid.UUID(str(space_id)) if space_id else ensure_global_kg_space_id()

    concepts: dict[str, dict] = {}
    relations: dict[str, dict] = {}

    with SyncSessionLocal() as session:
        rows = session.query(GraphElementReview).filter_by(space_id=sid).all()
        for row in rows:
            entry = {
                "times_examined": row.times_examined,
                "times_modified": row.times_modified,
                "last_examined_at": row.last_examined_at.isoformat() if row.last_examined_at else None,
                "last_modified_at": row.last_modified_at.isoformat() if row.last_modified_at else None,
            }
            if row.element_type == "concept":
                concepts[row.element_key] = entry
            elif row.element_type == "relation":
                relations[row.element_key] = entry

    return {"space_id": str(sid), "concepts": concepts, "relations": relations}

