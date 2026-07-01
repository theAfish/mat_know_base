"""JSON-safe validation error formatting for agent tool responses."""

from __future__ import annotations

from pydantic import ValidationError


def json_safe_validation_errors(exc: ValidationError) -> list[dict]:
    """Remove exception objects from Pydantic error context before ADK sees it."""
    return exc.errors(include_url=False, include_context=False, include_input=True)


def compact_validation_errors(exc: ValidationError, *, limit: int = 20) -> list[dict]:
    """Return bounded validation errors for agent-facing tool responses.

    Pydantic's ``input`` field can contain the full invalid graph section. That
    is useful for local debugging but expensive and distracting in LLM history.
    """
    errors = exc.errors(include_url=False, include_context=False, include_input=False)
    compact = []
    for error in errors[: max(1, int(limit))]:
        compact.append({
            "loc": list(error.get("loc", [])),
            "msg": error.get("msg"),
            "type": error.get("type"),
        })
    if len(errors) > len(compact):
        compact.append({
            "loc": [],
            "msg": f"{len(errors) - len(compact)} additional validation errors omitted",
            "type": "truncated",
        })
    return compact
