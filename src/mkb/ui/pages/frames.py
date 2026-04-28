"""Knowledge Frames page — browse and inspect extracted knowledge."""

import streamlit as st

from mkb import api
from mkb.ui.pages.projects import _render_project_detail


def render():
    st.header("Knowledge Frames")

    projects = api.list_projects(limit=100)
    frames = {frame["project_id"]: frame for frame in api.list_frames()}
    rows = []

    for project in projects:
        frame = frames.get(project["project_id"])
        if not frame:
            continue
        rows.append({
            "project_id": project["project_id"],
            "label": project["label"] or project["source_path"] or project["project_id"][:12],
            "asset_count": project["asset_count"],
            "status": frame["status"],
            "version": frame.get("extraction_version", 0),
            "extracted_at": (frame.get("extracted_at") or "")[:10] or "-",
        })

    if not rows:
        st.info("No knowledge frames found. Run extraction first.")
        return

    available_project_ids = {row["project_id"] for row in rows}
    selected_project_id = st.session_state.get("selected_frame_project")
    if selected_project_id not in available_project_ids:
        selected_project_id = rows[0]["project_id"]
        st.session_state["selected_frame_project"] = selected_project_id

    header_cols = st.columns([1.4, 4, 1, 1, 1.2, 1])
    header_cols[0].caption("Status")
    header_cols[1].caption("Label / Path")
    header_cols[2].caption("Files")
    header_cols[3].caption("Version")
    header_cols[4].caption("Extracted")
    header_cols[5].caption("")

    for row in rows:
        cols = st.columns([1.4, 4, 1, 1, 1.2, 1])
        cols[0].write(row["status"])
        cols[1].write(row["label"])
        cols[2].write(str(row["asset_count"]))
        cols[3].write(f"v{row['version']}")
        cols[4].write(row["extracted_at"])
        if cols[5].button("View", key=f"frame_view_{row['project_id']}"):
            st.session_state["selected_frame_project"] = row["project_id"]
            st.rerun()

    st.divider()

    project_id = st.session_state["selected_frame_project"]
    frame = api.get_frame(project_id)
    if not frame:
        st.error("Frame not found.")
        return

    _render_project_detail(project_id)


if __name__ == "__main__":
    render()
