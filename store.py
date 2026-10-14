"""Storage Layer: In-memory state, Supabase persistence, local JSON cache mirror, and delta sync.

Constraints:
- Load ONCE per session on login into memory (st.session_state).
- UI and counts read ONLY from memory.
- Batch-upsert changed rows right after an email reaches terminal status.
- Upsert account_state at the end of each tick and after user actions.
- Dirty set of message_ids with unsaved changes; clear only after successful upsert.
- Local JSON mirror cache (data/<email_hash>.json, atomic write).
- Delta sync at start of each tick (updated_at > last_synced, skipping dirty IDs).
- Delete my data: removes account_state (cascade delete), clears local cache & session.
"""
import copy
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Set, Tuple

from config.settings import get_secret


def now_iso() -> str:
    """Current UTC ISO timestamp."""
    return datetime.now(timezone.utc).isoformat()


class JsonStore:
    """Local atomic JSON mirror cache at data/<email_hash>.json."""

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = Path(base_dir) if base_dir else Path(__file__).parent / "data"
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _file_path(self, email_hash: str) -> Path:
        return self.base_dir / f"{email_hash}.json"

    def load(self, email_hash: str) -> Optional[Dict[str, Any]]:
        target = self._file_path(email_hash)
        if not target.exists():
            return None
        try:
            with open(target, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def save(self, email_hash: str, data: Dict[str, Any]):
        """Atomic write using tempfile and rename."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
        target = self._file_path(email_hash)
        fd, tmp_path = tempfile.mkstemp(dir=str(self.base_dir), prefix="store_tmp_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            # Atomic rename (on Windows, os.replace handles atomic overwrite)
            os.replace(tmp_path, target)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    def delete(self, email_hash: str):
        target = self._file_path(email_hash)
        if target.exists():
            target.unlink()


def get_supabase_client():
    """Create Supabase client using SUPABASE_SERVICE_KEY server-side."""
    url = get_secret("SUPABASE_URL")
    key = get_secret("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return None

    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception:
        return None


class SupabaseStore:
    """Supabase client wrapper for account_state and account_emails tables."""

    def __init__(self, client=None):
        self.client = client or get_supabase_client()

    def is_available(self) -> bool:
        return self.client is not None

    def load_account_state(self, email_hash: str) -> Optional[Dict[str, Any]]:
        if not self.is_available():
            return None
        try:
            res = (
                self.client.table("account_state")
                .select("*")
                .eq("email_hash", email_hash)
                .limit(1)
                .execute()
            )
            rows = res.data or []
            return rows[0] if rows else None
        except Exception:
            return None

    def load_all_account_emails(self, email_hash: str, page_size: int = 1000) -> List[Dict[str, Any]]:
        """Page with .range() to read all rows up to arbitrary limit."""
        if not self.is_available():
            return []
        all_rows = []
        offset = 0

        while True:
            try:
                res = (
                    self.client.table("account_emails")
                    .select("*")
                    .eq("email_hash", email_hash)
                    .range(offset, offset + page_size - 1)
                    .execute()
                )
                rows = res.data or []
                all_rows.extend(rows)
                if len(rows) < page_size:
                    break
                offset += page_size
            except Exception:
                break

        return all_rows

    def fetch_delta_emails(self, email_hash: str, last_synced_iso: str) -> List[Dict[str, Any]]:
        """Fetch emails updated after last_synced."""
        if not self.is_available() or not last_synced_iso:
            return []
        try:
            res = (
                self.client.table("account_emails")
                .select("*")
                .eq("email_hash", email_hash)
                .gt("updated_at", last_synced_iso)
                .order("updated_at", desc=False)
                .execute()
            )
            return res.data or []
        except Exception:
            return []

    def upsert_account_state(self, email_hash: str, settings: Dict[str, Any], audit: List[Dict[str, Any]]) -> bool:
        if not self.is_available():
            return False
        # Cap audit to last 200 entries
        capped_audit = audit[-200:]
        payload = {
            "email_hash": email_hash,
            "settings": settings,
            "audit": capped_audit,
            "updated_at": now_iso(),
        }
        try:
            self.client.table("account_state").upsert(payload, on_conflict="email_hash").execute()
            return True
        except Exception:
            return False

    def batch_upsert_emails(self, email_hash: str, email_records: List[Dict[str, Any]]) -> bool:
        """Batch upsert changed rows (on_conflict='email_hash,message_id')."""
        if not self.is_available() or not email_records:
            return True
        rows = []
        for rec in email_records:
            rows.append({
                "email_hash": email_hash,
                "message_id": rec["message_id"],
                "status": rec.get("status", "Queued"),
                "record": rec.get("record", {}),
                "updated_at": rec.get("updated_at", now_iso()),
            })
        try:
            self.client.table("account_emails").upsert(rows, on_conflict="email_hash,message_id").execute()
            return True
        except Exception:
            return False

    def delete_account_data(self, email_hash: str) -> bool:
        """Delete account_state row; foreign key cascade removes emails."""
        if not self.is_available():
            return False
        try:
            self.client.table("account_state").delete().eq("email_hash", email_hash).execute()
            return True
        except Exception:
            return False


class StoreManager:
    """Manages memory store, local json mirror, dirty tracking, and Supabase sync."""

    def __init__(self, email_hash: str, supabase_store: Optional[SupabaseStore] = None, json_store: Optional[JsonStore] = None):
        self.email_hash = email_hash
        self.supabase = supabase_store or SupabaseStore()
        self.local_cache = json_store or JsonStore()

        # In-memory working state
        self.settings: Dict[str, Any] = {
            "auto_poll": True,
            "auto_draft": True,
            "confidence_threshold": 0.6,
            "star_escalated": True,
        }
        self.audit: List[Dict[str, Any]] = []
        self.emails: Dict[str, Dict[str, Any]] = {}  # keyed by message_id
        self.dirty_message_ids: Set[str] = set()
        self.dirty_state: bool = False
        self.last_synced: Optional[str] = None
        self.sync_status_text: str = "Initialized"

    def log_audit(self, event_type: str, details: Dict[str, Any]):
        """Append an audit entry (capped at 200)."""
        entry = {
            "timestamp": now_iso(),
            "event": event_type,
            "details": details,
        }
        self.audit.append(entry)
        if len(self.audit) > 200:
            self.audit = self.audit[-200:]
        self.dirty_state = True

    def initialize_session(self):
        """Initial load ONCE per session: try Supabase first, fallback to local cache."""
        loaded_from_supabase = False

        if self.supabase.is_available():
            state_row = self.supabase.load_account_state(self.email_hash)
            if state_row:
                self.settings = state_row.get("settings", self.settings)
                self.audit = state_row.get("audit", [])
                email_rows = self.supabase.load_all_account_emails(self.email_hash)
                max_updated = state_row.get("updated_at")
                for r in email_rows:
                    m_id = r["message_id"]
                    self.emails[m_id] = {
                        "message_id": m_id,
                        "status": r.get("status", "Queued"),
                        "record": r.get("record", {}),
                        "updated_at": r.get("updated_at", now_iso()),
                    }
                    r_updated = r.get("updated_at")
                    if r_updated and (not max_updated or r_updated > max_updated):
                        max_updated = r_updated

                self.last_synced = max_updated
                loaded_from_supabase = True
                self.sync_status_text = f"Synced {time.strftime('%H:%M:%S')}"
                # Mirror to local cache
                self._save_to_local_cache()

        if not loaded_from_supabase:
            cached_data = self.local_cache.load(self.email_hash)
            if cached_data:
                self.settings = cached_data.get("settings", self.settings)
                self.audit = cached_data.get("audit", [])
                self.emails = cached_data.get("emails", {})
                self.last_synced = cached_data.get("last_synced")
                self.sync_status_text = "Loaded from local cache"
            else:
                self.sync_status_text = "New session"

    def _save_to_local_cache(self):
        """Mirror current state to local JSON file atomically."""
        data = {
            "email_hash": self.email_hash,
            "settings": self.settings,
            "audit": self.audit,
            "emails": self.emails,
            "last_synced": self.last_synced,
            "updated_at": now_iso(),
        }
        self.local_cache.save(self.email_hash, data)

    def upsert_email_memory(self, message_id: str, status: str, record: Dict[str, Any]):
        """Update memory first, mark dirty."""
        current_time = now_iso()
        existing = self.emails.get(message_id, {})
        existing_record = existing.get("record", {})
        merged_record = {**existing_record, **record}

        self.emails[message_id] = {
            "message_id": message_id,
            "status": status,
            "record": merged_record,
            "updated_at": current_time,
        }
        self.dirty_message_ids.add(message_id)

    def flush_dirty_emails(self) -> bool:
        """Batch-upsert dirty emails to Supabase and mirror to local cache."""
        if not self.dirty_message_ids:
            return True

        records_to_flush = [self.emails[m_id] for m_id in list(self.dirty_message_ids) if m_id in self.emails]
        success = True

        if self.supabase.is_available():
            success = self.supabase.batch_upsert_emails(self.email_hash, records_to_flush)
            if success:
                self.dirty_message_ids.clear()
                self.sync_status_text = f"Synced {time.strftime('%H:%M:%S')}"
            else:
                pending_count = len(self.dirty_message_ids)
                self.sync_status_text = f"Not synced ({pending_count} pending)"
        else:
            pending_count = len(self.dirty_message_ids)
            self.sync_status_text = f"Offline cache ({pending_count} pending)"

        # Always mirror to local cache
        self._save_to_local_cache()
        return success

    def flush_state(self) -> bool:
        """Upsert account_state to Supabase and mirror to local cache."""
        success = True
        if self.supabase.is_available():
            success = self.supabase.upsert_account_state(self.email_hash, self.settings, self.audit)
            if success:
                self.dirty_state = False
        self._save_to_local_cache()
        return success

    def perform_delta_sync(self):
        """Delta sync at the start of each tick: select updated_at > last_synced and merge into memory."""
        if not self.supabase.is_available() or not self.last_synced:
            return

        delta_rows = self.supabase.fetch_delta_emails(self.email_hash, self.last_synced)
        if not delta_rows:
            return

        max_updated = self.last_synced
        for row in delta_rows:
            m_id = row["message_id"]
            row_updated = row.get("updated_at", "")

            # SKIPPING any message_id in the dirty set
            if m_id in self.dirty_message_ids:
                continue

            existing = self.emails.get(m_id)
            if not existing or row_updated > existing.get("updated_at", ""):
                self.emails[m_id] = {
                    "message_id": m_id,
                    "status": row.get("status", "Queued"),
                    "record": row.get("record", {}),
                    "updated_at": row_updated,
                }

            if row_updated and row_updated > max_updated:
                max_updated = row_updated

        # Set last_synced to the max updated_at returned (never local clock)
        self.last_synced = max_updated
        self._save_to_local_cache()

    def delete_all_data(self):
        """Delete account_state row (cascade removes emails), wipe local cache and memory."""
        if self.supabase.is_available():
            self.supabase.delete_account_data(self.email_hash)
        self.local_cache.delete(self.email_hash)
        self.emails.clear()
        self.audit.clear()
        self.dirty_message_ids.clear()
        self.dirty_state = False
        self.last_synced = None
        self.sync_status_text = "Data deleted"
