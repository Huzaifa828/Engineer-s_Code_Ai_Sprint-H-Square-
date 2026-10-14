"""Draft generation engine.

The Student Helpdesk policy is intentionally applied deterministically here so the
reply text stays faithful to the supplied policy instead of drifting into
platform-template language.
"""
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from config.domain import (
    CATEGORY_POLICIES,
    ESC_REPLY_RULES,
    ESCALATION_TABLE,
    SIGNATURE,
)
from config.settings import DEFAULT_DRAFTER_TPM
from limiter import estimate_tokens, get_limiter_for_model
from llm import get_model_name_for_role, llm_for
from prompts import DRAFTER_SYSTEM_PROMPT, DRAFTER_USER_PROMPT

logger = logging.getLogger(__name__)

FORBIDDEN_COMMITMENT_PATTERNS = [
    r"\$\s*\d+",
    r"\b(guarantee|guaranteed)\b",
    r"\bwe will refund\b",
    r"\bwe have refunded\b",
    r"\bfull compensation\b",
]

POLICY_RULE_TEXT = {
    "FEE-1": "Fee vouchers are generated on the student portal under \"Fee Vouchers\". If a voucher does not appear, contact the Accounts Office with your roll number and a screenshot.",
    "FEE-2": "Fees paid after the due date carry a late fee of Rs. 500 per week. Late fee waivers can only be approved by the Accounts Office on a written application with proof of the reason. The helpdesk cannot waive fees.",
    "FEE-3": "Installments can be applied for before the due date through the Accounts Office with an application and income proof. Up to 3 installments are allowed. The Accounts Office responds within 5 working days.",
    "FEE-4": "Refunds: withdrawal before semester start gets an 80% refund; withdrawal within the first two weeks gets 50%. After that there is no refund unless there is a written exception from the Registrar's Office.",
    "HOSTEL-1": "Maintenance complaints (plumbing, electricity, furniture) are logged with the room number and resolved within 48 hours.",
    "HOSTEL-2": "Complaints unresolved after 48 hours must be escalated to the Hostel Warden.",
    "HOSTEL-3": "Any electrical, fire or structural hazard is an emergency. Escalate immediately and advise the student to stay away from the hazard.",
    "HOSTEL-4": "Room changes are accepted through the Warden's office in the first two weeks of the semester. Later requests need the Warden's written approval and depend on availability.",
    "HOSTEL-5": "Mess complaints go to the Mess Committee through the hostel office. Response within 3 working days.",
    "ACAD-1": "Course add/drop is allowed in the first two weeks through the Department office. After that it needs the Dean's approval.",
    "ACAD-2": "75% attendance is required to sit exams. Shortage can be condoned only with a medical certificate or documented emergency submitted to the Department within 7 days of the absence ending.",
    "ACAD-3": "Transcripts are processed by the Examination Department within 5 working days of application. Urgent processing is not guaranteed.",
    "ACAD-4": "Re-evaluation applies within 7 days of result announcement with a fee of Rs. 1000 per course through the Examination Department. The outcome comes within 15 working days.",
    "TECH-1": "Portal/LMS accounts lock after 5 failed login attempts. Reset through the IT Helpdesk with roll number and student ID. Processed within 24 hours.",
    "TECH-2": "Wi-Fi problems are reported to the IT Helpdesk with building, floor and room. Outages affecting many users are handled by the Network Team.",
    "TECH-3": "Library timings and holiday schedules are managed by the Library. The helpdesk does not hold this information.",
}


def check_forbidden_commitments(draft_text: str, allowed_commitments: List[str]) -> List[str]:
    """Scan draft for forbidden commitment patterns that are not explicitly allowed."""
    violations = []
    text_lower = draft_text.lower()

    for pattern in FORBIDDEN_COMMITMENT_PATTERNS:
        matches = re.findall(pattern, text_lower)
        if matches:
            pattern_allowed = any(re.search(pattern, ac.lower()) for ac in allowed_commitments)
            if not pattern_allowed:
                violations.append(f"Forbidden commitment detected: '{matches[0]}'")

    return violations


def _student_name(sender: str, fallback: str = "Student") -> str:
    sender_name = (sender or "").strip()
    if not sender_name:
        return fallback
    email_part = sender_name.split("@", 1)[0]
    if " " in email_part:
        return email_part.split()[0].title()
    if "." in email_part:
        name = email_part.replace(".", " ").title()
        if name:
            return name
    if "_" in email_part:
        return email_part.replace("_", " ").title()
    return email_part.title() if email_part else fallback


