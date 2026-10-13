"""Tests for pipeline: sweep with fake Gmail client, backlog > MAX_PER_TICK, retry behavior, no duplicate drafts."""
import tempfile
from unittest.mock import MagicMock, patch
from pipeline import PipelineRunner
from store import JsonStore, StoreManager, SupabaseStore


class FakeGmailClient:
    def __init__(self, message_count=8):
        self.message_ids = [f"fake_msg_{i:03d}" for i in range(message_count)]
        self.drafts_created = {}
        self.labels_applied = {}

    def list_inbox_message_ids(self, max_scan=300):
        return list(self.message_ids)

    def fetch_message_raw(self, message_id):
        return {"id": message_id}

    def parse_message(self, raw_message):
        m_id = raw_message["id"]
        return {
            "message_id": m_id,
            "thread_id": f"thread_{m_id}",
            "sender": f"customer_{m_id}@example.com",
            "reply_to": f"customer_{m_id}@example.com",
            "subject": f"Inquiry about service for {m_id}",
            "body": "Can you provide more information about your enterprise licensing tiers?",
            "is_auto_reply": False,
            "attachment_names": [],
        }

    def find_draft_for_thread(self, thread_id):
        return self.drafts_created.get(thread_id)

    def create_or_update_draft(self, thread_id, to_email, subject, body_text, in_reply_to=None, references=None, existing_draft_id=None):
        draft_id = existing_draft_id or f"draft_{thread_id}"
        self.drafts_created[thread_id] = draft_id
        return draft_id

    def apply_labels(self, message_id, label_names, star=False):
        self.labels_applied[message_id] = {"labels": label_names, "star": star}


@patch("pipeline.generate_draft", return_value=({"draft_text": "Sample draft", "requires_human_review": False, "versions": []}, 100))
@patch("pipeline.verify_email_blind", return_value=({"category": "Inquiry", "escalate": False, "short_reasoning": "Agreed"}, 50))
@patch("pipeline.classify_email", return_value=({"status": "Classified", "category": "Inquiry", "confidence": 0.95, "escalate": False, "evidence_quote": "enterprise licensing"}, 100))
def test_sweep_processes_backlog_exceeding_max_per_tick(mock_cls, mock_ver, mock_drf):
    with tempfile.TemporaryDirectory() as tmpdir:
        json_store = JsonStore(base_dir=tmpdir)
        store_manager = StoreManager(email_hash="fake_hash", supabase_store=SupabaseStore(None), json_store=json_store)
        fake_gmail = FakeGmailClient(message_count=8)

        runner = PipelineRunner(store_manager=store_manager, gmail_client=fake_gmail)

        # First tick with max_per_tick=5 (backlog is 8)
        summary1 = runner.run_tick(max_per_tick=5)
        assert summary1["processed"] + summary1["skipped"] == 5
        assert len(store_manager.emails) == 8  # All 8 were discovered and stubbed

        # Second tick picks up remaining 3
        summary2 = runner.run_tick(max_per_tick=5)
        assert summary2["processed"] + summary2["skipped"] == 3

        # Verify all 8 reached terminal status
        for m_id, d in store_manager.emails.items():
            assert d["status"] in ("Drafted", "Escalated", "NeedsReview", "Skipped")
            assert d["record"].get("draft") is not None
            assert fake_gmail.drafts_created[f"thread_{m_id}"] == f"draft_thread_{m_id}"


@patch("pipeline.generate_draft", return_value=({"draft_text": "Sample draft", "requires_human_review": False, "versions": []}, 100))
@patch("pipeline.verify_email_blind", return_value=({"category": "Inquiry", "escalate": False, "short_reasoning": "Agreed"}, 50))
@patch("pipeline.classify_email", return_value=({"status": "Classified", "category": "Inquiry", "confidence": 0.95, "escalate": False, "evidence_quote": "enterprise licensing"}, 100))
def test_failed_emails_are_retried(mock_cls, mock_ver, mock_drf):
    with tempfile.TemporaryDirectory() as tmpdir:
        json_store = JsonStore(base_dir=tmpdir)
        store_manager = StoreManager(email_hash="retry_hash", supabase_store=SupabaseStore(None), json_store=json_store)
        fake_gmail = FakeGmailClient(message_count=1)

        runner = PipelineRunner(store_manager=store_manager, gmail_client=fake_gmail)

        # Force failure on first attempt
        def blow_up(raw):
            raise RuntimeError("Transient network timeout")
        fake_gmail.parse_message = blow_up

        res = runner.process_single_email("fake_msg_000")
        assert res["status"] == "Failed"
        assert store_manager.emails["fake_msg_000"]["record"]["attempts"] == 1

        # Restore working method and retry
        fake_gmail.parse_message = FakeGmailClient().parse_message
        res2 = runner.process_single_email("fake_msg_000")
        assert res2["status"] in ("Drafted", "Escalated", "NeedsReview")
        assert store_manager.emails["fake_msg_000"]["record"]["attempts"] == 2
