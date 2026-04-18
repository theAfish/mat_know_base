"""
Space registry — CRUD operations for domain-specific extraction spaces.

A Space defines what structured data to extract from knowledge frames
for a specific research domain. Think of it as a "projection schema"
that tells the projection agent what to look for.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from mkb.db.engine import SyncSessionLocal
from mkb.db.models import Space

logger = logging.getLogger(__name__)


def create_space(
    name: str,
    domain: str,
    extraction_schema: dict,
    system_prompt: str,
    field_descriptions: dict,
    description: str | None = None,
) -> dict:
    """Create a new space definition.

    Args:
        name: Unique name for the space (e.g., "catalysis").
        domain: Research domain (e.g., "heterogeneous catalysis").
        extraction_schema: JSON schema defining what fields to extract.
        system_prompt: Domain-specific instructions for the projection agent.
        field_descriptions: Per-field extraction guidance.
        description: Optional human-readable description.

    Returns:
        Dict with space_id and name.
    """
    with SyncSessionLocal() as session:
        existing = session.query(Space).filter_by(name=name).first()
        if existing:
            return {"error": f"Space '{name}' already exists.", "space_id": str(existing.space_id)}

        space = Space(
            space_id=uuid.uuid4(),
            name=name,
            description=description,
            domain=domain,
            extraction_schema=extraction_schema,
            system_prompt=system_prompt,
            field_descriptions=field_descriptions,
            version=1,
        )
        session.add(space)
        session.commit()
        return {"space_id": str(space.space_id), "name": space.name}


def get_space(space_id_or_name: str) -> dict | None:
    """Get a space by ID or name."""
    with SyncSessionLocal() as session:
        # Try UUID first
        try:
            sid = uuid.UUID(space_id_or_name)
            space = session.query(Space).filter_by(space_id=sid).first()
        except ValueError:
            space = session.query(Space).filter_by(name=space_id_or_name).first()

        if not space:
            return None
        return _space_to_dict(space)


def list_spaces() -> list[dict]:
    """List all spaces."""
    with SyncSessionLocal() as session:
        spaces = session.query(Space).order_by(Space.name).all()
        return [_space_to_dict(s) for s in spaces]


def update_space(
    space_id: str | uuid.UUID,
    **changes,
) -> dict:
    """Update a space definition. Bumps version automatically.

    Accepted keys: description, extraction_schema, system_prompt,
    field_descriptions, domain.
    """
    sid = uuid.UUID(str(space_id))
    allowed_fields = {"description", "extraction_schema", "system_prompt", "field_descriptions", "domain"}

    with SyncSessionLocal() as session:
        space = session.query(Space).filter_by(space_id=sid).first()
        if not space:
            return {"error": f"Space {space_id} not found."}

        for key, value in changes.items():
            if key in allowed_fields:
                setattr(space, key, value)
            else:
                logger.warning("Ignoring unknown field: %s", key)

        space.version = space.version + 1
        session.commit()
        return {"space_id": str(space.space_id), "version": space.version}


def load_space_from_file(filepath: str | Path) -> dict:
    """Load a space definition from a JSON file and create it.

    Expected JSON format:
    {
        "name": "catalysis",
        "domain": "heterogeneous catalysis",
        "description": "...",
        "extraction_schema": {...},
        "system_prompt": "...",
        "field_descriptions": {...}
    }
    """
    path = Path(filepath)
    data = json.loads(path.read_text())
    return create_space(
        name=data["name"],
        domain=data["domain"],
        extraction_schema=data["extraction_schema"],
        system_prompt=data["system_prompt"],
        field_descriptions=data["field_descriptions"],
        description=data.get("description"),
    )


def _space_to_dict(space: Space) -> dict:
    return {
        "space_id": str(space.space_id),
        "name": space.name,
        "description": space.description,
        "domain": space.domain,
        "extraction_schema": space.extraction_schema,
        "system_prompt": space.system_prompt,
        "field_descriptions": space.field_descriptions,
        "version": space.version,
        "created_at": space.created_at.isoformat() if space.created_at else None,
        "updated_at": space.updated_at.isoformat() if space.updated_at else None,
    }
