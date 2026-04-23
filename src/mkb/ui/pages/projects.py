"""Projects page — browse and manage research projects."""

import streamlit as st
from mkb import api


def render():
    st.header("Research Projects")

    # Refresh button
    if st.button("Refresh"):
        st.rerun()

    projects = api.list_projects(limit=100)

    if not projects:
        st.info("No projects found. Ingest some data first.")
        return

    # Status color mapping
    status_colors = {
        "COMPLETED": "🟢",
        "IN_PROGRESS": "🟡",
        "PENDING": "⚪",
        "FAILED": "🔴",
        "NO_FRAME": "⚫",
    }

    # Project table
    for p in projects:
        status_icon = status_colors.get(p["frame_status"], "⚪")
        col1, col2, col3, col4 = st.columns([1, 3, 1, 1])
        with col1:
            st.write(status_icon, p["frame_status"])
        with col2:
            st.write(p["label"] or p["source_path"] or str(p["project_id"])[:8])
        with col3:
            st.write(f"{p['asset_count']} files")
        with col4:
            if st.button("View", key=f"view_{p['project_id']}"):
                st.session_state["selected_project"] = p["project_id"]

    # Project detail view
    if "selected_project" in st.session_state:
        st.divider()
        pid = st.session_state["selected_project"]
        _render_project_detail(pid)


def _render_project_detail(project_id: str):
    st.subheader(f"Project: {project_id[:12]}...")

    col1, col2 = st.columns(2)

    with col1:
        st.write("**Assets**")
        assets = api.list_assets(project_id=project_id)
        for a in assets:
            st.text(f"  {a['status']:<10}  {a['mime_type']:<25}  {a['filename']}")

    with col2:
        st.write("**Knowledge Frame**")
        frame = api.get_frame(project_id)
        if frame:
            st.write(f"Status: {frame['status']}")
            st.write(f"Version: {frame.get('extraction_version', 0)}")
            st.write(f"Extracted: {frame.get('extracted_at', 'N/A')}")
            if frame.get("content"):
                st.write(f"Sections: {', '.join(frame['content'].keys())}")
        else:
            st.write("No frame extracted yet.")

    # Extraction history
    history = api.get_extraction_history(project_id)
    if history:
        st.write("**Extraction History**")
        for h in history:
            st.text(f"  Pass {h['pass_number']} ({h['pass_type']}) — {h.get('created_at', '')}")


if __name__ == "__main__":
    render()
