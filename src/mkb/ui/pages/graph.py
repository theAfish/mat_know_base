"""Dataset graph page — browse the merged knowledge graph across datasets."""

import streamlit as st

from mkb.ui.components.graph_viz import render_global_knowledge_graph
from mkb.ui.data_cache import get_knowledge_graph_cached


def render():
    st.header("Dataset Graph")

    graph_payload = get_knowledge_graph_cached()
    graph = graph_payload.get("graph") if isinstance(graph_payload, dict) else None
    projection_count = graph_payload.get("projection_count", 0) if isinstance(graph_payload, dict) else 0

    concepts = graph.get("concepts") or [] if isinstance(graph, dict) else []
    relations = graph.get("relations") or [] if isinstance(graph, dict) else []

    if not concepts:
        st.info("No merged graph data is available yet. Run graph extraction first.")
        return

    metric_cols = st.columns(3)
    metric_cols[0].metric("Concepts", len(concepts))
    metric_cols[1].metric("Relations", len(relations))
    metric_cols[2].metric("Merged Projections", projection_count)

    render_global_knowledge_graph(graph)


if __name__ == "__main__":
    render()