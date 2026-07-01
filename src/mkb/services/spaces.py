"""Spaces API service functions."""

from __future__ import annotations

from mkb.services._api_common import (
    uuid,
)


def create_space(
    name: str,
    domain: str,
    extraction_schema: dict,
    system_prompt: str,
    field_descriptions: dict,
    description: str | None = None,
    purpose: str = "tabular_database",
    review_prompt: str | None = None,
    review_trackable: bool = True,
    review_allow_search: bool = False,
    review_search_tools: list[str] | None = None,
    post_processors: list[dict] | None = None,
) -> dict:
    """Create a new space (domain-specific extraction configuration)."""
    from mkb.spaces.registry import create_space as _create

    return _create(
        name=name,
        domain=domain,
        extraction_schema=extraction_schema,
        system_prompt=system_prompt,
        field_descriptions=field_descriptions,
        description=description,
        purpose=purpose,
        review_prompt=review_prompt,
        review_trackable=review_trackable,
        review_allow_search=review_allow_search,
        review_search_tools=review_search_tools,
        post_processors=post_processors,
    )

def update_space(space_id: str | uuid.UUID, **changes) -> dict:
    """Update fields on an existing space. Bumps version automatically.

    Accepted keys: name, description, extraction_schema, system_prompt,
    field_descriptions, domain, purpose, review_prompt.
    """
    from mkb.spaces.registry import update_space as _update

    return _update(space_id, **changes)

def delete_space(space_id: str | uuid.UUID) -> dict:
    """Delete a space by id."""
    from mkb.spaces.registry import delete_space as _delete

    return _delete(space_id)

def list_spaces() -> list[dict]:
    """List all spaces."""
    from mkb.spaces.registry import list_spaces as _list

    return _list()

def get_space(space_id_or_name: str) -> dict | None:
    """Get a space by ID or name."""
    from mkb.spaces.registry import get_space as _get

    return _get(space_id_or_name)


# ── Projections ──────────────────────────────────────────────────

