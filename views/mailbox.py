# """Mailbox view: Filterable, searchable inbox with multi-tab drilldown (Email, AI Triage, Draft)."""
# from __future__ import annotations

# import streamlit as st
# import pandas as pd

# from ui_kit import (
#     STATUS_ORDER,
#     chip,
#     classification_view,
#     draft_editor,
#     email_view,
#     get_ctx,
#     rows,
# )
# from config.domain import CATEGORIES


# def render_mailbox() -> None:
#     ctx = get_ctx()
#     all_rows = rows(ctx.store)

#     st.title("📥 Mailbox")
#     st.caption("Search, filter, and inspect incoming emails and their agent-generated drafts.")

#     if not all_rows:
#         st.info("No emails in mailbox yet. Fetch new emails or run the offline demo.")
#         return

#     # Filter toolbar
#     f1, f2, f3 = st.columns([2, 1, 1])
#     search_q = f1.text_input("🔍 Search subject, sender, or content", placeholder="Type keywords...")
#     status_filter = f2.selectbox("Filter Status", ["All"] + STATUS_ORDER)
#     category_filter = f3.selectbox("Filter Category", ["All"] + list(CATEGORIES))

#     # Apply filters
#     filtered = all_rows
#     if status_filter != "All":
#         filtered = [x for x in filtered if x["status"] == status_filter]
#     if category_filter != "All":
#         filtered = [x for x in filtered if x["cls"].get("category") == category_filter]
#     if search_q.strip():
#         q = search_q.lower()
#         filtered = [
#             x for x in filtered
#             if q in (x["rec"].get("subject", "") or "").lower()
#             or q in (x["rec"].get("sender", "") or "").lower()
#             or q in (x["rec"].get("body", "") or "").lower()
#         ]

#     st.caption(f"Showing {len(filtered)} of {len(all_rows)} emails")

#     left_pane, right_pane = st.columns([1.2, 1.8])

#     with left_pane:
#         st.subheader("Messages")
#         if not filtered:
#             st.info("No messages match current filter.")
#             selected_mid = None
#         else:
#             # Table preview list
#             options = {
#                 x["mid"]: f"{chip(x['status'])} | {x['rec'].get('subject', '(No Subject)')[:35]} ({x['rec'].get('sender', '?')[:20]})"
#                 for x in filtered
#             }
#             default_key = st.session_state.get("sel_email")
#             if default_key not in options:
#                 default_key = filtered[0]["mid"]
            
#             selected_mid = st.radio(
#                 "Select message",
#                 options=list(options.keys()),
#                 format_func=lambda k: options[k],
#                 index=list(options.keys()).index(default_key) if default_key in options else 0,
#                 label_visibility="collapsed",
#                 key="mailbox_radio_select",
#             )
#             st.session_state["sel_email"] = selected_mid

#     with right_pane:
#         if not selected_mid or selected_mid not in ctx.store.emails:
#             st.info("Select an email from the left list to inspect details.")
#             return

#         item = ctx.store.emails[selected_mid]
#         rec = item.get("record") or {}
#         st.subheader(rec.get("subject", "(No Subject)"))
#         st.caption(f"Status: {chip(item.get('status', 'Queued'))} · ID: `{selected_mid}`")

#         t_email, t_cls, t_draft = st.tabs(["📧 Original Email", "🧠 Classification & Verification", "✍️ Draft Reply"])

#         with t_email:
#             email_view(ctx, selected_mid, kp="mb_email")
#         with t_cls:
#             classification_view(ctx, selected_mid, kp="mb_cls")
#         with t_draft:
#             draft_editor(ctx, selected_mid, kp="mb_draft")


# render_mailbox()


"""Mailbox view: Filterable, searchable inbox table with drilldown (Email, AI Triage, Draft)."""
from __future__ import annotations

import html as _html
import re
from email.utils import parseaddr

import pandas as pd
import streamlit as st

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

