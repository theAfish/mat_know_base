"""Tools for knowledge graph review, deduplication, and quality maintenance."""

from __future__ import annotations

import uuid
from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Literal

from mkb.agents.tools._ids import invalid_identifier_message, parse_uuidish
from mkb.agents.tools.knowledge_graph import (
    _normalize_label,
    find_similar_concepts,
    get_current_graph_snapshot,
    normalize_knowledge_graph_payload,
)
from mkb.db.engine import SyncSessionLocal
from mkb.db.models import GraphElementReview, Projection, ProjectionStatus
from mkb.knowledge_graph import ensure_global_kg_space_id


# ── Session-level tracking (orchestration layer, not exposed to agent) ─────────


@dataclass
class _ReviewSession:
    examined: set[str] = field(default_factory=set)
    modified: set[str] = field(default_factory=set)


_active_session: ContextVar[_ReviewSession | None] = ContextVar("_active_session", default=None)

# Optional progress callback — set by orchestration before agent run
_progress_cb: ContextVar[Callable[[dict], None] | None] = ContextVar("_graph_review_progress_cb", default=None)


def _fire_progress(event: dict) -> None:
    """Fire a progress event to the registered callback, if any."""
    cb = _progress_cb.get()
    if cb is not None:
        try:
            cb(event)
        except Exception:
            pass


def _init_review_session() -> _ReviewSession:
    """Initialize a new review session for tracking examined/modified elements."""
    session = _ReviewSession()
    _active_session.set(session)
    return session


def _concept_key(label: str) -> str:
    return f"concept:{_normalize_label(label)}"


def _relation_key(source: str, relation: str, target: str) -> str:
    s = _normalize_label(source)
    r = _normalize_label(relation)
    t = _normalize_label(target)
    return f"relation:{s}||{r}||{t}"


def _mark_examined(key: str) -> None:
    s = _active_session.get()
    if s is not None:
        s.examined.add(key)


def _mark_modified(key: str) -> None:
    s = _active_session.get()
    if s is not None:
        s.modified.add(key)
        s.examined.add(key)


# ── Read tools ─────────────────────────────────────────────────────────────────


def get_concept_details(space_id: str, concept_label: str) -> dict:
    """Get full details for a single concept: its record plus all incoming and outgoing relations."""
    if not concept_label or not str(concept_label).strip():
        return {"error": "concept_label is required."}

    _fire_progress({"tool": "get_concept_details", "element_type": "concept", "label": concept_label})
    snapshot = get_current_graph_snapshot(space_id)
    if snapshot.get("error"):
        return snapshot

    norm = _normalize_label(concept_label)
    concepts_by_norm = {_normalize_label(c["label"]): c for c in snapshot["graph"]["concepts"]}
    concept = concepts_by_norm.get(norm)

    if concept is None:
        return {"error": f"Concept '{concept_label}' not found in the graph."}

    _mark_examined(_concept_key(concept_label))

    relations = snapshot["graph"]["relations"]
    outgoing = [r for r in relations if _normalize_label(r["source"]) == norm]
    incoming = [r for r in relations if _normalize_label(r["target"]) == norm]

    for r in outgoing + incoming:
        _mark_examined(_relation_key(r["source"], r["relation"], r["target"]))

    return {
        "concept": concept,
        "outgoing_relations": outgoing,
        "incoming_relations": incoming,
        "relation_count": len(outgoing) + len(incoming),
    }


