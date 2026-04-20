"""Helpers for parsing LLM-supplied identifiers in agent tools."""

from __future__ import annotations

import re
import uuid
from typing import Any

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}"
    r"-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
)


def invalid_identifier_message(name: str, value: Any) -> str:
    """Build a consistent error message for malformed identifiers."""
    return f"Invalid {name}: {value!r}"


def parse_uuidish(value: Any) -> uuid.UUID | None:
    """Parse a UUID from raw, noisy, or embedded text.

    LLM tool calls sometimes include labels like "asset_id=..." or copy a UUID
    out of a larger sentence. This helper extracts the UUID when possible and
    returns None instead of raising.
    """
    if isinstance(value, uuid.UUID):
        return value
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    try:
        return uuid.UUID(text)
    except (ValueError, AttributeError, TypeError):
        pass

    match = _UUID_RE.search(text)
    if not match:
        return None

    try:
        return uuid.UUID(match.group(0))
    except ValueError:
        return None
