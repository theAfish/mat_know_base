"""Tools for concept-only knowledge graph extraction."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone

from mkb.agents.tools._ids import invalid_identifier_message, parse_uuidish
from mkb.agents.tools.projection import (
    flag_for_feedback,
    get_frame_content,
    request_frame_clarification,
)
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import KnowledgeFrame, Projection, ProjectionStatus
from mkb.knowledge_graph import ensure_global_kg_space_id


def _normalize_label(value: str) -> str:
    collapsed = re.sub(r"\s+", " ", str(value or "").strip().lower())
    return collapsed


def _coerce_string_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        items = [str(value)]

    result = []
    seen = set()
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        key = _normalize_label(text)
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _normalize_reference_list(value) -> list[dict[str, str]]:
    if value is None:
        return []
    refs = value if isinstance(value, list) else [value]
    normalized = []
    for ref in refs:
        if isinstance(ref, str):
            text = ref.strip()
            if text:
                normalized.append({"snippet": text})
            continue
        if not isinstance(ref, dict):
            continue
        item = {
            "project_id": str(ref.get("project_id") or "").strip(),
            "frame_id": str(ref.get("frame_id") or "").strip(),
            "field_path": str(ref.get("field_path") or "").strip(),
            "snippet": str(ref.get("snippet") or "").strip(),
        }
        item = {k: v for k, v in item.items() if v}
        if item:
            normalized.append(item)
    return normalized


def normalize_knowledge_graph_payload(data: dict | None) -> tuple[dict, dict]:
    """Normalize graph payload into a strict concept-only structure."""
    warnings: list[str] = []
    if not isinstance(data, dict):
        return {"concepts": [], "relations": []}, {"warnings": ["Payload was not a mapping."]}

    concepts_raw = data.get("concepts")
    if concepts_raw is None:
        concepts_raw = data.get("nodes")
    relations_raw = data.get("relations")
    if relations_raw is None:
        relations_raw = data.get("edges")

    if not isinstance(concepts_raw, list):
        concepts_raw = []
    if not isinstance(relations_raw, list):
        relations_raw = []

    concepts_by_norm: dict[str, dict] = {}

    def _add_concept(
        label: str,
        aliases: list[str] | None = None,
        project_ids: list[str] | None = None,
        frame_ids: list[str] | None = None,
        refs: list[dict] | None = None,
    ):
        label = str(label or "").strip()
        if not label:
            return
        norm = _normalize_label(label)
        existing = concepts_by_norm.get(norm)
        if not existing:
            concepts_by_norm[norm] = {
                "label": label,
                "aliases": _coerce_string_list(aliases or []),
                "source_project_ids": _coerce_string_list(project_ids or []),
                "source_frame_ids": _coerce_string_list(frame_ids or []),
                "knowledge_refs": _normalize_reference_list(refs or []),
            }
            return

        existing["aliases"] = _coerce_string_list(existing.get("aliases", []) + _coerce_string_list(aliases or []))
        existing["source_project_ids"] = _coerce_string_list(
            existing.get("source_project_ids", []) + _coerce_string_list(project_ids or [])
        )
        existing["source_frame_ids"] = _coerce_string_list(
            existing.get("source_frame_ids", []) + _coerce_string_list(frame_ids or [])
        )
        existing["knowledge_refs"] = existing.get("knowledge_refs", []) + _normalize_reference_list(refs or [])

    for item in concepts_raw:
        if isinstance(item, str):
            _add_concept(item)
            continue
        if not isinstance(item, dict):
            warnings.append("Skipped non-dict concept item.")
            continue

        label = (
            item.get("label")
            or item.get("concept")
            or item.get("name")
            or item.get("id")
            or ""
        )
        if not str(label).strip():
            warnings.append("Skipped concept without label.")
            continue

        _add_concept(
            str(label),
            aliases=item.get("aliases"),
            project_ids=item.get("source_project_ids") or item.get("source_project_id"),
            frame_ids=item.get("source_frame_ids") or item.get("source_frame_id"),
            refs=item.get("knowledge_refs") or item.get("references"),
        )

    relation_rows = []
    relation_key_counts = Counter()
    for item in relations_raw:
        if not isinstance(item, dict):
            warnings.append("Skipped non-dict relation item.")
            continue

        source = item.get("source") or item.get("from") or item.get("subject")
        target = item.get("target") or item.get("to") or item.get("object")
        relation = item.get("relation") or item.get("predicate") or item.get("type")
        if not source or not target or not relation:
            warnings.append("Skipped relation missing source/target/relation.")
            continue

        source_txt = str(source).strip()
        target_txt = str(target).strip()
        relation_txt = str(relation).strip()
        if not source_txt or not target_txt or not relation_txt:
            warnings.append("Skipped relation with empty source/target/relation.")
            continue

        ev = item.get("evidence_level", 3)
        try:
            ev = int(ev)
        except (TypeError, ValueError):
            ev = 3
        ev = min(max(ev, 1), 4)

        _add_concept(source_txt)
        _add_concept(target_txt)

        relation_row = {
            "source": source_txt,
            "relation": relation_txt,
            "target": target_txt,
            "evidence_level": ev,
            "source_project_id": str(item.get("source_project_id") or "").strip(),
            "source_frame_id": str(item.get("source_frame_id") or "").strip(),
            "knowledge_ref": {},
        }
        relation_row = {k: v for k, v in relation_row.items() if v not in ({}, "")}

        refs = _normalize_reference_list(item.get("knowledge_ref") or item.get("knowledge_refs"))
        if refs:
            relation_row["knowledge_ref"] = refs[0]

        relation_rows.append(relation_row)
        relation_key = (
            _normalize_label(source_txt),
            _normalize_label(relation_txt),
            _normalize_label(target_txt),
        )
        relation_key_counts[relation_key] += 1

    deduped_relations: dict[tuple[str, str, str], dict] = {}
    for row in relation_rows:
        key = (
            _normalize_label(row["source"]),
            _normalize_label(row["relation"]),
            _normalize_label(row["target"]),
        )
        existing = deduped_relations.get(key)
        if not existing:
            deduped_relations[key] = dict(row)
            continue

        existing["evidence_level"] = min(existing.get("evidence_level", 4), row.get("evidence_level", 4))
        if not existing.get("source_project_id") and row.get("source_project_id"):
            existing["source_project_id"] = row["source_project_id"]
        if not existing.get("source_frame_id") and row.get("source_frame_id"):
            existing["source_frame_id"] = row["source_frame_id"]
        if not existing.get("knowledge_ref") and row.get("knowledge_ref"):
            existing["knowledge_ref"] = row["knowledge_ref"]

    concepts = sorted(concepts_by_norm.values(), key=lambda x: _normalize_label(x["label"]))
    relations = sorted(
        deduped_relations.values(),
        key=lambda x: (
            _normalize_label(x["source"]),
            _normalize_label(x["relation"]),
            _normalize_label(x["target"]),
        ),
    )

    duplicate_relations = [
        {
            "source": src,
            "relation": rel,
            "target": tgt,
            "count": count,
        }
        for (src, rel, tgt), count in relation_key_counts.items()
        if count > 1
    ]

    payload = {
        "concepts": concepts,
        "relations": relations,
    }
    validation = {
        "warnings": warnings,
        "concept_count": len(concepts),
        "relation_count": len(relations),
        "duplicate_relation_count": len(duplicate_relations),
    }
    if duplicate_relations:
        validation["duplicate_relations"] = duplicate_relations[:20]
    return payload, validation


def get_current_graph_snapshot(space_id: str, exclude_projection_id: str | None = None) -> dict:
    """Get merged graph data for redundancy checks before saving."""
    sid = parse_uuidish(space_id)
    if not sid:
        return {"error": invalid_identifier_message("space_id", space_id)}

    exclude_pid = None
    if exclude_projection_id:
        exclude_pid = parse_uuidish(exclude_projection_id)
        if not exclude_pid:
            return {"error": invalid_identifier_message("exclude_projection_id", exclude_projection_id)}

    aggregate = {"concepts": [], "relations": []}
    with SyncSessionLocal() as session:
        q = (
            session.query(Projection)
            .filter(Projection.space_id == sid)
            .filter(Projection.deleted_at.is_(None))
            .filter(Projection.status.in_([ProjectionStatus.COMPLETED, ProjectionStatus.REVIEWED]))
        )
        if exclude_pid:
            q = q.filter(Projection.projection_id != exclude_pid)
        rows = q.all()
        for row in rows:
            payload, _ = normalize_knowledge_graph_payload(row.data or {})
            aggregate["concepts"].extend(payload["concepts"])
            aggregate["relations"].extend(payload["relations"])

    normalized, validation = normalize_knowledge_graph_payload(aggregate)
    return {
        "space_id": str(sid),
        "projection_count": len(rows),
        "graph": normalized,
        "redundancy_report": {
            "duplicate_relation_count": validation.get("duplicate_relation_count", 0),
            "duplicate_relations": validation.get("duplicate_relations", []),
        },
    }


def find_similar_concepts(space_id: str, concept_label: str, limit: int = 10) -> dict:
    """Find concept labels in the current global graph that are likely duplicates."""
    if not concept_label or not str(concept_label).strip():
        return {"error": "concept_label is required."}
    snapshot = get_current_graph_snapshot(space_id)
    if snapshot.get("error"):
        return snapshot

    query_label = str(concept_label).strip()
    query_norm = _normalize_label(query_label)
    query_tokens = set(query_norm.split())

    scored = []
    for concept in snapshot["graph"]["concepts"]:
        label = str(concept.get("label") or "").strip()
        if not label:
            continue
        label_norm = _normalize_label(label)
        score = 0
        if label_norm == query_norm:
            score = 100
        elif query_norm in label_norm or label_norm in query_norm:
            score = 80
        else:
            tokens = set(label_norm.split())
            if tokens and query_tokens:
                overlap = len(tokens & query_tokens)
                if overlap:
                    score = int(100 * overlap / max(len(tokens), len(query_tokens)))
        if score > 0:
            scored.append({"label": label, "score": score, "aliases": concept.get("aliases", [])})

    scored.sort(key=lambda row: row["score"], reverse=True)
    return {
        "query": query_label,
        "similar_concepts": scored[: max(1, min(limit, 50))],
    }


def save_knowledge_graph(
    projection_id: str,
    data: dict,
    validation_notes: str = "",
    agent_notes: str = "",
) -> dict:
    """Save normalized concept-only graph data to a projection row."""
    pid = parse_uuidish(projection_id)
    if not pid:
        return {"error": invalid_identifier_message("projection_id", projection_id)}

    normalized, validation = normalize_knowledge_graph_payload(data)

    now = datetime.now(timezone.utc)
    with SyncSessionLocal() as session:
        projection = session.query(Projection).filter_by(projection_id=pid).first()
        if not projection:
            return {"error": f"Projection {projection_id} not found."}

        frame = session.query(KnowledgeFrame).filter_by(frame_id=projection.frame_id).first()
        if frame:
            project_id = str(frame.project_id)
            frame_id = str(frame.frame_id)
            for concept in normalized["concepts"]:
                if not concept.get("source_project_ids"):
                    concept["source_project_ids"] = [project_id]
                if not concept.get("source_frame_ids"):
                    concept["source_frame_ids"] = [frame_id]
            for relation in normalized["relations"]:
                relation.setdefault("source_project_id", project_id)
                relation.setdefault("source_frame_id", frame_id)

        if validation_notes:
            validation = {**validation, "notes": validation_notes}

        projection.data = normalized
        projection.validation_result = validation
        projection.agent_notes = agent_notes
        projection.status = ProjectionStatus.COMPLETED
        projection.extracted_at = now
        session.commit()

        return {
            "projection_id": str(projection.projection_id),
            "status": "completed",
            "concept_count": len(normalized["concepts"]),
            "relation_count": len(normalized["relations"]),
        }


def get_global_kg_space() -> dict:
    """Return global singleton KG space information."""
    sid = ensure_global_kg_space_id()
    return {"space_id": str(sid)}


KNOWLEDGE_GRAPH_TOOLS = [
    get_global_kg_space,
    get_frame_content,
    get_current_graph_snapshot,
    find_similar_concepts,
    save_knowledge_graph,
    request_frame_clarification,
    flag_for_feedback,
]
