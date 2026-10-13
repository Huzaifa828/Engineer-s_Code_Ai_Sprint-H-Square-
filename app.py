"""Entrypoint: auth gate, shared sidebar, and page navigation.

Pages live in views/. Everything shared (auth, store, engine, panels) lives in ui_kit.py.
"""
import streamlit as st

st.set_page_config(
    page_title="Mail agent",
    page_icon="📬",
    layout="wide",
    initial_sidebar_state="expanded",
)

from ui_kit import (  # noqa: E402  (must come after set_page_config)
    ATTENTION,
    build_ctx,
    handle_oauth_callback,
    inject_css,
    login_screen,
    render_sidebar,
    set_ctx,
    summary,
)

inject_css()
handle_oauth_callback()

if "credentials" not in st.session_state and not st.session_state.get("offline_mode"):
    login_screen()  # stops the script

ctx = build_ctx()
set_ctx(ctx)

if "flash" in st.session_state:
    st.toast(st.session_state.pop("flash"))

s = summary(ctx.store)
attention = sum(s["by_status"][k] for k in ATTENTION)

nav = st.navigation([
    st.Page("views/dashboard.py", title="Dashboard", icon="📊", url_path="dashboard", default=True),
    st.Page("views/mailbox.py", title=f"Mailbox ({s['total']})", icon="📥", url_path="mailbox"),
    st.Page("views/draft_studio.py", title="Draft studio", icon="✍️", url_path="studio"),
    st.Page("views/escalations.py", title=f"Escalations ({attention})" if attention else "Escalations",
            icon="🚨", url_path="escalations"),
    st.Page("views/evaluation.py", title="Evaluation", icon="🧪", url_path="evaluation"),
    st.Page("views/activity.py", title="Activity log", icon="📋", url_path="activity"),
])

nav.run()          # draw the page first so it never waits on a sweep...
render_sidebar(ctx)  # ...then run the live engine, which reruns the app if data changed
