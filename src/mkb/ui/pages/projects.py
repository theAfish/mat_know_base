"""Projects page — upload, browse, and manage research projects."""

from pathlib import Path

import streamlit as st
from mkb import api
from mkb.ui.data_cache import clear_graph_cache, get_knowledge_graph_cached


_UPLOAD_ROOT = Path("data/uploads")

_STATUS_ICONS = {
    "COMPLETED": "🟢",
    "IN_PROGRESS": "🟡",
    "PENDING": "⚪",
    "FAILED": "🔴",
    "NO_FRAME": "⚫",
}


def render():
    st.header("Research Projects")

    upload_tab, browse_tab = st.tabs(["Upload", "Browse"])

    with upload_tab:
        _render_upload()

    with browse_tab:
        _render_project_list()


# ── Upload ────────────────────────────────────────────────────────


def _render_upload():
    st.subheader("Add Research Files")
    st.caption(
        "Drop one or more files to create a new project folder. "
        "PDFs, DOCX, CSV, XLSX, JSON, TXT, and images are all supported."
    )

    uploaded_files = st.file_uploader(
        "Drag & drop files here",
        accept_multiple_files=True,
        label_visibility="collapsed",
    )

    if uploaded_files:
        # Derive folder/project name from the first file's stem
        project_name = Path(uploaded_files[0].name).stem
        st.caption(
            f"{len(uploaded_files)} file(s) selected — project: **{project_name}**  "
            f"({', '.join(f.name for f in uploaded_files)})"
        )

        if st.button("Ingest files", type="primary"):
            upload_dir = _UPLOAD_ROOT / project_name
            upload_dir.mkdir(parents=True, exist_ok=True)

            for f in uploaded_files:
                dest = upload_dir / f.name
                dest.write_bytes(f.read())

            with st.spinner("Ingesting into the knowledge base…"):
                # label=None → ingest_directory uses directory.name (= project_name)
                result = api.ingest(upload_dir)

            ingested = result.get("ingested", 0)
            dupes = result.get("duplicates", 0)
            st.success(f"Ingested {ingested} file(s) — {dupes} duplicate(s) skipped.")

            st.rerun()


# ── Project list ──────────────────────────────────────────────────


def _render_project_list():
    col_h, col_refresh = st.columns([5, 1])
    with col_refresh:
        if st.button("Refresh"):
            st.rerun()

    projects = api.list_projects(limit=100)

    if not projects:
        st.info("No projects yet — upload files in the Upload tab.")
        return

    header_cols = st.columns([1, 4, 1, 1])
    header_cols[0].caption("Status")
    header_cols[1].caption("Label / Path")
    header_cols[2].caption("Files")
    header_cols[3].caption("")

    for p in projects:
        icon = _STATUS_ICONS.get(p["frame_status"], "⚪")
        cols = st.columns([1, 4, 1, 1])
        cols[0].write(f"{icon} {p['frame_status']}")
        cols[1].write(p["label"] or p["source_path"] or str(p["project_id"])[:12])
        cols[2].write(str(p["asset_count"]))
        if cols[3].button("View", key=f"view_{p['project_id']}"):
            st.session_state["selected_frame_project"] = p["project_id"]
            st.session_state["pending_page"] = "Knowledge Frames"
            st.rerun()


# ── Project detail ────────────────────────────────────────────────


