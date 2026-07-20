"""Grounded context and persistence tools for the Schema Curator Agent."""

from __future__ import annotations

import uuid
from collections import Counter


from mkb.db.engine import SyncSessionLocal
from mkb.db.models import (
    CanonicalWorkflow, RawWorkflowExtraction, SchemaProposal,
    ResearchProject, WorkflowSchemaVersion,
)
from mkb.services.agent_tools.schema_curator_queries import (
    _as_induction_workflow,
    _card_workflow_evidence,
    _latest_reviewable_workflows,
    _normalize_text,
    _project_labels_by_id,
    _serialize_workflow_detail,
    _serialize_workflow_summary,
    _weighted_local_sample,
    _workflow_evidence,
    _workflow_search_text,
)
from mkb.workflows.curator import analyze_canonical_workflows
from mkb.workflows.schema_library import get_schema_library_payload
from mkb.services.agent_tools.curator_state import CURATOR_AUTHOR
from mkb.services.agent_tools.schema_curator_mutations import (
    revise_schema_proposal,
    submit_schema_proposal,
    submit_workflow_review,
)

SEARCHABLE_NODE_KINDS = {None, "", "object", "operation", "planning", "reasoning", "unknown"}


def set_curator_author(author: str):
    return CURATOR_AUTHOR.set(author)


def reset_curator_author(token) -> None:
    CURATOR_AUTHOR.reset(token)


def get_schema_curator_context(min_support: int = 2, max_workflows: int = 40) -> dict:
    """Load global schema, deterministic discovery signals, and bounded evidence."""
    min_support = max(1, int(min_support))
    max_workflows = min(80, max(1, int(max_workflows)))
    with SyncSessionLocal() as session:
        active = session.query(WorkflowSchemaVersion).filter_by(status="active").order_by(
            WorkflowSchemaVersion.version.desc()
        ).first()
        library = active.payload if active else get_schema_library_payload()
        requested_revisions = session.query(SchemaProposal).filter_by(
            status="revision_requested"
        ).order_by(SchemaProposal.created_at).all()
        rows = session.query(CanonicalWorkflow).filter_by(status="COMPLETED").order_by(
            CanonicalWorkflow.created_at.desc()
        ).limit(max_workflows).all()
        included_ids = {str(row.canonicalization_id) for row in rows}
        required_ids = {
            evidence_id
            for proposal in requested_revisions
            for evidence_id in (proposal.evidence_workflow_ids or [])
            if evidence_id not in included_ids
        }
        if required_ids:
            rows.extend(session.query(CanonicalWorkflow).filter(
                CanonicalWorkflow.status == "COMPLETED",
                CanonicalWorkflow.canonicalization_id.in_([
                    uuid.UUID(value) for value in required_ids
                ]),
            ).all())
        workflows = []
        evidence = []
        for row in rows:
            raw = session.query(RawWorkflowExtraction).filter_by(
                extraction_id=row.raw_extraction_id
            ).first()
            workflows.append({
                "canonicalization_id": str(row.canonicalization_id),
                "graph": row.graph,
                "raw_graph": raw.graph if raw else {},
            })
            evidence.append(_workflow_evidence(row, raw))
        # V2 graphs are already structured card instances. Include their latest
        # active versions directly and avoid requiring the removed per-paper
        # canonicalization stage. Old canonical workflows remain readable while
        # installations migrate.
        card_rows = (
            session.query(RawWorkflowExtraction)
            .filter(
                RawWorkflowExtraction.status == "COMPLETED",
                RawWorkflowExtraction.schema_version == "workflow-cards/2.0",
                RawWorkflowExtraction.record_status.in_(("active", "needs_review")),
            )
            .order_by(RawWorkflowExtraction.created_at.desc())
            .limit(max_workflows)
            .all()
        )
        card_workflows = []
        card_evidence = []
        for row in card_rows:
            card_workflows.append(_as_induction_workflow(row))
            card_evidence.append(_card_workflow_evidence(row))
        # Prefer the new direct evidence layer; fill any remaining context
        # budget with legacy canonicalizations.
        workflows = (card_workflows + workflows)[:max_workflows]
        evidence = (card_evidence + evidence)[:max_workflows]
        candidates = analyze_canonical_workflows(workflows, min_support=min_support)
        diagnostics = (
            candidates[0].get("analysis", {}).get("diagnostics", {})
            if candidates else {}
        )
        for candidate in candidates:
            candidate.get("analysis", {}).pop("diagnostics", None)
        return {
            "schema_library": library,
            "workflow_count": len(workflows),
            "min_support": min_support,
            "deterministic_candidates": candidates,
            "corpus_diagnostics": diagnostics,
            "workflow_evidence": evidence,
            "revision_requested_proposals": [{
                "proposal_id": str(proposal.proposal_id),
                "proposal_type": proposal.proposal_type,
                "payload": proposal.payload,
                "evidence_workflow_ids": proposal.evidence_workflow_ids,
                "rationale": proposal.rationale,
                "reviewer_notes": proposal.reviewer_notes,
                "base_schema_version": proposal.base_schema_version,
            } for proposal in requested_revisions],
            "instructions": (
                "Use only workflow_id/canonicalization_id values from workflow_evidence as evidence. "
                "Candidates are signals that require semantic review."
            ),
        }


