"""Draft Studio view: Focused environment for reviewing, refining guidelines, versioning, and syncing drafts."""
from __future__ import annotations

import streamlit as st

from ui_kit import (
    chip,
    draft_editor,
    get_ctx,
    rows,
)


def render_draft_studio() -> None:
    ctx = get_ctx()
    all_rows = rows(ctx.store)

    st.title("✍️ Draft Studio")
    st.caption("Dedicated space for inspecting AI drafts, steering prompts, comparing versions, and syncing to Gmail.")

    if not all_rows:
        st.info("No emails found to draft. Fetch emails or run the offline demo.")
        return

    # Filter selector for studio pick
    # Prioritize items that have drafts or are in terminal states
    options = {}
    for x in all_rows:
        has_draft = bool(x["rec"].get("draft", {}).get("draft_text"))
        indicator = "📝 " if has_draft else "⭕ "
        options[x["mid"]] = f"{indicator}{x['rec'].get('subject', '(No Subject)')[:40]} [{chip(x['status'])}]"

    cur_pick = st.session_state.get("studio_pick") or st.session_state.get("sel_email")
    if cur_pick not in options:
        cur_pick = list(options.keys())[0]

    sel_idx = list(options.keys()).index(cur_pick) if cur_pick in options else 0

    selected_mid = st.selectbox(
        "Choose an email conversation to review or draft:",
        options=list(options.keys()),
        index=sel_idx,
        format_func=lambda k: options[k],
        key="studio_select_box",
    )
    st.session_state["studio_pick"] = selected_mid
    st.session_state["sel_email"] = selected_mid

    if not selected_mid:
        return

    item = ctx.store.emails[selected_mid]
    rec = item.get("record") or {}
    cls = rec.get("classification") or {}
    draft = rec.get("draft") or {}

    col_meta1, col_meta2, col_meta3 = st.columns(3)
    col_meta1.metric("Category", cls.get("category", "—"))
    col_meta2.metric("Urgency", cls.get("urgency", "—"))
    col_meta3.metric("Draft Status", "Requires Review" if draft.get("requires_human_review") else ("Generated" if draft else "None"))

    st.divider()

    # Split: Left side displays the incoming request summary / snippet; Right side the draft editor
    col_left, col_right = st.columns([1, 1.2])

    with col_left:
        with st.container(border=True):
            st.subheader("Customer Inquiry")
            st.write(f"**From:** `{rec.get('sender', 'Unknown')}`")
            st.write(f"**Subject:** {rec.get('subject', '—')}")
            if rec.get("attachment_names"):
                st.write("**Attachments:** " + ", ".join(rec["attachment_names"]))
            st.text_area("Body", value=rec.get("body", ""), height=350, disabled=True, key=f"studio_body_{selected_mid}")

    with col_right:
        with st.container(border=True):
            st.subheader("AI Draft Response")
            draft_editor(ctx, selected_mid, kp="studio_editor")


render_draft_studio()
