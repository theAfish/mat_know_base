"""
Materials Knowledge Base — Streamlit UI

Main entry point for the web interface.
"""

import streamlit as st


PAGES = ["Projects", "Knowledge Frames", "Dataset Graph", "Projections", "Feedback"]

st.set_page_config(
    page_title="Materials Knowledge Base",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Sidebar navigation
st.sidebar.title("MKB")
current_page = st.session_state.setdefault("page", PAGES[0])
if st.session_state.get("page_nav") != current_page:
    st.session_state["page_nav"] = current_page

page = st.sidebar.radio(
    "Navigate",
    PAGES,
    key="page_nav",
    label_visibility="collapsed",
)
st.session_state["page"] = page

if page == "Projects":
    from mkb.ui.pages.projects import render
    render()
elif page == "Knowledge Frames":
    from mkb.ui.pages.frames import render
    render()
elif page == "Dataset Graph":
    from mkb.ui.pages.graph import render
    render()
elif page == "Projections":
    from mkb.ui.pages.projections import render
    render()
elif page == "Feedback":
    from mkb.ui.pages.feedback import render
    render()