def _render_project_detail(project_id: str):
    projects = api.list_projects(limit=100)
    p = next((x for x in projects if x["project_id"] == project_id), None)
    if not p:
        st.error("Project not found.")
        return

    label = p["label"] or p["source_path"] or project_id[:12]

    title_col, close_col = st.columns([6, 1])
    with title_col:
        st.subheader(label)
    with close_col:
        if st.button("✕ Close", key="close_detail"):
            st.session_state.pop("selected_frame_project", None)
            st.rerun()

    # ── Pipeline actions ──────────────────────────────────────────
    st.write("**Pipeline**")

    spaces = api.list_spaces()
    space_name_to_id = {s["name"]: s["space_id"] for s in spaces}

    action_cols = st.columns([1, 1, 2, 1, 1])

    with action_cols[0]:
        if st.button("Process", key=f"proc_{project_id}", help="Convert raw files to LLM-readable formats"):
            with st.spinner("Processing assets…"):
                result = api.process(project_id=project_id)
            st.toast(f"Processed {result.get('assets_processed', 0)} asset(s)")
            st.rerun()

    with action_cols[1]:
        if st.button("Extract", key=f"extr_{project_id}", help="Run LLM knowledge extraction"):
            with st.spinner("Extracting knowledge frame (this may take a while)…"):
                result = api.extract(project_id=project_id)
            st.toast(f"Extraction: {result.get('status', 'done')}")
            st.rerun()

    with action_cols[2]:
        if spaces:
            selected_space_name = st.selectbox(
                "Space",
                list(space_name_to_id.keys()),
                key=f"space_sel_{project_id}",
                label_visibility="collapsed",
            )
        else:
            selected_space_name = None
            st.caption("No spaces defined")

    with action_cols[3]:
        if spaces and selected_space_name:
            if st.button("Project", key=f"proj_{project_id}", help="Run domain-specific projection"):
                sid = space_name_to_id[selected_space_name]
                with st.spinner("Running projection…"):
                    result = api.project(space_id=sid, project_id=project_id)
                st.toast(f"Projection: {result.get('status', 'done')}")
                st.rerun()

    with action_cols[4]:
        if st.button("Extract Graph", key=f"kg_{project_id}", help="Extract knowledge graph elements"):
            with st.spinner("Extracting knowledge graph…"):
                result = api.extract_knowledge_graph(project_id=project_id)
            clear_graph_cache()
            st.toast("Knowledge graph extraction complete")
            st.rerun()

    st.divider()

    # ── Detail tabs ───────────────────────────────────────────────
    feedback_count = len(api.list_feedback(project_id=project_id))

    tab_assets, tab_frame, tab_projections, tab_graph, tab_feedback = st.tabs(
        [
            "Assets",
            "Knowledge Frame",
            "Projections",
            "Knowledge Graph",
            f"Feedback ({feedback_count})",
        ]
    )

    with tab_assets:
        _render_assets_tab(project_id)

    with tab_frame:
        _render_frame_tab(project_id)

    with tab_projections:
        _render_projections_tab(project_id)

    with tab_graph:
        _render_graph_tab(project_id)

    with tab_feedback:
        _render_feedback_tab(project_id)


# ── Detail tab renderers ──────────────────────────────────────────


def _render_assets_tab(project_id: str):
    assets = api.list_assets(project_id=project_id)
    if not assets:
        st.info("No assets ingested yet.")
        return

    st.write(f"**{len(assets)} raw asset(s)**")
    for a in assets:
        cols = st.columns([2, 3, 1])
        cols[0].caption(a["filename"])
        cols[1].caption(a["mime_type"] or "—")
        cols[2].caption(a["status"])

    processed = api.list_processed_assets(project_id=project_id)
    if processed:
        st.write(f"**{len(processed)} processed output(s)**")
        for pa in processed:
            cols = st.columns([2, 2, 1])
            cols[0].caption(pa["filename"] or "—")
            cols[1].caption(pa["processing_type"])
            cols[2].caption(f".{pa['output_format']}")


def _render_frame_tab(project_id: str):
    frame = api.get_frame(project_id)
    if not frame:
        st.info("No knowledge frame yet. Run **Extract** to generate one.")
        return

    meta_col, hist_col = st.columns([2, 1])
    with meta_col:
        st.write(f"Status: **{frame['status']}**")
        st.write(f"Version: {frame.get('extraction_version', 0)}")
        if frame.get("extracted_at"):
            st.write(f"Extracted: {frame['extracted_at']}")

    with hist_col:
        history = api.get_extraction_history(project_id)
        if history:
            st.write("**Extraction passes**")
            for h in history:
                st.caption(
                    f"Pass {h['pass_number']} ({h['pass_type']}) — "
                    f"{(h.get('created_at') or '')[:10]}"
                )

    if frame.get("content"):
        st.divider()
        from mkb.ui.components.frame_viewer import render_frame_content
        render_frame_content(frame["content"])

    annotations = frame.get("agent_annotations") or {}
    clarifications = annotations.get("clarifications") or []
    resolved_feedback = annotations.get("resolved_feedback") or []
    if clarifications or resolved_feedback:
        st.divider()
        st.write("**Agent Memory**")
        if clarifications:
            with st.expander(f"Clarifications ({len(clarifications)})"):
                for c in clarifications:
                    st.markdown(
                        f"- **Q ({c.get('field') or 'general'}):** {c.get('question', '')}\n"
                        f"  *A:* {c.get('summary', '')}  "
                        f"({'frame updated' if c.get('frame_updated') else 'no change'}, {(c.get('resolved_at') or '')[:10]})"
                    )
        if resolved_feedback:
            with st.expander(f"Resolved Feedback ({len(resolved_feedback)})"):
                for r in resolved_feedback:
                    st.markdown(
                        f"- **[{r.get('status')}] {r.get('category', '')}** "
                        f"({r.get('field_path') or 'general'}): {r.get('question', '')}  \n"
                        f"  *Resolution:* {r.get('resolution_notes', '')}  "
                        f"({(r.get('resolved_at') or '')[:10]})"
                    )

    with st.expander("Raw JSON"):
        st.json(frame)