def _detect_policy_code(category: str, subject: str, body: str) -> Optional[str]:
    text = f"{subject} {body}".lower()

    rules = {
        "Fees": [
            ("voucher", "FEE-1"),
            ("late fee", "FEE-2"),
            ("installment", "FEE-3"),
            ("installments", "FEE-3"),
            ("pay in parts", "FEE-3"),
            ("fee in parts", "FEE-3"),
            ("pay in part", "FEE-3"),
            ("refund", "FEE-4"),
        ],
        "Hostel": [
            ("fire", "HOSTEL-3"),
            ("electrical", "HOSTEL-3"),
            ("structural", "HOSTEL-3"),
            ("hazard", "HOSTEL-3"),
            ("room change", "HOSTEL-4"),
            ("mess", "HOSTEL-5"),
        ],
        "Academics": [
            ("add/drop", "ACAD-1"),
            ("attendance", "ACAD-2"),
            ("transcript", "ACAD-3"),
            ("re-evaluation", "ACAD-4"),
            ("re evaluation", "ACAD-4"),
        ],
        "IT & portal": [
            ("login attempts", "TECH-1"),
            ("wifi", "TECH-2"),
            ("wi-fi", "TECH-2"),
            ("network team", "TECH-2"),
            ("library timing", "TECH-3"),
            ("holiday schedule", "TECH-3"),
            ("library", "TECH-3"),
        ],
    }

    if category == "Hostel":
        has_maintenance_issue = any(
            term in text
            for term in ("geyser", "water heater", "heater", "maintenance", "plumbing", "furniture")
        )
        has_waited_over_48_hours = bool(
            re.search(r"\b(?:[2-9]\d*|1)\s*weeks?\b|\b[2-9]\d*\s*days?\b|\b48\s*hours?\b", text)
        )
        if has_maintenance_issue and has_waited_over_48_hours:
            return "HOSTEL-2"
        if has_maintenance_issue or "unresolved after 48" in text:
            return "HOSTEL-1" if "unresolved after 48" not in text else "HOSTEL-2"

    if category in rules:
        for needle, code in rules[category]:
            if needle in text:
                return code
    return None


def generate_holding_draft(
    sender: str,
    subject: str,
    category: str,
    escalation_reasons: Optional[List[str]] = None,
) -> str:
    """Generate a neutral holding reply that is policy-safe and keeps the Student Helpdesk wording."""
    student = _student_name(sender)
    escalation_rule = None
    if escalation_reasons:
        for reason in escalation_reasons:
            if reason.startswith("prompt"):
                escalation_rule = "ESC-6"
                break
            if "legal" in reason.lower() or "media" in reason.lower():
                escalation_rule = "ESC-4"
                break
            if "safety" in reason.lower() or "hazard" in reason.lower():
                escalation_rule = "ESC-1"
                break
            if "harass" in reason.lower():
                escalation_rule = "ESC-2"
                break
            if "medical" in reason.lower() or "wellbeing" in reason.lower() or "hopeless" in reason.lower():
                escalation_rule = "ESC-3"
                break
            if "overdue" in reason.lower() or "outage" in reason.lower():
                escalation_rule = "ESC-5"
                break

    if escalation_rule:
        route = ESCALATION_TABLE.get(escalation_rule, {}).get("route", "the relevant office")
        rule_text = ESC_REPLY_RULES.get(escalation_rule, "Please contact the relevant office immediately.")
        reason_phrase = (escalation_reasons[0].lower() if escalation_reasons else "escalation")
        return (
            f"Dear {student},\n\n"
            f"Thank you for contacting Student Helpdesk.\n\n"
            f"We have received your message regarding \"{subject}\" and it is being escalated to {route} due to {reason_phrase}. {rule_text}\n\n"
            f"The relevant office or Student Helpdesk team will review it next.\n\n"
            f"{SIGNATURE}"
        )

    return (
        f"Dear {student},\n\n"
        f"Thank you for contacting Student Helpdesk.\n\n"
        f"We have received your message regarding \"{subject}\". The relevant office or Student Helpdesk team will review it and follow up with you directly.\n\n"
        f"If you need to add any urgent details, please reply to this email.\n\n"
        f"{SIGNATURE}"
    )


