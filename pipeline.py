"""Pipeline Engine: Sweep, Delta Sync, State Machine, and per-email processing.

Statuses:
- Queued, Fetched, Classified, Verified, Drafted, Escalated, NeedsReview, Skipped, Failed.
- Terminal: Drafted, Escalated, NeedsReview, Skipped (and Failed after 3 attempts).

Safety:
- One in-process lock per account.
- Try/except around each email: one failure never halts the sweep.
- Rate-limit deferral without hanging the UI.
- Never re-classify a message already terminal unless explicitly forced.
- Updates Gmail drafts and applies Agent labels.
"""
import logging
import threading
import time
from typing import Dict, Any, List, Optional, Set

from config.settings import MAX_PER_TICK, MAX_SCAN, CONFIDENCE_THRESHOLD
from classifier import classify_email
from verifier import verify_email_blind, compare_classifications
from drafter import generate_draft
from rules import check_pre_llm_rules
from store import StoreManager

logger = logging.getLogger(__name__)

# Registry of per-account locks to prevent race conditions across tabs/sessions
_ACCOUNT_LOCKS: Dict[str, threading.Lock] = {}
_REGISTRY_LOCK = threading.Lock()


def get_account_lock(email_hash: str) -> threading.Lock:
    with _REGISTRY_LOCK:
        if email_hash not in _ACCOUNT_LOCKS:
            _ACCOUNT_LOCKS[email_hash] = threading.Lock()
        return _ACCOUNT_LOCKS[email_hash]


TERMINAL_STATUSES = {"Classified", "Drafted", "Escalated", "NeedsReview", "Skipped", "Failed"}