def get_concept_neighbors(space_id: str, concept_label: str) -> dict:
    """Get a concept and its immediate neighbors (all concepts connected by one hop)."""
    if not concept_label or not str(concept_label).strip():
        return {"error": "concept_label is required."}

    _fire_progress({"tool": "get_concept_neighbors", "element_type": "concept", "label": concept_label})
    snapshot = get_current_graph_snapshot(space_id)
    if snapshot.get("error"):
        return snapshot

    norm = _normalize_label(concept_label)
    concepts_by_norm = {_normalize_label(c["label"]): c for c in snapshot["graph"]["concepts"]}
    concept = concepts_by_norm.get(norm)

    if concept is None:
        return {"error": f"Concept '{concept_label}' not found in the graph."}

    _mark_examined(_concept_key(concept_label))

    relations = snapshot["graph"]["relations"]
    outgoing = [r for r in relations if _normalize_label(r["source"]) == norm]
    incoming = [r for r in relations if _normalize_label(r["target"]) == norm]

    for r in outgoing + incoming:
        _mark_examined(_relation_key(r["source"], r["relation"], r["target"]))

    neighbors: dict[str, dict] = {}
    for r in outgoing:
        neighbor_norm = _normalize_label(r["target"])
        if neighbor_norm not in neighbors and neighbor_norm != norm:
            neighbors[neighbor_norm] = concepts_by_norm.get(neighbor_norm, {"label": r["target"]})
        _mark_examined(_concept_key(r["target"]))

    for r in incoming:
        neighbor_norm = _normalize_label(r["source"])
        if neighbor_norm not in neighbors and neighbor_norm != norm:
            neighbors[neighbor_norm] = concepts_by_norm.get(neighbor_norm, {"label": r["source"]})
        _mark_examined(_concept_key(r["source"]))

    return {
        "concept": concept,
        "outgoing_relations": outgoing,
        "incoming_relations": incoming,
        "neighbors": sorted(neighbors.values(), key=lambda c: _normalize_label(c["label"])),
        "neighbor_count": len(neighbors),
    }


def get_relation_type_distribution(space_id: str, limit: int = 50) -> dict:
    """Get the distribution of relation type/label names across the entire knowledge graph."""
    _fire_progress({"tool": "get_relation_type_distribution", "element_type": "relation", "label": "full graph"})
    snapshot = get_current_graph_snapshot(space_id)
    if snapshot.get("error"):
        return snapshot

    relations = snapshot["graph"]["relations"]
    counter: Counter = Counter()
    for r in relations:
        name = str(r.get("relation") or "").strip()
        if name:
            counter[name] += 1
        _mark_examined(_relation_key(r.get("source", ""), r.get("relation", ""), r.get("target", "")))

    return {
        "total_relations": len(relations),
        "unique_relation_types": len(counter),
        "distribution": [
            {"relation_name": name, "count": count}
            for name, count in counter.most_common(max(1, min(limit, 200)))
        ],
    }


def search_graph_elements(
    space_id: str,
    keyword: str,
    element_type: str = "both",
    limit: int = 30,
) -> dict:
    """Search for concepts and/or relations matching a keyword in labels, aliases, or relation names.

    element_type: "concept", "relation", or "both"
    """
    if not keyword or not str(keyword).strip():
        return {"error": "keyword is required."}

    _fire_progress({"tool": "search_graph_elements", "element_type": element_type, "label": keyword})
    snapshot = get_current_graph_snapshot(space_id)
    if snapshot.get("error"):
        return snapshot

    kw = _normalize_label(keyword)
    matched_concepts = []
    matched_relations = []

    if element_type in ("concept", "both"):
        for c in snapshot["graph"]["concepts"]:
            label_norm = _normalize_label(c.get("label", ""))
            alias_norms = [_normalize_label(a) for a in c.get("aliases", [])]
            if kw in label_norm or any(kw in a for a in alias_norms):
                matched_concepts.append(c)
                _mark_examined(_concept_key(c["label"]))

    if element_type in ("relation", "both"):
        for r in snapshot["graph"]["relations"]:
            rel_norm = _normalize_label(r.get("relation", ""))
            src_norm = _normalize_label(r.get("source", ""))
            tgt_norm = _normalize_label(r.get("target", ""))
            if kw in rel_norm or kw in src_norm or kw in tgt_norm:
                matched_relations.append(r)
                _mark_examined(_relation_key(r.get("source", ""), r.get("relation", ""), r.get("target", "")))

    return {
        "keyword": keyword,
        "concepts": matched_concepts[:limit],
        "relations": matched_relations[:limit],
        "concept_count": len(matched_concepts),
        "relation_count": len(matched_relations),
    }


