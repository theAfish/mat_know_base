"""Grounded context and persistence tools for the Schema Curator Agent."""

from __future__ import annotations

import random
import re
import uuid
from collections import Counter
from contextvars import ContextVar

from sqlalchemy import func

from mkb.db.engine import SyncSessionLocal
from mkb.db.models import (
    CanonicalWorkflow, RawWorkflowExtraction, SchemaProposal,
    SchemaProposalRevision, ResearchProject, WorkflowSchemaVersion,
)
from mkb.workflows.curator import analyze_canonical_workflows, validate_proposal
from mkb.workflows.review import audit_raw_graph, rebase_graph
from mkb.workflows.schema_library import get_schema_library_payload

_CURATOR_AUTHOR: ContextVar[str] = ContextVar(
    "schema_curator_author", default="schema-curator-agent/unknown-model"
)


def set_curator_author(author: str):
    return _CURATOR_AUTHOR.set(author)


def reset_curator_author(token) -> None:
    _CURATOR_AUTHOR.reset(token)


def _workflow_evidence(row: CanonicalWorkflow, raw: RawWorkflowExtraction | None) -> dict:
    graph = row.graph or {}
    raw_graph = raw.graph if raw and raw.graph else {}
    raw_nodes = {node.get("node_id"): node for node in raw_graph.get("nodes", [])}
    unmatched = []
    for item in graph.get("unmatched_raw_information", []):
        for raw_id in item.get("raw_node_ids", []):
            node = raw_nodes.get(raw_id, {})
            unmatched.append({
                "raw_node_id": raw_id,
                "raw_name": node.get("raw_name"),
                "node_kind": node.get("node_kind_guess"),
                "evidence_text": str(node.get("evidence_text") or "")[:600],
                "reason": item.get("reason"),
            })
    return {
        "canonicalization_id": str(row.canonicalization_id),
        "project_id": str(row.project_id),
        "schema_version": row.schema_version,
        "nodes": [{
            "node_id": node.get("node_id"),
            "label": node.get("label"),
            "node_kind": node.get("node_kind"),
            "object_schema": node.get("object_schema"),
            "operation_template_id": node.get("operation_template_id"),
            "attributes": node.get("attributes", {}),
        } for node in graph.get("nodes", [])[:60]],
        "unmatched": unmatched[:40],
        "granularity_mappings": graph.get("granularity_mappings", []),
        "existing_schema_suggestions": graph.get("proposed_schema_updates", [])[:20],
    }


def _card_workflow_evidence(row: RawWorkflowExtraction) -> dict:
    """Bounded evidence view for a v2 extraction with no canonicalization pass."""
    graph = row.graph or {}
    return {
        "workflow_id": str(row.extraction_id),
        # compatibility key used by the proposal UI/storage layer
        "canonicalization_id": str(row.extraction_id),
        "project_id": str(row.project_id),
        "schema_version": row.schema_version,
        "nodes": [{
            "node_id": node.get("node_id"),
            "label": node.get("canonical_name") or node.get("raw_name"),
            "raw_name": node.get("raw_name"),
            "node_kind": node.get("node_kind") or node.get("node_kind_guess"),
            "semantic_type": node.get("semantic_type"),
            "card_id": node.get("card_id"),
            "ontology_status": node.get("ontology_status"),
            "parameters": node.get("parameters", {}),
            "identity": node.get("identity", {}),
            "state": node.get("state", {}),
            "role": node.get("role", {}),
            "context": node.get("context", {}),
            "evidence_text": str(node.get("evidence_text") or "")[:600],
        } for node in graph.get("nodes", [])[:80]],
        "edges": graph.get("edges", [])[:120],
        "reproducibility": graph.get("reproducibility", {}),
        "unresolved_information": graph.get("unresolved_information", [])[:30],
    }


