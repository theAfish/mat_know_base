"""
Materials Knowledge Base — Streamlit UI

Main entry point for the web interface.
"""

import streamlit as st

st.set_page_config(
    page_title="Materials Knowledge Base",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Sidebar navigation
st.sidebar.title("MKB")
page = st.sidebar.radio(
    "Navigate",
    ["Projects", "Knowledge Frames", "Projections", "Feedback"],
    label_visibility="collapsed",
)

if page == "Projects":
    from mkb.ui.pages.projects import render
    render()
elif page == "Knowledge Frames":
    from mkb.ui.pages.frames import render
    render()
elif page == "Projections":
    from mkb.ui.pages.projections import render
    render()
elif page == "Feedback":
    from mkb.ui.pages.feedback import render
    render()
