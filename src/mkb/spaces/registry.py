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
from mkb.spaces.schema_utils import (
    merge_field_descriptions_into_schema,
    normalize_extraction_schema,
)

logger = logging.getLogger(__name__)


VALID_PURPOSES = {"tabular_database", "qa_benchmark", "skill_cards", "freeform"}
VALID_REVIEW_SEARCH_TOOLS = {"web", "uniprot", "crossref", "ncbi"}
VALID_POST_PROCESSOR_TOOL_GROUPS = {"reading", "web", "uniprot", "crossref", "ncbi"}


def _maybe_normalize_schema(extraction_schema: dict, purpose: str) -> dict:
    """Tabular schemas get normalized; freeform/qa/skill keep their shape."""
    if purpose == "tabular_database":
        return normalize_extraction_schema(extraction_schema)
    return extraction_schema if isinstance(extraction_schema, dict) else {}


def _normalize_review_search_tools(value) -> list[str]:
    if value is None:
        return ["web"]
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list):
        candidates = value
    else:
        candidates = []

    tools: list[str] = []
    for item in candidates:
        key = str(item).strip().lower()
        if key in VALID_REVIEW_SEARCH_TOOLS and key not in tools:
            tools.append(key)
    return tools or ["web"]


def _slug_processor_id(value: str | None, fallback: str = "default") -> str:
    text = (value or fallback).strip().lower()
    chars = [ch if ch.isalnum() else "_" for ch in text]
    slug = "".join(chars).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or fallback


def _normalize_tool_groups(value) -> list[str]:
    if value is None:
        candidates = ["reading"]
    elif isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list):
        candidates = value
    else:
        candidates = []
    groups: list[str] = []
    for item in candidates:
        key = str(item).strip().lower()
        if key in VALID_POST_PROCESSOR_TOOL_GROUPS and key not in groups:
            groups.append(key)
    return groups or ["reading"]


def _default_post_processor_from_legacy(
    *,
    review_prompt: str | None,
    review_allow_search: bool,
    review_search_tools,
) -> dict:
    tool_groups = ["reading"]
    if review_allow_search:
        for tool in _normalize_review_search_tools(review_search_tools):
            if tool not in tool_groups:
                tool_groups.append(tool)
    return {
        "id": "default",
        "name": "Default reviewer",
        "description": "General projection review and correction.",
        "prompt": review_prompt or None,
        "tool_groups": tool_groups,
        "skill_ids": [],
        "output_columns": [],
        "enabled": True,
    }


def _normalize_output_columns(value) -> list[dict]:
    if value is None:
        return []
    if isinstance(value, str):
        candidates = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        candidates = value
    else:
        candidates = []

    columns: list[dict] = []
    seen: set[str] = set()
    for item in candidates:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("id") or "").strip()
            description = str(item.get("description") or "").strip()
        else:
            name = str(item).strip()
            description = ""
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        columns.append({
            "name": name,
            "description": description,
        })
    return columns


def _normalize_post_processor_script(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    script_id = str(value.get("script_id") or "").strip()
    if not script_id:
        return None
    try:
        timeout_seconds = int(value.get("timeout_seconds", 30))
    except (TypeError, ValueError):
        timeout_seconds = 30
    return {
        "script_id": script_id,
        "timeout_seconds": min(300, max(1, timeout_seconds)),
    }


def _normalize_post_processors(value, *, legacy_defaults: dict | None = None) -> list[dict]:
    processors: list[dict] = []
    if isinstance(value, list):
        for index, raw in enumerate(value):
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or raw.get("id") or f"Reviewer {index + 1}").strip()
            processor_id = _slug_processor_id(str(raw.get("id") or name), fallback=f"reviewer_{index + 1}")
            processors.append({
                "id": processor_id,
                "name": name or processor_id,
                "description": str(raw.get("description") or ""),
                "prompt": raw.get("prompt") if isinstance(raw.get("prompt"), str) and raw.get("prompt").strip() else None,
                "tool_groups": _normalize_tool_groups(raw.get("tool_groups") or raw.get("tools")),
                "skill_ids": _normalize_skill_ids(raw.get("skill_ids") or raw.get("skills")),
                "script": _normalize_post_processor_script(raw.get("script")),
                "output_columns": _normalize_output_columns(
                    raw.get("output_columns")
                    or raw.get("allowed_output_columns")
                    or raw.get("new_columns")
                ),
                "enabled": bool(raw.get("enabled", True)),
            })

    if not processors:
        legacy_defaults = legacy_defaults or {}
        processors.append(_default_post_processor_from_legacy(
            review_prompt=legacy_defaults.get("review_prompt"),
            review_allow_search=bool(legacy_defaults.get("review_allow_search", False)),
            review_search_tools=legacy_defaults.get("review_search_tools"),
        ))

    seen: set[str] = set()
    unique: list[dict] = []
    for processor in processors:
        pid = processor["id"]
        if pid in seen:
            suffix = 2
            base = pid
            while f"{base}_{suffix}" in seen:
                suffix += 1
            processor = {**processor, "id": f"{base}_{suffix}"}
        seen.add(processor["id"])
        unique.append(processor)
    return unique


