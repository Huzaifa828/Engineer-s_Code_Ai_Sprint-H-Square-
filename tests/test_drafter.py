"""Tests for drafter: holding replies, forbidden commitment checks, version history, and human edits."""
from unittest.mock import MagicMock

import pytest

from drafter import (
    generate_holding_draft,
    check_forbidden_commitments,
    generate_draft,
    record_human_edit,
)


def test_holding_draft_is_neutral_and_uncommitted():
    draft = generate_holding_draft(
        sender="angry@customer.com",
        subject="Lawsuit Warning",
        category="Billing Complaint",
        escalation_reasons=["legal threat"]
    )
    assert "Thank you for contacting Student Helpdesk" in draft
    assert "relevant office" in draft.lower()
    assert "legal threat" in draft
    assert "$ " not in draft
    assert "we will refund" not in draft.lower()
    assert "Apex" not in draft


def test_check_forbidden_commitments_detects_violations():
    allowed = ["We will investigate within 1 to 2 business days"]
    bad_draft = "Don't worry, we guarantee that we will refund $150 to your card immediately."
    violations = check_forbidden_commitments(bad_draft, allowed)

    assert len(violations) >= 2
    assert any("Forbidden commitment detected: '$150'" in v for v in violations)
    assert any("we will refund" in v for v in violations)


def test_check_forbidden_commitments_permits_allowed():
    allowed = ["We will refund eligible charges"]
    clean_draft = "Our team will review your account details."
    violations = check_forbidden_commitments(clean_draft, allowed)
    assert len(violations) == 0


def test_version_history_capped_at_five():
    existing_versions = [
        {"version": i, "text": f"Version {i}", "timestamp": "2026-10-12T00:00:00Z", "author": "human"}
        for i in range(1, 6)
    ]
    assert len(existing_versions) == 5

    record = {"versions": existing_versions}
    updated = record_human_edit(record, "Brand new 6th version")

    assert len(updated["versions"]) == 5
    assert updated["versions"][-1]["text"] == "Brand new 6th version"
    assert updated["versions"][-1]["version"] == 6
    assert updated["edited_by_human"] is True
    assert updated["requires_human_review"] is False


@pytest.mark.parametrize(
    ("sender", "subject", "body", "category", "policy_code", "policy_detail"),
    [
        (
            "Hamza Qureshi <hamza.q@example.com>",
            "Late fee waiver request",
            "I paid my semester fee on 9 October, three days after the due date, because the bank was closed for a public holiday on the due date. I have attached the receipt. Please waive the late fee.\nRoll No: 24-CE-118",
            "Fees",
            "FEE-2",
            "Accounts Office",
        ),
        (
            "Ayesha Siddiqui <ayesha.s@example.com>",
            "Can I pay my fee in parts?",
            "My father's business has been struggling and I cannot pay the full semester fee at once. Is it possible to pay in parts? The due date is next Friday.",
            "Fees",
            "FEE-3",
            "income proof",
        ),
        (
            "Bilal Raza <bilal.r@example.com>",
            "Refund request",
            "I want to withdraw from the semester because I got admission in another university. Classes started 3 weeks ago. I want my complete fee refunded.\nBilal Raza, 24-ME-042",
            "Fees",
            "FEE-4",
            "Registrar's Office",
        ),
        (
            "Saad Ahmed <saad.a@example.com>",
            "Geyser not working Room 214",
            "The geyser in my room (Block B, Room 214) has not worked for 5 days. I have reported it to the hostel office twice but nobody has come. Winter is coming and this is very inconvenient.\nSaad",
            "Hostel",
            "HOSTEL-2",
            "Hostel Warden",
        ),
    ],
)
def test_generate_draft_uses_llm_and_includes_case_policy(
    monkeypatch, sender, subject, body, category, policy_code, policy_detail
):
    client = MagicMock()
    client.invoke.return_value.content = f"Personalized reply for {policy_code}."
    limiter = MagicMock()
    limiter.acquire.return_value = True
    monkeypatch.setattr("drafter.llm_for", MagicMock(return_value=client))
    monkeypatch.setattr("drafter.get_limiter_for_model", MagicMock(return_value=limiter))

    result, tokens = generate_draft(
        sender=sender,
        subject=subject,
        body=body,
        category=category,
        status="Drafted",
    )

    prompt = client.invoke.call_args.args[0][1][1]
    assert result["generation_mode"] == "llm"
    assert result["policy_code"] == policy_code
    assert result["draft_text"] == f"Personalized reply for {policy_code}."
    assert body in prompt
    assert policy_code in prompt
    assert policy_detail in prompt
    assert tokens > 0
    limiter.record_usage.assert_called_once_with(tokens)


def test_generate_draft_marks_and_logs_policy_fallback(monkeypatch, caplog):
    client = MagicMock()
    client.invoke.side_effect = RuntimeError("gateway timeout")
    limiter = MagicMock()
    limiter.acquire.return_value = True
    monkeypatch.setattr("drafter.llm_for", MagicMock(return_value=client))
    monkeypatch.setattr("drafter.get_limiter_for_model", MagicMock(return_value=limiter))

    with caplog.at_level("WARNING", logger="drafter"):
        result, tokens = generate_draft(
            sender="Hamza Qureshi <hamza.q@example.com>",
            subject="Late fee waiver request",
            body="I paid three days late and attached my receipt. Please waive the late fee.",
            category="Fees",
            status="Drafted",
        )

    assert result["generation_mode"] == "policy_fallback"
    assert result["requires_human_review"] is True
    assert "gateway timeout" in result["fallback_reason"]
    assert "FEE-2" in result["draft_text"]
    assert "Accounts Office" in result["draft_text"]
    assert tokens == 0
    assert "Draft model failed" in caplog.text


def test_overdue_hostel_case_gets_policy_holding_reply():
    result, tokens = generate_draft(
        sender="Saad Ahmed <saad.a@example.com>",
        subject="Geyser not working Room 214",
        body="The geyser has not worked for 5 days. I reported it to the hostel office twice.",
        category="Hostel",
        status="Escalated",
        escalation_reasons=["overdue complaint"],
    )

    assert result["generation_mode"] == "holding_template"
    assert result["policy_applied"] == "HOSTEL-2"
    assert "Hostel Warden" in result["draft_text"]
    assert tokens == 0
