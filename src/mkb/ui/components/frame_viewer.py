"""Frame content viewer component.

Renders knowledge frame content with expandable sections
and evidence level color coding.
"""

import streamlit as st


# Evidence level colors and labels
EVIDENCE_LEVELS = {
    1: ("🟢", "Causal Experimental"),
    2: ("🔵", "Direct Observation"),
    3: ("🟡", "Correlative"),
    4: ("🟠", "Predicted/Inferred"),
}


def render_frame_content(content: dict):
    """Render knowledge frame content in a structured view."""
    if not content:
        st.warning("No content to display.")
        return

    # Paper metadata (always shown first if present)
    if "paper" in content:
        _render_paper_metadata(content["paper"])

    # Domain
    if "domain" in content:
        st.write(f"**Domain:** {content['domain']}")

    st.divider()

    # Render all other sections
    for key, value in content.items():
        if key in ("paper", "domain"):
            continue
        _render_section(key, value)


def _render_paper_metadata(paper: dict):
    """Render paper metadata section."""
    st.subheader("Paper Metadata")
    if paper.get("title"):
        st.write(f"**Title:** {paper['title']}")
    if paper.get("authors"):
        authors = paper["authors"]
        if isinstance(authors, list):
            st.write(f"**Authors:** {', '.join(authors)}")
        else:
            st.write(f"**Authors:** {authors}")
    cols = st.columns(3)
    if paper.get("journal"):
        cols[0].write(f"**Journal:** {paper['journal']}")
    if paper.get("year"):
        cols[1].write(f"**Year:** {paper['year']}")
    if paper.get("doi"):
        cols[2].write(f"**DOI:** {paper['doi']}")


def _render_section(key: str, value):
    """Render a single content section."""
    # Format key as title
    title = key.replace("_", " ").title()

    if isinstance(value, list):
        with st.expander(f"**{title}** ({len(value)} items)"):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    _render_item(item, i)
                else:
                    st.write(f"  {i+1}. {item}")
    elif isinstance(value, dict):
        with st.expander(f"**{title}**"):
            st.json(value)
    else:
        st.write(f"**{title}:** {value}")


def _render_item(item: dict, index: int):
    """Render a single list item with evidence level badge."""
    evidence = item.get("evidence_level")
    badge = ""
    if evidence and evidence in EVIDENCE_LEVELS:
        icon, label = EVIDENCE_LEVELS[evidence]
        badge = f" {icon} L{evidence}"

    # Build a concise display
    # Try common display fields in priority order
    display_fields = ["name", "claim", "property", "description", "method", "subject"]
    primary = None
    for field in display_fields:
        if field in item:
            primary = f"**{item[field]}**"
            break

    if not primary:
        # Fall back to first string field
        for k, v in item.items():
            if isinstance(v, str) and k != "evidence_level":
                primary = f"**{v}**"
                break

    if not primary:
        primary = f"Item {index + 1}"

    st.markdown(f"{index + 1}. {primary}{badge}")

    # Show remaining fields in a compact format
    detail_fields = {k: v for k, v in item.items() if k != "evidence_level" and k not in display_fields[:1]}
    if detail_fields:
        detail_parts = []
        for k, v in detail_fields.items():
            if isinstance(v, dict):
                detail_parts.append(f"  *{k}*: {', '.join(f'{dk}={dv}' for dk, dv in v.items())}")
            elif isinstance(v, list):
                detail_parts.append(f"  *{k}*: {', '.join(str(x) for x in v)}")
            else:
                detail_parts.append(f"  *{k}*: {v}")
        st.caption("\n".join(detail_parts))
