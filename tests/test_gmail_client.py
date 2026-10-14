"""Tests for gmail_client: body cleaning, HTML conversion, parsing, and draft creation."""
from unittest.mock import MagicMock
from gmail_client import strip_html_tags, clean_email_body, GmailClient


def test_strip_html_tags():
    html_content = "<p>Hello <b>World</b>!</p><br><div>How are you?</div>"
    result = strip_html_tags(html_content)
    assert "Hello World!" in result
    assert "How are you?" in result


def test_clean_email_body_strips_quotes_and_signatures():
    raw = """Hello support,

I need help with my invoice.

On Mon, Oct 12, 2026 at 10:00 AM Support wrote:
> Previous message here
> More quotes

--
John Doe
CEO, Example Corp
"""
    cleaned = clean_email_body(raw)
    assert "Hello support," in cleaned
    assert "I need help with my invoice." in cleaned
    assert "Previous message here" not in cleaned
    assert "John Doe" not in cleaned


def test_parse_message_multipart():
    mock_creds = MagicMock()
    client = GmailClient(credentials=mock_creds)

    import base64
    body_text = "Urgent: Delivery delayed for order 12345"
    encoded_body = base64.urlsafe_b64encode(body_text.encode("utf-8")).decode("ASCII")

    raw_message = {
        "id": "msg_001",
        "threadId": "thread_001",
        "internalDate": "1791799200000",
        "payload": {
            "headers": [
                {"name": "From", "value": "customer@example.com"},
                {"name": "Subject", "value": "Order late"},
                {"name": "Message-ID", "value": "<abc@example.com>"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": encoded_body},
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "receipt.pdf",
                    "body": {},
                }
            ]
        }
    }

    parsed = client.parse_message(raw_message)
    assert parsed["message_id"] == "msg_001"
    assert parsed["received_at"] == "2026-10-12T10:00:00+00:00"
    assert parsed["sender"] == "customer@example.com"
    assert parsed["subject"] == "Order late"
    assert "Urgent: Delivery delayed" in parsed["body"]
    assert "receipt.pdf" in parsed["attachment_names"]
    assert parsed["is_auto_reply"] is False
