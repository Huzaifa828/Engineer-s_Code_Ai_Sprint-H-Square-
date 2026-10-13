"""Tests for drafter: holding replies, forbidden commitment checks, version history, and human edits."""
from drafter import (
    generate_holding_draft,
    check_forbidden_commitments,
    generate_draft,
    record_human_edit,
)
from config.domain import CATEGORY_POLICIES


def test_holding_draft_is_neutral_and_uncommitted():
    draft = generate_holding_draft(
        sender="angry@customer.com",
        subject="Lawsuit Warning",
        category="Billing Complaint",
        escalation_reasons=["legal threat"]
    )
    assert "Thank you for contacting Apex Horizon Solutions" in draft
    assert "senior specialist" in draft
    assert "legal threat" in draft
    assert "$ " not in draft
    assert "we will refund" not in draft.lower()


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
