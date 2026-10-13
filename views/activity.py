"""Activity log view: System telemetry, pipeline sweeps, sync history, and audit trails."""
from __future__ import annotations

import streamlit as st
import pandas as pd

from ui_kit import get_ctx


def render_activity() -> None:
    ctx = get_ctx()
    store = ctx.store

    st.title("📋 Activity & Audit Log")
    st.caption("Inspect live pipeline sweeps, audit records, human overrides, and synchronization telemetry.")

    col1, col2, col3 = st.columns(3)
    col1.metric("Sync Status", store.sync_status_text)
    col2.metric("Audited Events", len(store.audit_log))
    col3.metric("Dirty In-Memory Items", len(store.dirty_email_ids))

    st.divider()

    tab_audit, tab_sync, tab_diag = st.tabs(["🛡️ User & Agent Audit Trail", "🔄 Synchronization Details", "⚙️ Diagnostics"])

    with tab_audit:
        st.subheader("Event Trail")
        if not store.audit_log:
            st.info("No audit logs recorded yet in this session.")
        else:
            # Reverse chronologically
            rev_logs = list(reversed(store.audit_log))
            rows = []
            for entry in rev_logs:
                rows.append({
                    "Timestamp": entry.get("timestamp", ""),
                    "Action": entry.get("action", ""),
                    "Details": str(entry.get("payload", "")),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True)

    with tab_sync:
        st.subheader("State & Database Sync")
        st.write(f"**Last Sync Attempt:** `{store.last_synced_at or 'Never'}`")
        st.write(f"**Account Identifier (Hash):** `{store.email_hash}`")
        st.write(f"**Persistence Target:** {'Local Cache Only (Offline)' if ctx.is_offline else 'Supabase + Atomic Local Cache'}")
        
        if st.button("Flush In-Memory State to Persistent Storage Now"):
            with st.spinner("Persisting state..."):
                store.flush_dirty_emails()
                store.sync_with_supabase()
            st.toast("State successfully synchronized")
            st.rerun()

    with tab_diag:
        st.subheader("Active Settings & Engine Flags")
        st.json({
            "settings": store.settings,
            "offline_mode": ctx.is_offline,
            "user_email": ctx.user_email,
            "emails_loaded_count": len(store.emails),
            "dirty_ids": list(store.dirty_email_ids),
        })


render_activity()
