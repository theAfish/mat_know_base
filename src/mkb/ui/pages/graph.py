"""Dataset graph page — browse the merged knowledge graph across datasets."""

import queue
import threading

import streamlit as st

from mkb.ui.components.graph_viz import render_global_knowledge_graph
from mkb.ui.data_cache import clear_graph_cache, get_graph_review_counts_cached, get_knowledge_graph_cached


_NODE_MODES = {
    "Default (teal)": "default",
    "Review coverage": "review_coverage",
    "Modification heat": "modification_heat",
    "Connectivity": "connectivity",
}

_EDGE_MODES = {
    "Evidence level": "evidence_level",
    "Review coverage": "review_coverage",
    "Modification heat": "modification_heat",
    "Default (gray)": "default",
}

_REVIEW_MODES = {"review_coverage", "modification_heat"}


def _init_review_state() -> None:
    for key, default in [
        ("review_running", False),
        ("review_queue", None),
        ("review_events", []),
        ("review_result", None),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default


def _start_review(mode: str, seed_count: int) -> None:
    """Launch graph review in a daemon thread, feeding events into a queue."""
    q: queue.Queue = queue.Queue()
    st.session_state.review_queue = q
    st.session_state.review_running = True
    st.session_state.review_events = []
    st.session_state.review_result = None

    def _run() -> None:
        from mkb.agents.graph_review import run_graph_review

        def _cb(event: dict) -> None:
            q.put({"type": "progress", **event})

        try:
            result = run_graph_review(mode=mode, seed_count=seed_count, progress_callback=_cb)
            q.put({"type": "done", "result": result})
        except Exception as exc:  # noqa: BLE001
            q.put({"type": "error", "error": str(exc)})

    threading.Thread(target=_run, daemon=True).start()


def _poll_review_queue() -> bool:
    """Drain available events from the review queue. Returns True if state changed."""
    q: queue.Queue | None = st.session_state.review_queue
    if q is None:
        return False

    changed = False
    while True:
        try:
            event = q.get_nowait()
        except queue.Empty:
            break

        changed = True
        if event["type"] == "done":
            st.session_state.review_running = False
            st.session_state.review_result = event["result"]
            st.session_state.review_queue = None
            clear_graph_cache()
            break
        elif event["type"] == "error":
            st.session_state.review_running = False
            st.session_state.review_result = {"error": event["error"]}
            st.session_state.review_queue = None
            break
        elif event["type"] == "progress":
            # Keep only the last 20 events to avoid unbounded growth
            st.session_state.review_events.append(event)
            if len(st.session_state.review_events) > 20:
                st.session_state.review_events = st.session_state.review_events[-20:]

    return changed


_ACTION_ICONS = {
    "merge": "🔀",
    "standardize": "🏷️",
    "delete": "🗑️",
}

_TOOL_ICONS = {
    "get_concept_details": "🔍",
    "get_concept_neighbors": "🌐",
    "get_relation_type_distribution": "📊",
    "search_graph_elements": "🔎",
    "merge_concepts": "🔀",
    "standardize_relation_name": "🏷️",
    "delete_concept": "🗑️",
    "delete_relation": "🗑️",
}


@st.fragment(run_every=0.4)
def _render_review_section() -> None:
    _init_review_state()
    was_running = bool(st.session_state.review_running)

    with st.expander("Run Graph Review", expanded=was_running):
        if was_running:
            st.info("Review in progress…")

        col1, col2, col3 = st.columns([2, 1, 1])
        mode = col1.selectbox(
            "Mode",
            ["auto", "global", "local"],
            disabled=was_running,
            help=(
                "**auto**: randomly picks global or local each time. "
                "**global**: relation standardization + concept deduplication across the full graph. "
                "**local**: explores neighborhoods of the least-reviewed concepts."
            ),
        )
        seed_count = col2.number_input(
            "Seed concepts",
            min_value=1,
            max_value=50,
            value=10,
            disabled=was_running,
            help="Number of starting concepts for local mode.",
        )
        col3.write("")  # vertical spacer
        col3.write("")
        start_btn = col3.button(
            "Start Review",
            type="primary",
            disabled=was_running,
            use_container_width=True,
        )

        if start_btn and not was_running:
            _start_review(mode=mode, seed_count=int(seed_count))
            st.rerun()

        # Poll for new events; the fragment timer handles the 0.4 s interval.
        _poll_review_queue()

        # Progress log
        if st.session_state.review_events:
            st.caption("Recent activity:")
            for ev in reversed(st.session_state.review_events[-10:]):
                action = ev.get("action", "")
                tool = ev.get("tool", "")
                label = ev.get("label", "")
                etype = ev.get("element_type", "")
                icon = _ACTION_ICONS.get(action) or _TOOL_ICONS.get(tool, "·")
                st.caption(f"{icon} `{tool}` — {etype}: **{label}**")

        # Final result
        result = st.session_state.review_result
        if result:
            if "error" in result:
                st.error(f"Review failed: {result['error']}")
            else:
                stats = result.get("reviewed_elements", {})
                actual_mode = result.get("mode", mode)
                examined = stats.get("examined", 0)
                modified = stats.get("modified", 0)
                st.success(
                    f"Review complete — mode: **{actual_mode}** · "
                    f"examined: {examined} · modified: {modified}"
                )
                if result.get("agent_summary"):
                    with st.expander("Agent summary"):
                        st.write(result["agent_summary"])

    # When the review just finished, trigger a full page rerun so the graph
    # visualisation (outside this fragment) refreshes with the updated data.
    if was_running and not st.session_state.review_running:
        st.rerun()


def render():
    st.header("Dataset Graph")

    graph_payload = get_knowledge_graph_cached()
    graph = graph_payload.get("graph") if isinstance(graph_payload, dict) else None
    projection_count = graph_payload.get("projection_count", 0) if isinstance(graph_payload, dict) else 0

    concepts = graph.get("concepts") or [] if isinstance(graph, dict) else []
    relations = graph.get("relations") or [] if isinstance(graph, dict) else []

    if not concepts:
        st.info("No merged graph data is available yet. Run graph extraction first.")
        _render_review_section()
        return

    metric_cols = st.columns(3)
    metric_cols[0].metric("Concepts", len(concepts))
    metric_cols[1].metric("Relations", len(relations))
    metric_cols[2].metric("Merged Projections", projection_count)

    # Color mode controls
    with st.expander("Visualization options", expanded=False):
        col1, col2 = st.columns(2)
        node_mode_label = col1.selectbox(
            "Node color",
            list(_NODE_MODES.keys()),
            index=0,
            help=(
                "Default: uniform teal. "
                "Review coverage: gray=unreviewed → teal → amber=most reviewed. "
                "Modification heat: highlights nodes that have been modified most. "
                "Connectivity: node size and color scale with number of connected edges."
            ),
        )
        edge_mode_label = col2.selectbox(
            "Edge color",
            list(_EDGE_MODES.keys()),
            index=0,
            help=(
                "Evidence level: green=causal, blue=direct, yellow=correlative, orange=predicted. "
                "Review coverage: gray=unreviewed → teal → amber=most reviewed. "
                "Modification heat: highlights relations modified most often. "
                "Default: uniform gray."
            ),
        )

    node_color_mode = _NODE_MODES[node_mode_label]
    edge_color_mode = _EDGE_MODES[edge_mode_label]

    # Only fetch review counts when a review-based mode is selected
    review_counts: dict | None = None
    if node_color_mode in _REVIEW_MODES or edge_color_mode in _REVIEW_MODES:
        review_counts = get_graph_review_counts_cached()

    render_global_knowledge_graph(
        graph,
        node_color_mode=node_color_mode,
        edge_color_mode=edge_color_mode,
        review_counts=review_counts,
    )

    _render_review_section()


if __name__ == "__main__":
    render()