def _as_induction_workflow(row: RawWorkflowExtraction) -> dict:
    """Adapt card instances to the deterministic discovery signal analyzer."""
    raw_graph = row.graph or {}
    nodes = []
    unmatched = []
    for node in raw_graph.get("nodes", []):
        kind = node.get("node_kind") or node.get("node_kind_guess")
        card_id = node.get("card_id")
        nodes.append({
            "node_id": node.get("node_id"),
            "label": node.get("canonical_name") or node.get("raw_name"),
            "node_kind": kind,
            "object_schema": node.get("semantic_type") if kind == "object" else None,
            "operation_template_id": card_id if kind == "operation" else None,
            "attributes": node.get("parameters", {}),
        })
        if kind == "operation" and not card_id:
            unmatched.append({"raw_node_ids": [node.get("node_id")], "reason": "unmapped card"})
    return {
        "canonicalization_id": str(row.extraction_id),
        "graph": {
            "nodes": nodes,
            "edges": raw_graph.get("edges", []),
            "unmatched_raw_information": unmatched,
            "granularity_mappings": [
                {"coarse": edge.get("source_node"), "fine": edge.get("target_node")}
                for edge in raw_graph.get("edges", [])
                if edge.get("relation_type") in {"part_of", "has_part", "expands_to", "summarized_by"}
            ],
        },
        "raw_graph": raw_graph,
    }


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


def _latest_reviewable_workflows(session) -> list[RawWorkflowExtraction]:
    rows = (
        session.query(RawWorkflowExtraction)
        .filter(
            RawWorkflowExtraction.status == "COMPLETED",
            RawWorkflowExtraction.record_status.in_(("active", "needs_review")),
        )
        .order_by(RawWorkflowExtraction.project_id, RawWorkflowExtraction.version.desc())
        .all()
    )
    latest: dict[uuid.UUID, RawWorkflowExtraction] = {}
    for row in rows:
        latest.setdefault(row.project_id, row)
    return list(latest.values())


def _workflow_search_text(row: RawWorkflowExtraction, project_label: str | None = None) -> str:
    graph = row.graph or {}
    node_names = " ".join(
        filter(
            None,
            (
                node.get("canonical_name") or node.get("raw_name")
                for node in graph.get("nodes", [])[:120]
            ),
        )
    )
    return _normalize_text(f"{project_label or ''} {row.project_id} {node_names}")


def _serialize_workflow_summary(row: RawWorkflowExtraction, project_label: str | None = None) -> dict:
    graph = row.graph or {}
    review_count = int((row.provenance or {}).get("workflow_review_count", 0))
    node_count = len(graph.get("nodes", []))
    unmapped = sum(
        1 for node in graph.get("nodes", [])
        if (node.get("ontology_status") or "unmapped") != "matched"
    )
    return {
        "workflow_id": str(row.extraction_id),
        "project_id": str(row.project_id),
        "project_label": project_label,
        "version": row.version,
        "record_status": row.record_status,
        "schema_version": row.schema_version,
        "node_count": node_count,
        "edge_count": len(graph.get("edges", [])),
        "unmapped_node_count": unmapped,
        "review_count": review_count,
        "review_flags": row.review_flags or [],
        "sample_labels": [
            node.get("canonical_name") or node.get("raw_name")
            for node in graph.get("nodes", [])[:8]
        ],
    }


def _serialize_workflow_detail(row: RawWorkflowExtraction, project_label: str | None = None) -> dict:
    payload = _card_workflow_evidence(row)
    payload.update({
        "project_label": project_label,
        "version": row.version,
        "record_status": row.record_status,
        "review_flags": row.review_flags or [],
        "review_count": int((row.provenance or {}).get("workflow_review_count", 0)),
    })
    return payload


def _project_labels_by_id(session, project_ids: list[uuid.UUID]) -> dict[uuid.UUID, str | None]:
    if not project_ids:
        return {}
    return {
        row.project_id: row.label
        for row in session.query(ResearchProject).filter(
            ResearchProject.project_id.in_(project_ids)
        ).all()
    }


def _weighted_local_sample(rows: list[RawWorkflowExtraction], sample_size: int) -> list[RawWorkflowExtraction]:
    pool = list(rows)
    chosen: list[RawWorkflowExtraction] = []
    target = min(max(1, int(sample_size)), len(pool))
    while pool and len(chosen) < target:
        weights = []
        for row in pool:
            review_count = int((row.provenance or {}).get("workflow_review_count", 0))
            review_flag_bonus = 4 if row.record_status == "needs_review" else 1
            weights.append(max(1, review_flag_bonus * (6 - min(review_count, 5))))
        pick = random.choices(pool, weights=weights, k=1)[0]
        chosen.append(pick)
        pool = [row for row in pool if row.extraction_id != pick.extraction_id]
    return chosen


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
    if node_kind not in {None, "", "object", "operation"}:
        return {"error": "node_kind must be 'object', 'operation', or omitted"}
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
        if node_kind == "object":
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
    if node_kind not in {None, "", "object", "operation"}:
        return {"error": "node_kind must be 'object', 'operation', or omitted"}
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


