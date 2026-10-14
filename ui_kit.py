"""Shared helpers for the multipage app: auth, context, live engine, sidebar, panels."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

from auth import (
    exchange_code_for_credentials,
    get_authorization_url,
    get_user_email,
    hash_email,
    verify_oauth_state,
)
from config.domain import CATEGORIES, COMPANY_NAME
from config.settings import CONFIDENCE_THRESHOLD, POLL_SECONDS
from drafter import generate_draft, record_human_edit
from gmail_client import GmailClient
from pipeline import PipelineRunner
from store import StoreManager, SupabaseStore, get_supabase_client

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
STATUS_ICON = {
    "Queued": "🔵", "Fetched": "🔵", "Classified": "🟣", "Verified": "🟣",
    "Drafted": "🟢", "Escalated": "🔴", "NeedsReview": "🟠",
    "Skipped": "⚪", "Failed": "❌",
}
STATUS_ORDER = list(STATUS_ICON)
ATTENTION = ("Escalated", "NeedsReview", "Failed")

_CSS = """
<style>
.block-container {padding-top: 1.5rem; max-width: 1400px;}
.pill {display:inline-block; padding:2px 10px; border-radius:999px;
       font-size:.78rem; font-weight:600; background:rgba(128,128,128,.18);}
.pill.live {background:rgba(22,163,74,.18); color:#16a34a;}
.pill.off  {background:rgba(220,38,38,.15); color:#dc2626;}
</style>
"""


def chip(status: str) -> str:
    return f"{STATUS_ICON.get(status, '⚪')} {status}"


def inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Context (rebuilt by app.py on every rerun, read by every page)
# --------------------------------------------------------------------------- #
@dataclass
class Ctx:
    store: StoreManager
    runner: PipelineRunner
    gmail: Optional[GmailClient]
    is_offline: bool
    user_email: str


def set_ctx(ctx: Ctx) -> None:
    st.session_state["_ctx"] = ctx


def get_ctx() -> Ctx:
    return st.session_state["_ctx"]


def rows(store: StoreManager) -> List[Dict[str, Any]]:
    """Normalised view of store.emails (the dict key is the message id)."""
    out = []
    for mid, item in store.emails.items():
        rec = item.get("record") or {}
        out.append({
            "mid": mid,
            "status": item.get("status", "Queued"),
            "rec": rec,
            "cls": rec.get("classification") or {},
            "updated": item.get("updated_at", "") or "",
        })
    return out


def summary(store: StoreManager) -> Dict[str, Any]:
    r = rows(store)
    by_status = {s: 0 for s in STATUS_ORDER}
    for x in r:
        by_status[x["status"]] = by_status.get(x["status"], 0) + 1
    confs = [x["cls"].get("confidence") for x in r if x["cls"].get("confidence")]
    return {
        "rows": r,
        "total": len(r),
        "by_status": by_status,
        "processed": len(r) - by_status["Queued"],
        "avg_conf": (sum(confs) / len(confs)) if confs else 0.0,
    }


def open_in(mid: str, page: str) -> None:
    """Jump to the mailbox or draft studio with an email preselected."""
    st.session_state["sel_email"] = mid
    st.session_state["studio_pick"] = mid
    st.switch_page("views/mailbox.py" if page == "mailbox" else "views/draft_studio.py")


# --------------------------------------------------------------------------- #
# Auth screens
# --------------------------------------------------------------------------- #
def handle_oauth_callback() -> None:
    qp = st.query_params
    if "code" not in qp or "state" not in qp:
        return
    code, state = qp["code"], qp["state"]
    if not verify_oauth_state(state):
        st.error("Invalid or expired sign-in link. Please sign in again.")
        st.query_params.clear()
        return
    try:
        creds = exchange_code_for_credentials(code=code, state=state)
        email = get_user_email(creds)
        st.session_state.update(
            credentials=creds, user_email=email,
            email_hash=hash_email(email), offline_mode=False,
        )
        st.query_params.clear()
        st.rerun()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Sign-in failed: {exc}")
        st.query_params.clear()


def login_screen() -> None:
    _, mid, _ = st.columns([1, 1.7, 1])
    with mid:
        st.title("📬 Mail agent")
        st.caption(f"Triage, verify and draft replies for {COMPANY_NAME} — drafts only, nothing is ever sent.")
        with st.container(border=True):
            st.subheader("Sign in with Google")
            st.write("Allow the agent to read your inbox and save drafts in the original thread.")
            try:
                url, _ = get_authorization_url()
                st.link_button("🔑 Sign in with Google", url, type="primary", width="stretch")
            except Exception as exc:  # noqa: BLE001
                st.warning(f"Google sign-in is not configured: {exc}")
        with st.container(border=True):
            st.subheader("Try the offline demo")
            st.write("Runs on precomputed sample emails. No Google or Groq calls.")
            if st.button("Launch offline demo", width="stretch"):
                st.session_state.update(
                    offline_mode=True, user_email="demo.judge@example.com",
                    email_hash=hash_email("demo.judge@example.com"),
                )
                st.rerun()
    st.stop()


# --------------------------------------------------------------------------- #
# Store / context bootstrap
# --------------------------------------------------------------------------- #
@st.cache_resource
def _supabase():
    return get_supabase_client()


def _session_store(email_hash: str) -> StoreManager:
    if st.session_state.get("current_email_hash") != email_hash or "store_manager" not in st.session_state:
        store = StoreManager(email_hash=email_hash, supabase_store=SupabaseStore(client=_supabase()))
        store.initialize_session()
        st.session_state.store_manager = store
        st.session_state.current_email_hash = email_hash
    return st.session_state.store_manager


def _load_fixtures(store: StoreManager) -> None:
    path = Path(__file__).parent / "fixtures" / "precomputed_results.json"
    if path.exists():
        for mid, item in json.loads(path.read_text(encoding="utf-8")).items():
            store.emails[mid] = item
        store.sync_status_text = "Offline fixture data loaded"


def build_ctx() -> Ctx:
    email = st.session_state.get("user_email", "demo.judge@example.com")
    ehash = st.session_state.get("email_hash") or hash_email(email)
    offline = st.session_state.get("offline_mode", False)
    store = _session_store(ehash)
    if offline and not store.emails:
        _load_fixtures(store)

    gmail = None
    if not offline and "credentials" in st.session_state:
        gmail = st.session_state.get("_gmail")  # build the API client once per session
        if gmail is None:
            try:
                gmail = GmailClient(st.session_state.credentials)
                st.session_state["_gmail"] = gmail
            except Exception as exc:  # noqa: BLE001
                st.sidebar.error(f"Gmail connection error: {exc}")
    return Ctx(store, PipelineRunner(store_manager=store, gmail_client=gmail), gmail, offline, email)


# --------------------------------------------------------------------------- #
# Live engine: ticks only when due, refreshes the whole app only on change
# --------------------------------------------------------------------------- #
def _snapshot(store: StoreManager) -> Dict[str, str]:
    return {mid: item.get("status", "Queued") for mid, item in store.emails.items()}


def maybe_tick(ctx: Ctx, force: bool = False) -> int:
    """Run one sweep if due. Returns how many emails changed status."""
    if ctx.is_offline:
        return 0
    if not force and time.time() - st.session_state.get("last_tick_ts", 0.0) < POLL_SECONDS - 1:
        return 0
    before = _snapshot(ctx.store)
    ctx.runner.run_tick()
    st.session_state["last_tick_ts"] = time.time()
    after = _snapshot(ctx.store)
    return sum(1 for k, v in after.items() if before.get(k) != v)


def retry_failed(ctx: Ctx) -> int:
    n = 0
    for mid, item in list(ctx.store.emails.items()):
        if item.get("status") == "Failed":
            rec = item.setdefault("record", {})
            rec["attempts"] = 0
            ctx.store.upsert_email_memory(mid, "Queued", rec)
            n += 1
    if n:
        maybe_tick(ctx, force=True)
    return n


@st.fragment(run_every=POLL_SECONDS)
def engine_status() -> None:
    ctx = get_ctx()
    auto = ctx.store.settings.get("auto_poll", True) and not ctx.is_offline
    if auto:
        with st.spinner("Checking inbox…"):
            changed = maybe_tick(ctx)
        if changed:
            st.session_state["flash"] = f"✅ {changed} email(s) updated"
            st.rerun(scope="app")  # redraw every page with the new data

    last = st.session_state.get("last_tick_ts")
    pill = '<span class="pill live">● live</span>' if auto else '<span class="pill off">⏸ paused</span>'
    st.markdown(pill, unsafe_allow_html=True)
    if last:
        st.caption(f"Last checked {time.strftime('%H:%M:%S', time.localtime(last))} · "
                   f"next ≈ {time.strftime('%H:%M:%S', time.localtime(last + POLL_SECONDS))}")
    st.caption(f"Sync: {ctx.store.sync_status_text}")


@st.dialog("Delete all data?")
def _confirm_delete() -> None:
    st.write("This permanently removes your stored emails, drafts and audit log. Gmail itself is untouched.")
    if st.button("Yes, delete everything", type="primary"):
        get_ctx().store.delete_all_data()
        st.session_state.clear()
        st.rerun()


def render_sidebar(ctx: Ctx) -> None:
    store = ctx.store
    with st.sidebar:
        st.divider()
        engine_status()
        with st.container(border=True):
            st.caption("Signed in as")
            st.write(f"**{ctx.user_email}**")
            if ctx.is_offline:
                st.caption("Offline demo · sample data only")

        c1, c2 = st.columns(2)
        if c1.button("⚡ Fetch now", width="stretch", disabled=ctx.is_offline):
            with st.spinner("Sweeping inbox…"):
                changed = maybe_tick(ctx, force=True)
            st.session_state["flash"] = f"Sweep finished · {changed} email(s) updated"
            st.rerun()
        if c2.button("🔄 Retry failed", width="stretch", disabled=ctx.is_offline):
            st.session_state["flash"] = f"Retried {retry_failed(ctx)} failed email(s)"
            st.rerun()

        with st.expander("Settings"):
            store.settings["auto_poll"] = st.toggle("Auto-poll inbox", value=store.settings.get("auto_poll", True))
            store.settings["auto_draft"] = st.toggle(
                "Auto-generate drafts",
                value=store.settings.get("auto_draft", False),
                help="If off, incoming emails are classified and triaged, and drafts are only generated on demand.",
            )
            store.settings["confidence_threshold"] = st.slider(
                "Confidence threshold", 0.3, 0.9,
                float(store.settings.get("confidence_threshold", CONFIDENCE_THRESHOLD)), 0.05,
            )
        with st.expander("Account"):
            if st.button("Log out", width="stretch"):
                st.session_state.clear()
                st.rerun()
            if st.button("Delete my data", width="stretch"):
                _confirm_delete()
        st.caption("Keep this tab visible during the demo so polling stays on schedule.")


# --------------------------------------------------------------------------- #
# Reusable panels
# --------------------------------------------------------------------------- #
def email_view(ctx: Ctx, mid: str, kp: str) -> None:
    item = ctx.store.emails[mid]
    rec = item.get("record") or {}
    st.write(f"**From:** `{rec.get('sender', '?')}`  ·  **Reply-To:** `{rec.get('reply_to') or '—'}`")
    if rec.get("attachment_names"):
        st.write("**Attachments:** " + ", ".join(rec["attachment_names"]))
    st.text_area("Body", rec.get("body", ""), height=240, disabled=True, key=f"{kp}_body_{mid}")
    if item.get("status") == "Skipped":
        st.warning(f"Skipped: {rec.get('skip_reason', 'automated filter')}")
        if st.button("⚡ Process anyway", key=f"{kp}_force_{mid}", disabled=ctx.is_offline):
            with st.spinner("Processing…"):
                ctx.runner.process_single_email(mid, force_reclassify=True)
            st.rerun()


def classification_view(ctx: Ctx, mid: str, kp: str) -> None:
    store = ctx.store
    item = store.emails[mid]
    rec = item.setdefault("record", {})
    cls = rec.get("classification") or {}
    ver = rec.get("verifier_comparison") or {}

    a, b = st.columns(2)
    a.write(f"**Category:** `{cls.get('category', '—')}`")
    a.write(f"**Sentiment:** `{cls.get('sentiment', '—')}` · **Urgency:** `{cls.get('urgency', '—')}`")
    conf = max(0.0, min(1.0, float(cls.get("confidence") or 0.0)))
    a.progress(conf, text=f"Confidence {conf:.0%}")
    agreed = ver.get("agreed")
    verdict = "⏳ pending" if agreed is None else ("✅ agrees" if agreed else "⚠️ disagrees")
    b.write(f"**Escalate:** `{cls.get('escalate', False)}`")
    b.write(f"**Verifier:** {verdict}")
    if cls.get("escalation_reasons"):
        b.error("Reasons: " + ", ".join(cls["escalation_reasons"]))
    if cls.get("evidence_quote"):
        st.info(f"📌 “{cls['evidence_quote']}”")
    if cls.get("short_reasoning"):
        st.caption(cls["short_reasoning"])
    with st.expander("Raw model output"):
        st.json({"classification": cls, "verification": rec.get("verification"), "comparison": ver})

    st.divider()
    current = cls.get("category")
    new_cat = st.selectbox(
        "Correct the category", CATEGORIES,
        index=CATEGORIES.index(current) if current in CATEGORIES else 0, key=f"{kp}_ovr_{mid}",
    )
    if st.button("Apply correction and redraft", key=f"{kp}_ovr_btn_{mid}", disabled=new_cat == current):
        cls["category"] = new_cat
        rec["classification"] = cls
        store.log_audit("human_override", {"message_id": mid, "new_category": new_cat})
        with st.spinner("Redrafting…"):
            draft, _ = generate_draft(
                sender=rec.get("sender", ""), subject=rec.get("subject", ""), body=rec.get("body", ""),
                category=new_cat, status="Drafted",
            )
        rec["draft"] = draft
        store.upsert_email_memory(mid, "Drafted", rec)
        store.flush_dirty_emails()
        st.toast("Category updated and draft regenerated")
        st.rerun()


def draft_editor(ctx: Ctx, mid: str, kp: str) -> None:
    store = ctx.store
    item = store.emails[mid]
    rec = item.setdefault("record", {})
    status = item.get("status", "Queued")
    cls = rec.get("classification") or {}
    draft = rec.get("draft") or {}
    category = cls.get("category", "Other/Unclear")

    def _persist(new_status: str = status) -> None:
        store.upsert_email_memory(mid, new_status, rec)
        store.flush_dirty_emails()

    if not draft or not draft.get("draft_text"):
        st.info("No draft generated yet for this email.")
        init_guidelines = st.text_input(
            "Instructions / Guidelines for this draft (optional)",
            value="",
            key=f"{kp}_init_guide_{mid}",
            placeholder="e.g. politely decline, offer a phone call next week, keep under 60 words",
        )
        if st.button("✨ Generate draft", key=f"{kp}_gen_{mid}", type="primary"):
            with st.spinner("Drafting…"):
                new, _ = generate_draft(
                    sender=rec.get("sender", ""), subject=rec.get("subject", ""),
                    body=rec.get("body", ""), category=category, status=status,
                    reviewer_guidelines=init_guidelines.strip() or None,
                    escalation_reasons=cls.get("escalation_reasons", []),
                )
            rec["draft"] = new
            _persist(new_status="Drafted" if status == "Classified" else status)
            st.toast("Draft generated successfully")
            st.rerun()
        return

    if draft.get("requires_human_review"):
        st.warning("Requires human review — this is a holding reply.")
    if draft.get("edited_by_human"):
        st.success("Edited by a human reviewer")

    guidelines = st.text_input(
        "Guidelines for regeneration / prompt steering",
        value=draft.get("guidelines", ""),
        key=f"{kp}_guide_{mid}",
        placeholder="e.g. offer a call on Thursday, keep it under 80 words, mention promo discount",
    )
    stored = draft.get("draft_text", "")
    # Key includes the stored text so regenerate/restore/save refresh the box.
    text = st.text_area("Draft", value=stored, height=260, key=f"{kp}_area_{mid}_{hash(stored)}")
    dirty = text.strip() != stored.strip()
    st.caption(f"{len(text.split())} words" + ("  ·  ✏️ unsaved changes" if dirty else ""))

    b1, b2, b3 = st.columns(3)
    if b1.button("💾 Save to Gmail draft", key=f"{kp}_save_{mid}", type="primary" if dirty else "secondary",
                 width="stretch"):
        new_draft = record_human_edit(draft, text) if dirty else draft
        if guidelines.strip() != (new_draft.get("guidelines") or ""):
            new_draft["guidelines"] = guidelines.strip()
        if ctx.gmail:
            try:
                res = ctx.gmail.create_or_update_draft(
                    thread_id=rec.get("thread_id", ""),
                    to_email=rec.get("reply_to") or rec.get("sender", ""),
                    subject=rec.get("subject", ""), body_text=text,
                    in_reply_to=rec.get("in_reply_to"), references=rec.get("references"),
                    existing_draft_id=draft.get("gmail_draft_id"),
                )
                gid = res.get("id") if isinstance(res, dict) else (res if isinstance(res, str) else None)
                if gid:
                    new_draft["gmail_draft_id"] = gid  # avoids duplicate drafts on the next save
                st.toast("Saved and synced to Gmail")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Gmail sync failed: {exc}")
        else:
            st.toast("Saved locally (offline mode)")
        rec["draft"] = new_draft
        _persist()
        st.rerun()

    if b2.button("✨ Regenerate", key=f"{kp}_regen_{mid}", width="stretch"):
        with st.spinner("Regenerating with guidelines…"):
            new, _ = generate_draft(
                sender=rec.get("sender", ""), subject=rec.get("subject", ""), body=rec.get("body", ""),
                category=category, status=status, reviewer_guidelines=guidelines.strip() or None,
                escalation_reasons=cls.get("escalation_reasons", []),
                existing_versions=draft.get("versions", []),
            )
        rec["draft"] = new
        _persist()
        st.toast("Draft regenerated with new guidelines")
        st.rerun()

    if rec.get("thread_id"):
        b3.link_button("🌐 Open in Gmail", f"https://mail.google.com/mail/u/0/#inbox/{rec['thread_id']}",
                       width="stretch")

    versions = draft.get("versions", [])
    if len(versions) > 1:
        with st.expander(f"🕘 Version history ({len(versions)})"):
            labels = [f"v{v.get('version')} · {v.get('author')} · {v.get('timestamp')}" for v in versions]
            pick = st.selectbox("Version", labels, index=len(labels) - 1, key=f"{kp}_ver_{mid}")
            chosen = versions[labels.index(pick)]
            st.text_area("Preview", chosen.get("text", ""), height=160, disabled=True, key=f"{kp}_vprev_{mid}_{pick}")
            if st.button("↩️ Restore this version", key=f"{kp}_restore_{mid}"):
                rec["draft"] = record_human_edit(draft, chosen.get("text", ""))
                _persist()
                st.rerun()