def _render_projections_tab(project_id: str):
    projections = api.list_projections(project_id=project_id, include_data=True)
    if not projections:
        st.info("No projections yet. Select a space and run **Project**.")
        return

    spaces_map = {s["space_id"]: s["name"] for s in api.list_spaces()}

    for proj in projections:
        space_name = spaces_map.get(proj["space_id"], proj["space_id"][:8])
        extracted = (proj.get("extracted_at") or "")[:10]
        header = f"{space_name} — {proj['status']} @ {extracted}"

        with st.expander(header):
            detail_cols = st.columns(3)
            detail_cols[0].write(f"Status: **{proj['status']}**")
            detail_cols[1].write(f"Reviews: {proj.get('times_reviewed', 0)}")
            detail_cols[2].write(f"Version: {proj.get('space_version', '—')}")

            if proj.get("agent_notes"):
                st.caption(f"Agent notes: {proj['agent_notes']}")
            if proj.get("review_notes"):
                st.caption(f"Review notes: {proj['review_notes']}")

            data = proj.get("data") or {}
            if data:
                for section_key, section_val in data.items():
                    section_title = section_key.replace("_", " ").title()
                    if isinstance(section_val, list) and section_val:
                        st.write(f"**{section_title}** ({len(section_val)} items)")
                        st.dataframe(
                            section_val,
                            width="stretch",
                            hide_index=True,
                        )
                    elif section_val:
                        st.write(f"**{section_title}**")
                        st.json(section_val)


def _render_graph_tab(project_id: str):
    kg = get_knowledge_graph_cached(project_id=project_id)
    graph = kg.get("graph", {})
    concepts = graph.get("concepts") or []
    relations = graph.get("relations") or []

    if not concepts:
        st.info(
            "No graph elements extracted for this project yet. "
            "Run **Extract Graph** in the pipeline actions above."
        )
        return

    st.write(f"**{len(concepts)} concept(s), {len(relations)} relation(s)**")

    from mkb.ui.components.graph_viz import render_global_knowledge_graph
    render_global_knowledge_graph(graph)

    with st.expander(f"Concepts ({len(concepts)})"):
        for c in concepts:
            if not isinstance(c, dict):
                continue
            name = c.get("label") or c.get("name") or "?"
            ctype = c.get("type") or "—"
            desc = (c.get("description") or "")[:120]
            st.markdown(f"- **{name}** _{ctype}_: {desc}")

    with st.expander(f"Relations ({len(relations)})"):
        for r in relations:
            if not isinstance(r, dict):
                continue
            src = r.get("source") or "?"
            rel = r.get("relation") or "→"
            tgt = r.get("target") or "?"
            ev = r.get("evidence_level", "?")
            st.caption(f"{src}  **{rel}**  {tgt}  (L{ev})")


def _render_feedback_tab(project_id: str):
    """Show all feedback items for this project with inline resolution."""
    items = api.list_feedback(project_id=project_id)

    if not items:
        st.info("No feedback items for this project yet.")
        return

    status_colors = {
        "OPEN": "🔴",
        "ACKNOWLEDGED": "🟡",
        "RESOLVED": "🟢",
        "DISMISSED": "⚪",
        "DEV_ISSUE": "🔵",
    }

    open_count = sum(1 for fb in items if fb["status"] == "OPEN")
    st.write(f"**{len(items)} item(s)** — {open_count} open")

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
                    key=f"proj_resolve_{fb['feedback_id']}",
                )
                notes = st.text_input("Notes", key=f"proj_notes_{fb['feedback_id']}")
                if st.button("Resolve", key=f"proj_btn_{fb['feedback_id']}"):
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
