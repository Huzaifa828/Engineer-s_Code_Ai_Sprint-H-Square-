"""Gmail API client handling listing, fetching, parsing multipart, labeling, and draft management.

HARD CONSTRAINT:
NEVER send email. Drafts only. No code path may call message sending APIs.
"""
import base64
import html
import re
import time
from email.mime.text import MIMEText
from html.parser import HTMLParser
from typing import Dict, List, Optional, Any, Tuple
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials

from config.settings import MAX_SCAN, MAX_BODY_CHARS


class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.reset()
        self.strict = False
        self.convert_charrefs = True
        self.text_parts = []

    def handle_data(self, d):
        self.text_parts.append(d)

    def get_data(self):
        return "".join(self.text_parts)


def strip_html_tags(html_text: str) -> str:
    """Convert HTML content to plain text using standard library HTMLParser."""
    if not html_text:
        return ""
    stripper = _HTMLStripper()
    stripper.feed(html_text)
    return stripper.get_data()


def clean_email_body(text: str) -> str:
    """Strip quoted reply history and common signature blocks from email body."""
    if not text:
        return ""

    lines = text.splitlines()
    cleaned_lines = []

    for line in lines:
        stripped = line.strip()
        # Common quote markers
        if stripped.startswith(">"):
            continue
        if re.match(r"^-+\s*(Original Message|Quoted message)\s*-+", stripped, re.IGNORECASE):
            break
        if re.match(r"^On .+ wrote:$", stripped):
            break
        if stripped == "--" or stripped == "-- ":
            break
        cleaned_lines.append(line)

    body = "\n".join(cleaned_lines).strip()
    return body[:MAX_BODY_CHARS]