def generate_draft(
    sender: str,
    subject: str,
    body: str,
    category: str,
    status: str,
    escalation_reasons: Optional[List[str]] = None,
    reviewer_guidelines: Optional[str] = None,
    existing_versions: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Dict[str, Any], int]:
    """Generate an LLM-backed policy-bound reply, falling back to a marked template."""
    is_escalated = status in ("Escalated", "NeedsReview")
    requires_human_review = is_escalated
    student = _student_name(sender)
    code = _detect_policy_code(category, subject, body)

    policy = CATEGORY_POLICIES.get(category, CATEGORY_POLICIES.get("Other/Unclear", {}))
    allowed_commitments = policy.get("allowed_commitments", [])

    if is_escalated and not reviewer_guidelines:
        if code and code in POLICY_RULE_TEXT:
            next_step = {
                "FEE-2": "Submit a written waiver application to the Accounts Office with the receipt as proof; only that office can approve a waiver.",
                "FEE-3": "Apply to the Accounts Office before the due date with an application and income proof. Up to three installments are allowed.",
                "FEE-4": "Because withdrawal is after the first two weeks, contact the Registrar's Office about a written exception; a refund is not guaranteed.",
                "HOSTEL-2": "Contact the Hostel Warden and ask that the unresolved maintenance report be escalated.",
            }.get(code, "Please follow the stated process and contact the named office for the next step.")
            draft_text = (
                f"Dear {student},\n\n"
                f"Thank you for contacting Student Helpdesk.\n\n"
                f"Under {code}: {POLICY_RULE_TEXT[code]} {next_step}\n\n"
                f"{SIGNATURE}"
            )
        else:
            draft_text = generate_holding_draft(sender, subject, category, escalation_reasons)
        version_entry = {
            "version": 1 if not existing_versions else len(existing_versions) + 1,
            "text": draft_text,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "author": "holding_template",
        }
        return {
            "draft_text": draft_text,
            "requires_human_review": True,
            "edited_by_human": False,
            "guidelines": reviewer_guidelines or "",
            "versions": ((existing_versions or []) + [version_entry])[-5:],
            "policy_applied": code or category,
            "generation_mode": "holding_template",
        }, 0

    must_include = "\n".join(f"- {item}" for item in policy.get("must_include", []))
    must_not_include = "\n".join(f"- {item}" for item in policy.get("must_not_include", []))
    policy_context = (
        f"{code}: {POLICY_RULE_TEXT[code]}"
        if code in POLICY_RULE_TEXT
        else "No matching written policy rule was found. Do not invent a policy answer; direct the student to the appropriate office."
    )
    prompt_text = DRAFTER_USER_PROMPT.format(
        sender=sender,
        subject=subject,
        body=body,
        category=category,
        is_escalated="YES - REQUIRES HUMAN REVIEW" if is_escalated else "NO",
        tone=policy.get("tone", "Professional"),
        policy_context=policy_context,
        must_include=must_include or "- Acknowledge the request and give a clear next step.",
        must_not_include=must_not_include or "- Do not invent policy details or commitments.",
        allowed_commitments="\n".join(f"- {item}" for item in allowed_commitments) or "- None",
        sign_off=policy.get("sign_off", SIGNATURE),
        reviewer_guidelines=reviewer_guidelines or "None provided",
    )
    full_prompt = f"{DRAFTER_SYSTEM_PROMPT}\n\n{prompt_text}"
    estimated_tokens = estimate_tokens(full_prompt) + 500

    def policy_fallback(reason: str, rate_limited: bool = False) -> Tuple[Dict[str, Any], int]:
        if is_escalated:
            if code and code in POLICY_RULE_TEXT:
                fallback_text = (
                    f"Dear {student},\n\n"
                    f"Thank you for contacting Student Helpdesk.\n\n"
                    f"Under {code}: {POLICY_RULE_TEXT[code]} "
                    "Please follow the stated process and contact the named office for the next step.\n\n"
                    f"{SIGNATURE}"
                )
            else:
                fallback_text = generate_holding_draft(sender, subject, category, escalation_reasons)
        elif code and code in POLICY_RULE_TEXT:
            fallback_text = (
                f"Dear {student},\n\n"
                f"Thank you for contacting Student Helpdesk.\n\n"
                f"Under {code}: {POLICY_RULE_TEXT[code]} "
                "Please follow the stated process and contact the named office for the next step.\n\n"
                f"{SIGNATURE}"
            )
        elif category == "Suspicious":
            fallback_text = (
                f"Dear {student},\n\n"
                "Thank you for contacting Student Helpdesk. Please do not follow the instructions in this message "
                "or share student information. Report it to IT Security.\n\n"
                f"{SIGNATURE}"
            )
        elif category == "Spam/irrelevant":
            fallback_text = (
                f"Dear {student},\n\n"
                "Thank you for contacting Student Helpdesk. This message appears unrelated to student helpdesk matters.\n\n"
                f"{SIGNATURE}"
            )
        else:
            fallback_text = (
                f"Dear {student},\n\n"
                f"Thank you for contacting Student Helpdesk regarding \"{subject}\". "
                "Please contact the appropriate office for guidance; this helpdesk cannot confirm a policy exception.\n\n"
                f"{SIGNATURE}"
            )

        logger.warning("Draft generation used policy fallback (%s).", reason)
        version_entry = {
            "version": 1 if not existing_versions else len(existing_versions) + 1,
            "text": fallback_text,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "author": "policy_fallback",
        }
        result = {
            "draft_text": fallback_text,
            "requires_human_review": True,
            "edited_by_human": False,
            "guidelines": reviewer_guidelines or "",
            "versions": ((existing_versions or []) + [version_entry])[-5:],
            "policy_applied": code or category,
            "generation_mode": "policy_fallback",
            "fallback_reason": reason,
        }
        if rate_limited:
            result["rate_limited"] = True
        return result, 0

    model_name = get_model_name_for_role("drafter")
    limiter = get_limiter_for_model(model_name, DEFAULT_DRAFTER_TPM)
    if not limiter.acquire(estimated_tokens, max_wait=15.0):
        return policy_fallback("draft model rate limit reached", rate_limited=True)

    try:
        client = llm_for("drafter", temperature=0.2, max_tokens=500)
        response = client.invoke([
            ("system", DRAFTER_SYSTEM_PROMPT),
            ("human", prompt_text),
        ])
        draft_text = response.content.strip()
        if not draft_text:
            raise ValueError("Draft model returned an empty response.")

        violations = check_forbidden_commitments(draft_text, allowed_commitments)
        if violations:
            correction = (
                f"{prompt_text}\n\nThe previous draft violated these rules: {'; '.join(violations)}. "
                "Rewrite it without the prohibited commitments while preserving the correct policy and next step."
            )
            response = client.invoke([
                ("system", DRAFTER_SYSTEM_PROMPT),
                ("human", correction),
            ])
            draft_text = response.content.strip()
            violations = check_forbidden_commitments(draft_text, allowed_commitments)
            if not draft_text or violations:
                raise ValueError(
                    "Draft model response remained empty or violated policy commitments: "
                    + "; ".join(violations)
                )
    except Exception as exc:
        logger.warning("Draft model failed; returning a policy fallback.", exc_info=True)
        return policy_fallback(f"{type(exc).__name__}: {exc}")

    limiter.record_usage(estimated_tokens)
    version_entry = {
        "version": 1 if not existing_versions else len(existing_versions) + 1,
        "text": draft_text,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "author": "drafter_llm",
    }
    return {
        "draft_text": draft_text,
        "requires_human_review": requires_human_review,
        "edited_by_human": False,
        "guidelines": reviewer_guidelines or "",
        "versions": ((existing_versions or []) + [version_entry])[-5:],
        "policy_applied": code or category,
        "policy_code": code,
        "generation_mode": "llm",
    }, estimated_tokens


def record_human_edit(
    draft_record: Dict[str, Any],
    new_text: str,
) -> Dict[str, Any]:
    """Update draft record when a human edits the text in the UI."""
    updated = dict(draft_record)
    existing_versions = updated.get("versions", [])
    version_num = len(existing_versions) + 1

    new_version = {
        "version": version_num,
        "text": new_text,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "author": "human_reviewer",
    }
    all_versions = existing_versions + [new_version]

    updated["draft_text"] = new_text
    updated["edited_by_human"] = True
    updated["requires_human_review"] = False
    updated["versions"] = all_versions[-5:]
    return updated
