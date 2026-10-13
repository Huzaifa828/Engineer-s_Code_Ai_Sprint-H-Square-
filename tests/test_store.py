"""Tests for store.py: memory store, JSON mirror cache, Supabase mocking, delta merge skipping dirty IDs, and fallback."""
import tempfile
from unittest.mock import MagicMock
from store import JsonStore, StoreManager, SupabaseStore


def test_json_store_atomic_save_and_load():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = JsonStore(base_dir=tmpdir)
        email_hash = "abc123hash"
        data = {"hello": "world", "emails": {"m1": {"status": "Drafted"}}}

        store.save(email_hash, data)
        loaded = store.load(email_hash)
        assert loaded == data

        store.delete(email_hash)
        assert store.load(email_hash) is None


def test_store_manager_dirty_tracking_and_flush():
    with tempfile.TemporaryDirectory() as tmpdir:
        json_store = JsonStore(base_dir=tmpdir)

        # Mock Supabase
        mock_supabase_client = MagicMock()
        mock_table = MagicMock()
        mock_supabase_client.table.return_value = mock_table
        mock_table.upsert.return_value.execute.return_value = MagicMock(data=[])

        supa_store = SupabaseStore(client=mock_supabase_client)
        manager = StoreManager(email_hash="user_hash_1", supabase_store=supa_store, json_store=json_store)

        # Upsert in memory
        manager.upsert_email_memory("msg_1", "Drafted", {"subject": "Test Subj"})
        assert "msg_1" in manager.dirty_message_ids
        assert manager.emails["msg_1"]["status"] == "Drafted"

        # Flush
        success = manager.flush_dirty_emails()
        assert success is True
        assert len(manager.dirty_message_ids) == 0

        # Check local cache was also saved
        cached = json_store.load("user_hash_1")
        assert cached is not None
        assert "msg_1" in cached["emails"]


def test_delta_sync_skips_dirty_ids():
    with tempfile.TemporaryDirectory() as tmpdir:
        json_store = JsonStore(base_dir=tmpdir)
        mock_supabase = MagicMock(spec=SupabaseStore)
        mock_supabase.is_available.return_value = True

        manager = StoreManager(email_hash="user_hash_2", supabase_store=mock_supabase, json_store=json_store)
        manager.last_synced = "2026-10-12T10:00:00Z"

        # Dirty email in local memory
        manager.upsert_email_memory("msg_local", "Drafted", {"subject": "Local version"})
        assert "msg_local" in manager.dirty_message_ids

        # Remote has an update for msg_local AND another msg_remote
        mock_supabase.fetch_delta_emails.return_value = [
            {
                "message_id": "msg_local",
                "status": "Queued",
                "record": {"subject": "Remote obsolete version"},
                "updated_at": "2026-10-12T10:05:00Z",
            },
            {
                "message_id": "msg_remote",
                "status": "Escalated",
                "record": {"subject": "Remote email"},
                "updated_at": "2026-10-12T10:06:00Z",
            }
        ]

        manager.perform_delta_sync()

        # msg_local must NOT be overwritten because it is in dirty set
        assert manager.emails["msg_local"]["status"] == "Drafted"
        assert manager.emails["msg_local"]["record"]["subject"] == "Local version"

        # msg_remote must be merged
        assert manager.emails["msg_remote"]["status"] == "Escalated"
        assert manager.last_synced == "2026-10-12T10:06:00Z"


def test_fallback_to_cache_when_supabase_unavailable():
    with tempfile.TemporaryDirectory() as tmpdir:
        json_store = JsonStore(base_dir=tmpdir)
        email_hash = "cache_test_hash"
        # Prepopulate cache
        json_store.save(email_hash, {
            "settings": {"auto_poll": False},
            "audit": [{"event": "login"}],
            "emails": {"m1": {"status": "Queued", "record": {}}},
            "last_synced": "2026-10-12T00:00:00Z"
        })

        supa_store = SupabaseStore(client=None)  # Unavailable
        manager = StoreManager(email_hash=email_hash, supabase_store=supa_store, json_store=json_store)
        manager.initialize_session()

        assert manager.settings["auto_poll"] is False
        assert len(manager.audit) == 1
        assert "m1" in manager.emails
        assert "cache" in manager.sync_status_text.lower()
