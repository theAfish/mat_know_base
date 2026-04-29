"""Projects page — upload, browse, and manage research projects."""

import shutil
from pathlib import Path
import re

import streamlit as st
import streamlit.components.v1 as st_components
from mkb import api
from mkb.knowledge_graph import GLOBAL_KG_SPACE_NAME
from mkb.ui.data_cache import clear_graph_cache, get_knowledge_graph_cached, search_library_cached
from mkb.ui.background_jobs import get_project_jobs, get_running_job, render_project_job_status, start_job
from mkb.ui.upload_server import ensure_upload_server, get_upload_url, session_dir

# Custom drop-zone component: captures webkitRelativePath so folder structure
# is preserved when the user drops mixed files + folders in one gesture.
# Files are uploaded via a lightweight HTTP endpoint (bypasses Streamlit's
# setComponentValue payload-size limit); only metadata is sent through the
# component protocol.
_COMPONENT_DIR = Path(__file__).parent.parent / "components" / "folder_drop_zone"
_folder_drop_zone = st_components.declare_component(
    "folder_drop_zone",
    path=str(_COMPONENT_DIR),
)


_UPLOAD_ROOT = Path("data/uploads")

_STATUS_ICONS = {
    "COMPLETED": "🟢",
    "IN_PROGRESS": "🟡",
    "PENDING": "⚪",
    "FAILED": "🔴",
    "NO_FRAME": "⚫",
}

_JOB_BUTTON_LABELS = {
    "process": "Process",
    "extract": "Extract",
    "project": "Project",
    "knowledge_graph": "Extract Graph",
}


def _is_user_visible_space(space: dict) -> bool:
    """Return True for spaces that should appear in user-facing UI controls."""
    return space.get("name") != GLOBAL_KG_SPACE_NAME


def render():
    st.header("Research Projects")

    upload_tab, browse_tab = st.tabs(["Upload", "Browse"])

    with upload_tab:
        _render_upload()

    with browse_tab:
        _render_project_list()


# ── Upload ────────────────────────────────────────────────────────


def _render_upload():
    """Single drag-and-drop area for mixed files and folders.

    Uses a custom Streamlit component backed by the browser's
    DataTransferItem.webkitGetAsEntry() API so that:
      • a dropped folder  → one project, sub-folder structure preserved
      • a dropped file    → one project named from the file stem
      • M files + N folders dropped together → M+N projects, default-named,
        each editable before the final ingest click.

    File data is uploaded via a companion HTTP endpoint (raw binary POST)
    so that large folder payloads never hit Streamlit's setComponentValue
    size limit.  The component only sends lightweight metadata through the
    Streamlit protocol.
    """
    st.subheader("Add Research Files")
    st.caption("PDFs, DOCX, CSV, XLSX, JSON, TXT, and images are all supported.")

    # Ensure the upload server is running (idempotent — only starts once).
    ensure_upload_server()

    # Show a persistent success banner from the previous ingest, if any.
    if "_upload_success" in st.session_state:
        info = st.session_state.pop("_upload_success")
        st.toast(info["text"], icon="✅")

    recent_uploads = get_project_jobs("__upload__")
    completed_upload = next(
        (
            job for job in recent_uploads
            if job["status"] == "COMPLETED"
            and not job.get("metadata", {}).get("result_acknowledged")
            and isinstance(job.get("result"), dict)
            and job["result"].get("message")
        ),
        None,
    )
    if completed_upload:
        st.session_state["_upload_success"] = {"text": completed_upload["result"]["message"]}
        completed_upload.setdefault("metadata", {})["result_acknowledged"] = True
        st.rerun()

    upload_job = get_running_job("__upload__", "upload")
    if upload_job:
        st.info("Upload ingest is running in the background. You can switch pages while files are being ingested.")
        for event in reversed((upload_job.get("events") or [])[-6:]):
            st.caption(f"• {event['message']}")

    # Rotate the component key after each successful ingest so the component
    # remounts fresh (clears the last submitted value).
    key = f"folder_drop_zone_{st.session_state.get('_upload_gen', 0)}"
    payload = _folder_drop_zone(key=key, default=None, upload_url=get_upload_url())

    if not payload:
        return

    start_job(
        kind="upload",
        label="Upload Ingest",
        project_id="__upload__",
        target=_run_upload_ingest,
        args=(payload,),
    )
    st.session_state["_upload_gen"] = st.session_state.get("_upload_gen", 0) + 1
    st.rerun()


