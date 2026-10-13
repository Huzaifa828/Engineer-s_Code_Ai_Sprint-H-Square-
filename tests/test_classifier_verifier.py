"""Tests for classifier, verifier comparison, and golden evaluation."""
from classifier import ClassificationResult
from verifier import compare_classifications
from eval.run_eval import run_evaluation, load_golden_set


def test_classification_result_pydantic_schema():
    obj = ClassificationResult(
        category="Refund Request",
        confidence=0.88,
        urgency="high",
        sentiment="negative",
        language="en",
        multi_issue=False,
        escalate=False,
        escalation_reasons=[],
        evidence_quote="need my money back",
        short_reasoning="Customer requests refund for unused subscription",
    )
    assert obj.category == "Refund Request"
    assert obj.confidence == 0.88


def test_verifier_comparison_logic():
    c_res = {"category": "Billing Complaint", "escalate": False}
    v_res = {"category": "Billing Complaint", "escalate": False, "short_reasoning": "Agreed"}
    comp = compare_classifications(c_res, v_res)
    assert comp["agreed"] is True

    # Disagree on escalation
    v_res_esc = {"category": "Billing Complaint", "escalate": True}
    comp_esc = compare_classifications(c_res, v_res_esc)
    assert comp_esc["agreed"] is False
    assert comp_esc["escalate_agree"] is False


def test_golden_set_smoke_and_pre_rules_coverage():
    golden_cases = load_golden_set()
    assert len(golden_cases) >= 20

    # Test runner in deterministic pre-rule mode
    report = run_evaluation(golden_cases=golden_cases, run_llm=False)
    assert report["total_cases"] >= 20
    # Adversarial cases (prompt injection, empty, auto-reply, safety, legal) must be caught
    assert report["escalation_recall"] >= 0.8
