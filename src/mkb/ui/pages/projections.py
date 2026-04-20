"""Projections page — view and manage space projections."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from mkb import api


def _stringify_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return "; ".join(f"{key}: {_stringify_value(item)}" for key, item in value.items())
    if isinstance(value, list):
        return ", ".join(_stringify_value(item) for item in value)
    return str(value)


def _mapping_to_rows(mapping: dict) -> list[dict[str, str]]:
    return [
        {"Field": str(key), "Value": _stringify_value(value)}
        for key, value in mapping.items()
    ]


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
                if source_project_id:
                    row["references"] = source_project_id
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

    # Projections list
    projections = api.list_projections(space_id=space_id)

    if not projections:
        st.info("No projections for this space yet.")
        return

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
            st.caption(f"Frame: {p['frame_id'][:12]}...")
        with col3:
            if st.button("View", key=f"proj_{p['projection_id']}"):
                st.session_state["selected_projection"] = p["projection_id"]

    # Projection detail
    if "selected_projection" in st.session_state:
        st.divider()
        proj = api.get_projection(st.session_state["selected_projection"])
        if proj:
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
