"""Knowledge graph visualization component.

Supports both legacy per-frame graph extraction and merged global
concept-graph rendering.

Uses pyvis for rendering: labels are hidden by default and appear only on
hover (as tooltips), and Barnes-Hut physics keeps large graphs responsive.
"""

import json

import streamlit as st
import streamlit.components.v1 as components

try:
    from pyvis.network import Network
    HAS_PYVIS = True
except ImportError:
    HAS_PYVIS = False

# Evidence level colors for edges
EDGE_COLORS = {
    1: "#22c55e",  # green — causal
    2: "#3b82f6",  # blue — direct observation
    3: "#eab308",  # yellow — correlative
    4: "#f97316",  # orange — predicted
}

def _vis_options(enable_physics: bool) -> str:
    options = {
        "layout": {"improvedLayout": False},
        "physics": {
            "enabled": enable_physics,
            "solver": "barnesHut",
            "barnesHut": {
                "gravitationalConstant": -6000,
                "centralGravity": 0.3,
                "springLength": 110,
                "springConstant": 0.05,
                "damping": 0.12,
                "avoidOverlap": 0.1,
            },
            "stabilization": {
                "enabled": enable_physics,
                "iterations": 80,
                "updateInterval": 25,
            },
        },
        "nodes": {
            "font": {"size": 0},
            "borderWidth": 1,
            "shadow": False,
        },
        "edges": {
            "font": {"size": 0},
            "smooth": False,
            "arrows": {"to": {"enabled": True, "scaleFactor": 0.5}},
        },
        "interaction": {
            "hover": True,
            "tooltipDelay": 80,
            "hideEdgesOnDrag": True,
            "hideNodesOnDrag": False,
        },
    }
    return json.dumps(options)


def _make_net(
    height: int = 500,
    directed: bool = True,
    enable_physics: bool = True,
) -> "Network":
    try:
        net = Network(
            height=f"{height}px",
            width="100%",
            directed=directed,
            bgcolor="#0e1117",
            font_color="#e2e8f0",
            cdn_resources="remote",
        )
    except TypeError:
        net = Network(
            height=f"{height}px",
            width="100%",
            directed=directed,
            bgcolor="#0e1117",
            font_color="#e2e8f0",
        )

    net.set_options(_vis_options(enable_physics=enable_physics))
    return net


def _serialize_graph(graph: dict) -> str:
    """Create a stable, cacheable representation of graph payload."""
    data = {
        "concepts": graph.get("concepts") or [],
        "relations": graph.get("relations") or [],
    }
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


@st.cache_data(ttl=300, max_entries=16, show_spinner=False)
def _build_global_graph_html(graph_json: str, height: int) -> str:
    """Build pyvis HTML once per graph payload to avoid expensive rerenders."""
    payload = json.loads(graph_json)
    concepts = payload.get("concepts") or []
    relations = payload.get("relations") or []
    enable_physics = len(concepts) <= 500 and len(relations) <= 1200

    net = _make_net(height=height, enable_physics=enable_physics)
    node_ids: set[str] = set()

    for concept in concepts:
        if not isinstance(concept, dict):
            continue
        label = str(concept.get("label") or "").strip()
        if not label or label in node_ids:
            continue
        aliases = concept.get("aliases") or []
        alias_text = ", ".join(str(a).strip() for a in aliases if str(a).strip())
        tooltip = f"{label}\nAliases: {alias_text}" if alias_text else label
        net.add_node(label, label="", title=tooltip, size=16, color="#34d399")
        node_ids.add(label)

    for relation in relations:
        if not isinstance(relation, dict):
            continue
        source = str(relation.get("source") or "").strip()
        target = str(relation.get("target") or "").strip()
        rel = str(relation.get("relation") or "").strip()
        if not source or not target or not rel:
            continue
        if source not in node_ids:
            net.add_node(source, label="", title=source, size=14, color="#34d399")
            node_ids.add(source)
        if target not in node_ids:
            net.add_node(target, label="", title=target, size=14, color="#34d399")
            node_ids.add(target)

        ev = relation.get("evidence_level", 3)
        try:
            ev = int(ev)
        except (TypeError, ValueError):
            ev = 3
        net.add_edge(source, target, title=rel, color=EDGE_COLORS.get(ev, "#888"))

    return net.generate_html()


def render_global_knowledge_graph(graph: dict):
    """Render a merged concept graph returned by api.get_knowledge_graph()."""
    if not HAS_PYVIS:
        st.warning("pyvis not installed. Install it with: pip install pyvis")
        return

    if not isinstance(graph, dict):
        st.info("No graph data available.")
        return

    concepts = graph.get("concepts") or []
    relations = graph.get("relations") or []
    if not concepts:
        st.info("No concept nodes available yet. Run `mkb kg-extract` first.")
        return

    graph_json = _serialize_graph(graph)
    html = _build_global_graph_html(graph_json=graph_json, height=720)

    st.caption(
        "Merged global concept graph. **Hover** nodes/edges to see labels. "
        "Edge colors: causal (🟢) | direct (🔵) | correlative (🟡) | predicted (🟠)"
    )
    components.html(html, height=720, scrolling=False)


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
