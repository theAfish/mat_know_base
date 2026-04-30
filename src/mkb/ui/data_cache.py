"""UI data-access caching helpers.

These wrappers reduce repeated DB reads and graph aggregation during
Streamlit reruns and page switches.
"""

from __future__ import annotations

import streamlit as st

from mkb import api


@st.cache_data(ttl=60, max_entries=128, show_spinner=False)
def get_knowledge_graph_cached(project_id: str | None = None) -> dict:
    """Fetch merged knowledge graph payload with short-lived cache."""
    return api.get_knowledge_graph(project_id=project_id)


@st.cache_data(ttl=30, max_entries=128, show_spinner=False)
def search_library_cached(query: str, limit: int = 25, project_id: str | None = None) -> dict:
    """Fetch keyword search results with a short-lived cache for reruns."""
    return api.search_library(query=query, limit=limit, project_id=project_id)


def clear_graph_cache() -> None:
    """Clear cached graph payloads after graph-changing operations."""
    get_knowledge_graph_cached.clear()
    get_graph_review_counts_cached.clear()


@st.cache_data(ttl=60, max_entries=16, show_spinner=False)
def get_graph_review_counts_cached(space_id: str | None = None) -> dict:
    """Fetch per-element review counts with short-lived cache."""
    return api.get_graph_review_counts(space_id=space_id)
