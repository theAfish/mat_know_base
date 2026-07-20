"""Canonical graph/projection identity, evidence, merge, and patch rules."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any


def canonical_label(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def relation_identity(source: Any, relation: Any, target: Any) -> tuple[str, str, str]:
    return canonical_label(source), canonical_label(relation), canonical_label(target)


def unique_strings(values: Any) -> list[str]:
    items = values if isinstance(values, list) else ([] if values is None else [values])
    result: list[str] = []
    seen: set[str] = set()
    for value in items:
        text = str(value).strip()
        key = canonical_label(text)
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def preserve_evidence(*groups: list[dict] | None, limit: int = 50) -> list[dict]:
    """Union evidence records without losing distinct paper/path/snippet provenance."""
    result: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    for group in groups:
        for raw in group or []:
            if not isinstance(raw, dict):
                continue
            item = {key: str(raw.get(key) or "").strip() for key in ("project_id", "frame_id", "field_path", "snippet")}
            item = {key: value for key, value in item.items() if value}
            identity = tuple(item.get(key, "") for key in ("project_id", "frame_id", "field_path", "snippet"))
            if item and identity not in seen:
                seen.add(identity)
                result.append(item)
    return result[:limit]


def merge_aliases(canonical: str, *alias_groups: Any) -> list[str]:
    aliases = unique_strings([item for group in alias_groups for item in unique_strings(group)])
    canonical_key = canonical_label(canonical)
    return [alias for alias in aliases if canonical_label(alias) != canonical_key]


def parse_patch_path(path: str) -> list[str | int]:
    parts: list[str | int] = []
    token = ""
    index = 0
    while index < len(path):
        char = path[index]
        if char == ".":
            if token:
                parts.append(token)
                token = ""
        elif char == "[":
            if token:
                parts.append(token)
                token = ""
            close = path.find("]", index)
            value = path[index + 1:close].strip() if close >= 0 else ""
            if close < 0 or not value.isdigit():
                raise ValueError(f"Invalid patch path {path!r}")
            parts.append(int(value))
            index = close
        else:
            token += char
        index += 1
    if token:
        parts.append(token)
    if not parts:
        raise ValueError("Patch path cannot be empty")
    return parts


def set_patch_value(data: Any, path: str, value: Any) -> None:
    parts = parse_patch_path(path)
    current = data
    for part in parts[:-1]:
        if isinstance(part, int):
            if not isinstance(current, list) or part >= len(current):
                raise ValueError(f"Path {path!r} index {part} is out of range")
            current = current[part]
        else:
            if not isinstance(current, dict):
                raise ValueError(f"Path {path!r} expected an object before {part!r}")
            current = current.setdefault(part, {})
    last = parts[-1]
    if isinstance(last, int):
        if not isinstance(current, list) or last >= len(current):
            raise ValueError(f"Path {path!r} index {last} is out of range")
        current[last] = deepcopy(value)
    elif isinstance(current, dict):
        current[last] = deepcopy(value)
    else:
        raise ValueError(f"Path {path!r} expected an object before {last!r}")
