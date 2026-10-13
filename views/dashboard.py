"""Dashboard view: Key metrics, category & status breakdown, high-priority queue."""
from __future__ import annotations

import streamlit as st
import pandas as pd

from ui_kit import (
    ATTENTION,
    STATUS_ICON,
    chip,
    get_ctx,
    open_in,
    summary,
)
from config.domain import CATEGORIES


def render_dashboard() -> None:
    ctx = get_ctx()
    s = summary(ctx.store)
    r = s["rows"]

    st.title("📊 Dashboard")
    st.caption("Real-time telemetry and overview of incoming mail triage.")

    # Top KPI metric cards
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total in Store", s["total"])
    c2.metric("Processed", s["processed"])
    attention_count = sum(s["by_status"].get(k, 0) for k in ATTENTION)
    c3.metric("Action Required", attention_count, delta=f"{attention_count} critical" if attention_count else None, delta_color="inverse")
    c4.metric("Drafted", s["by_status"].get("Drafted", 0))
    c5.metric("Avg Confidence", f"{s['avg_conf']:.0%}")

    st.divider()

    # Visual breakdowns
    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.subheader("Emails by Status")
        status_data = [{"Status": f"{STATUS_ICON.get(k, '⚪')} {k}", "Count": v} for k, v in s["by_status"].items() if v > 0]
        if status_data:
            df_status = pd.DataFrame(status_data).set_index("Status")
            st.bar_chart(df_status, use_container_width=True)
        else:
            st.info("No emails indexed yet.")

    with col_right:
        st.subheader("Category Distribution")
        cat_counts = {cat: 0 for cat in CATEGORIES}
        for item in r:
            cat = item["cls"].get("category")
            if cat in cat_counts:
                cat_counts[cat] += 1
            elif cat:
                cat_counts[cat] = cat_counts.get(cat, 0) + 1

        active_cats = [{"Category": k, "Count": v} for k, v in cat_counts.items() if v > 0]
        if active_cats:
            df_cat = pd.DataFrame(active_cats).set_index("Category")
            st.bar_chart(df_cat, use_container_width=True)
        else:
            st.info("No classifications completed yet.")

    st.divider()

    # Priority Action Queue
    st.subheader("🚨 Attention Required")
    st.caption("Emails marked as Escalated, NeedsReview, or Failed that require human inspection.")

    flagged = [x for x in r if x["status"] in ATTENTION]
    if not flagged:
        st.success("🎉 Inbox zero for attention items! No escalations or review requests pending.")
    else:
        for item in flagged:
            mid = item["mid"]
            rec = item["rec"]
            cls = item["cls"]
            status = item["status"]

            with st.container(border=True):
                top_l, top_r = st.columns([3, 1])
                with top_l:
                    st.markdown(f"**{rec.get('subject') or '(No Subject)'}**")
                    st.caption(f"From: `{rec.get('sender', 'Unknown')}` · Status: {chip(status)}")
                with top_r:
                    if st.button("Open in Studio ✍️", key=f"dash_open_studio_{mid}", use_container_width=True):
                        open_in(mid, "studio")

                if cls.get("escalation_reasons"):
                    st.error("Escalation reasons: " + ", ".join(cls["escalation_reasons"]))
                elif status == "Failed":
                    st.error(f"Error: {rec.get('error', 'Execution failure')}")
                elif cls.get("evidence_quote"):
                    st.info(f"Quote: “{cls['evidence_quote']}”")


render_dashboard()