def submit_schema_proposal(
    proposal_type: str,
    payload: dict,
    evidence_workflow_ids: list[str],
    rationale: str,
    analysis: dict | None = None,
) -> dict:
    """Validate and save one LLM-authored proposal for human review."""
    if not rationale.strip():
        return {"error": "rationale is required"}
    try:
        evidence_uuids = [uuid.UUID(value) for value in evidence_workflow_ids]
    except (TypeError, ValueError, AttributeError):
        return {"error": "evidence_workflow_ids must contain canonicalization UUIDs"}
    author = _CURATOR_AUTHOR.get()
    with SyncSessionLocal() as session:
        active = session.query(WorkflowSchemaVersion).filter_by(status="active").order_by(
            WorkflowSchemaVersion.version.desc()
        ).first()
        library = active.payload if active else get_schema_library_payload()
        base_version = active.name if active else library["schema_version"]
        known = {
            str(value) for (value,) in session.query(CanonicalWorkflow.canonicalization_id).filter(
                CanonicalWorkflow.status == "COMPLETED",
                CanonicalWorkflow.canonicalization_id.in_(evidence_uuids),
            ).all()
        }
        known.update({
            str(value) for (value,) in session.query(RawWorkflowExtraction.extraction_id).filter(
                RawWorkflowExtraction.status == "COMPLETED",
                RawWorkflowExtraction.extraction_id.in_(evidence_uuids),
            ).all()
        })
        errors = validate_proposal(
            proposal_type, payload, evidence_workflow_ids, library,
        )
        missing = sorted(set(evidence_workflow_ids) - known)
        if missing:
            errors.append(f"unknown evidence workflows: {', '.join(missing)}")
        if errors:
            return {"error": "proposal validation failed", "details": errors}
        duplicate = session.query(SchemaProposal).filter(
            SchemaProposal.status.in_(("pending", "revision_requested")),
            SchemaProposal.proposal_type == proposal_type,
            SchemaProposal.payload == payload,
        ).first()
        if duplicate:
            return {
                "status": "duplicate", "proposal_id": str(duplicate.proposal_id),
            }
        proposal = SchemaProposal(
            proposal_type=proposal_type, status="pending", payload=payload,
            evidence_workflow_ids=evidence_workflow_ids,
            analysis={**(analysis or {}), "curator_method": "llm", "validation_errors": []},
            rationale=rationale.strip(), base_schema_version=base_version,
            created_by=author,
        )
        session.add(proposal)
        session.flush()
        session.add(SchemaProposalRevision(
            proposal_id=proposal.proposal_id, revision_number=1,
            payload=proposal.payload,
            evidence_workflow_ids=proposal.evidence_workflow_ids,
            analysis=proposal.analysis, rationale=proposal.rationale,
            author=author, author_type="agent",
            change_note="Initial LLM curator draft", validation_errors=[],
        ))
        session.commit()
        return {
            "status": "created", "proposal_id": str(proposal.proposal_id),
            "base_schema_version": base_version,
        }


