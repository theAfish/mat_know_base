"""Knowledge graph helpers and global-space management.

The KG pipeline uses one singleton Space across all domains so concept
connections can be discovered inter-domain.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from mkb.db.engine import SyncSessionLocal
from mkb.db.models import KnowledgeFrame, Projection, Space

GLOBAL_KG_SPACE_NAME = "__global_concept_graph__"
GLOBAL_KG_SPACE_DOMAIN = "cross-domain-concept-graph"
LEGACY_KG_SPACE_NAMES = {
    "knowledge_graph",
    "knowledge-graph",
    "kg",
    "concept_graph",
    "concept-graph",
    GLOBAL_KG_SPACE_NAME,
}

GLOBAL_KG_SPACE_SCHEMA = {
    "concepts": {
        "type": "list",
        "item_schema": {
            "label": {"type": "string", "required": True},
            "aliases": {"type": "list", "item_type": "string"},
            "source_project_ids": {"type": "list", "item_type": "string"},
            "source_frame_ids": {"type": "list", "item_type": "string"},
            "knowledge_refs": {
                "type": "list",
                "item_schema": {
                    "project_id": {"type": "string"},
                    "frame_id": {"type": "string"},
                    "field_path": {"type": "string"},
                    "snippet": {"type": "string"},
                },
            },
        },
    },
    "relations": {
        "type": "list",
        "item_schema": {
            "source": {"type": "string", "required": True},
            "relation": {"type": "string", "required": True},
            "target": {"type": "string", "required": True},
            "evidence_level": {"type": "integer"},
            "source_project_id": {"type": "string"},
            "source_frame_id": {"type": "string"},
            "knowledge_ref": {
                "type": "object",
                "item_schema": {
                    "project_id": {"type": "string"},
                    "frame_id": {"type": "string"},
                    "field_path": {"type": "string"},
                    "snippet": {"type": "string"},
                },
            },
        },
    },
}

GLOBAL_KG_SYSTEM_PROMPT = (
    "Build a concept graph from knowledge frames. Nodes are concepts only, "
    "edges are relations between concepts only. Put rich details in knowledge "
    "references, not as extra nodes."
)

GLOBAL_KG_FIELD_DESCRIPTIONS = {
    "concepts": "Concept nodes with optional aliases and references to frame/database context.",
    "relations": "Directed concept-to-concept relations with evidence level and source references.",
}

LEGACY_FRAME_GRAPH_KEYS = {
    "knowledge_graph",
    "knowledge_graphs",
    "concept_graph",
    "concept_graphs",
}


def ensure_global_kg_space() -> Space:
    """Create or return the singleton global knowledge-graph space."""
    with SyncSessionLocal() as session:
        space = session.query(Space).filter_by(name=GLOBAL_KG_SPACE_NAME).first()
        if space:
            return space

        space = Space(
            space_id=uuid.uuid4(),
            name=GLOBAL_KG_SPACE_NAME,
            description="Singleton global concept graph space across all domains.",
            domain=GLOBAL_KG_SPACE_DOMAIN,
            extraction_schema=GLOBAL_KG_SPACE_SCHEMA,
            system_prompt=GLOBAL_KG_SYSTEM_PROMPT,
            field_descriptions=GLOBAL_KG_FIELD_DESCRIPTIONS,
            version=1,
        )
        session.add(space)
        session.commit()
        session.refresh(space)
        return space


def ensure_global_kg_space_id() -> uuid.UUID:
    """Return the singleton global knowledge-graph space ID."""
    return ensure_global_kg_space().space_id


def clear_knowledge_graph_projections(
    project_id: uuid.UUID | None = None,
    frame_id: uuid.UUID | None = None,
    include_legacy_spaces: bool = True,
) -> dict:
    """Soft-delete existing knowledge-graph projections.

    By default clears projections in the global KG space and known legacy KG
    space names.
    """
    now = datetime.now(timezone.utc)

    with SyncSessionLocal() as session:
        candidate_spaces = session.query(Space).all()
        if include_legacy_spaces:
            target_space_ids = {
                s.space_id
                for s in candidate_spaces
                if (s.name or "").strip().lower() in LEGACY_KG_SPACE_NAMES
            }
        else:
            target_space_ids = {
                s.space_id
                for s in candidate_spaces
                if s.name == GLOBAL_KG_SPACE_NAME
            }

        if not target_space_ids:
            return {
                "deleted_count": 0,
                "space_count": 0,
                "project_id": str(project_id) if project_id else None,
                "frame_id": str(frame_id) if frame_id else None,
            }

        q = session.query(Projection).filter(Projection.space_id.in_(target_space_ids))
        q = q.filter(Projection.deleted_at.is_(None))
        if frame_id:
            q = q.filter(Projection.frame_id == frame_id)
        if project_id:
            q = q.join(KnowledgeFrame, Projection.frame_id == KnowledgeFrame.frame_id)
            q = q.filter(KnowledgeFrame.project_id == project_id)

        rows = q.all()
        for row in rows:
            row.deleted_at = now
        session.commit()

        return {
            "deleted_count": len(rows),
            "space_count": len(target_space_ids),
            "project_id": str(project_id) if project_id else None,
            "frame_id": str(frame_id) if frame_id else None,
        }


def purge_legacy_graph_sections(project_id: uuid.UUID | None = None) -> dict:
    """Remove legacy graph sections from knowledge-frame content if present."""

    def _looks_like_graph_payload(value) -> bool:
        if not isinstance(value, dict):
            return False
        keys = {str(k).lower() for k in value}
        return (
            {"nodes", "edges"}.issubset(keys)
            or {"concepts", "relations"}.issubset(keys)
        )

    with SyncSessionLocal() as session:
        q = session.query(KnowledgeFrame)
        if project_id:
            q = q.filter(KnowledgeFrame.project_id == project_id)
        frames = q.all()

        updated = 0
        for frame in frames:
            content = frame.content or {}
            if not isinstance(content, dict):
                continue

            changed = False
            next_content = dict(content)
            for key in list(next_content.keys()):
                key_norm = str(key).strip().lower()
                if key_norm in LEGACY_FRAME_GRAPH_KEYS and _looks_like_graph_payload(next_content[key]):
                    del next_content[key]
                    changed = True

            if changed:
                frame.content = next_content
                updated += 1

        if updated:
            session.commit()

        return {
            "updated_frames": updated,
            "project_id": str(project_id) if project_id else None,
        }
