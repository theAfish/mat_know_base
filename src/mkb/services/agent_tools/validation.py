from __future__ import annotations

import uuid

from mkb.agents.tools._ids import invalid_identifier_message, parse_uuidish


def validate_identifier(name: str, value: str) -> tuple[uuid.UUID | None, dict | None]:
    parsed = parse_uuidish(value)
    return (parsed, None) if parsed else (None, {"error": invalid_identifier_message(name, value)})
