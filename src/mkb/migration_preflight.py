"""Deterministic, read-only comparison of migration preservation inventories."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mkb.exceptions import ValidationError


def load_inventory(path: str | Path) -> dict[str, Any]:
    """Load one inventory JSON document without changing the source file."""
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Cannot read migration inventory {source}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("format_version") != 1:
        raise ValidationError(f"Unsupported migration inventory format: {source}")
    return payload


def _identity(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _index(rows: list[dict[str, Any]], *fields: str) -> dict[str, dict[str, Any]]:
    return {
        _identity([row.get(field) for field in fields]): row
        for row in rows
    }


def compare_migration_inventories(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    """Compare preservation inventories; additions are allowed, loss/change blocks."""
    if before.get("format_version") != 1 or after.get("format_version") != 1:
        raise ValidationError("Both inventories must use format_version 1")

    blockers: list[dict[str, Any]] = []
    table_results: dict[str, Any] = {}
    before_tables = before.get("database", {}).get("tables", {})
    after_tables = after.get("database", {}).get("tables", {})
    for table in sorted(set(before_tables) | set(after_tables)):
        old = before_tables.get(table, {})
        new = after_tables.get(table, {})
        old_ids = {_identity(value) for value in old.get("primary_keys", [])}
        new_ids = {_identity(value) for value in new.get("primary_keys", [])}
        missing = sorted(old_ids - new_ids)
        added = sorted(new_ids - old_ids)
        table_results[table] = {
            "before_count": int(old.get("row_count", 0)),
            "after_count": int(new.get("row_count", 0)),
            "missing_primary_keys": [json.loads(value) for value in missing],
            "added_primary_keys": [json.loads(value) for value in added],
        }
        if table in before_tables and table not in after_tables:
            blockers.append({"category": "missing_table", "table": table})
        for value in missing:
            blockers.append(
                {
                    "category": "missing_database_id",
                    "table": table,
                    "primary_key": json.loads(value),
                }
            )

    bucket_results: dict[str, Any] = {}
    before_buckets = before.get("object_storage", {}).get("buckets", {})
    after_buckets = after.get("object_storage", {}).get("buckets", {})
    for bucket in sorted(set(before_buckets) | set(after_buckets)):
        old = _index(before_buckets.get(bucket, {}).get("objects", []), "key")
        new = _index(after_buckets.get(bucket, {}).get("objects", []), "key")
        missing = sorted(set(old) - set(new))
        added = sorted(set(new) - set(old))
        changed = []
        for key in sorted(set(old) & set(new)):
            content_field = (
                "sha256"
                if old[key].get("sha256") is not None
                and new[key].get("sha256") is not None
                else "etag"
            )
            differences = {
                field: {"before": old[key].get(field), "after": new[key].get(field)}
                for field in ("bytes", content_field)
                if old[key].get(field) != new[key].get(field)
            }
            if differences:
                changed.append({"key": old[key].get("key"), "changes": differences})
        bucket_results[bucket] = {
            "before_count": len(old),
            "after_count": len(new),
            "missing_keys": [old[key].get("key") for key in missing],
            "added_keys": [new[key].get("key") for key in added],
            "changed_objects": changed,
        }
        for key in missing:
            blockers.append(
                {
                    "category": "missing_object",
                    "bucket": bucket,
                    "key": old[key].get("key"),
                }
            )
        for item in changed:
            blockers.append(
                {
                    "category": "changed_object",
                    "bucket": bucket,
                    **item,
                }
            )

    old_files = _index(before.get("local_files", {}).get("files", []), "root", "path")
    new_files = _index(after.get("local_files", {}).get("files", []), "root", "path")
    missing_files = sorted(set(old_files) - set(new_files))
    added_files = sorted(set(new_files) - set(old_files))
    changed_files = []
    for key in sorted(set(old_files) & set(new_files)):
        differences = {
            field: {
                "before": old_files[key].get(field),
                "after": new_files[key].get(field),
            }
            for field in ("bytes", "sha256")
            if old_files[key].get(field) != new_files[key].get(field)
        }
        if differences:
            changed_files.append(
                {
                    "root": old_files[key].get("root"),
                    "path": old_files[key].get("path"),
                    "changes": differences,
                }
            )
    for key in missing_files:
        blockers.append(
            {
                "category": "missing_local_file",
                "root": old_files[key].get("root"),
                "path": old_files[key].get("path"),
            }
        )
    for item in changed_files:
        blockers.append({"category": "changed_local_file", **item})

    local_result = {
        "before_count": len(old_files),
        "after_count": len(new_files),
        "missing_files": [
            {"root": old_files[key].get("root"), "path": old_files[key].get("path")}
            for key in missing_files
        ],
        "added_files": [
            {"root": new_files[key].get("root"), "path": new_files[key].get("path")}
            for key in added_files
        ],
        "changed_files": changed_files,
    }
    return {
        "format_version": 1,
        "ok": not blockers,
        "summary": {
            "blocker_count": len(blockers),
            "tables_compared": len(table_results),
            "buckets_compared": len(bucket_results),
            "local_files_compared": len(set(old_files) & set(new_files)),
        },
        "blockers": blockers,
        "database": {"tables": table_results},
        "object_storage": {"buckets": bucket_results},
        "local_files": local_result,
    }
