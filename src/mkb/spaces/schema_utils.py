"""Utilities for normalizing space schemas and projection payloads."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any


_TYPE_ALIASES = {
    "array": "list",
    "object": "dict",
}


def _canonical_type(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    normalized = value.strip().lower().replace("-", "_")
    return _TYPE_ALIASES.get(normalized, normalized)


def normalize_extraction_schema(extraction_schema: dict | None) -> dict:
    """Normalize a space extraction schema to a canonical, backwards-compatible form."""
    if not isinstance(extraction_schema, dict):
        return {}
    return {str(key): _normalize_schema_node(value) for key, value in extraction_schema.items()}


def _normalize_schema_node(node: Any) -> Any:
    if isinstance(node, dict):
        normalized = {key: _normalize_schema_node(value) for key, value in node.items()}

        node_type = _canonical_type(normalized.get("type"))
        if node_type is not None:
            normalized["type"] = node_type

        item_type = normalized.get("item_type")
        if item_type is not None:
            normalized["item_type"] = _canonical_type(item_type)

        if "properties" in normalized and "item_schema" not in normalized:
            properties = normalized.get("properties")
            if isinstance(properties, dict):
                normalized["item_schema"] = {
                    str(key): _normalize_schema_node(value)
                    for key, value in properties.items()
                }

        if node_type == "list" and "items" in normalized:
            items = normalized["items"]
            if isinstance(items, dict):
                item_node_type = _canonical_type(items.get("type"))
                if item_node_type in {"string", "integer", "number", "boolean"}:
                    normalized.setdefault("item_type", item_node_type)
                elif "properties" in items or "item_schema" in items:
                    normalized.setdefault(
                        "item_schema",
                        _normalize_schema_node(items.get("item_schema") or items.get("properties") or {}),
                    )

        return normalized

    if isinstance(node, list):
        return [_normalize_schema_node(item) for item in node]

    return deepcopy(node)


def normalize_projection_data(data: dict | None, extraction_schema: dict | None) -> tuple[dict, dict]:
    """Normalize projection payloads according to the space schema.

    Returns a tuple of:
      1. normalized data
      2. validation metadata containing coercions, filtering, and warnings
    """
    schema = normalize_extraction_schema(extraction_schema)
    if not isinstance(data, dict):
        return {}, {"warnings": ["Projection data was not a mapping."]}

    validation: dict[str, Any] = {
        "missing_required": [],
        "coerced_fields": defaultdict(int),
        "filtered_counts": defaultdict(int),
        "warnings": [],
    }

    normalized: dict[str, Any] = {}
    ordered_keys = list(dict.fromkeys([*schema.keys(), *data.keys()]))
    for key in ordered_keys:
        normalized[str(key)] = _normalize_value(
            data.get(key),
            schema.get(key),
            str(key),
            validation,
        )

    result = {
        "missing_required": validation["missing_required"],
        "coerced_fields": dict(validation["coerced_fields"]),
        "filtered_counts": dict(validation["filtered_counts"]),
        "warnings": validation["warnings"],
    }
    compact_result = {key: value for key, value in result.items() if value}
    return normalized, compact_result


def stringify_value(value: Any) -> str:
    """Render nested projection values as table-friendly strings."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return "; ".join(f"{key}: {stringify_value(item)}" for key, item in value.items())
    if isinstance(value, list):
        return ", ".join(stringify_value(item) for item in value if item is not None)
    return str(value)


def _normalize_value(value: Any, schema_node: Any, path: str, validation: dict[str, Any]) -> Any:
    if not isinstance(schema_node, dict):
        return _normalize_unknown(value)

    schema_type = _canonical_type(schema_node.get("type"))
    if schema_type == "list":
        return _normalize_list(value, schema_node, path, validation)
    if schema_type in {"dict", "object"}:
        return _normalize_object(value, schema_node.get("item_schema") or {}, path, validation)
    if schema_type == "string":
        return _coerce_string(value, path, validation)
    if schema_type == "integer":
        return _coerce_integer(value, path, validation)
    if schema_type == "number":
        return _coerce_number(value, path, validation)
    if schema_type == "boolean":
        return _coerce_boolean(value, path, validation)

    item_schema = schema_node.get("item_schema")
    if isinstance(item_schema, dict):
        return _normalize_object(value, item_schema, path, validation)

    return _normalize_unknown(value)