# ── Mutation tools ─────────────────────────────────────────────────────────────


def _load_kg_projections(space_id: uuid.UUID) -> list:
    """Load all active (non-deleted COMPLETED/REVIEWED) projections for the global KG space."""
    with SyncSessionLocal() as session:
        return (
            session.query(Projection)
            .filter(
                Projection.space_id == space_id,
                Projection.deleted_at.is_(None),
                Projection.status.in_([ProjectionStatus.COMPLETED, ProjectionStatus.REVIEWED]),
            )
            .all()
        )


def merge_concepts(
    space_id: str,
    labels_to_merge: list[str],
    canonical_label: str,
    aliases: list[str] | None = None,
) -> dict:
    """Merge multiple concepts into one canonical concept across all knowledge graph projections.

    All concepts whose labels match any entry in labels_to_merge (case-insensitive) will be
    removed and their aliases, references, and relations re-pointed to canonical_label.
    """
    sid = parse_uuidish(space_id)
    if not sid:
        return {"error": invalid_identifier_message("space_id", space_id)}
    if not labels_to_merge:
        return {"error": "labels_to_merge must be a non-empty list."}
    if not canonical_label or not str(canonical_label).strip():
        return {"error": "canonical_label is required."}

    canonical_label = str(canonical_label).strip()
    canonical_norm = _normalize_label(canonical_label)
    norm_to_merge = {_normalize_label(l) for l in labels_to_merge if str(l).strip()}
    extra_aliases: list[str] = list(aliases or [])

    _fire_progress({"tool": "merge_concepts", "element_type": "concept", "label": canonical_label, "action": "merge", "merging": labels_to_merge})

    projections_updated = 0
    total_concept_merges = 0
    total_relation_updates = 0

    with SyncSessionLocal() as session:
        projections = (
            session.query(Projection)
            .filter(
                Projection.space_id == sid,
                Projection.deleted_at.is_(None),
                Projection.status.in_([ProjectionStatus.COMPLETED, ProjectionStatus.REVIEWED]),
            )
            .all()
        )

        for proj in projections:
            data = proj.data or {}
            concepts = list(data.get("concepts", []))
            relations = list(data.get("relations", []))

            # Collect info from concepts being merged
            merge_aliases: list[str] = list(extra_aliases)
            merge_project_ids: list[str] = []
            merge_frame_ids: list[str] = []
            merge_refs: list[dict] = []
            kept_concepts = []
            n_merged = 0

            for c in concepts:
                label = str(c.get("label") or "").strip()
                if _normalize_label(label) in norm_to_merge and _normalize_label(label) != canonical_norm:
                    n_merged += 1
                    merge_aliases.extend(c.get("aliases", []))
                    merge_aliases.append(label)  # original label becomes alias
                    merge_project_ids.extend(c.get("source_project_ids", []))
                    merge_frame_ids.extend(c.get("source_frame_ids", []))
                    merge_refs.extend(c.get("knowledge_refs", []))
                else:
                    kept_concepts.append(c)

            total_concept_merges += n_merged

            if n_merged == 0 and canonical_norm not in {_normalize_label(c.get("label", "")) for c in concepts}:
                # Nothing to do for this projection
                continue

            # Update or create canonical concept in kept_concepts
            canonical_existing = next(
                (c for c in kept_concepts if _normalize_label(c.get("label", "")) == canonical_norm),
                None,
            )
            if canonical_existing is not None:
                existing_aliases = canonical_existing.get("aliases", [])
                from mkb.agents.tools.knowledge_graph import _coerce_string_list
                canonical_existing["aliases"] = _coerce_string_list(existing_aliases + merge_aliases)
                canonical_existing["source_project_ids"] = _coerce_string_list(
                    canonical_existing.get("source_project_ids", []) + merge_project_ids
                )
                canonical_existing["source_frame_ids"] = _coerce_string_list(
                    canonical_existing.get("source_frame_ids", []) + merge_frame_ids
                )
                canonical_existing["knowledge_refs"] = (
                    canonical_existing.get("knowledge_refs", []) + merge_refs
                )[:50]
            else:
                from mkb.agents.tools.knowledge_graph import _coerce_string_list
                kept_concepts.append({
                    "label": canonical_label,
                    "aliases": _coerce_string_list(merge_aliases),
                    "source_project_ids": _coerce_string_list(merge_project_ids),
                    "source_frame_ids": _coerce_string_list(merge_frame_ids),
                    "knowledge_refs": merge_refs[:50],
                })

            # Re-point relations
            new_relations = []
            for r in relations:
                rel = dict(r)
                changed = False
                if _normalize_label(rel.get("source", "")) in norm_to_merge:
                    rel["source"] = canonical_label
                    changed = True
                if _normalize_label(rel.get("target", "")) in norm_to_merge:
                    rel["target"] = canonical_label
                    changed = True
                new_relations.append(rel)
                if changed:
                    total_relation_updates += 1

            normalized, _ = normalize_knowledge_graph_payload(
                {"concepts": kept_concepts, "relations": new_relations}
            )
            proj.data = normalized
            projections_updated += 1

        session.commit()

    # Track
    for label in list(labels_to_merge) + [canonical_label]:
        _mark_modified(_concept_key(label))

    return {
        "canonical_label": canonical_label,
        "merged_labels": labels_to_merge,
        "projections_updated": projections_updated,
        "concept_merges": total_concept_merges,
        "relation_updates": total_relation_updates,
    }


