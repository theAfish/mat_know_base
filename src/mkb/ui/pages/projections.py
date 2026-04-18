"""Projections page — view and manage space projections."""

import streamlit as st
from mkb import api


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
            st.write(f"Frame: {p['frame_id'][:12]}...")
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
            if proj.get("agent_notes"):
                st.write(f"**Agent Notes:** {proj['agent_notes']}")
            if proj.get("data"):
                st.json(proj["data"])
            if proj.get("validation_result"):
                with st.expander("Validation"):
                    st.json(proj["validation_result"])
