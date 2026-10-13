"""Draft generation engine.

Hard constraints:
- Global rules: never invent order numbers, names, dates, links, amounts, or policies.
- No promises of refunds, compensation, or timelines unless policy allows.
- If info is missing, ask for it or insert [PLACEHOLDER].
- No legal or medical advice. Keep drafts short.
- Escalated/NeedsReview: neutral holding reply flagged REQUIRES HUMAN REVIEW.
- Reviewer guidelines go in a delimited section and can NEVER override global rules.
- Post-generation guard: regex check for forbidden commitments (money amounts, "guarantee", "we will refund") unless allowed. Regenerate once, then flag.
- Keep version history per email (last 5, persisted).
"""
import re
import time
from typing import Dict, Any, List, Optional, Tuple

from config.domain import CATEGORY_POLICIES, HOLDING_REPLY_POLICY, SIGNATURE, COMPANY_NAME
from config.settings import DEFAULT_DRAFTER_TPM
from limiter import get_limiter_for_model, estimate_tokens
from llm import llm_for, get_model_name_for_role
from prompts import DRAFTER_SYSTEM_PROMPT, DRAFTER_USER_PROMPT

FORBIDDEN_COMMITMENT_PATTERNS = [
    r"\$\s*\d+",                          # Specific dollar amounts
    r"\b(guarantee|guaranteed)\b",        # Guarantees
    r"\bwe will refund\b",                # Explicit refund promises
    r"\bwe have refunded\b",              # False claim of completed refund
    r"\bfull compensation\b",             # Compensation promises
]


def check_forbidden_commitments(draft_text: str, allowed_commitments: List[str]) -> List[str]:
    """Scan draft for forbidden commitment patterns that are not explicitly allowed."""
    violations = []
    text_lower = draft_text.lower()

    for pattern in FORBIDDEN_COMMITMENT_PATTERNS:
        matches = re.findall(pattern, text_lower)
        if matches:
            # Check if this pattern is part of allowed commitments
            pattern_allowed = any(re.search(pattern, ac.lower()) for ac in allowed_commitments)
            if not pattern_allowed:
                violations.append(f"Forbidden commitment detected: '{matches[0]}'")

    return violations


