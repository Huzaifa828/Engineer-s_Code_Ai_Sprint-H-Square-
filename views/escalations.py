"""Escalations view: Focus purely on threats, policy breaches, disputes, and confidence issues."""
from __future__ import annotations

import streamlit as st

from ui_kit import (
    ATTENTION,
    chip,
    classification_view,
    draft_editor,
    email_view,
    get_ctx,
    rows,
)


def render_escalations() -> None:
    ctx = get_ctx()
    all_rows = rows(ctx.store)

    st.title("🚨 Escalations & Incidents")
    st.caption("All correspondence requiring human attention: policy triggers, prompt injections, low confidence, and verifier disagreements.")

    flagged = [x for x in all_rows if x["status"] in ATTENTION]

    # Metrics top bar
    m1, m2, m3, m4 = st.columns(4)
    esc_count = sum(1 for x in flagged if x["status"] == "Escalated")
    rev_count = sum(1 for x in flagged if x["status"] == "NeedsReview")
    fail_count = sum(1 for x in flagged if x["status"] == "Failed")

    m1.metric("Total Incident Backlog", len(flagged))
    m2.metric("Escalated (Security/Legal)", esc_count, delta=f"{esc_count}" if esc_count else None, delta_color="inverse")
    m3.metric("Needs Review (Uncertain)", rev_count)
    m4.metric("Failed Processing", fail_count)

    st.divider()

    if not flagged:
        st.success("🎉 All clear! There are currently 0 escalations or unreviewed issues in the system.")
        return

    # List and review layout
    sel_mid = st.session_state.get("sel_email")
    if not sel_mid or sel_mid not in [x["mid"] for x in flagged]:
        sel_mid = flagged[0]["mid"]

    opts = {
        x["mid"]: f"{chip(x['status'])} | {x['rec'].get('subject', '(No Subject)')[:40]} — {x['rec'].get('sender', '?')[:22]}"
        for x in flagged
    }

    pick_col, _ = st.columns([2, 1])
    chosen_mid = pick_col.selectbox(
        "Select an item from the escalation queue:",
        options=list(opts.keys()),
        index=list(opts.keys()).index(sel_mid) if sel_mid in opts else 0,
        format_func=lambda k: opts[k],
        key="esc_selectbox",
    )
    st.session_state["sel_email"] = chosen_mid

    item = ctx.store.emails[chosen_mid]
    rec = item.get("record") or {}
    cls = rec.get("classification") or {}
    status = item.get("status", "Escalated")

    # Threat & Reason Alert Banner
    if cls.get("escalation_reasons"):
        st.error("🚨 **Triggered Reasons:** " + ", ".join(cls["escalation_reasons"]))
    if cls.get("evidence_quote"):
        st.warning(f"💬 **Verbatim Flagged Text:** “{cls['evidence_quote']}”")
    if status == "Failed" and rec.get("error"):
        st.error(f"❌ **System Error:** {rec['error']}")

    # Tabs for handling the issue
    tab_review, tab_draft, tab_orig = st.tabs(["🛡️ Triage & Reclassify", "✍️ Review Holding Draft", "📧 Original Message"])

    with tab_review:
        classification_view(ctx, chosen_mid, kp="esc_cls")
    with tab_draft:
        draft_editor(ctx, chosen_mid, kp="esc_draft")
    with tab_orig:
        email_view(ctx, chosen_mid, kp="esc_view")


render_escalations()
