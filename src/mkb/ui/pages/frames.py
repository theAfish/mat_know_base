"""Knowledge Frames page — browse and inspect extracted knowledge."""

import json

import streamlit as st
from mkb import api
from mkb.ui.components.frame_viewer import render_frame_content
from mkb.ui.components.graph_viz import render_global_knowledge_graph


def render():
    st.header("Knowledge Frames")

    frames = api.list_frames()

    if not frames:
        st.info("No knowledge frames found. Run extraction first.")
        return

    # Frame selector
    frame_options = {
        f"{f['project_id'][:8]}... — {f['status']} (v{f.get('extraction_version', 0)})": f["project_id"]
        for f in frames
    }

    selected_label = st.selectbox("Select a frame", list(frame_options.keys()))
    if not selected_label:
        return

    project_id = frame_options[selected_label]
    frame = api.get_frame(project_id)
    if not frame:
        st.error("Frame not found.")
        return

    # Frame metadata
    col1, col2, col3 = st.columns(3)
    col1.metric("Status", frame["status"])
    col2.metric("Version", frame.get("extraction_version", 0))
    col3.metric("Extracted", frame.get("extracted_at", "N/A")[:10] if frame.get("extracted_at") else "N/A")

    if frame.get("extraction_summary"):
        st.write(f"**Summary:** {frame['extraction_summary']}")

    # Content viewer tabs
    content = frame.get("content", {})
    if not content:
        st.warning("Frame has no content.")
        return

    tab_names = ["Structured View", "Graph View", "Raw JSON"]
    tabs = st.tabs(tab_names)

    with tabs[0]:
        render_frame_content(content)

    with tabs[1]:
        graph_payload = api.get_knowledge_graph()
        graph = graph_payload.get("graph") if isinstance(graph_payload, dict) else None
        projection_count = graph_payload.get("projection_count", 0) if isinstance(graph_payload, dict) else 0
        st.caption(f"Graph projections merged: {projection_count}")
        render_global_knowledge_graph(graph or {})

    with tabs[2]:
        st.json(content)