def revise_schema_proposal(
    proposal_id: str,
    payload: dict,
    evidence_workflow_ids: list[str],
    rationale: str,
    response_to_review: str,
) -> dict:
    """Respond to human revision notes with a validated agent-authored revision."""
    try:
        pid = uuid.UUID(proposal_id)
        evidence_uuids = [uuid.UUID(value) for value in evidence_workflow_ids]
    except (TypeError, ValueError, AttributeError):
        return {"error": "proposal and evidence IDs must be UUIDs"}
    if not rationale.strip() or not response_to_review.strip():
        return {"error": "rationale and response_to_review are required"}
    author = _CURATOR_AUTHOR.get()
    with SyncSessionLocal() as session:
        proposal = session.query(SchemaProposal).filter_by(proposal_id=pid).first()
        if not proposal or proposal.status != "revision_requested":
            return {"error": "Revision-requested proposal not found"}
        active = session.query(WorkflowSchemaVersion).filter_by(status="active").order_by(
            WorkflowSchemaVersion.version.desc()
        ).first()
        library = active.payload if active else get_schema_library_payload()
        base_version = active.name if active else library["schema_version"]
        known = {
            str(value) for (value,) in session.query(CanonicalWorkflow.canonicalization_id).filter(
                CanonicalWorkflow.status == "COMPLETED",
                CanonicalWorkflow.canonicalization_id.in_(evidence_uuids),
            ).all()
        }
        known.update({
            str(value) for (value,) in session.query(RawWorkflowExtraction.extraction_id).filter(
                RawWorkflowExtraction.status == "COMPLETED",
                RawWorkflowExtraction.extraction_id.in_(evidence_uuids),
            ).all()
        })
        errors = validate_proposal(
            proposal.proposal_type, payload, evidence_workflow_ids, library,
        )
        missing = sorted(set(evidence_workflow_ids) - known)
        if missing:
            errors.append(f"unknown evidence workflows: {', '.join(missing)}")
        if errors:
            return {"error": "revised proposal validation failed", "details": errors}
        revision_number = int(
            session.query(func.coalesce(func.max(SchemaProposalRevision.revision_number), 0))
            .filter_by(proposal_id=pid).scalar()
        ) + 1
        proposal.payload = payload
        proposal.evidence_workflow_ids = evidence_workflow_ids
        proposal.rationale = rationale.strip()
        proposal.base_schema_version = base_version
        proposal.analysis = {
            **(proposal.analysis or {}), "curator_method": "llm",
            "revision_response": response_to_review.strip(),
            "validation_errors": [],
        }
        proposal.status = "pending"
        session.add(SchemaProposalRevision(
            proposal_id=pid, revision_number=revision_number,
            payload=payload, evidence_workflow_ids=evidence_workflow_ids,
            analysis=proposal.analysis, rationale=proposal.rationale,
            author=author, author_type="agent",
            change_note=f"Agent response to review: {response_to_review.strip()}",
            validation_errors=[],
        ))
        session.commit()
        return {
            "status": "revised", "proposal_id": proposal_id,
            "revision_number": revision_number,
        }


def submit_workflow_review(
    workflow_id: str,
    graph: dict,
    reason: str,
    evidence: str,
    affected_nodes: list[str] | None = None,
    affected_edges: list[str] | None = None,
) -> dict:
    """Persist an immutable reviewed workflow revision with edited nodes/edges."""
    if not reason.strip() or not evidence.strip():
        return {"error": "reason and evidence are required"}
    try:
        wid = uuid.UUID(str(workflow_id))
    except (TypeError, ValueError, AttributeError):
        return {"error": "workflow_id must be a UUID"}
    author = _CURATOR_AUTHOR.get()
    with SyncSessionLocal() as session:
        source = session.query(RawWorkflowExtraction).filter_by(extraction_id=wid).first()
        if not source or source.status != "COMPLETED":
            return {"error": "completed workflow not found"}
        new_id = uuid.uuid4()
        corrected = dict(graph or {})
        corrected["paper_id"] = str(source.project_id)
        corrected["schema_version"] = source.schema_version
        try:
            corrected = rebase_graph(corrected, new_id)
        except Exception as exc:
            return {"error": f"corrected graph validation failed: {exc}"}
        version = int(
            session.query(func.coalesce(func.max(RawWorkflowExtraction.version), 0))
            .filter_by(project_id=source.project_id)
            .scalar()
        ) + 1
        review_count = int((source.provenance or {}).get("workflow_review_count", 0)) + 1
        flags = audit_raw_graph(corrected)
        row = RawWorkflowExtraction(
            extraction_id=new_id,
            project_id=source.project_id,
            version=version,
            schema_version=source.schema_version,
            extractor_version=source.extractor_version,
            model=source.model,
            status="COMPLETED",
            record_status="needs_review" if flags else "active",
            supersedes_extraction_id=source.extraction_id,
            graph=corrected,
            correction_reason=reason.strip(),
            correction_author=author,
            correction_details={
                "affected_nodes": affected_nodes or [],
                "affected_edges": affected_edges or [],
                "evidence": evidence.strip(),
            },
            review_flags=flags,
            provenance={
                **(source.provenance or {}),
                "workflow_review_count": review_count,
                "last_review_agent": author,
                "last_review_reason": reason.strip(),
            },
            extracted_at=source.extracted_at,
        )
        source.record_status = "superseded"
        session.add(row)
        session.commit()
        return {
            "status": "reviewed",
            "workflow_id": str(new_id),
            "project_id": str(source.project_id),
            "version": version,
            "review_flags": flags,
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