def _normalize_list(value: Any, schema_node: dict, path: str, validation: dict[str, Any]) -> list:
    if value is None:
        items: list[Any] = []
    elif isinstance(value, list):
        items = value
    else:
        items = [value]
        _record_coercion(validation, path)

    item_schema = schema_node.get("item_schema")
    item_type = _canonical_type(schema_node.get("item_type"))
    filter_config = schema_node.get("filter")

    normalized_items: list[Any] = []
    prefiltered_count = 0
    for item in items:
        if isinstance(filter_config, dict) and isinstance(item, dict) and not _matches_filter(item, filter_config):
            prefiltered_count += 1
            continue

        item_path = f"{path}[]"
        if isinstance(item_schema, dict) and item_schema:
            normalized_items.append(_normalize_object(item, item_schema, item_path, validation))
        elif item_type == "string":
            normalized_items.append(_coerce_string(item, path, validation))
        elif item_type == "integer":
            normalized_items.append(_coerce_integer(item, path, validation))
        elif item_type == "number":
            normalized_items.append(_coerce_number(item, path, validation))
        elif item_type == "boolean":
            normalized_items.append(_coerce_boolean(item, path, validation))
        else:
            normalized_items.append(_normalize_unknown(item))

    if prefiltered_count:
        validation["filtered_counts"][path] += prefiltered_count

    return _apply_filter(normalized_items, filter_config, path, validation)


def _normalize_object(value: Any, field_schema: dict[str, Any], path: str, validation: dict[str, Any]) -> dict:
    if value is None:
        mapping: dict[str, Any] = {}
    elif isinstance(value, dict):
        mapping = value
    else:
        mapping = {"value": value}
        _record_coercion(validation, path)

    normalized: dict[str, Any] = {}
    ordered_keys = list(dict.fromkeys([*field_schema.keys(), *mapping.keys()]))
    for key in ordered_keys:
        sub_path = f"{path}.{key}" if path else str(key)
        schema_node = field_schema.get(key)
        field_value = mapping.get(key)
        if isinstance(schema_node, dict) and schema_node.get("required") and field_value in (None, "", [], {}):
            validation["missing_required"].append(sub_path)
        normalized[str(key)] = _normalize_value(field_value, schema_node, sub_path, validation)
    return normalized


def _apply_filter(items: list[Any], filter_config: Any, path: str, validation: dict[str, Any]) -> list[Any]:
    if not isinstance(filter_config, dict):
        return items

    kept_items: list[Any] = []
    filtered_count = 0
    for item in items:
        if _matches_filter(item, filter_config):
            kept_items.append(item)
        else:
            filtered_count += 1

    if filtered_count:
        validation["filtered_counts"][path] += filtered_count

    return kept_items


def _matches_filter(item: Any, filter_config: dict[str, Any]) -> bool:
    if not isinstance(item, dict):
        return True

    field = filter_config.get("field")
    if not field:
        return True

    current = _get_nested_value(item, str(field))

    if "equals" in filter_config:
        return current == filter_config["equals"]
    if "in" in filter_config:
        return current in set(filter_config["in"])
    if "not_equals" in filter_config:
        return current != filter_config["not_equals"]
    if "not_in" in filter_config:
        return current not in set(filter_config["not_in"])
    if "exists" in filter_config:
        return (current not in (None, "", [], {})) is bool(filter_config["exists"])

    return True


def _get_nested_value(item: dict[str, Any], field: str) -> Any:
    current: Any = item
    for part in field.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _coerce_string(value: Any, path: str, validation: dict[str, Any]) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        _record_coercion(validation, path)
        return ", ".join(str(item) for item in value if item is not None)
    if isinstance(value, dict):
        _record_coercion(validation, path)
        return stringify_value(value)
    return str(value)


def _coerce_integer(value: Any, path: str, validation: dict[str, Any]) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        _record_coercion(validation, path)
        return int(value)
    if isinstance(value, int):
        _validate_evidence_level_if_needed(value, path, validation)
        return value
    if isinstance(value, float) and value.is_integer():
        _record_coercion(validation, path)
        coerced = int(value)
        _validate_evidence_level_if_needed(coerced, path, validation)
        return coerced
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit() or (stripped.startswith("-") and stripped[1:].isdigit()):
            _record_coercion(validation, path)
            coerced = int(stripped)
            _validate_evidence_level_if_needed(coerced, path, validation)
            return coerced
    validation["warnings"].append(f"Could not coerce {path} to integer.")
    return value


def _coerce_number(value: Any, path: str, validation: dict[str, Any]) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            _record_coercion(validation, path)
            return float(value.strip())
        except ValueError:
            validation["warnings"].append(f"Could not coerce {path} to number.")
            return value
    validation["warnings"].append(f"Could not coerce {path} to number.")
    return value


def _coerce_boolean(value: Any, path: str, validation: dict[str, Any]) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            _record_coercion(validation, path)
            return True
        if lowered in {"false", "no", "0"}:
            _record_coercion(validation, path)
            return False
    validation["warnings"].append(f"Could not coerce {path} to boolean.")
    return value


def _validate_evidence_level_if_needed(value: int, path: str, validation: dict[str, Any]) -> None:
    if path.endswith("evidence_level") and value not in {1, 2, 3, 4}:
        validation["warnings"].append(f"{path} should be between 1 and 4.")


def _normalize_unknown(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize_unknown(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_unknown(item) for item in value]
    return value


def _record_coercion(validation: dict[str, Any], path: str) -> None:
    validation["coerced_fields"][path] += 1