# ----------------------------------------------------------------------------
# Styling
# ----------------------------------------------------------------------------
CSS = """
<style>
.mb-head {
    font-size: 11px; font-weight: 700; letter-spacing: .08em;
    color: #9a948a; text-transform: uppercase; padding: 10px 6px;
}
.mb-cell { padding: 10px 6px; border-top: 1px solid #ece7de; min-height: 64px;
           display: flex; flex-direction: column; justify-content: center; }
.mb-cell.sel { background: #f3ede2; }

/* sender */
.mb-sender { display: flex; align-items: center; gap: 12px; }
.mb-avatar {
    width: 36px; height: 36px; border-radius: 50%; flex: none;
    background: #fdebc0; border: 1.5px solid #f4c64e; color: #8a5a00;
    display: flex; align-items: center; justify-content: center; font-weight: 700;
}
.mb-name  { font-weight: 700; font-size: 14px; line-height: 1.2; }
.mb-mail  { font-family: ui-monospace, Menlo, monospace; font-size: 11px;
            color: #7d8aa3; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* subject */
.mb-subj  { font-weight: 700; font-size: 14px; white-space: nowrap;
            overflow: hidden; text-overflow: ellipsis; }
.mb-prev  { font-size: 13px; color: #6b665e; white-space: nowrap;
            overflow: hidden; text-overflow: ellipsis; }

/* pills */
.mb-pill  { display: inline-block; padding: 3px 12px; border-radius: 999px;
            font-size: 12px; font-weight: 600; border: 1px solid; width: fit-content; }
.mb-status{ display: inline-block; padding: 8px 14px; border-radius: 18px; width: fit-content;
            background: #f0eeea; color: #333; font-size: 13px; font-weight: 600; text-align: center; }

/* priority */
.mb-prio  { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px;
            border-radius: 999px; font-size: 12px; font-weight: 700; width: fit-content; }
.mb-dot   { width: 11px; height: 11px; border-radius: 50%; border: 1.5px solid #222; }

.mb-date  { font-family: ui-monospace, Menlo, monospace; font-size: 12px; color: #6b665e; }
</style>
"""

"""Mailbox view: Filterable, searchable inbox table with drilldown (Email, AI Triage, Draft)."""

import html as _html
import re
from email.utils import parseaddr

import pandas as pd
import streamlit as st

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

# ----------------------------------------------------------------------------
# Styling
# ----------------------------------------------------------------------------
CSS = """
<style>
.mb-head {
    font-size: 11px; font-weight: 700; letter-spacing: .08em;
    color: #9a948a; text-transform: uppercase; padding: 10px 6px;
}
.mb-cell { padding: 10px 6px; border-top: 1px solid #ece7de; min-height: 64px;
           display: flex; flex-direction: column; justify-content: center; }
.mb-cell.sel { background: #f3ede2; }

/* sender */
.mb-sender { display: flex; align-items: center; gap: 12px; }
.mb-avatar {
    width: 36px; height: 36px; border-radius: 50%; flex: none;
    background: #fdebc0; border: 1.5px solid #f4c64e; color: #8a5a00;
    display: flex; align-items: center; justify-content: center; font-weight: 700;
}
.mb-name  { font-weight: 700; font-size: 14px; line-height: 1.2; }
.mb-mail  { font-family: ui-monospace, Menlo, monospace; font-size: 11px;
            color: #7d8aa3; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* subject */
.mb-subj  { font-weight: 700; font-size: 14px; white-space: nowrap;
            overflow: hidden; text-overflow: ellipsis; }
.mb-prev  { font-size: 13px; color: #6b665e; white-space: nowrap;
            overflow: hidden; text-overflow: ellipsis; }

/* pills */
.mb-pill  { display: inline-block; padding: 3px 12px; border-radius: 999px;
            font-size: 12px; font-weight: 600; border: 1px solid; width: fit-content; }
.mb-status{ display: inline-block; padding: 8px 14px; border-radius: 18px; width: fit-content;
            background: #f0eeea; color: #333; font-size: 13px; font-weight: 600; text-align: center; }

/* urgency / priority */
.mb-prio  { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px;
            border-radius: 999px; font-size: 12px; font-weight: 700; width: fit-content; }
.mb-dot   { width: 11px; height: 11px; border-radius: 50%; border: 1.5px solid #222; }

.mb-date  { font-family: ui-monospace, Menlo, monospace; font-size: 12px; color: #6b665e; }
</style>
"""