def _run_upload_ingest(payload: list[dict], progress_callback=None) -> dict:
    total_ingested = 0
    total_dupes = 0
    created: list[str] = []
    upload_id = payload[0].get("upload_id", "")
    temp_root = session_dir(upload_id)

    def _emit(message: str) -> None:
        if progress_callback:
            progress_callback({"message": message})

    try:
        _emit(f"Preparing {len(payload)} project(s) for ingest")
        for idx, proj in enumerate(payload, start=1):
            project_name = _normalize_project_name(proj["name"], fallback="project")
            upload_dir = _create_unique_project_dir(project_name)
            _emit(f"Moving files for {upload_dir.name} ({idx}/{len(payload)})")

            for file_info in proj["files"]:
                rel = Path(file_info["relativePath"]) if file_info.get("relativePath") else Path(file_info["name"])
                src = temp_root / rel
                dest = upload_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest = _next_available_path(dest)
                if src.is_file():
                    shutil.move(str(src), str(dest))

            _emit(f"Ingesting {upload_dir.name}")
            result = api.ingest(upload_dir)
            total_ingested += result.get("ingested", 0)
            total_dupes += result.get("duplicates", 0)
            created.append(upload_dir.name)
    finally:
        if temp_root.is_dir():
            shutil.rmtree(temp_root, ignore_errors=True)

    message = (
        f"Created {len(created)} project(s) · "
        f"{total_ingested} file(s) ingested, {total_dupes} duplicate(s) skipped."
    )
    return {
        "status": "completed",
        "message": message,
        "created_projects": created,
        "ingested": total_ingested,
        "duplicates": total_dupes,
    }


def _normalize_project_name(name: str, fallback: str) -> str:
    candidate = (name or "").strip()
    if not candidate:
        candidate = fallback
    candidate = re.sub(r"[^A-Za-z0-9._ -]+", "_", candidate)
    candidate = candidate.strip(" ._")
    return candidate or "project"


def _create_unique_project_dir(project_name: str) -> Path:
    _UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    base_name = _normalize_project_name(project_name, fallback="project")
    candidate = _UPLOAD_ROOT / base_name
    suffix = 2
    while candidate.exists():
        candidate = _UPLOAD_ROOT / f"{base_name}_{suffix}"
        suffix += 1
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


def _next_available_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    idx = 2
    while True:
        candidate = path.with_name(f"{stem}_{idx}{suffix}")
        if not candidate.exists():
            return candidate
        idx += 1


# ── Project list ──────────────────────────────────────────────────


def _render_project_list():
    col_h, col_refresh = st.columns([5, 1])
    with col_refresh:
        if st.button("Refresh"):
            st.rerun()

    search_query = st.text_input(
        "Search papers and data",
        value=st.session_state.get("projects_search_query", ""),
        placeholder="Try enamel, mineralization, csv, odontogenic...",
    )
    st.session_state["projects_search_query"] = search_query

    if search_query.strip():
        results = search_library_cached(query=search_query, limit=50)
        _render_search_results(results)
        return

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
        running_job = get_running_job(p["project_id"])
        status_text = running_job["label"] if running_job else p["frame_status"]
        status_icon = "🟡" if running_job else icon
        cols[0].write(f"{status_icon} {status_text}")
        cols[1].write(p["label"] or p["source_path"] or str(p["project_id"])[:12])
        cols[2].write(str(p["asset_count"]))
        if cols[3].button("View", key=f"view_{p['project_id']}"):
            st.session_state["selected_frame_project"] = p["project_id"]
            st.session_state["frame_detail_project"] = p["project_id"]
            st.session_state["pending_page"] = "Knowledge Frames"
            st.rerun()


