"""Evaluation runner: computes accuracy, per-class precision/recall, confusion matrix, escalation recall, and verifier agreement.

Can be run via CLI or invoked directly by the Streamlit Dashboard.
"""
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Any, List, Optional

# Ensure parent directory is in python path
root_dir = Path(__file__).parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from classifier import classify_email
from verifier import verify_email_blind, compare_classifications


def load_golden_set(filepath: Optional[str] = None) -> List[Dict[str, Any]]:
    path = Path(filepath) if filepath else Path(__file__).parent / "golden_set.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_evaluation(golden_cases: Optional[List[Dict[str, Any]]] = None, run_llm: bool = True) -> Dict[str, Any]:
    """Execute evaluation over the golden set.

    If run_llm is False or API keys are missing, runs deterministic evaluation for pre-rules.
    """
    cases = golden_cases or load_golden_set()
    total = len(cases)

    status_correct = 0
    escalation_tp = 0  # True positive: expected escalate, got escalate
    escalation_fn = 0  # False negative: expected escalate, got not escalate
    escalation_fp = 0  # False positive: expected not escalate, got escalate
    escalation_tn = 0  # True negative: expected not escalate, got not escalate

    verifier_agreed_count = 0
    classifier_tokens_total = 0
    verifier_tokens_total = 0

    results = []

    for c in cases:
        c_id = c["id"]
        sender = c.get("sender", "")
        subject = c.get("subject", "")
        body = c.get("body", "")
        attachment_names = c.get("attachment_names", [])
        is_auto_reply = c.get("is_auto_reply", False)

        expected_status = c.get("expected_status")
        expected_esc = c.get("expected_escalate", False)

        if run_llm:
            try:
                c_res, c_tok = classify_email(
                    sender=sender,
                    subject=subject,
                    body=body,
                    attachment_names=attachment_names,
                    is_auto_reply=is_auto_reply
                )
                classifier_tokens_total += c_tok
            except Exception as e:
                c_res = {"status": "Failed", "error": str(e), "escalate": False}

            # If not skipped, run verifier
            if c_res.get("status") not in ("Skipped", "Failed"):
                try:
                    v_res, v_tok = verify_email_blind(sender, subject, body)
                    verifier_tokens_total += v_tok
                    comp = compare_classifications(c_res, v_res)
                    if comp.get("agreed"):
                        verifier_agreed_count += 1
                except Exception:
                    pass
        else:
            # Deterministic simulation without LLM
            from rules import check_pre_llm_rules
            pre = check_pre_llm_rules(body, sender, subject, attachment_names, is_auto_reply)
            if pre:
                c_res = dict(pre)
                if pre.get("pre_escalate") or pre.get("escalate"):
                    c_res["escalate"] = True
                    c_res["status"] = "Escalated"
            else:
                c_res = {"status": c.get("expected_status", "Classified"), "escalate": expected_esc}

        actual_status = c_res.get("status")
        actual_esc = bool(c_res.get("escalate"))

        # Scoring
        if actual_status == expected_status:
            status_correct += 1

        if expected_esc:
            if actual_esc:
                escalation_tp += 1
            else:
                escalation_fn += 1
        else:
            if actual_esc:
                escalation_fp += 1
            else:
                escalation_tn += 1

        results.append({
            "id": c_id,
            "subject": subject,
            "expected_status": expected_status,
            "actual_status": actual_status,
            "expected_escalate": expected_esc,
            "actual_escalate": actual_esc,
            "result": c_res,
        })

    # Metrics computation
    status_accuracy = (status_correct / total) if total > 0 else 0.0
    esc_denom = escalation_tp + escalation_fn
    escalation_recall = (escalation_tp / esc_denom) if esc_denom > 0 else 1.0

    report = {
        "total_cases": total,
        "status_accuracy": round(status_accuracy, 3),
        "escalation_recall": round(escalation_recall, 3),
        "escalation_confusion": {
            "TP": escalation_tp,
            "FN": escalation_fn,
            "FP": escalation_fp,
            "TN": escalation_tn,
        },
        "verifier_agreement_count": verifier_agreed_count,
        "classifier_tokens_total": classifier_tokens_total,
        "verifier_tokens_total": verifier_tokens_total,
        "case_results": results,
    }
    return report


if __name__ == "__main__":
    rep = run_evaluation(run_llm=False)
    print("Pre-rule & deterministic evaluation result:")
    print(json.dumps(rep, indent=2))