def standardize_relation_name(
    space_id: str,
    old_names: list[str],
    canonical_name: str,
) -> dict:
    """Rename all relations matching any of old_names to canonical_name across all KG projections.

    Use this to consolidate synonymous relation types (e.g. "is_a", "is a", "is-a" → "is_a").
    """
    sid = parse_uuidish(space_id)
    if not sid:
        return {"error": invalid_identifier_message("space_id", space_id)}
    if not old_names:
        return {"error": "old_names must be a non-empty list."}
    if not canonical_name or not str(canonical_name).strip():
        return {"error": "canonical_name is required."}

    canonical_name = str(canonical_name).strip()
    norm_old = {_normalize_label(n) for n in old_names if str(n).strip()}

    _fire_progress({"tool": "standardize_relation_name", "element_type": "relation", "label": canonical_name, "action": "standardize", "replacing": old_names})

    projections_updated = 0
    relations_renamed = 0

    with SyncSessionLocal() as session:
        projections = (
            session.query(Projection)
            .filter(
                Projection.space_id == sid,
                Projection.deleted_at.is_(None),
                Projection.status.in_([ProjectionStatus.COMPLETED, ProjectionStatus.REVIEWED]),
            )
            .all()
        )

        for proj in projections:
            data = proj.data or {}
            relations = list(data.get("relations", []))
            changed = False

            new_relations = []
            for r in relations:
                rel_norm = _normalize_label(r.get("relation", ""))
                if rel_norm in norm_old:
                    old_key = _relation_key(r.get("source", ""), r.get("relation", ""), r.get("target", ""))
                    r = dict(r)
                    r["relation"] = canonical_name
                    new_key = _relation_key(r["source"], canonical_name, r.get("target", ""))
                    _mark_modified(old_key)
                    _mark_modified(new_key)
                    relations_renamed += 1
                    changed = True
                new_relations.append(r)

            if changed:
                normalized, _ = normalize_knowledge_graph_payload(
                    {"concepts": data.get("concepts", []), "relations": new_relations}
                )
                proj.data = normalized
                projections_updated += 1

        session.commit()

    return {
        "canonical_name": canonical_name,
        "old_names": old_names,
        "projections_updated": projections_updated,
        "relations_renamed": relations_renamed,
    }


