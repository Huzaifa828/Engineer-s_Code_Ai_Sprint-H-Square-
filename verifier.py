"""Independent Verifier module.

Features:
- Independent blind classification using a DIFFERENT model (VERIFIER_MODEL) and prompt.
- Never sees the first classifier's output.
- Compares classifications:
    - If agree -> verifier passes.
    - If disagree -> flagged for review with both labels.
"""
import json
from typing import Dict, Any, Tuple, Optional
from pydantic import BaseModel, Field

from config.settings import DEFAULT_VERIFIER_TPM
from limiter import get_limiter_for_model, estimate_tokens
from llm import llm_for, get_model_name_for_role
from prompts import VERIFIER_SYSTEM_PROMPT, VERIFIER_USER_PROMPT


class VerifierResult(BaseModel):
    category: str = Field(description="Independent category choice")
    escalate: bool = Field(default=False)
    short_reasoning: str = Field(default="")


def verify_email_blind(
    sender: str,
    subject: str,
    body: str,
) -> Tuple[Dict[str, Any], int]:
    """Run independent blind verification call using VERIFIER_MODEL."""
    model_name = get_model_name_for_role("verifier")
    limiter = get_limiter_for_model(model_name, DEFAULT_VERIFIER_TPM)

    prompt_text = VERIFIER_USER_PROMPT.format(
        sender=sender,
        subject=subject,
        body=body,
    )
    full_prompt = f"{VERIFIER_SYSTEM_PROMPT}\n\n{prompt_text}"
    est_tokens = estimate_tokens(full_prompt) + 200

    acquired = limiter.acquire(est_tokens, max_wait=15.0)
    if not acquired:
        return {
            "status": "Deferred",
            "rate_limited": True,
            "error": "Verifier rate limit reached",
        }, 0

    client = llm_for("verifier", temperature=0.0, max_tokens=250)

    try:
        structured_llm = client.with_structured_output(VerifierResult)
        response = structured_llm.invoke([
            ("system", VERIFIER_SYSTEM_PROMPT),
            ("human", prompt_text),
        ])
        if isinstance(response, VerifierResult):
            output = response.model_dump()
        else:
            output = dict(response)
    except Exception:
        # Fallback to plain prompt
        try:
            raw = client.invoke([
                ("system", VERIFIER_SYSTEM_PROMPT + "\nRespond in JSON with keys category, escalate, short_reasoning."),
                ("human", prompt_text),
            ])
            text = raw.content.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            output = json.loads(text.strip())
        except Exception as e:
            output = {
                "category": "Other/Unclear",
                "escalate": False,
                "short_reasoning": f"Verifier error: {str(e)}",
            }

    limiter.record_usage(est_tokens)
    return output, est_tokens


def compare_classifications(
    classifier_result: Dict[str, Any],
    verifier_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Compare primary classifier output with verifier output."""
    c_cat = classifier_result.get("category")
    v_cat = verifier_result.get("category")
    c_esc = bool(classifier_result.get("escalate"))
    v_esc = bool(verifier_result.get("escalate"))

    cat_agree = (c_cat == v_cat)
    esc_agree = (c_esc == v_esc)
    agreed = cat_agree and esc_agree

    return {
        "agreed": agreed,
        "category_agree": cat_agree,
        "escalate_agree": esc_agree,
        "classifier_category": c_cat,
        "verifier_category": v_cat,
        "classifier_escalate": c_esc,
        "verifier_escalate": v_esc,
        "verifier_reasoning": verifier_result.get("short_reasoning", ""),
    }
