"""Streamlit app – Knowledge Graph Explorer."""

from __future__ import annotations

import uuid

import streamlit as st
from streamlit_agraph import Config, Edge, Node, agraph

from mkb.db.engine import SyncSessionLocal
from mkb.db.models import IngestionBatch, KnowledgeEdge, KnowledgeNode

# ── Colour palette per entity type ─────────────────────────────
_PALETTE = {
    "Material": "#4FC3F7",
    "ChemicalElement": "#81C784",
    "Property": "#FFB74D",
    "PropertyType": "#FFB74D",
    "Method": "#CE93D8",
    "Device": "#90A4AE",
    "Author": "#F48FB1",
    "Paper": "#FFF176",
    "Application": "#A5D6A7",
    "Mechanism": "#FFAB91",
    "Observation": "#80DEEA",
    "Defect": "#EF9A9A",
    "Parameter": "#B0BEC5",
}
_DEFAULT_COLOUR = "#E0E0E0"

_ENTITY_SIZE = {
    "Paper": 30,
    "Material": 25,
    "Author": 18,
}
_DEFAULT_SIZE = 20


# ── Data loading (cached) ──────────────────────────────────────
@st.cache_data(ttl=30)
def _load_batches() -> list[dict]:
    with SyncSessionLocal() as s:
        batches = s.query(IngestionBatch).order_by(IngestionBatch.created_at.desc()).all()
        result = []
        for b in batches:
            count = s.query(KnowledgeNode).filter_by(source_batch_id=b.batch_id).count()
            result.append({
                "batch_id": str(b.batch_id),
                "label": b.label or "(none)",
                "node_count": count,
                "created_at": b.created_at,
            })
        return result


@st.cache_data(ttl=30)
def _load_graph(batch_id: str | None) -> tuple[list[dict], list[dict]]:
    with SyncSessionLocal() as s:
        q = s.query(KnowledgeNode)
        if batch_id:
            q = q.filter_by(source_batch_id=uuid.UUID(batch_id))
        nodes = q.all()

        node_ids = [n.node_id for n in nodes]
        if not node_ids:
            return [], []

        edges = (
            s.query(KnowledgeEdge)
            .filter(
                KnowledgeEdge.source_node_id.in_(node_ids)
                | KnowledgeEdge.target_node_id.in_(node_ids)
            )
            .all()
        )

        node_dicts = [
            {
                "id": str(n.node_id),
                "label": n.label,
                "entity_type": n.entity_type,
                "properties": n.properties or {},
            }
            for n in nodes
        ]
        edge_dicts = [
            {
                "source": str(e.source_node_id),
                "target": str(e.target_node_id),
                "relation": e.relation_type,
                "properties": e.properties or {},
            }
            for e in edges
        ]
        return node_dicts, edge_dicts


# ── Build agraph objects ────────────────────────────────────────
def _build_agraph(
    node_dicts: list[dict],
    edge_dicts: list[dict],
    selected_types: set[str],
) -> tuple[list[Node], list[Edge]]:
    # Filter nodes by selected types
    visible = {n["id"] for n in node_dicts if n["entity_type"] in selected_types}

    nodes = []
    for n in node_dicts:
        if n["id"] not in visible:
            continue
        colour = _PALETTE.get(n["entity_type"], _DEFAULT_COLOUR)
        size = _ENTITY_SIZE.get(n["entity_type"], _DEFAULT_SIZE)
        tooltip = f"[{n['entity_type']}] {n['label']}"
        if n["properties"]:
            props = ", ".join(f"{k}={v}" for k, v in list(n["properties"].items())[:5])
            tooltip += f"\n{props}"
        nodes.append(
            Node(
                id=n["id"],
                label=n["label"],
                size=size,
                color=colour,
                title=tooltip,
                font={"size": 12, "color": "#333"},
            )
        )

    edges = []
    for e in edge_dicts:
        if e["source"] not in visible or e["target"] not in visible:
            continue
        edges.append(
            Edge(
                source=e["source"],
                target=e["target"],
                label=e["relation"],
                color="#999",
                font={"size": 9, "color": "#666", "strokeWidth": 0},
            )
        )

    return nodes, edges