def delete_concept(space_id: str, label: str, reason: str = "") -> dict:
    """Delete a concept from the knowledge graph. The concept must have no relations.

    If the concept still has relations, the operation is rejected; delete or merge
    its relations first, then retry.
    """
    sid = parse_uuidish(space_id)
    if not sid:
        return {"error": invalid_identifier_message("space_id", space_id)}
    if not label or not str(label).strip():
        return {"error": "label is required."}

    label = str(label).strip()
    norm = _normalize_label(label)

    _fire_progress({"tool": "delete_concept", "element_type": "concept", "label": label, "action": "delete"})
    # Check for relations in merged graph
    snapshot = get_current_graph_snapshot(str(sid))
    if snapshot.get("error"):
        return snapshot

    blocking_relations = [
        r for r in snapshot["graph"]["relations"]
        if _normalize_label(r.get("source", "")) == norm or _normalize_label(r.get("target", "")) == norm
    ]
    if blocking_relations:
        return {
            "error": (
                f"Concept '{label}' still has {len(blocking_relations)} relation(s). "
                "Delete or merge its relations before deleting the concept."
            ),
            "blocking_relations": blocking_relations[:10],
        }

    projections_updated = 0
    removed = 0

    with SyncSessionLocal() as session:
        projections = (
            session.query(Projection)
            .filter(
                Projection.space_id == sid,
                Projection.deleted_at.is_(None),
                Projection.status.in_([ProjectionStatus.COMPLETED, ProjectionStatus.REVIEWED]),
            )
            .all()
        )

        for proj in projections:
            data = proj.data or {}
            concepts = list(data.get("concepts", []))
            new_concepts = [c for c in concepts if _normalize_label(c.get("label", "")) != norm]
            if len(new_concepts) < len(concepts):
                removed += len(concepts) - len(new_concepts)
                normalized, _ = normalize_knowledge_graph_payload(
                    {"concepts": new_concepts, "relations": data.get("relations", [])}
                )
                proj.data = normalized
                projections_updated += 1

        session.commit()

    _mark_modified(_concept_key(label))
    return {
        "label": label,
        "removed": removed,
        "projections_updated": projections_updated,
        "reason": reason,
    }


def delete_relation(
    space_id: str,
    source: str,
    relation: str,
    target: str,
    reason: str = "",
) -> dict:
    """Delete a specific directed relation from all knowledge graph projections.

    source, relation, and target are matched case-insensitively (normalized).
    """
    sid = parse_uuidish(space_id)
    if not sid:
        return {"error": invalid_identifier_message("space_id", space_id)}
    for name, val in (("source", source), ("relation", relation), ("target", target)):
        if not val or not str(val).strip():
            return {"error": f"{name} is required."}

    source = str(source).strip()
    relation = str(relation).strip()
    target = str(target).strip()
    src_norm = _normalize_label(source)
    rel_norm = _normalize_label(relation)
    tgt_norm = _normalize_label(target)

    _fire_progress({"tool": "delete_relation", "element_type": "relation", "label": f"{source} → {target}", "action": "delete", "relation": relation})

    projections_updated = 0
    removed = 0

    with SyncSessionLocal() as session:
        projections = (
            session.query(Projection)
            .filter(
                Projection.space_id == sid,
                Projection.deleted_at.is_(None),
                Projection.status.in_([ProjectionStatus.COMPLETED, ProjectionStatus.REVIEWED]),
            )
            .all()
        )

        for proj in projections:
            data = proj.data or {}
            relations = list(data.get("relations", []))
            new_relations = [
                r for r in relations
                if not (
                    _normalize_label(r.get("source", "")) == src_norm
                    and _normalize_label(r.get("relation", "")) == rel_norm
                    and _normalize_label(r.get("target", "")) == tgt_norm
                )
            ]
            if len(new_relations) < len(relations):
                removed += len(relations) - len(new_relations)
                normalized, _ = normalize_knowledge_graph_payload(
                    {"concepts": data.get("concepts", []), "relations": new_relations}
                )
                proj.data = normalized
                projections_updated += 1

        session.commit()

    _mark_modified(_relation_key(source, relation, target))
    return {
        "source": source,
        "relation": relation,
        "target": target,
        "removed": removed,
        "projections_updated": projections_updated,
        "reason": reason,
    }


