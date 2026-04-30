"""Knowledge graph visualization component.

Supports both legacy per-frame graph extraction and merged global
concept-graph rendering.

Uses pyvis for rendering: labels are hidden by default and appear only on
hover (as tooltips), and Barnes-Hut physics keeps large graphs responsive.

Color modes
-----------
Nodes:
  - "default"          — single teal color for all nodes
  - "review_coverage"  — gray (never reviewed) → teal → amber (most reviewed)
  - "connectivity"     — node size scales with number of relations; color fixed

Edges:
  - "evidence_level"   — green (causal) / blue (direct) / yellow (correlative)
                         / orange (predicted)  [default]
  - "review_coverage"  — gray (never reviewed) → teal → amber (most reviewed)
  - "default"          — single gray for all edges
"""

import json
import re

import streamlit as st
import streamlit.components.v1 as st_components

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

# ── Color helpers ──────────────────────────────────────────────────────────────


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lerp_color(c1: str, c2: str, t: float) -> str:
    """Linear interpolation between two hex colors, t ∈ [0, 1]."""
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    r = int(r1 + (r2 - r1) * t)
    g = int(g1 + (g2 - g1) * t)
    b = int(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _coverage_color(count: int, max_count: int) -> str:
    """Map a review count to a color on a three-stop gradient.

    0 reviews  → #6b7280 (gray)
    ~mid       → #34d399 (teal/green)
    max reviews → #f59e0b (amber)
    """
    if max_count <= 0 or count <= 0:
        return "#6b7280"
    t = min(count / max_count, 1.0)
    if t < 0.5:
        return _lerp_color("#6b7280", "#34d399", t / 0.5)
    return _lerp_color("#34d399", "#f59e0b", (t - 0.5) / 0.5)


def _normalize_label(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _relation_key_from_triple(source: str, relation: str, target: str) -> str:
    return f"{_normalize_label(source)}||{_normalize_label(relation)}||{_normalize_label(target)}"


# ── pyvis options ──────────────────────────────────────────────────────────────


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


# ── Core build function ────────────────────────────────────────────────────────


@st.cache_data(ttl=300, max_entries=32, show_spinner=False)
def _build_global_graph_html(
    graph_json: str,
    height: int,
    node_color_mode: str,
    edge_color_mode: str,
    review_json: str,
) -> str:
    """Build pyvis HTML. All parameters form the cache key.

    review_json: JSON-encoded dict from api.get_graph_review_counts(), or "{}".
    """
    payload = json.loads(graph_json)
    concepts = payload.get("concepts") or []
    relations = payload.get("relations") or []
    enable_physics = len(concepts) <= 500 and len(relations) <= 1200

    review = json.loads(review_json) if review_json else {}
    review_concepts: dict[str, dict] = review.get("concepts") or {}
    review_relations: dict[str, dict] = review.get("relations") or {}

    # Pre-compute max counts for normalization
    max_node_examined = max((v.get("times_examined", 0) for v in review_concepts.values()), default=0)
    max_node_modified = max((v.get("times_modified", 0) for v in review_concepts.values()), default=0)
    max_edge_examined = max((v.get("times_examined", 0) for v in review_relations.values()), default=0)
    max_edge_modified = max((v.get("times_modified", 0) for v in review_relations.values()), default=0)

    # Pre-compute connectivity (degree) per concept label
    degree: dict[str, int] = {}
    if node_color_mode == "connectivity":
        for r in relations:
            src = str(r.get("source") or "").strip()
            tgt = str(r.get("target") or "").strip()
            if src:
                degree[src] = degree.get(src, 0) + 1
            if tgt:
                degree[tgt] = degree.get(tgt, 0) + 1
        max_degree = max(degree.values(), default=1)
    else:
        max_degree = 1

    net = _make_net(height=height, enable_physics=enable_physics)
    node_ids: set[str] = set()

    def _add_concept_node(label: str, aliases: list[str], tooltip_extra: str = "") -> None:
        if not label or label in node_ids:
            return

        norm = _normalize_label(label)
        alias_text = ", ".join(str(a).strip() for a in aliases if str(a).strip())
        tooltip_parts = [label]
        if alias_text:
            tooltip_parts.append(f"Aliases: {alias_text}")

        node_size = 16
        color = "#34d399"  # default teal

        if node_color_mode == "review_coverage":
            rec = review_concepts.get(norm, {})
            examined = rec.get("times_examined", 0)
            modified = rec.get("times_modified", 0)
            color = _coverage_color(examined, max_node_examined)
            if examined > 0:
                tooltip_parts.append(f"Reviewed: {examined}x examined, {modified}x modified")
            else:
                tooltip_parts.append("Never reviewed")

        elif node_color_mode == "modification_heat":
            rec = review_concepts.get(norm, {})
            modified = rec.get("times_modified", 0)
            examined = rec.get("times_examined", 0)
            color = _coverage_color(modified, max_node_modified)
            if examined > 0:
                tooltip_parts.append(f"Modified: {modified}x, examined: {examined}x")

        elif node_color_mode == "connectivity":
            deg = degree.get(label, 0)
            t = min(deg / max_degree, 1.0)
            color = _lerp_color("#60a5fa", "#f97316", t)
            node_size = 12 + int(t * 18)
            tooltip_parts.append(f"Connections: {deg}")

        if tooltip_extra:
            tooltip_parts.append(tooltip_extra)

        net.add_node(
            label, label="",
            title="\n".join(tooltip_parts),
            size=node_size,
            color=color,
        )
        node_ids.add(label)

    for concept in concepts:
        if not isinstance(concept, dict):
            continue
        label = str(concept.get("label") or "").strip()
        if not label:
            continue
        _add_concept_node(label, concept.get("aliases") or [])

    for relation in relations:
        if not isinstance(relation, dict):
            continue
        source = str(relation.get("source") or "").strip()
        target = str(relation.get("target") or "").strip()
        rel = str(relation.get("relation") or "").strip()
        if not source or not target or not rel:
            continue

        _add_concept_node(source, [])
        _add_concept_node(target, [])

        ev = relation.get("evidence_level", 3)
        try:
            ev = int(ev)
        except (TypeError, ValueError):
            ev = 3

        # Edge color
        if edge_color_mode == "evidence_level":
            edge_color = EDGE_COLORS.get(ev, "#888")
        elif edge_color_mode in ("review_coverage", "modification_heat"):
            rkey = _relation_key_from_triple(source, rel, target)
            rec = review_relations.get(rkey, {})
            if edge_color_mode == "review_coverage":
                val = rec.get("times_examined", 0)
                max_val = max_edge_examined
            else:
                val = rec.get("times_modified", 0)
                max_val = max_edge_modified
            edge_color = _coverage_color(val, max_val)
        else:
            edge_color = "#555"

        # Edge tooltip
        ev_labels = {1: "causal", 2: "direct", 3: "correlative", 4: "predicted"}
        edge_tip = f"{rel}\nEvidence: {ev_labels.get(ev, str(ev))}"
        if edge_color_mode in ("review_coverage", "modification_heat"):
            rkey = _relation_key_from_triple(source, rel, target)
            rec = review_relations.get(rkey, {})
            examined = rec.get("times_examined", 0)
            modified = rec.get("times_modified", 0)
            edge_tip += f"\nReviewed: {examined}x examined, {modified}x modified"

        net.add_edge(source, target, title=edge_tip, color=edge_color)

    return net.generate_html()


# ── Public render function ─────────────────────────────────────────────────────


def render_global_knowledge_graph(
    graph: dict,
    node_color_mode: str = "default",
    edge_color_mode: str = "evidence_level",
    review_counts: dict | None = None,
):
    """Render a merged concept graph returned by api.get_knowledge_graph().

    Args:
        graph: ``{"concepts": [...], "relations": [...]}`` from api.get_knowledge_graph().
        node_color_mode: One of "default", "review_coverage", "modification_heat",
            "connectivity".
        edge_color_mode: One of "evidence_level", "review_coverage",
            "modification_heat", "default".
        review_counts: Optional output of api.get_graph_review_counts() for coloring
            by review metadata. Required when mode is "review_coverage" or
            "modification_heat".
    """
    if not HAS_PYVIS:
        st.warning("pyvis not installed. Install it with: pip install pyvis")
        return

    if not isinstance(graph, dict):
        st.info("No graph data available.")
        return

    concepts = graph.get("concepts") or []
    if not concepts:
        st.info("No concept nodes available yet. Run `mkb kg-extract` first.")
        return

    graph_json = _serialize_graph(graph)
    review_json = json.dumps(review_counts or {}, sort_keys=True, separators=(",", ":"), default=str)

    html = _build_global_graph_html(
        graph_json=graph_json,
        height=720,
        node_color_mode=node_color_mode,
        edge_color_mode=edge_color_mode,
        review_json=review_json,
    )

    # Build legend caption
    edge_legend = {
        "evidence_level": "Edge colors: causal (🟢) | direct (🔵) | correlative (🟡) | predicted (🟠)",
        "review_coverage": "Edge colors: gray = never reviewed → teal → amber = most reviewed",
        "modification_heat": "Edge colors: gray = never modified → teal → amber = most modified",
        "default": "",
    }.get(edge_color_mode, "")

    node_legend = {
        "default": "",
        "review_coverage": "Node colors: gray = never reviewed → teal → amber = most reviewed",
        "modification_heat": "Node colors: gray = never modified → teal → amber = most modified",
        "connectivity": "Node size + color: blue (few connections) → orange (many connections)",
    }.get(node_color_mode, "")

    caption_parts = [p for p in [node_legend, edge_legend] if p]
    caption_parts.append("**Hover** nodes/edges to see labels and metadata.")
    st.caption(" · ".join(caption_parts))

    st_components.html(html, height=720)


# Section-based node colors (used by legacy per-frame rendering)
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
