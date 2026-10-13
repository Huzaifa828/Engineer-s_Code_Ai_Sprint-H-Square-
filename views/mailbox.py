"""Mailbox view: Filterable, searchable inbox with multi-tab drilldown (Email, AI Triage, Draft)."""
from __future__ import annotations

import streamlit as st
import pandas as pd

from ui_kit import (
    STATUS_ORDER,
    chip,
    classification_view,
    draft_editor,
    email_view,
    get_ctx,
    rows,
)
from config.domain import CATEGORIES


def render_mailbox() -> None:
    ctx = get_ctx()
    all_rows = rows(ctx.store)

    st.title("📥 Mailbox")
    st.caption("Search, filter, and inspect incoming emails and their agent-generated drafts.")

    if not all_rows:
        st.info("No emails in mailbox yet. Fetch new emails or run the offline demo.")
        return

    # Filter toolbar
    f1, f2, f3 = st.columns([2, 1, 1])
    search_q = f1.text_input("🔍 Search subject, sender, or content", placeholder="Type keywords...")
    status_filter = f2.selectbox("Filter Status", ["All"] + STATUS_ORDER)
    category_filter = f3.selectbox("Filter Category", ["All"] + list(CATEGORIES))

    # Apply filters
    filtered = all_rows
    if status_filter != "All":
        filtered = [x for x in filtered if x["status"] == status_filter]
    if category_filter != "All":
        filtered = [x for x in filtered if x["cls"].get("category") == category_filter]
    if search_q.strip():
        q = search_q.lower()
        filtered = [
            x for x in filtered
            if q in (x["rec"].get("subject", "") or "").lower()
            or q in (x["rec"].get("sender", "") or "").lower()
            or q in (x["rec"].get("body", "") or "").lower()
        ]

    st.caption(f"Showing {len(filtered)} of {len(all_rows)} emails")

    left_pane, right_pane = st.columns([1.2, 1.8])

    with left_pane:
        st.subheader("Messages")
        if not filtered:
            st.info("No messages match current filter.")
            selected_mid = None
        else:
            # Table preview list
            options = {
                x["mid"]: f"{chip(x['status'])} | {x['rec'].get('subject', '(No Subject)')[:35]} ({x['rec'].get('sender', '?')[:20]})"
                for x in filtered
            }
            default_key = st.session_state.get("sel_email")
            if default_key not in options:
                default_key = filtered[0]["mid"]
            
            selected_mid = st.radio(
                "Select message",
                options=list(options.keys()),
                format_func=lambda k: options[k],
                index=list(options.keys()).index(default_key) if default_key in options else 0,
                label_visibility="collapsed",
                key="mailbox_radio_select",
            )
            st.session_state["sel_email"] = selected_mid

    with right_pane:
        if not selected_mid or selected_mid not in ctx.store.emails:
            st.info("Select an email from the left list to inspect details.")
            return

        item = ctx.store.emails[selected_mid]
        rec = item.get("record") or {}
        st.subheader(rec.get("subject", "(No Subject)"))
        st.caption(f"Status: {chip(item.get('status', 'Queued'))} · ID: `{selected_mid}`")

        t_email, t_cls, t_draft = st.tabs(["📧 Original Email", "🧠 Classification & Verification", "✍️ Draft Reply"])

        with t_email:
            email_view(ctx, selected_mid, kp="mb_email")
        with t_cls:
            classification_view(ctx, selected_mid, kp="mb_cls")
        with t_draft:
            draft_editor(ctx, selected_mid, kp="mb_draft")


render_mailbox()
