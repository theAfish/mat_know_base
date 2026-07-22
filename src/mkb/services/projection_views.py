"""Pure projection-to-table transformations, independent of any UI framework."""

from __future__ import annotations

from pathlib import Path

from mkb.spaces.schema_utils import stringify_value


def mapping_to_rows(mapping: dict) -> list[dict[str, str]]:
    return [{"Field": str(key), "Value": stringify_value(value)} for key, value in mapping.items()]


def paper_folder_name(source_path: str | None) -> str:
    return Path(source_path.rstrip("/\\")).name if source_path and source_path.rstrip("/\\") else ""


def default_visible_columns(columns: list[str], schema_order: list[str] | None = None) -> list[str]:
    ids = [c for c in columns if c.lower() == "id" or c.lower().endswith("_id") or "_id_" in c.lower()]
    metadata = [c for c in ["paper_name", "source_paper_name", "is_core_study_data", "extracted_at"] if c in columns]
    if schema_order:
        schema = [c for c in schema_order if c in columns]
        rest = [c for c in columns if c not in set(schema + metadata + ids)]
        return schema + metadata + rest or columns
    return metadata + [c for c in columns if c not in set(metadata + ids)] or columns


def projection_timestamp(projection: dict) -> str:
    return projection.get("extracted_at") or projection.get("created_at") or ""


def filter_latest_projections(projections: list[dict]) -> list[dict]:
    latest: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for projection in projections:
        key = (str(projection.get("project_id") or ""), str(projection.get("space_id") or projection.get("frame_id") or ""))
        if key not in latest:
            order.append(key)
        if key not in latest or projection_timestamp(projection) >= projection_timestamp(latest[key]):
            latest[key] = projection
    return [latest[key] for key in order]


def _section_rows(value, metadata: dict[str, str], names: dict[str, str]) -> list[dict[str, str]]:
    if isinstance(value, list):
        if not value:
            return []
        if all(isinstance(item, dict) for item in value):
            rows = [{**metadata, **{str(k): stringify_value(v) for k, v in item.items()}} for item in value]
            for row in rows:
                if row.get("source_project_id") in names:
                    row.setdefault("source_paper_name", names[row["source_project_id"]])
            return sorted(rows, key=lambda row: str(row.get("is_core_study_data", "")).lower() not in {"true", "1", "yes"})
        return [{**metadata, "value": stringify_value(item)} for item in value]
    if isinstance(value, dict):
        return [{**metadata, **{str(k): stringify_value(v) for k, v in value.items()}}]
    return [{**metadata, "value": stringify_value(value)}]


def projection_to_section_rows(projection: dict, project_paper_lookup: dict[str, str] | None = None) -> dict[str, list[dict[str, str]]]:
    if projection.get("status") not in ("COMPLETED", "REVIEWED") or not projection.get("data"):
        return {}
    names = project_paper_lookup or {}
    project_id = projection.get("project_id") or ""
    metadata = {"project_id": project_id, "projection_id": projection.get("projection_id") or "", "extracted_at": projection_timestamp(projection)}
    if names.get(project_id):
        metadata["paper_name"] = names[project_id]
    return {name: rows for name, value in projection["data"].items() if (rows := _section_rows(value, metadata, names))}


def build_projection_section_rows(projections: list[dict], project_paper_lookup: dict[str, str] | None = None) -> dict[str, list[dict[str, str]]]:
    combined: dict[str, list[dict[str, str]]] = {}
    for projection in projections:
        for name, rows in projection_to_section_rows(projection, project_paper_lookup).items():
            combined.setdefault(name, []).extend(rows)
    return combined


def paginate_table_rows(rows: list[dict[str, str]], page_size: int, page_number: int) -> tuple[list[dict[str, str]], int]:
    if page_size < 1 or page_number < 1:
        raise ValueError("page_size and page_number must be positive")
    pages = max(1, (len(rows) + page_size - 1) // page_size)
    current = min(page_number, pages)
    start = (current - 1) * page_size
    return rows[start:start + page_size], pages