def _normalize_skill_ids(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list):
        candidates = value
    else:
        candidates = []
    skill_ids: list[str] = []
    for item in candidates:
        key = str(item).strip()
        if key and key not in skill_ids:
            skill_ids.append(key)
    return skill_ids


def resolve_post_processor(space: Space, processor_id: str | None = None) -> dict:
    processors = _normalize_post_processors(
        getattr(space, "post_processors", None),
        legacy_defaults={
            "review_prompt": getattr(space, "review_prompt", None),
            "review_allow_search": getattr(space, "review_allow_search", False),
            "review_search_tools": getattr(space, "review_search_tools", None),
        },
    )
    if processor_id:
        wanted = _slug_processor_id(processor_id)
        for processor in processors:
            if processor["id"] == wanted:
                return processor
    for processor in processors:
        if processor.get("enabled", True):
            return processor
    return processors[0]


def create_space(
    name: str,
    domain: str,
    extraction_schema: dict,
    system_prompt: str,
    field_descriptions: dict | None = None,
    description: str | None = None,
    purpose: str = "tabular_database",
    review_prompt: str | None = None,
    review_trackable: bool = True,
    review_allow_search: bool = False,
    review_search_tools: list[str] | None = None,
    post_processors: list[dict] | None = None,
) -> dict:
    """Create a new space definition.

    Args:
        name: Unique name for the space (e.g., "catalysis").
        domain: Research domain (e.g., "heterogeneous catalysis").
        extraction_schema: JSON schema defining what fields to extract.
        system_prompt: Domain-specific instructions for the projection agent.
        field_descriptions: Legacy per-field extraction guidance. Merged into
            top-level schema descriptions on save.
        description: Optional human-readable description.
        purpose: Kind of projection (tabular_database | qa_benchmark | skill_cards | freeform).
        review_prompt: Optional override for the projection reviewer prompt.
            When None, the reviewer uses the default prompt for ``purpose``.

    Returns:
        Dict with space_id and name.
    """
    if purpose not in VALID_PURPOSES:
        return {"error": f"Invalid purpose '{purpose}'. Must be one of {sorted(VALID_PURPOSES)}."}

    with SyncSessionLocal() as session:
        existing = session.query(Space).filter_by(name=name).first()
        if existing:
            return {"error": f"Space '{name}' already exists.", "space_id": str(existing.space_id)}

        merged_schema = merge_field_descriptions_into_schema(extraction_schema, field_descriptions)
        normalized_schema = _maybe_normalize_schema(merged_schema, purpose)
        normalized_search_tools = _normalize_review_search_tools(review_search_tools)
        normalized_processors = _normalize_post_processors(
            post_processors,
            legacy_defaults={
                "review_prompt": review_prompt,
                "review_allow_search": review_allow_search,
                "review_search_tools": normalized_search_tools,
            },
        )

        space = Space(
            space_id=uuid.uuid4(),
            name=name,
            description=description,
            domain=domain,
            purpose=purpose,
            extraction_schema=normalized_schema,
            system_prompt=system_prompt,
            field_descriptions={},
            review_prompt=(review_prompt or None),
            review_trackable=bool(review_trackable),
            review_allow_search=bool(review_allow_search),
            review_search_tools=normalized_search_tools,
            post_processors=normalized_processors,
            version=1,
        )
        session.add(space)
        session.commit()
        return {"space_id": str(space.space_id), "name": space.name, "purpose": purpose}


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
    field_descriptions, domain, purpose.
    """
    sid = uuid.UUID(str(space_id))
    allowed_fields = {
        "description",
        "extraction_schema",
        "system_prompt",
        "field_descriptions",
        "domain",
        "purpose",
        "name",
        "review_prompt",
        "review_trackable",
        "review_allow_search",
        "review_search_tools",
        "post_processors",
    }

    with SyncSessionLocal() as session:
        space = session.query(Space).filter_by(space_id=sid).first()
        if not space:
            return {"error": f"Space {space_id} not found."}

        new_purpose = changes.get("purpose", space.purpose)
        if "purpose" in changes and new_purpose not in VALID_PURPOSES:
            return {"error": f"Invalid purpose '{new_purpose}'."}

        if "extraction_schema" in changes or "field_descriptions" in changes:
            schema_value = changes.get("extraction_schema", space.extraction_schema)
            guidance_value = changes.get("field_descriptions", space.field_descriptions)
            changes["extraction_schema"] = merge_field_descriptions_into_schema(
                schema_value,
                guidance_value,
            )
            changes["field_descriptions"] = {}

        for key, value in changes.items():
            if key not in allowed_fields:
                logger.warning("Ignoring unknown field: %s", key)
                continue

            if key == "extraction_schema":
                value = _maybe_normalize_schema(value, new_purpose)
            if key == "review_prompt":
                # Normalize empty string to NULL so the default prompt is used.
                value = value if (value and str(value).strip()) else None
            if key == "review_trackable":
                value = bool(value)
            if key == "review_allow_search":
                value = bool(value)
            if key == "review_search_tools":
                value = _normalize_review_search_tools(value)
            if key == "post_processors":
                value = _normalize_post_processors(
                    value,
                    legacy_defaults={
                        "review_prompt": changes.get("review_prompt", space.review_prompt),
                        "review_allow_search": changes.get("review_allow_search", space.review_allow_search),
                        "review_search_tools": changes.get("review_search_tools", space.review_search_tools),
                    },
                )
            setattr(space, key, value)

        space.version = space.version + 1
        session.commit()
        return {"space_id": str(space.space_id), "version": space.version, "purpose": space.purpose}


def delete_space(space_id: str | uuid.UUID) -> dict:
    """Delete a space. Returns {ok: True} or {error: ...}."""
    sid = uuid.UUID(str(space_id))
    with SyncSessionLocal() as session:
        space = session.query(Space).filter_by(space_id=sid).first()
        if not space:
            return {"error": f"Space {space_id} not found."}
        name = space.name
        session.delete(space)
        session.commit()
        return {"ok": True, "deleted": name}


def load_space_from_file(filepath: str | Path) -> dict:
    """Load a space definition from a JSON file and create it.

    Expected JSON format:
    {
        "name": "catalysis",
        "domain": "heterogeneous catalysis",
        "description": "...",
        "extraction_schema": {...},
        "system_prompt": "...",
        "field_descriptions": {...}  # legacy; merged into schema descriptions
    }
    """
    path = Path(filepath)
    data = json.loads(path.read_text())
    return create_space(
        name=data["name"],
        domain=data["domain"],
        extraction_schema=data["extraction_schema"],
        system_prompt=data["system_prompt"],
        field_descriptions=data.get("field_descriptions", {}),
        description=data.get("description"),
        purpose=data.get("purpose", "tabular_database"),
        review_prompt=data.get("review_prompt"),
        review_trackable=bool(data.get("review_trackable", True)),
        review_allow_search=bool(data.get("review_allow_search", False)),
        review_search_tools=_normalize_review_search_tools(data.get("review_search_tools")),
        post_processors=data.get("post_processors"),
    )


def _space_to_dict(space: Space) -> dict:
    purpose = getattr(space, "purpose", "tabular_database") or "tabular_database"
    schema = merge_field_descriptions_into_schema(
        space.extraction_schema or {},
        space.field_descriptions or {},
    )
    if purpose == "tabular_database":
        schema = normalize_extraction_schema(schema)
    return {
        "space_id": str(space.space_id),
        "name": space.name,
        "description": space.description,
        "domain": space.domain,
        "purpose": purpose,
        "extraction_schema": schema,
        "system_prompt": space.system_prompt,
        "field_descriptions": {},
        "review_prompt": getattr(space, "review_prompt", None),
        "review_trackable": bool(getattr(space, "review_trackable", True)),
        "review_allow_search": bool(getattr(space, "review_allow_search", False)),
        "review_search_tools": _normalize_review_search_tools(
            getattr(space, "review_search_tools", None)
        ),
        "post_processors": _normalize_post_processors(
            getattr(space, "post_processors", None),
            legacy_defaults={
                "review_prompt": getattr(space, "review_prompt", None),
                "review_allow_search": getattr(space, "review_allow_search", False),
                "review_search_tools": getattr(space, "review_search_tools", None),
            },
        ),
        "version": space.version,
        "created_at": space.created_at.isoformat() if space.created_at else None,
        "updated_at": space.updated_at.isoformat() if space.updated_at else None,
    }
