"""Projections page — view and manage space projections."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from mkb import api
from mkb.spaces.schema_utils import stringify_value as _stringify_value


def _mapping_to_rows(mapping: dict) -> list[dict[str, str]]:
    return [
        {"Field": str(key), "Value": _stringify_value(value)}
        for key, value in mapping.items()
    ]


def _projection_section_to_rows(value, metadata: dict[str, str]) -> list[dict[str, str]]:
    base_row = {**metadata}

    if isinstance(value, list):
        if not value:
            return []
        if all(isinstance(item, dict) for item in value):
            rows = [
                {**base_row, **{str(key): _stringify_value(item_value) for key, item_value in item.items()}}
                for item in value
            ]
            return sorted(
                rows,
                key=lambda row: str(row.get("is_core_study_data", "")).lower() not in {"true", "1", "yes"},
            )
        return [{**base_row, "value": _stringify_value(item)} for item in value]

    if isinstance(value, dict):
        return [{**base_row, **{str(key): _stringify_value(item) for key, item in value.items()}}]

    return [{**base_row, "value": _stringify_value(value)}]


def _projection_timestamp(projection: dict) -> str:
    return projection.get("extracted_at") or projection.get("created_at") or ""


def _filter_latest_projections(projections: list[dict]) -> list[dict]:
    latest_by_key: dict[tuple[str, str], dict] = {}

    for projection in projections:
        key = (
            projection.get("space_id") or "",
            projection.get("project_id") or projection.get("frame_id") or projection.get("projection_id") or "",
        )
        current = latest_by_key.get(key)
        if current is None or _projection_timestamp(projection) >= _projection_timestamp(current):
            latest_by_key[key] = projection

    return sorted(
        latest_by_key.values(),
        key=lambda projection: _projection_timestamp(projection),
        reverse=True,
    )


def _projection_to_section_rows(projection: dict) -> dict[str, list[dict[str, str]]]:
    if projection.get("status") != "COMPLETED" or not projection.get("data"):
        return {}

    metadata = {
        "project_id": projection.get("project_id") or "",
        "projection_id": projection.get("projection_id") or "",
        "extracted_at": _projection_timestamp(projection),
    }

    section_rows: dict[str, list[dict[str, str]]] = {}
    for section_name, section_value in projection["data"].items():
        rows = _projection_section_to_rows(section_value, metadata)
        if rows:
            section_rows[section_name] = rows
    return section_rows


def _build_projection_section_rows(projections: list[dict]) -> dict[str, list[dict[str, str]]]:
    section_rows: dict[str, list[dict[str, str]]] = {}
    for projection in projections:
        for section_name, rows in _projection_to_section_rows(projection).items():
            section_rows.setdefault(section_name, []).extend(rows)
    return section_rows


def _paginate_table_rows(rows: list[dict[str, str]], page_size: int, page_number: int) -> tuple[list[dict[str, str]], int]:
    if page_size < 1:
        raise ValueError("page_size must be positive")
    if page_number < 1:
        raise ValueError("page_number must be positive")

    total_pages = max(1, (len(rows) + page_size - 1) // page_size)
    current_page = min(page_number, total_pages)
    start = (current_page - 1) * page_size
    end = start + page_size
    return rows[start:end], total_pages


def _render_combined_projection_table(projections: list[dict]):
    section_rows = _build_projection_section_rows(projections)

    st.subheader("All Extracted Projection Rows")
    if not section_rows:
        st.info("No completed projection data available for this space yet.")
        return

    page_size = int(
        st.number_input(
            "Rows per page",
            min_value=10,
            max_value=500,
            value=50,
            step=10,
        )
    )

    for section_name, rows in section_rows.items():
        st.markdown(f"#### {section_name.replace('_', ' ').title()}")

        _, total_pages = _paginate_table_rows(rows, page_size=page_size, page_number=1)
        page_number = int(
            st.number_input(
                f"Page for {section_name}",
                min_value=1,
                max_value=total_pages,
                value=1,
                step=1,
                key=f"projection_section_page_{section_name}",
            )
        )
        page_rows, _ = _paginate_table_rows(rows, page_size=page_size, page_number=page_number)

        start_index = (page_number - 1) * page_size + 1
        end_index = min(page_number * page_size, len(rows))
        st.caption(f"Showing rows {start_index}-{end_index} of {len(rows)}")

        table = pd.DataFrame(page_rows)
        priority_columns = ["project_id", "is_core_study_data", "source_project_id", "extracted_at", "projection_id"]
        ordered_columns = priority_columns + [
            column for column in table.columns if column not in priority_columns
        ]
        st.dataframe(table[ordered_columns], width="stretch", hide_index=True)


def _render_mapping_table(mapping: dict):
    table = pd.DataFrame(_mapping_to_rows(mapping)).set_index("Field")
    st.table(table)


def _render_projection_section(name: str, value, source_project_id: str | None = None):
    st.markdown(f"#### {name.replace('_', ' ').title()}")

    if isinstance(value, list):
        if not value:
            st.caption("No entries.")
            return

        if all(isinstance(item, dict) for item in value):
            rows = []
            for item in value:
                row = {str(key): _stringify_value(val) for key, val in item.items()}
                if source_project_id and "source_project_id" not in row:
                    row["source_project_id"] = source_project_id
                rows.append(row)
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
            return

        st.write([_stringify_value(item) for item in value])
        return

    if isinstance(value, dict):
        _render_mapping_table(value)
        return

    st.write(value)


def _render_projection_data(data: dict, source_project_id: str | None = None):
    for section_name, section_value in data.items():
        _render_projection_section(section_name, section_value, source_project_id=source_project_id)


def render():
    st.header("Projections")

    # Space selector
    spaces = api.list_spaces()
    if not spaces:
        st.info("No spaces defined yet. Create a space first using the CLI.")
        st.code("mkb space create --name catalysis --domain 'heterogeneous catalysis' ...")
        return

    space_options = {s["name"]: s["space_id"] for s in spaces}
    selected_space = st.selectbox("Select a space", list(space_options.keys()))

    if not selected_space:
        return

    space_id = space_options[selected_space]
    space = api.get_space(space_id)

    # Space info
    with st.expander("Space Details"):
        st.write(f"**Domain:** {space['domain']}")
        st.write(f"**Version:** {space['version']}")
        if space.get("description"):
            st.write(f"**Description:** {space['description']}")
        st.json(space["extraction_schema"])

    # Main tabs: Raw Projections vs Reviewed Projections
    tab_raw, tab_reviewed = st.tabs(["Raw Projections", "Reviewed Projections"])

    with tab_raw:
        _render_raw_projections_tab(space_id)

    with tab_reviewed:
        _render_reviewed_projections_tab(space_id)


def _render_raw_projections_tab(space_id: str):
    """Render the raw projections tab (existing behavior)."""
    view_mode = st.radio(
        "Projection versions",
        ["Newest only", "All history"],
        horizontal=True,
    )
    newest_only = view_mode == "Newest only"

    # Projections list
    projections = api.list_projections(
        space_id=space_id,
        include_data=True,
        newest_only=newest_only,
    )

    if newest_only:
        projections = _filter_latest_projections(projections)

    if not projections:
        st.info("No projections for this space yet.")
        return

    st.caption(f"Showing {len(projections)} projection run(s) for this space.")
    _render_combined_projection_table(projections)
    st.divider()

    # Status colors
    status_colors = {
        "COMPLETED": "🟢",
        "IN_PROGRESS": "🟡",
        "PENDING": "⚪",
        "FAILED": "🔴",
        "NEEDS_FEEDBACK": "🟠",
    }

    for p in projections:
        icon = status_colors.get(p["status"], "⚪")
        col1, col2, col3 = st.columns([1, 3, 1])
        with col1:
            st.write(f"{icon} {p['status']}")
        with col2:
            if p.get("project_id"):
                st.write(f"Project: {p['project_id'][:12]}...")
            timestamp = _projection_timestamp(p)
            if timestamp:
                st.caption(f"Frame: {p['frame_id'][:12]}... | Extracted: {timestamp}")
            else:
                st.caption(f"Frame: {p['frame_id'][:12]}...")
        with col3:
            if st.button("View", key=f"proj_{p['projection_id']}"):
                st.session_state["selected_projection"] = p["projection_id"]

    # Projection detail
    if "selected_projection" in st.session_state:
        proj = api.get_projection(st.session_state["selected_projection"])
        if proj and proj["space_id"] == space_id:
            st.divider()
            st.subheader("Projection Data")
            st.write(f"**Status:** {proj['status']}")
            st.write(f"**Space Version:** {proj['space_version']}")
            if proj.get("project_id"):
                st.write(f"**Source Project ID:** {proj['project_id']}")
            if proj.get("agent_notes"):
                st.write(f"**Agent Notes:** {proj['agent_notes']}")
            if proj.get("data"):
                _render_projection_data(proj["data"], source_project_id=proj.get("project_id"))
            if proj.get("validation_result"):
                with st.expander("Validation"):
                    if isinstance(proj["validation_result"], dict):
                        _render_mapping_table(proj["validation_result"])
                    else:
                        st.write(proj["validation_result"])
        elif proj is None or proj["space_id"] != space_id:
            st.session_state.pop("selected_projection", None)


def _render_reviewed_projections_tab(space_id: str):
    """Render the reviewed projections tab."""
    reviewed_list = api.list_reviewed_projections(space_id=space_id, include_data=True)

    if not reviewed_list:
        st.info(
            "No reviewed projections yet. Use the CLI to run a projection review:\n\n"
            "`mkb review-projections --space <id> --project-id <uuid>`"
        )
        return

    st.caption(f"Showing {len(reviewed_list)} reviewed projection(s) for this space.")

    # Render combined table from reviewed projections
    reviewed_as_projections = [
        {
            "projection_id": rp["reviewed_projection_id"],
            "project_id": rp["project_id"],
            "status": rp["status"],
            "data": rp.get("data"),
            "extracted_at": rp.get("reviewed_at"),
            "created_at": rp.get("created_at"),
        }
        for rp in reviewed_list
    ]
    _render_combined_projection_table(reviewed_as_projections)
    st.divider()

    status_colors = {
        "REVIEWED": "🟣",
        "COMPLETED": "🟢",
        "IN_PROGRESS": "🟡",
        "PENDING": "⚪",
        "FAILED": "🔴",
    }

    for rp in reviewed_list:
        icon = status_colors.get(rp["status"], "⚪")
        col1, col2, col3 = st.columns([1, 3, 1])
        with col1:
            st.write(f"{icon} {rp['status']}")
        with col2:
            st.write(f"Project: {rp['project_id'][:12]}...")
            reviewed_at = rp.get("reviewed_at") or rp.get("created_at") or ""
            source_count = len(rp.get("source_projection_ids") or [])
            st.caption(
                f"Reviewed: {reviewed_at} | "
                f"Sources: {source_count} projection(s) | "
                f"v{rp['space_version']}"
            )
        with col3:
            if st.button("View", key=f"rp_{rp['reviewed_projection_id']}"):
                st.session_state["selected_reviewed_projection"] = rp["reviewed_projection_id"]

    # Detail view
    if "selected_reviewed_projection" in st.session_state:
        rp = api.get_reviewed_projection(st.session_state["selected_reviewed_projection"])
        if rp and rp["space_id"] == space_id:
            st.divider()
            st.subheader("Reviewed Projection Data")
            st.write(f"**Status:** {rp['status']}")
            st.write(f"**Space Version:** {rp['space_version']}")
            st.write(f"**Project ID:** {rp['project_id']}")
            if rp.get("reviewed_at"):
                st.write(f"**Reviewed At:** {rp['reviewed_at']}")
            if rp.get("source_projection_ids"):
                st.write(f"**Source Projections:** {len(rp['source_projection_ids'])} run(s) reviewed")
                with st.expander("Source Projection IDs"):
                    for pid in rp["source_projection_ids"]:
                        st.code(pid)
            if rp.get("review_notes"):
                with st.expander("Review Notes"):
                    st.write(rp["review_notes"])
            if rp.get("data"):
                _render_projection_data(rp["data"], source_project_id=rp.get("project_id"))
            if rp.get("validation_result"):
                with st.expander("Validation"):
                    if isinstance(rp["validation_result"], dict):
                        _render_mapping_table(rp["validation_result"])
                    else:
                        st.write(rp["validation_result"])
        elif rp is None or rp["space_id"] != space_id:
            st.session_state.pop("selected_reviewed_projection", None)