CATEGORY_PALETTE = [  # (bg, border, text)
    ("#e0f7ee", "#7fdcb6", "#0b7a52"),
    ("#e4e6ea", "#c9ccd2", "#33373f"),
    ("#fff1c9", "#f4cf5b", "#7a5a00"),
    ("#e5eefc", "#9cbcf0", "#1f4f9e"),
    ("#f1e5fb", "#c9a2ec", "#6a2fa0"),
]
SPAM_STYLE = ("#fde8e8", "#f5a3a3", "#c0392b")
EMPTY_STYLE = ("#f0eeea", "#dcd8d0", "#8a857b")

PRIORITY_STYLE = {  # dot colour, bg, text
    "high": ("#e53935", "#fde8ec", "#b71c3c"),
    "med":  ("#fdd835", "#fff6d6", "#a86a00"),
    "low":  ("#43a047", "#e6f6ea", "#1b7a3a"),
}
# Maps whatever the classifier returns for `urgency` onto high / med / low
URGENCY_ALIAS = {
    "high": "high", "urgent": "high", "critical": "high", "immediate": "high",
    "medium": "med", "med": "med", "normal": "med", "moderate": "med",
    "low": "low", "none": "low", "not urgent": "low",
}

PAGE_SIZE = 25
WIDTHS = [2.3, 3.3, 1.4, 1.1, 1.1, 1.0, 1.0]


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def _esc(s) -> str:
    return _html.escape(str(s or ""))


def _plain(text: str, limit: int = 90) -> str:
    """Strip HTML tags/entities so previews never show raw markup."""
    t = re.sub(r"<[^>]+>", " ", text or "")
    t = _html.unescape(t.replace("&nbsp;", " "))
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit]


def _split_sender(raw: str) -> tuple[str, str]:
    name, addr = parseaddr(raw or "")
    addr = addr or (raw or "?")
    name = name or addr.split("@")[0]
    return name, addr


def _category_style(cat: str):
    c = (cat or "").strip()
    if c in ("", "—", "-"):
        return EMPTY_STYLE
    if c.lower() == "spam":
        return SPAM_STYLE
    cats = list(CATEGORIES)
    idx = cats.index(c) if c in cats else sum(map(ord, c))
    return CATEGORY_PALETTE[idx % len(CATEGORY_PALETTE)]


def _priority_html(urgency, escalate=False) -> str:
    key = URGENCY_ALIAS.get(str(urgency or "").strip().lower())
    if key is None:
        return '<span class="mb-date">—</span>'
    dot, bg, fg = PRIORITY_STYLE[key]
    label = {"high": "High", "med": "Med", "low": "Low"}[key]
    flag = " ⚑" if escalate else ""  # small flag when Escalate is True
    return (f'<span class="mb-prio" style="background:{bg};color:{fg}">'
            f'<span class="mb-dot" style="background:{dot}"></span>{label}{flag}</span>')


def _date_str(rec: dict) -> str:
    raw = rec.get("date") or rec.get("received_at") or rec.get("timestamp")
    if not raw:
        return "—"
    try:
        return pd.to_datetime(raw, utc=True).strftime("%b %d, %Y")
    except Exception:
        return str(raw)[:12]


def _received_sort_key(row: dict) -> int:
    rec = row.get("rec") or {}
    raw = (
        rec.get("date")
        or rec.get("received_at")
        or rec.get("timestamp")
        or row.get("updated")
    )
    parsed = pd.to_datetime(raw, utc=True, errors="coerce")
    return int(parsed.value) if not pd.isna(parsed) else -1


def _status_label(status: str) -> str:
    return _esc(status or "Queued")


def _header_row() -> None:
    cols = st.columns(WIDTHS, vertical_alignment="center")
    for c, h in zip(cols, ["Sender", "Subject", "Category", "Urgency", "Status", "Date", "Actions"]):
        c.markdown(f'<div class="mb-head">{h}</div>', unsafe_allow_html=True)