# ── Page layout ─────────────────────────────────────────────────
def main() -> None:
    st.set_page_config(page_title="MKB – Knowledge Graph", layout="wide")
    st.title("Knowledge Graph Explorer")

    # ── Sidebar ─────────────────────────────────────────────
    st.sidebar.header("Filters")

    batches = _load_batches()
    batch_options = {f"{b['label']}  ({b['node_count']} nodes)  {b['batch_id'][:8]}…": b["batch_id"] for b in batches}
    batch_options = {"All batches": None, **batch_options}
    selected_label = st.sidebar.selectbox("Batch", list(batch_options.keys()))
    selected_batch = batch_options[selected_label]

    node_dicts, edge_dicts = _load_graph(selected_batch)

    if not node_dicts:
        st.info("No knowledge entities found. Run extraction first.")
        return

    # Entity type filter
    all_types = sorted({n["entity_type"] for n in node_dicts})
    selected_types = st.sidebar.multiselect(
        "Entity types",
        all_types,
        default=all_types,
    )
    if not selected_types:
        st.warning("Select at least one entity type.")
        return

    # Layout options
    layout = st.sidebar.selectbox(
        "Layout",
        ["forceAtlas2Based", "barnesHut", "repulsion", "hierarchicalRepulsion"],
        index=0,
    )

    # ── Legend ───────────────────────────────────────────────
    st.sidebar.markdown("### Legend")
    for t in all_types:
        c = _PALETTE.get(t, _DEFAULT_COLOUR)
        st.sidebar.markdown(
            f'<span style="display:inline-block;width:14px;height:14px;'
            f'background:{c};border-radius:50%;margin-right:6px;vertical-align:middle;">'
            f"</span> {t}",
            unsafe_allow_html=True,
        )

    # ── Stats ───────────────────────────────────────────────
    visible_nodes = {n["id"] for n in node_dicts if n["entity_type"] in set(selected_types)}
    visible_edges = [e for e in edge_dicts if e["source"] in visible_nodes and e["target"] in visible_nodes]
    c1, c2, c3 = st.columns(3)
    c1.metric("Nodes", len(visible_nodes))
    c2.metric("Edges", len(visible_edges))
    c3.metric("Entity types", len(selected_types))

    # ── Graph ───────────────────────────────────────────────
    ag_nodes, ag_edges = _build_agraph(node_dicts, edge_dicts, set(selected_types))

    config = Config(
        width="100%",
        height=700,
        directed=True,
        physics={
            "enabled": True,
            "solver": layout,
        },
        nodeHighlightBehavior=True,
        highlightColor="#F7A7A6",
        collapsible=False,
    )

    result = agraph(nodes=ag_nodes, edges=ag_edges, config=config)

    # ── Detail pane (when a node is clicked) ────────────────
    if result:
        # result is the id of the selected node
        selected_node = next((n for n in node_dicts if n["id"] == result), None)
        if selected_node:
            st.markdown("---")
            st.subheader(f"🔍 {selected_node['label']}")
            st.caption(f"Type: **{selected_node['entity_type']}**")
            if selected_node["properties"]:
                st.json(selected_node["properties"])

            # Show connected edges
            connected = [
                e for e in edge_dicts
                if e["source"] == result or e["target"] == result
            ]
            if connected:
                st.markdown("**Connections:**")
                node_map = {n["id"]: n["label"] for n in node_dicts}
                for e in connected:
                    src = node_map.get(e["source"], e["source"][:8])
                    tgt = node_map.get(e["target"], e["target"][:8])
                    st.markdown(f"- {src} →  `{e['relation']}` →  {tgt}")


if __name__ == "__main__":
    main()