class GmailClient:
    def __init__(self, credentials: Credentials):
        self.credentials = credentials
        self.service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        self._labels_cache: Optional[Dict[str, str]] = None

    def execute_with_backoff(self, request, max_retries: int = 3):
        """Execute Google API request with exponential backoff on transient errors."""
        for attempt in range(max_retries):
            try:
                return request.execute()
            except HttpError as error:
                status = error.resp.status
                if status in (429, 500, 503) and attempt < max_retries - 1:
                    sleep_time = (2 ** attempt) + 0.5
                    time.sleep(sleep_time)
                else:
                    raise

    def list_inbox_message_ids(self, max_scan: int = MAX_SCAN) -> List[str]:
        """List INBOX message IDs newest first, paging until max_scan or exhaustion."""
        message_ids = []
        page_token = None

        while len(message_ids) < max_scan:
            batch_size = min(100, max_scan - len(message_ids))
            req = self.service.users().messages().list(
                userId="me",
                q="in:inbox",
                maxResults=batch_size,
                pageToken=page_token,
            )
            resp = self.execute_with_backoff(req)
            messages = resp.get("messages", [])
            for m in messages:
                message_ids.append(m["id"])
                if len(message_ids) >= max_scan:
                    break

            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        return message_ids

    def fetch_message_raw(self, message_id: str) -> Dict[str, Any]:
        """Fetch full message details by ID."""
        req = self.service.users().messages().get(
            userId="me",
            id=message_id,
            format="full"
        )
        return self.execute_with_backoff(req)

    def parse_message(self, raw_message: Dict[str, Any]) -> Dict[str, Any]:
        """Parse raw Gmail API response into structured dict."""
        message_id = raw_message.get("id", "")
        thread_id = raw_message.get("threadId", "")
        payload = raw_message.get("payload", {})
        headers_list = payload.get("headers", [])

        headers = {}
        for h in headers_list:
            headers[h.get("name", "").lower()] = h.get("value", "")

        sender = headers.get("from", "")
        reply_to = headers.get("reply-to", sender)
        subject = headers.get("subject", "")
        msg_id_header = headers.get("message-id", "")
        references = headers.get("references", "")
        in_reply_to = headers.get("in-reply-to", msg_id_header)

        # Detect auto-reply / bounce headers
        auto_submitted = headers.get("auto-submitted", "").lower()
        precedence = headers.get("precedence", "").lower()
        is_auto_reply = (
            auto_submitted.startswith("auto-") or
            precedence in ("bulk", "junk", "auto_reply") or
            "mailer-daemon" in sender.lower() or
            "no-reply" in sender.lower() or
            "noreply" in sender.lower()
        )

        plain_body, html_body, attachment_names = self._extract_parts(payload)

        raw_body = plain_body if plain_body else strip_html_tags(html_body)
        cleaned_body = clean_email_body(raw_body)

        return {
            "message_id": message_id,
            "thread_id": thread_id,
            "sender": sender,
            "reply_to": reply_to,
            "subject": subject,
            "message_id_header": msg_id_header,
            "references": references,
            "in_reply_to": in_reply_to,
            "is_auto_reply": is_auto_reply,
            "body": cleaned_body,
            "attachment_names": attachment_names,
            "headers": headers,
        }

    def _extract_parts(self, payload: Dict[str, Any]) -> Tuple[str, str, List[str]]:
        """Recursively extract plain text, HTML, and attachment filenames from MIME parts."""
        plain_text = ""
        html_text = ""
        attachment_names = []

        def _traverse(part):
            nonlocal plain_text, html_text
            filename = part.get("filename", "")
            if filename:
                attachment_names.append(filename)

            mime_type = part.get("mimeType", "")
            body_data = part.get("body", {}).get("data", "")

            if body_data:
                try:
                    decoded_bytes = base64.urlsafe_b64decode(body_data.encode("ASCII"))
                    decoded_str = decoded_bytes.decode("utf-8", errors="replace")
                except Exception:
                    decoded_str = ""

                if mime_type == "text/plain" and not plain_text:
                    plain_text = decoded_str
                elif mime_type == "text/html" and not html_text:
                    html_text = decoded_str

            for subpart in part.get("parts", []):
                _traverse(subpart)

        _traverse(payload)
        return plain_text, html_text, attachment_names

    def get_or_create_label_id(self, label_name: str) -> str:
        """Find or create a user label in Gmail."""
        if self._labels_cache is None:
            self._labels_cache = {}
            req = self.service.users().labels().list(userId="me")
            res = self.execute_with_backoff(req)
            for lab in res.get("labels", []):
                self._labels_cache[lab["name"]] = lab["id"]

        if label_name in self._labels_cache:
            return self._labels_cache[label_name]

        label_body = {
            "name": label_name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        }
        create_req = self.service.users().labels().create(userId="me", body=label_body)
        created = self.execute_with_backoff(create_req)
        label_id = created["id"]
        self._labels_cache[label_name] = label_id
        return label_id

    def apply_labels(self, message_id: str, label_names: List[str], star: bool = False):
        """Apply specified Agent labels and optional STARRED label without altering read/archive state."""
        label_ids_to_add = [self.get_or_create_label_id(name) for name in label_names]
        if star:
            label_ids_to_add.append("STARRED")

        if not label_ids_to_add:
            return

        body = {"addLabelIds": label_ids_to_add, "removeLabelIds": []}
        req = self.service.users().messages().modify(userId="me", id=message_id, body=body)
        self.execute_with_backoff(req)

    def find_draft_for_thread(self, thread_id: str) -> Optional[str]:
        """Check thread for an existing DRAFT message ID to prevent duplicates."""
        if not thread_id:
            return None
        try:
            req = self.service.users().drafts().list(userId="me", q=f"thread:{thread_id}")
            res = self.execute_with_backoff(req)
            drafts = res.get("drafts", [])
            if drafts:
                return drafts[0]["id"]
        except Exception:
            pass
        return None

    def create_or_update_draft(
        self,
        thread_id: str,
        to_email: str,
        subject: str,
        body_text: str,
        in_reply_to: Optional[str] = None,
        references: Optional[str] = None,
        existing_draft_id: Optional[str] = None,
    ) -> str:
        """Create or update draft in the same thread. NEVER SENDS. Returns draft ID."""
        msg = MIMEText(body_text)
        msg["To"] = to_email
        msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = references

        raw_str = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        draft_body = {
            "message": {
                "raw": raw_str,
                "threadId": thread_id,
            }
        }

        # Check existing draft ID
        target_draft_id = existing_draft_id or self.find_draft_for_thread(thread_id)

        if target_draft_id:
            try:
                update_req = self.service.users().drafts().update(
                    userId="me",
                    id=target_draft_id,
                    body=draft_body
                )
                res = self.execute_with_backoff(update_req)
                return res.get("id", target_draft_id)
            except HttpError as e:
                if e.resp.status != 404:
                    raise

        # Create new draft if none exists or previous was deleted
        create_req = self.service.users().drafts().create(
            userId="me",
            body=draft_body
        )
        res = self.execute_with_backoff(create_req)
        return res["id"]