def _email_row(x: dict, selected: bool) -> tuple[bool, bool]:
    """Render one table row. Returns (open_clicked, delete_clicked)."""
    rec, cls = x["rec"], x.get("cls") or {}
    mid = x["mid"]
    sel = " sel" if selected else ""

    name, addr = _split_sender(rec.get("sender", ""))
    cat = cls.get("category") or "—"
    bg, bd, fg = _category_style(cat)

    cols = st.columns(WIDTHS, vertical_alignment="center")

    cols[0].markdown(
        f'<div class="mb-cell{sel}"><div class="mb-sender">'
        f'<div class="mb-avatar">{_esc(name[:1].upper())}</div>'
        f'<div style="min-width:0"><div class="mb-name">{_esc(name)}</div>'
        f'<div class="mb-mail">{_esc(addr)}</div></div></div></div>',
        unsafe_allow_html=True,
    )
    cols[1].markdown(
        f'<div class="mb-cell{sel}"><div class="mb-subj">{_esc(rec.get("subject") or "(No Subject)")}</div>'
        f'<div class="mb-prev">{_esc(_plain(rec.get("body", "")))}</div></div>',
        unsafe_allow_html=True,
    )
    cols[2].markdown(
        f'<div class="mb-cell{sel}"><span class="mb-pill" '
        f'style="background:{bg};border-color:{bd};color:{fg}">{_esc(cat)}</span></div>',
        unsafe_allow_html=True,
    )
    cols[3].markdown(
        f'<div class="mb-cell{sel}">{_priority_html(cls.get("urgency"), cls.get("escalate"))}</div>',
        unsafe_allow_html=True,
    )
    cols[4].markdown(
        f'<div class="mb-cell{sel}"><span class="mb-status">{_status_label(x["status"])}</span></div>',
        unsafe_allow_html=True,
    )
    cols[5].markdown(
        f'<div class="mb-cell{sel}"><span class="mb-date">{_esc(_date_str(rec))}</span></div>',
        unsafe_allow_html=True,
    )

    a1, a2 = cols[6].columns(2)
    open_clicked = a1.button("👁", key=f"open_{mid}", help="Open email", type="tertiary")
    del_clicked = a2.button("🗑", key=f"del_{mid}", help="Delete email", type="tertiary")
    return open_clicked, del_clicked


# ----------------------------------------------------------------------------
# Page
# ----------------------------------------------------------------------------
def render_mailbox() -> None:
    ctx = get_ctx()
    all_rows = sorted(rows(ctx.store), key=_received_sort_key, reverse=True)
    st.markdown(CSS, unsafe_allow_html=True)

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

    if not filtered:
        st.info("No messages match current filter.")
        return

    # Selection state
    if st.session_state.get("sel_email") not in {x["mid"] for x in filtered}:
        st.session_state["sel_email"] = filtered[0]["mid"]

    # Simple "load more" pagination
    limit = st.session_state.setdefault("mb_limit", PAGE_SIZE)

    # Table
    _header_row()
    to_delete = None
    for x in filtered[:limit]:
        opened, deleted = _email_row(x, selected=(x["mid"] == st.session_state["sel_email"]))
        if opened:
            st.session_state["sel_email"] = x["mid"]
            st.rerun()
        if deleted:
            to_delete = x["mid"]

    if len(filtered) > limit:
        if st.button(f"Load more ({len(filtered) - limit} remaining)"):
            st.session_state["mb_limit"] = limit + PAGE_SIZE
            st.rerun()

    if to_delete:
        store = ctx.store
        if hasattr(store, "delete_email"):
            store.delete_email(to_delete)
        else:
            store.emails.pop(to_delete, None)
        st.rerun()

    # Detail drilldown
    st.divider()
    selected_mid = st.session_state["sel_email"]
    if selected_mid not in ctx.store.emails:
        st.info("Select an email from the table to inspect details.")
        return

    item = ctx.store.emails[selected_mid]
    rec = item.get("record") or {}
    st.subheader(rec.get("subject", "(No Subject)"))
    st.caption(f"Status: {chip(item.get('status', 'Queued'))} · ID: `{selected_mid}`")

    t_email, t_cls, t_draft = st.tabs(
        ["📧 Original Email", "🧠 Classification & Verification", "✍️ Draft Reply"]
    )
    with t_email:
        email_view(ctx, selected_mid, kp="mb_email")
    with t_cls:
        classification_view(ctx, selected_mid, kp="mb_cls")
    with t_draft:
        draft_editor(ctx, selected_mid, kp="mb_draft")


render_mailbox()
