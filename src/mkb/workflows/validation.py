"""JSON-safe validation error formatting for agent tool responses."""

from __future__ import annotations

from pydantic import ValidationError


def json_safe_validation_errors(exc: ValidationError) -> list[dict]:
    """Remove exception objects from Pydantic error context before ADK sees it."""
    return exc.errors(include_url=False, include_context=False, include_input=True)