def _render_search_results(results: dict):
    projects = results.get("projects") or []
    assets = results.get("assets") or []

    st.caption(
        f"{results.get('total', 0)} match(es) for '{results.get('query', '')}'"
    )

    if not projects and not assets:
        st.info("No matching papers or data assets found.")
        return

    if projects:
        st.write("**Projects**")
        for project in projects:
            cols = st.columns([5, 1])
            cols[0].write(project["label"] or project["source_path"] or project["project_id"])
            if cols[1].button("View", key=f"search_project_{project['project_id']}"):
                st.session_state["selected_frame_project"] = project["project_id"]
                st.session_state["frame_detail_project"] = project["project_id"]
                st.session_state["pending_page"] = "Knowledge Frames"
                st.rerun()

    if assets:
        st.write("**Assets**")
        for asset in assets:
            cols = st.columns([4, 2, 1])
            cols[0].caption(asset["filename"])
            cols[1].caption(asset["mime_type"] or "—")
            if asset.get("project_id") and cols[2].button("Open", key=f"search_asset_{asset['asset_id']}"):
                st.session_state["selected_frame_project"] = asset["project_id"]
                st.session_state["frame_detail_project"] = asset["project_id"]
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
            st.session_state.pop("frame_detail_project", None)
            st.rerun()

    # ── Pipeline actions ──────────────────────────────────────────
    st.write("**Pipeline**")
    render_project_job_status(project_id)

    spaces = [space for space in api.list_spaces() if _is_user_visible_space(space)]
    space_name_to_id = {s["name"]: s["space_id"] for s in spaces}

    action_cols = st.columns([1, 1, 2, 1, 1])
    process_job = get_running_job(project_id, "process")
    extract_job = get_running_job(project_id, "extract")
    projection_job = get_running_job(project_id, "project")
    kg_job = get_running_job(project_id, "knowledge_graph")

    with action_cols[0]:
        if st.button(
            "Process",
            key=f"proc_{project_id}",
            help="Convert raw files to LLM-readable formats",
            disabled=process_job is not None,
        ):
            start_job(
                kind="process",
                label="Process Assets",
                project_id=project_id,
                target=api.process,
                kwargs={"project_id": project_id},
            )
            st.rerun()
        if process_job:
            st.caption(process_job.get("current_message") or "Running")

    with action_cols[1]:
        if st.button(
            "Extract",
            key=f"extr_{project_id}",
            help="Run LLM knowledge extraction",
            disabled=extract_job is not None,
        ):
            start_job(
                kind="extract",
                label="Extract Knowledge Frame",
                project_id=project_id,
                target=api.extract,
                kwargs={"project_id": project_id},
            )
            st.rerun()
        if extract_job:
            st.caption(extract_job.get("current_message") or "Running")

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
            if st.button(
                "Project",
                key=f"proj_{project_id}",
                help="Run domain-specific projection",
                disabled=projection_job is not None,
            ):
                sid = space_name_to_id[selected_space_name]
                start_job(
                    kind="project",
                    label="Run Projection",
                    project_id=project_id,
                    target=api.project,
                    kwargs={"space_id": sid, "project_id": project_id},
                )
                st.rerun()
        if projection_job:
            st.caption(projection_job.get("current_message") or "Running")

    with action_cols[4]:
        if st.button(
            "Extract Graph",
            key=f"kg_{project_id}",
            help="Extract knowledge graph elements",
            disabled=kg_job is not None,
        ):
            start_job(
                kind="knowledge_graph",
                label="Extract Knowledge Graph",
                project_id=project_id,
                target=_run_knowledge_graph_job,
                kwargs={"project_id": project_id},
            )
            st.rerun()
        if kg_job:
            st.caption(kg_job.get("current_message") or "Running")

    st.divider()

    # ── Detail tabs ───────────────────────────────────────────────
    feedback_items = api.list_feedback(project_id=project_id)

    tab_assets, tab_frame, tab_projections, tab_graph, tab_feedback = st.tabs(
        [
            "Assets",
            "Knowledge Frame",
            "Projections",
            "Knowledge Graph",
            f"Feedback ({len(feedback_items)})",
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
        _render_feedback_tab(project_id, feedback_items)


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


def _run_knowledge_graph_job(project_id: str, progress_callback=None) -> dict:
    result = api.extract_knowledge_graph(project_id=project_id, progress_callback=progress_callback)
    clear_graph_cache()
    return result


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
    all_spaces = api.list_spaces()
    visible_spaces = [space for space in all_spaces if _is_user_visible_space(space)]
    visible_space_ids = {space["space_id"] for space in visible_spaces}

    projections = api.list_projections(project_id=project_id, include_data=True)
    projections = [proj for proj in projections if proj.get("space_id") in visible_space_ids]
    if not projections:
        st.info("No projections yet. Select a space and run **Project**.")
        return

    spaces_map = {s["space_id"]: s["name"] for s in visible_spaces}

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


def _render_feedback_tab(project_id: str, items: list[dict] | None = None):
    """Show all feedback items for this project with inline resolution."""
    if items is None:
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