def get_workflow_review_overview(
    mode: str = "global",
    sample_size: int = 8,
    min_support: int = 2,
) -> dict:
    """Return bounded, mode-specific context for the workflow review agent."""
    resolved_mode = str(mode or "global").strip().lower()
    if resolved_mode not in {"global", "local"}:
        return {"error": "mode must be 'local' or 'global'"}
    min_support = max(1, int(min_support))
    sample_size = max(1, min(int(sample_size), 30))
    with SyncSessionLocal() as session:
        latest_rows = _latest_reviewable_workflows(session)
        labels = _project_labels_by_id(session, [row.project_id for row in latest_rows])
        selected = (
            _weighted_local_sample(latest_rows, sample_size)
            if resolved_mode == "local"
            else latest_rows[: max(sample_size, 20)]
        )
        workflows = [_as_induction_workflow(row) for row in selected]
        deterministic = analyze_canonical_workflows(workflows, min_support=min_support)
        diagnostics = (
            deterministic[0].get("analysis", {}).get("diagnostics", {})
            if deterministic else {}
        )
        for candidate in deterministic:
            candidate.get("analysis", {}).pop("diagnostics", None)
        review_counts = Counter(
            int((row.provenance or {}).get("workflow_review_count", 0))
            for row in latest_rows
        )
        return {
            "mode": resolved_mode,
            "workflow_count": len(latest_rows),
            "selected_workflow_count": len(selected),
            "selected_workflows": [
                _serialize_workflow_summary(row, labels.get(row.project_id))
                for row in selected
            ],
            "revision_requested_proposals": [{
                "proposal_id": str(proposal.proposal_id),
                "proposal_type": proposal.proposal_type,
                "payload": proposal.payload,
                "evidence_workflow_ids": proposal.evidence_workflow_ids,
                "rationale": proposal.rationale,
                "reviewer_notes": proposal.reviewer_notes,
            } for proposal in session.query(SchemaProposal).filter_by(
                status="revision_requested"
            ).order_by(SchemaProposal.created_at).all()],
            "deterministic_candidates": deterministic[:20],
            "corpus_diagnostics": diagnostics,
            "sampling_policy": (
                "Local mode prefers newest workflows with lower workflow_review_count "
                "and needs_review status."
                if resolved_mode == "local"
                else "Global mode summarizes newest per-project workflows only."
            ),
            "review_count_histogram": dict(sorted(review_counts.items())),
        }


def search_existing_workflows(query: str = "", limit: int = 10) -> dict:
    """Search the newest available workflow for each project."""
    effective_limit = max(1, min(int(limit), 30))
    needle = _normalize_text(query)
    with SyncSessionLocal() as session:
        rows = _latest_reviewable_workflows(session)
        labels = _project_labels_by_id(session, [row.project_id for row in rows])
        matched = []
        for row in rows:
            label = labels.get(row.project_id)
            if needle and needle not in _workflow_search_text(row, label):
                continue
            matched.append(_serialize_workflow_summary(row, label))
        matched.sort(key=lambda item: (-int(item["unmapped_node_count"]), -int(item["node_count"]), item["workflow_id"]))
        return {
            "query": query,
            "results": matched[:effective_limit],
            "searched_latest_workflows_only": True,
        }


def get_existing_workflow(workflow_id: str) -> dict:
    """Retrieve one newest workflow by ID, with bounded but editable graph detail."""
    try:
        wid = uuid.UUID(str(workflow_id))
    except (TypeError, ValueError, AttributeError):
        return {"error": "workflow_id must be a UUID"}
    with SyncSessionLocal() as session:
        row = session.query(RawWorkflowExtraction).filter_by(extraction_id=wid).first()
        if not row or row.status != "COMPLETED":
            return {"error": "workflow not found"}
        label = session.query(ResearchProject.label).filter_by(project_id=row.project_id).scalar()
        return _serialize_workflow_detail(row, label)