class PipelineRunner:
    def __init__(self, store_manager: StoreManager, gmail_client=None):
        self.store = store_manager
        self.gmail = gmail_client
        self.email_hash = store_manager.email_hash
        self.lock = get_account_lock(self.email_hash)

    def sweep_inbox_stubs(self, max_scan: int = MAX_SCAN) -> int:
        """List INBOX IDs (IDs only, newest first), diff against memory, and add Queued stubs."""
        if not self.gmail:
            return 0

        inbox_ids = self.gmail.list_inbox_message_ids(max_scan=max_scan)
        new_stubs_count = 0

        for m_id in inbox_ids:
            if m_id not in self.store.emails:
                # Add as Queued stub so dashboard shows it immediately
                self.store.upsert_email_memory(
                    message_id=m_id,
                    status="Queued",
                    record={
                        "message_id": m_id,
                        "subject": "(Pending fetch...)",
                        "sender": "(Pending fetch...)",
                        "attempts": 0,
                    }
                )
                new_stubs_count += 1

        if new_stubs_count > 0:
            self.store.flush_dirty_emails()
        return new_stubs_count

    def run_tick(self, max_per_tick: int = MAX_PER_TICK) -> Dict[str, Any]:
        """Execute one complete polling cycle for the current account."""
        # Non-blocking lock check to ensure single execution per account
        if not self.lock.acquire(blocking=False):
            return {"status": "busy", "message": "Another tick is currently running for this mailbox."}

        tick_summary = {
            "processed": 0,
            "failed": 0,
            "skipped": 0,
            "deferred": False,
            "errors": [],
        }

        try:
            # 1. Delta sync from remote storage
            self.store.perform_delta_sync()

            # 2. State-based sweep
            if self.gmail:
                self.sweep_inbox_stubs()

            # 3. Identify queued or retriable emails (newest first so fresh judge emails jump queue)
            eligible_ids = []
            for m_id, data in self.store.emails.items():
                status = data.get("status")
                attempts = data.get("record", {}).get("attempts", 0)

                # Process if Queued, or Failed with < 3 attempts
                if status == "Queued" or (status == "Failed" and attempts < 3):
                    eligible_ids.append(m_id)

            # Process up to MAX_PER_TICK
            batch = eligible_ids[:max_per_tick]

            for m_id in batch:
                process_result = self.process_single_email(m_id)
                status = process_result.get("status")

                if process_result.get("rate_limited"):
                    tick_summary["deferred"] = True
                    break

                if status == "Failed":
                    tick_summary["failed"] += 1
                elif status == "Skipped":
                    tick_summary["skipped"] += 1
                else:
                    tick_summary["processed"] += 1

            # 4. Upsert account_state at the end of each tick
            self.store.flush_state()

        finally:
            self.lock.release()

        return tick_summary

    def process_single_email(self, message_id: str, force_reclassify: bool = False) -> Dict[str, Any]:
        """Process an individual email through the state machine wrapped in try/except."""
        email_data = self.store.emails.get(message_id, {})
        current_status = email_data.get("status", "Queued")
        rec = dict(email_data.get("record", {}))
        attempts = rec.get("attempts", 0) + 1
        rec["attempts"] = attempts

        # If already terminal and not forcing reclassify, do nothing.
        # Failed emails are retried until they hit the three-attempt retry cap.
        if current_status == "Failed" and rec.get("attempts", 0) >= 3 and not force_reclassify:
            return {"status": current_status, "record": rec}
        if current_status in TERMINAL_STATUSES and current_status != "Failed" and not force_reclassify:
            return {"status": current_status, "record": rec}

        try:
            # Step A: Fetch full content if not already populated
            if not rec.get("body") or not rec.get("sender"):
                if self.gmail:
                    raw_msg = self.gmail.fetch_message_raw(message_id)
                    parsed = self.gmail.parse_message(raw_msg)
                    rec.update(parsed)
                    self.store.upsert_email_memory(message_id, "Fetched", rec)
                else:
                    # Offline / fixture mode fallback
                    rec.setdefault("body", "")
                    rec.setdefault("sender", "offline@example.com")
                    rec.setdefault("subject", "Offline Test")
                    rec.setdefault("is_auto_reply", False)
                    rec.setdefault("attachment_names", [])

            # Step B: Classify
            conf_threshold = self.store.settings.get("confidence_threshold", CONFIDENCE_THRESHOLD)
            classification, class_tokens = classify_email(
                sender=rec.get("sender", ""),
                subject=rec.get("subject", ""),
                body=rec.get("body", ""),
                attachment_names=rec.get("attachment_names", []),
                is_auto_reply=rec.get("is_auto_reply", False),
                confidence_threshold=conf_threshold,
            )

            # Check if rate-limited
            if classification.get("rate_limited"):
                rec["error"] = "Rate limit reached. Will resume next tick."
                self.store.upsert_email_memory(message_id, "Queued", rec)
                return {"status": "Queued", "rate_limited": True, "record": rec}

            rec["classification"] = classification
            rec["tokens_used"] = rec.get("tokens_used", 0) + class_tokens
            cls_status = classification.get("status", "Classified")

            # If skipped by pre-LLM rule
            if cls_status == "Skipped":
                rec["skip_reason"] = classification.get("skip_reason", "Pre-rule filter")
                self.store.upsert_email_memory(message_id, "Skipped", rec)
                self.store.log_audit("email_skipped", {"message_id": message_id, "reason": rec["skip_reason"]})
                self.store.flush_dirty_emails()
                return {"status": "Skipped", "record": rec}

            # Step C: Verifier (blind cross-examination)
            verifier_res, ver_tokens = verify_email_blind(
                sender=rec.get("sender", ""),
                subject=rec.get("subject", ""),
                body=rec.get("body", ""),
            )
            rec["tokens_used"] += ver_tokens
            rec["verification"] = verifier_res
            comparison = compare_classifications(classification, verifier_res)
            rec["verifier_comparison"] = comparison

            # Determine pipeline status after verifier check
            category = classification.get("category", "Other/Unclear")
            escalate = classification.get("escalate", False) or verifier_res.get("escalate", False)

            if escalate:
                final_status = "Escalated"
            elif not comparison.get("agreed") or cls_status == "NeedsReview":
                final_status = "NeedsReview"
            else:
                final_status = "Drafted"

            # Step D: Draft creation (only if auto_draft is enabled in settings or already drafted)
            should_auto_draft = self.store.settings.get("auto_draft", True)
            existing_draft_info = rec.get("draft", {})

            if should_auto_draft or existing_draft_info.get("draft_text"):
                existing_versions = existing_draft_info.get("versions", [])
                reviewer_guidelines = existing_draft_info.get("guidelines", "")

                draft_result, draft_tokens = generate_draft(
                    sender=rec.get("sender", ""),
                    subject=rec.get("subject", ""),
                    body=rec.get("body", ""),
                    category=category,
                    status=final_status,
                    escalation_reasons=classification.get("escalation_reasons", []),
                    reviewer_guidelines=reviewer_guidelines,
                    existing_versions=existing_versions,
                )
                rec["tokens_used"] += draft_tokens
                if draft_result.get("requires_human_review") and final_status == "Drafted":
                    final_status = "NeedsReview"

                # Synchronize draft to Gmail if connected
                if self.gmail:
                    try:
                        gmail_draft_id = self.gmail.create_or_update_draft(
                            thread_id=rec.get("thread_id", ""),
                            to_email=rec.get("reply_to") or rec.get("sender", ""),
                            subject=rec.get("subject", ""),
                            body_text=draft_result["draft_text"],
                            in_reply_to=rec.get("in_reply_to"),
                            references=rec.get("references"),
                            existing_draft_id=existing_draft_info.get("gmail_draft_id"),
                        )
                        draft_result["gmail_draft_id"] = gmail_draft_id
                    except Exception as draft_err:
                        rec["draft_sync_error"] = str(draft_err)

                rec["draft"] = draft_result
            else:
                # If auto_draft is off and not escalated/needs_review, state remains Classified awaiting user draft request
                if final_status == "Drafted":
                    final_status = "Classified"

            # Step E: Apply Agent labels if Gmail connected
            if self.gmail:
                try:
                    labels_to_add = [f"Agent/{final_status}", f"Agent/Cat/{category}"]
                    star = (final_status == "Escalated" and self.store.settings.get("star_escalated", True))
                    self.gmail.apply_labels(message_id, labels_to_add, star=star)
                except Exception as label_err:
                    rec["label_error"] = str(label_err)

            # Mark email terminal ONLY after classification (and optional draft) are in the store
            self.store.upsert_email_memory(message_id, final_status, rec)
            self.store.log_audit("email_processed", {
                "message_id": message_id,
                "status": final_status,
                "category": category,
                "escalate": escalate,
            })
            # Batch-upsert immediately upon reaching terminal status
            self.store.flush_dirty_emails()
            return {"status": final_status, "record": rec}

        except Exception as e:
            logger.error(f"Error processing email {message_id}: {str(e)}", exc_info=True)
            rec["error"] = str(e)
            fail_status = "Failed"
            self.store.upsert_email_memory(message_id, fail_status, rec)
            self.store.log_audit("email_failed", {"message_id": message_id, "error": str(e), "attempts": attempts})
            self.store.flush_dirty_emails()
            return {"status": fail_status, "error": str(e), "record": rec}
