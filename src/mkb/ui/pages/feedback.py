"""Feedback page — view and manage feedback items."""

import streamlit as st
from mkb import api


def render():
    st.header("Feedback")

    # Filters
    col1, col2 = st.columns(2)
    with col1:
        status_filter = st.selectbox(
            "Status",
            [None, "OPEN", "ACKNOWLEDGED", "RESOLVED", "DISMISSED", "DEV_ISSUE"],
            format_func=lambda x: "All" if x is None else x,
        )
    with col2:
        # Get projects for filter
        projects = api.list_projects(limit=100)
        project_options = {p["label"] or p["project_id"][:12]: p["project_id"] for p in projects}
        project_options = {**{"All Projects": None}, **project_options}
        selected_project_label = st.selectbox("Project", list(project_options.keys()))
        project_filter = project_options[selected_project_label]

    items = api.list_feedback(project_id=project_filter, status=status_filter)

    if not items:
        st.info("No feedback items found.")
        return

    # Status colors
    status_colors = {
        "OPEN": "🔴",
        "ACKNOWLEDGED": "🟡",
        "RESOLVED": "🟢",
        "DISMISSED": "⚪",
        "DEV_ISSUE": "🔵",
    }

    st.write(f"**{len(items)} feedback item(s)**")

    for fb in items:
        icon = status_colors.get(fb["status"], "⚪")
        with st.expander(f"{icon} [{fb['category']}] {fb['question'][:80]}"):
            st.write(f"**Status:** {fb['status']}")
            st.write(f"**Category:** {fb['category']}")
            st.write(f"**Question:** {fb['question']}")
            if fb.get("field_path"):
                st.write(f"**Field:** {fb['field_path']}")
            if fb.get("context"):
                st.write(f"**Context:** {fb['context']}")
            st.write(f"**Source:** {fb['source_agent']}")
            st.write(f"**Created:** {fb.get('created_at', 'N/A')}")

            if fb["status"] == "OPEN":
                st.write("---")
                resolve_status = st.selectbox(
                    "Resolve as",
                    ["RESOLVED", "DISMISSED", "DEV_ISSUE"],
                    key=f"resolve_{fb['feedback_id']}",
                )
                notes = st.text_input("Notes", key=f"notes_{fb['feedback_id']}")
                if st.button("Resolve", key=f"btn_{fb['feedback_id']}"):
                    result = api.resolve_feedback(fb["feedback_id"], resolve_status, notes)
                    if "error" not in result:
                        st.success("Resolved!")
                        st.rerun()
                    else:
                        st.error(result["error"])

            if fb.get("resolution_notes"):
                st.write(f"**Resolution:** {fb['resolution_notes']} (by {fb.get('resolved_by', 'N/A')})")


if __name__ == "__main__":
    render()