def search_workflow_cards(query: str, node_kind: str | None = None, limit: int = 10) -> dict:
    """Search the newest shared workflow card base."""
    text = _normalize_text(query)
    if not text:
        return {"error": "query is required"}
    if node_kind not in SEARCHABLE_NODE_KINDS:
        return {"error": "node_kind must be one of object, operation, planning, reasoning, unknown, or omitted"}
    library = get_schema_library_payload()
    results = []
    for card_id, payload in (library.get("cards", {}) or {}).items():
        kind = payload.get("kind")
        if node_kind and kind != node_kind:
            continue
        haystacks = [
            card_id,
            str(payload.get("canonical_name") or ""),
            *[str(value) for value in payload.get("aliases", []) if value],
        ]
        score = sum(3 for item in haystacks if text == _normalize_text(item))
        score += sum(1 for item in haystacks if text in _normalize_text(item))
        if score:
            results.append({
                "match_type": "card",
                "score": score,
                "card_id": card_id,
                "canonical_name": payload.get("canonical_name"),
                "kind": kind,
                "aliases": payload.get("aliases", []),
                "parameter_slots": payload.get("parameter_slots", []),
                "status": payload.get("status", "active"),
            })
    for template_id, payload in (library.get("operation_templates", {}) or {}).items():
        if node_kind and node_kind != "operation":
            continue
        haystacks = [
            template_id,
            str(payload.get("label") or ""),
            *[str(value) for value in payload.get("aliases", []) if value],
        ]
        score = sum(3 for item in haystacks if text == _normalize_text(item))
        score += sum(1 for item in haystacks if text in _normalize_text(item))
        if score:
            results.append({
                "match_type": "operation_template",
                "score": score,
                "template_id": template_id,
                "label": payload.get("label"),
                "kind": "operation",
                "aliases": payload.get("aliases", []),
                "slots": payload.get("slots", []),
                "parameters": payload.get("parameters", {}),
                "deprecated": bool(payload.get("deprecated")),
            })
    results.sort(key=lambda item: (-int(item["score"]), str(item.get("canonical_name") or item.get("label") or "")))
    return {
        "schema_version": library.get("schema_version"),
        "query": query,
        "results": results[: max(1, min(int(limit), 30))],
    }


def search_similar_workflow_nodes(
    query: str,
    node_kind: str | None = None,
    limit: int = 20,
) -> dict:
    """Search similar nodes across the newest workflow of each project."""
    text = _normalize_text(query)
    if not text:
        return {"error": "query is required"}
    if node_kind not in SEARCHABLE_NODE_KINDS:
        return {"error": "node_kind must be one of object, operation, planning, reasoning, unknown, or omitted"}
    effective_limit = max(1, min(int(limit), 50))
    with SyncSessionLocal() as session:
        rows = _latest_reviewable_workflows(session)
        labels = _project_labels_by_id(session, [row.project_id for row in rows])
        matches = []
        for row in rows:
            for node in (row.graph or {}).get("nodes", []):
                kind = node.get("node_kind") or node.get("node_kind_guess")
                if node_kind and kind != node_kind:
                    continue
                haystacks = [
                    str(node.get("canonical_name") or ""),
                    str(node.get("raw_name") or ""),
                    str(node.get("card_id") or ""),
                    *[str(value) for value in node.get("aliases_observed", []) if value],
                ]
                score = sum(3 for item in haystacks if text == _normalize_text(item))
                score += sum(1 for item in haystacks if text in _normalize_text(item))
                if not score:
                    continue
                matches.append({
                    "score": score,
                    "workflow_id": str(row.extraction_id),
                    "project_id": str(row.project_id),
                    "project_label": labels.get(row.project_id),
                    "version": row.version,
                    "node_id": node.get("node_id"),
                    "canonical_name": node.get("canonical_name"),
                    "raw_name": node.get("raw_name"),
                    "node_kind": kind,
                    "card_id": node.get("card_id"),
                    "ontology_status": node.get("ontology_status"),
                    "semantic_type": node.get("semantic_type"),
                    "evidence_text": str(node.get("evidence_text") or "")[:400],
                })
        matches.sort(key=lambda item: (-int(item["score"]), item["workflow_id"], item["node_id"]))
        return {"query": query, "results": matches[:effective_limit]}


def get_workflow_review_statistics(min_support: int = 2) -> dict:
    """Global statistics for similarity, unmapped nodes, and review risk."""
    min_support = max(1, int(min_support))
    with SyncSessionLocal() as session:
        rows = _latest_reviewable_workflows(session)
        labels = _project_labels_by_id(session, [row.project_id for row in rows])
        label_counts: Counter[str] = Counter()
        unmapped_labels: Counter[str] = Counter()
        card_counts: Counter[str] = Counter()
        flagged = []
        for row in rows:
            graph = row.graph or {}
            if row.review_flags:
                flagged.append(_serialize_workflow_summary(row, labels.get(row.project_id)))
            for node in graph.get("nodes", []):
                name = _normalize_text(node.get("canonical_name") or node.get("raw_name") or "")
                if name:
                    label_counts[name] += 1
                if node.get("card_id"):
                    card_counts[str(node["card_id"])] += 1
                if (node.get("ontology_status") or "unmapped") != "matched" and name:
                    unmapped_labels[name] += 1
        return {
            "workflow_count": len(rows),
            "frequent_node_labels": [
                {"label": label, "count": count}
                for label, count in label_counts.most_common(40)
                if count >= min_support
            ],
            "frequent_unmapped_labels": [
                {"label": label, "count": count}
                for label, count in unmapped_labels.most_common(40)
                if count >= min_support
            ],
            "frequent_card_usage": [
                {"card_id": card_id, "count": count}
                for card_id, count in card_counts.most_common(40)
                if count >= min_support
            ],
            "flagged_workflows": flagged[:20],
        }


SCHEMA_CURATOR_TOOLS = [
    get_workflow_review_overview,
    search_existing_workflows,
    get_existing_workflow,
    search_similar_workflow_nodes,
    search_workflow_cards,
    get_workflow_review_statistics,
    submit_workflow_review,
    submit_schema_proposal,
    revise_schema_proposal,
]
