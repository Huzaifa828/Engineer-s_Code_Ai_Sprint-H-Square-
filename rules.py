"""Deterministic guards (pre and post LLM).

Pre-LLM Guards:
- Empty or near-empty body (< 10 chars without attachments) -> Skipped
- Attachment-only body (< 15 chars with attachments) -> Skipped
- Auto-replies, bounces, out-of-office, no-reply senders -> Skipped
- Prompt injection keywords -> Escalate immediately
- Legal / safety / regulatory keywords -> Force escalate=True

Post-LLM Guards:
- Low confidence (< CONFIDENCE_THRESHOLD) -> NeedsReview
- Unsupported language (non-English) -> NeedsReview
- Multi-issue emails -> NeedsReview
- Evidence quote verification: must be a verbatim span (normalized whitespace); downgrade if missing
- Schema / parse errors -> NeedsReview
"""
import re
from typing import Dict, Any, Optional, Tuple, List
from config.settings import CONFIDENCE_THRESHOLD
from config.domain import ESCALATION_REASONS

INJECTION_PATTERNS = [
    r"ignore (all )?previous instructions",
    r"system prompt",
    r"you are now (a|an)",
    r"disregard (all )?(prior|previous)",
    r"jailbreak",
    r"do anything now",
    r"developer mode",
    r"<\|im_start\|>",
]

LEGAL_SAFETY_PATTERNS = [
    (r"\b(lawyer|attorney|lawsuit|litigation|court|sue you|legal counsel)\b", "legal threat"),
    (r"\b(safety|hazard|hospital|poison|injury|burn|fire hazard)\b", "safety/health"),
    (r"\b(press|journalist|media|reporter|ftc|bbb|attorney general|regulator)\b", "press/regulator"),
    (r"\b(gdpr|ccpa|delete my data|right to be forgotten|privacy officer)\b", "privacy/data request"),
]


def normalize_whitespace(text: str) -> str:
    """Normalize internal and external whitespace for robust verbatim span checking."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def check_pre_llm_rules(
    body: str,
    sender: str,
    subject: str,
    attachment_names: List[str],
    is_auto_reply: bool
) -> Optional[Dict[str, Any]]:
    """Check pre-LLM deterministic rules. Returns decision dict if triggered, or None if passed to LLM."""
    clean_body = body.strip() if body else ""
    sender_lower = sender.lower() if sender else ""
    combined_text = f"{subject} {clean_body}".lower()

    # 1. Auto-reply, bounce, or no-reply check
    if is_auto_reply or "no-reply" in sender_lower or "noreply" in sender_lower or "mailer-daemon" in sender_lower:
        return {
            "status": "Skipped",
            "skip_reason": "Automated delivery or no-reply notification",
            "requires_llm": False,
        }

    # 2. Out of office detection
    if re.search(r"\b(out of office|automatic reply|away from my desk|vacation notice)\b", combined_text):
        return {
            "status": "Skipped",
            "skip_reason": "Out of office automatic responder",
            "requires_llm": False,
        }

    # 3. Empty or near-empty body
    if len(clean_body) < 10 and not attachment_names:
        return {
            "status": "Skipped",
            "skip_reason": "Empty or near-empty message body",
            "requires_llm": False,
        }

    # 4. Attachment-only message
    if len(clean_body) < 15 and attachment_names:
        return {
            "status": "Skipped",
            "skip_reason": "Attachment-only email with insufficient text",
            "requires_llm": False,
        }

    # 5. Prompt injection detection -> Escalate
    for pat in INJECTION_PATTERNS:
        if re.search(pat, combined_text, re.IGNORECASE):
            return {
                "status": "Escalated",
                "escalate": True,
                "escalation_reasons": ["prompt injection suspected"],
                "short_reasoning": "Detected prompt injection attempt in email contents.",
                "confidence": 1.0,
                "category": "Spam/Irrelevant",
                "evidence_quote": clean_body[:80],
                "requires_llm": False,
            }

    # 6. Legal / Safety / Regulator keywords -> Pre-flagged for escalation
    pre_escalate_reasons = []
    for pat, reason in LEGAL_SAFETY_PATTERNS:
        if re.search(pat, combined_text, re.IGNORECASE):
            pre_escalate_reasons.append(reason)

    if pre_escalate_reasons:
        # Pre-flagged: will be enforced or merged even after LLM
        return {
            "pre_escalate": True,
            "pre_escalation_reasons": pre_escalate_reasons,
            "requires_llm": True,
        }

    return None


def verify_evidence_quote(quote: str, email_body: str) -> bool:
    """Verifies that the evidence quote is a verbatim span of the email (whitespace-normalized)."""
    if not quote or not email_body:
        return False
    norm_quote = normalize_whitespace(quote).lower()
    norm_body = normalize_whitespace(email_body).lower()
    return norm_quote in norm_body


def apply_post_llm_rules(
    llm_result: Dict[str, Any],
    original_body: str,
    pre_rule_result: Optional[Dict[str, Any]] = None,
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
) -> Dict[str, Any]:
    """Apply post-LLM validation and downgrade rules."""
    result = dict(llm_result)

    # Merge any pre-escalation flags
    if pre_rule_result and pre_rule_result.get("pre_escalate"):
        result["escalate"] = True
        current_reasons = set(result.get("escalation_reasons") or [])
        current_reasons.update(pre_rule_result.get("pre_escalation_reasons") or [])
        result["escalation_reasons"] = list(current_reasons)

    # 1. Check evidence quote verbatim span
    quote = result.get("evidence_quote", "")
    quote_valid = verify_evidence_quote(quote, original_body) if quote else False
    result["quote_verified"] = quote_valid

    # 2. Check conditions requiring review
    reasons_for_review = []
    conf = float(result.get("confidence", 0.0))

    if conf < confidence_threshold:
        reasons_for_review.append(f"Low confidence ({conf:.2f} < {confidence_threshold:.2f})")

    lang = str(result.get("language", "en")).lower()
    if lang not in ("en", "english"):
        reasons_for_review.append(f"Unsupported language ({lang})")

    if result.get("multi_issue"):
        reasons_for_review.append("Multiple distinct customer issues identified")

    if not quote_valid and len(original_body.strip()) > 30:
        reasons_for_review.append("Evidence quote could not be verified verbatim")

    # 3. Determine final status
    if result.get("escalate"):
        result["status"] = "Escalated"
    elif reasons_for_review:
        result["status"] = "NeedsReview"
        result["review_reasons"] = reasons_for_review
    else:
        result["status"] = "Classified"

    return result
