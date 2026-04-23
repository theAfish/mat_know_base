"""Knowledge graph visualization component.

Supports both legacy per-frame graph extraction and merged global
concept-graph rendering.

Uses pyvis for rendering: labels are hidden by default and appear only on
hover (as tooltips), and Barnes-Hut physics keeps large graphs responsive.
"""

import base64

import streamlit as st

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

# Barnes-Hut physics options — fast for large graphs
_PHYSICS_OPTIONS = """
{
  "physics": {
    "enabled": true,
    "solver": "barnesHut",
    "barnesHut": {
      "gravitationalConstant": -8000,
      "centralGravity": 0.3,
      "springLength": 120,
      "springConstant": 0.04,
      "damping": 0.09,
      "avoidOverlap": 0.1
    },
    "stabilization": {
      "enabled": true,
      "iterations": 200,
      "updateInterval": 25
    }
  },
  "nodes": {
    "font": { "size": 0 },
    "borderWidth": 1,
    "shadow": false
  },
  "edges": {
    "font": { "size": 0 },
    "smooth": { "type": "dynamic" },
    "arrows": { "to": { "enabled": true, "scaleFactor": 0.5 } }
  },
  "interaction": {
    "hover": true,
    "tooltipDelay": 100,
    "hideEdgesOnDrag": true,
    "hideNodesOnDrag": false
  }
}
"""


def _make_net(height: int = 500, directed: bool = True) -> "Network":
    net = Network(
        height=f"{height}px",
        width="100%",
        directed=directed,
        bgcolor="#0e1117",
        font_color="#e2e8f0",
    )
    net.set_options(_PHYSICS_OPTIONS)
    return net


def _render_net(net: "Network") -> None:
    """Render a pyvis Network inside Streamlit via an iframe data URL."""
    html = net.generate_html()
    encoded_html = base64.b64encode(html.encode("utf-8")).decode("ascii")
    iframe_src = f"data:text/html;base64,{encoded_html}"
    height = net.height if isinstance(net.height, int) else int(net.height.rstrip("px"))
    st.iframe(iframe_src, height=height)


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

    net = _make_net(height=720)
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

    st.caption(
        "Merged global concept graph. **Hover** nodes/edges to see labels. "
        "Edge colors: causal (🟢) | direct (🔵) | correlative (🟡) | predicted (🟠)"
    )
    _render_net(net)


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