def generate_holding_draft(
    sender: str,
    subject: str,
    category: str,
    escalation_reasons: Optional[List[str]] = None,
) -> str:
    """Generate a clean, professional neutral holding reply without calling LLM."""
    reasons_str = f" regarding {', '.join(escalation_reasons)}" if escalation_reasons else ""
    return (
        f"Hello,\n\n"
        f"Thank you for contacting {COMPANY_NAME}.\n\n"
        f"We have received your message regarding \"{subject}\". A senior specialist from our "
        f"team has been assigned to your case{reasons_str} and will review the details to follow up with you directly.\n\n"
        f"If you have any urgent reference numbers or additional details, please feel free to reply to this email.\n\n"
        f"{HOLDING_REPLY_POLICY['sign_off']}"
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
    """Generate or regenerate draft adhering to policy guards and version tracking.

    Returns:
        (draft_dict, tokens_used)
    """
    is_escalated = status in ("Escalated", "NeedsReview")
    requires_human_review = is_escalated

    policy = CATEGORY_POLICIES.get(category, CATEGORY_POLICIES.get("Other/Unclear"))
    allowed_commitments = policy.get("allowed_commitments", [])

    # If Escalated or NeedsReview, default to standard compliant holding reply
    if is_escalated and not reviewer_guidelines:
        draft_text = generate_holding_draft(
            sender=sender,
            subject=subject,
            category=category,
            escalation_reasons=escalation_reasons,
        )
        version_entry = {
            "version": 1 if not existing_versions else len(existing_versions) + 1,
            "text": draft_text,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "author": "holding_template",
        }
        all_versions = (existing_versions or []) + [version_entry]
        return {
            "draft_text": draft_text,
            "requires_human_review": True,
            "edited_by_human": False,
            "guidelines": reviewer_guidelines or "",
            "versions": all_versions[-5:],  # Keep last 5
            "policy_applied": "HOLDING_REPLY_POLICY",
        }, 0

    # Otherwise call Drafter model
    model_name = get_model_name_for_role("drafter")
    limiter = get_limiter_for_model(model_name, DEFAULT_DRAFTER_TPM)

    must_inc_str = "\n".join(f"- {x}" for x in policy.get("must_include", []))
    must_not_str = "\n".join(f"- {x}" for x in policy.get("must_not_include", []))
    allowed_str = "\n".join(f"- {x}" for x in allowed_commitments) if allowed_commitments else "- None"

    prompt_text = DRAFTER_USER_PROMPT.format(
        sender=sender,
        subject=subject,
        body=body,
        category=category,
        is_escalated="YES - REQUIRES CAREFUL HOLDING / HUMAN REVIEW" if is_escalated else "NO",
        tone=policy.get("tone", "Professional"),
        must_include=must_inc_str,
        must_not_include=must_not_str,
        allowed_commitments=allowed_str,
        sign_off=policy.get("sign_off", SIGNATURE),
        reviewer_guidelines=reviewer_guidelines or "None provided",
    )

    full_prompt = f"{DRAFTER_SYSTEM_PROMPT}\n\n{prompt_text}"
    est_tokens = estimate_tokens(full_prompt) + 500

    acquired = limiter.acquire(est_tokens, max_wait=15.0)
    if not acquired:
        # Fallback to neutral holding reply rather than failure
        fallback_draft = generate_holding_draft(sender, subject, category, escalation_reasons)
        return {
            "draft_text": fallback_draft,
            "requires_human_review": True,
            "edited_by_human": False,
            "guidelines": reviewer_guidelines or "",
            "versions": [{"version": 1, "text": fallback_draft, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "author": "rate_limit_fallback"}],
            "rate_limited": True,
        }, 0

    client = llm_for("drafter", temperature=0.2, max_tokens=500)
    actual_tokens = est_tokens

    def _call_llm(additional_instruction: str = "") -> str:
        messages = [
            ("system", DRAFTER_SYSTEM_PROMPT + (f"\n\nWARNING: {additional_instruction}" if additional_instruction else "")),
            ("human", prompt_text),
        ]
        resp = client.invoke(messages)
        return resp.content.strip()

    try:
        draft_content = _call_llm()
    except Exception:
        draft_content = generate_holding_draft(sender, subject, category, escalation_reasons)

    # Post-generation guard check
    violations = check_forbidden_commitments(draft_content, allowed_commitments)
    if violations:
        # Regenerate once with strict correction
        try:
            correction_instruction = f"Your previous draft violated commitments: {'; '.join(violations)}. Remove all promises of specific money or refunds!"
            draft_content = _call_llm(additional_instruction=correction_instruction)
            violations_retry = check_forbidden_commitments(draft_content, allowed_commitments)
            if violations_retry:
                # If still failing, fall back to holding draft and flag for review
                draft_content = generate_holding_draft(sender, subject, category, escalation_reasons)
                requires_human_review = True
        except Exception:
            draft_content = generate_holding_draft(sender, subject, category, escalation_reasons)
            requires_human_review = True

    limiter.record_usage(actual_tokens)

    # Append to version history (keep last 5)
    version_num = 1 if not existing_versions else len(existing_versions) + 1
    new_version = {
        "version": version_num,
        "text": draft_content,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "author": "drafter_llm" if not is_escalated else "holding_template",
    }
    updated_versions = (existing_versions or []) + [new_version]
    trimmed_versions = updated_versions[-5:]

    return {
        "draft_text": draft_content,
        "requires_human_review": requires_human_review,
        "edited_by_human": False,
        "guidelines": reviewer_guidelines or "",
        "versions": trimmed_versions,
        "policy_applied": category,
    }, actual_tokens


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
