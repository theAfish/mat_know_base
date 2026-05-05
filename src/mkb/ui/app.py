"""
Materials Knowledge Base — Streamlit UI

Main entry point for the web interface.
"""

import streamlit as st

from mkb.ui.background_jobs import auto_refresh_if_running, poll_jobs


PAGES = ["Assistant", "Projects", "Knowledge Frames", "Dataset Graph", "Projections", "Feedback"]

st.set_page_config(
    page_title="Materials Knowledge Base",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Sidebar navigation
st.sidebar.title("MKB")
default_page = st.session_state.setdefault("page", PAGES[0])
if "page_nav" not in st.session_state:
    st.session_state["page_nav"] = default_page

pending_page = st.session_state.pop("pending_page", None)
if pending_page in PAGES:
    st.session_state["page_nav"] = pending_page

page = st.sidebar.radio(
    "Navigate",
    PAGES,
    key="page_nav",
    label_visibility="collapsed",
)
st.session_state["page"] = page

poll_jobs()

if page == "Assistant":
    from mkb.ui.pages.assistant import render
    render()
elif page == "Projects":
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

auto_refresh_if_running()
