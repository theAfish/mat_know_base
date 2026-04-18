"""Knowledge graph visualization component.

Renders relationships from knowledge frames as an interactive graph
using streamlit-agraph.
"""

import streamlit as st

try:
    from streamlit_agraph import agraph, Node, Edge, Config
    HAS_AGRAPH = True
except ImportError:
    HAS_AGRAPH = False


# Evidence level colors for edges
EDGE_COLORS = {
    1: "#22c55e",  # green — causal
    2: "#3b82f6",  # blue — direct observation
    3: "#eab308",  # yellow — correlative
    4: "#f97316",  # orange — predicted
}


def render_knowledge_graph(content: dict):
    """Render a knowledge graph from frame content.

    Extracts nodes from all list sections and edges from
    relationship-like entries.
    """
    if not HAS_AGRAPH:
        st.warning("streamlit-agraph not installed. Install it with: pip install streamlit-agraph")
        return

    nodes = []
    edges = []
    node_ids = set()

    # Extract nodes from all list sections
    for key, value in content.items():
        if key in ("paper", "domain"):
            continue
        if not isinstance(value, list):
            continue

        for item in value:
            if not isinstance(item, dict):
                continue

            # Check if this is a relationship (has subject/predicate/object)
            if all(k in item for k in ("subject", "predicate", "object")):
                subj = str(item["subject"])
                obj = str(item["object"])
                pred = str(item["predicate"])
                ev = item.get("evidence_level", 3)

                if subj not in node_ids:
                    nodes.append(Node(id=subj, label=subj, size=20))
                    node_ids.add(subj)
                if obj not in node_ids:
                    nodes.append(Node(id=obj, label=obj, size=20))
                    node_ids.add(obj)

                edges.append(Edge(
                    source=subj,
                    target=obj,
                    label=pred,
                    color=EDGE_COLORS.get(ev, "#888"),
                ))
            else:
                # Extract entity name as a node
                name = item.get("name") or item.get("claim", "")[:40] or item.get("property", "")
                if name and name not in node_ids:
                    nodes.append(Node(
                        id=name,
                        label=name,
                        size=15,
                        color=_section_color(key),
                    ))
                    node_ids.add(name)

    if not nodes:
        st.info("No graph data to visualize. The frame may not contain relationships or named entities.")
        return

    # Legend
    st.caption(
        "Edge colors: 🟢 Causal | 🔵 Direct | 🟡 Correlative | 🟠 Predicted"
    )

    config = Config(
        width=800,
        height=500,
        directed=True,
        physics=True,
        hierarchical=False,
    )

    agraph(nodes=nodes, edges=edges, config=config)


# Section-based node colors
_SECTION_COLORS = {
    "materials": "#60a5fa",
    "compounds": "#60a5fa",
    "catalysts": "#60a5fa",
    "methods": "#a78bfa",
    "techniques": "#a78bfa",
    "concepts": "#34d399",
    "properties": "#fbbf24",
    "experimental_data": "#fbbf24",
    "measurements": "#fbbf24",
}


def _section_color(section_key: str) -> str:
    """Get a color for a section's nodes."""
    key_lower = section_key.lower()
    for pattern, color in _SECTION_COLORS.items():
        if pattern in key_lower:
            return color
    return "#94a3b8"  # default gray