# ── Orchestration helpers (not exposed to agent) ───────────────────────────────


def _flush_review_session_to_db(space_id: uuid.UUID, review_session: _ReviewSession) -> dict:
    """Write accumulated examined/modified counts to the graph_element_reviews table."""
    now = datetime.now(timezone.utc)

    def _element_type_and_key(raw_key: str) -> tuple[str, str] | None:
        if raw_key.startswith("concept:"):
            return "concept", raw_key[len("concept:"):]
        if raw_key.startswith("relation:"):
            return "relation", raw_key[len("relation:"):]
        return None

    all_keys = review_session.examined | review_session.modified
    if not all_keys:
        return {"examined": 0, "modified": 0}

    with SyncSessionLocal() as session:
        for raw_key in all_keys:
            parsed = _element_type_and_key(raw_key)
            if not parsed:
                continue
            element_type, element_key = parsed
            is_modified = raw_key in review_session.modified

            record = session.query(GraphElementReview).filter_by(
                space_id=space_id,
                element_type=element_type,
                element_key=element_key,
            ).first()

            if record is None:
                record = GraphElementReview(
                    review_id=uuid.uuid4(),
                    space_id=space_id,
                    element_type=element_type,
                    element_key=element_key,
                    times_examined=0,
                    times_modified=0,
                )
                session.add(record)

            record.times_examined += 1
            record.last_examined_at = now
            if is_modified:
                record.times_modified += 1
                record.last_modified_at = now

        session.commit()

    return {
        "examined": len(review_session.examined),
        "modified": len(review_session.modified),
    }


def _get_least_examined_concepts(space_id: uuid.UUID, count: int) -> list[str]:
    """Return up to `count` concept labels with the lowest times_examined, with random tie-breaking."""
    import random

    snapshot = get_current_graph_snapshot(str(space_id))
    all_concepts = snapshot.get("graph", {}).get("concepts", [])
    if not all_concepts:
        return []

    with SyncSessionLocal() as session:
        review_rows = session.query(GraphElementReview).filter_by(
            space_id=space_id,
            element_type="concept",
        ).all()
        count_map = {row.element_key: row.times_examined for row in review_rows}

    labeled = [
        (count_map.get(_normalize_label(c["label"]), 0), c["label"])
        for c in all_concepts
    ]
    random.shuffle(labeled)  # random shuffle before sort for tiebreaking
    labeled.sort(key=lambda x: x[0])
    return [label for _, label in labeled[:count]]


# ── Tool lists ─────────────────────────────────────────────────────────────────

from mkb.agents.tools.knowledge_graph import get_global_kg_space  # noqa: E402
from mkb.agents.tools.projection import get_frame_content  # noqa: E402

GRAPH_REVIEW_COMMON_TOOLS = [
    get_global_kg_space,
    find_similar_concepts,
    get_concept_details,
    get_concept_neighbors,
    get_relation_type_distribution,
    search_graph_elements,
    merge_concepts,
    standardize_relation_name,
    delete_concept,
    delete_relation,
]

GRAPH_REVIEW_LOCAL_TOOLS = GRAPH_REVIEW_COMMON_TOOLS + [
    get_frame_content,
]
