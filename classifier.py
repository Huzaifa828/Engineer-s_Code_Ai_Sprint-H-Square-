"""Email Classifier using LangChain ChatGroq with structured output and fallback.

Outputs Pydantic schema:
- category: one of CATEGORIES
- confidence: float 0.0 to 1.0
- urgency: "low" | "medium" | "high" | "critical"
- sentiment: "positive" | "neutral" | "negative" | "furious"
- language: ISO code or name (e.g., "en", "es")
- multi_issue: bool
- escalate: bool
- escalation_reasons: List of strings from ESCALATION_REASONS
- evidence_quote: verbatim excerpt from the email
- short_reasoning: brief rationale
"""
import json
import logging
from typing import List, Optional, Dict, Any, Literal, Tuple
from pydantic import BaseModel, Field

from config.domain import CATEGORIES, ESCALATION_REASONS
from config.settings import DEFAULT_CLASSIFIER_TPM
from limiter import get_limiter_for_model, estimate_tokens
from llm import llm_for, get_model_name_for_role
from prompts import CLASSIFIER_SYSTEM_PROMPT, CLASSIFIER_USER_PROMPT
from rules import check_pre_llm_rules, apply_post_llm_rules


class ClassificationResult(BaseModel):
    category: str = Field(description="Selected issue category from predefined list")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0")
    urgency: Literal["low", "medium", "high", "critical"] = Field(default="medium")
    sentiment: Literal["positive", "neutral", "negative", "furious"] = Field(default="neutral")
    language: str = Field(default="en", description="Detected language, e.g. en, fr, de, es")
    multi_issue: bool = Field(default=False, description="True if customer raises 2 or more distinct issues")
    escalate: bool = Field(default=False, description="True if email requires immediate human escalation")
    escalation_reasons: List[str] = Field(default_factory=list, description="Reasons for escalation if any")
    evidence_quote: str = Field(default="", description="Verbatim quote from the email text proving the category")
    short_reasoning: str = Field(default="", description="Brief 1-2 sentence explanation")


def classify_email(
    sender: str,
    subject: str,
    body: str,
    attachment_names: Optional[List[str]] = None,
    is_auto_reply: bool = False,
    confidence_threshold: float = 0.6,
) -> Tuple[Dict[str, Any], int]:
    """Run pre-LLM guards, structured LLM classification, and post-LLM validation.

    Returns:
        (result_dict, tokens_used)
    """
    attachments = attachment_names or []
    pre_rule = check_pre_llm_rules(
        body=body,
        sender=sender,
        subject=subject,
        attachment_names=attachments,
        is_auto_reply=is_auto_reply
    )

    # If deterministic rule says no LLM needed (e.g. skipped or obvious injection)
    if pre_rule and not pre_rule.get("requires_llm", True):
        return pre_rule, 0

    model_name = get_model_name_for_role("classifier")
    limiter = get_limiter_for_model(model_name, DEFAULT_CLASSIFIER_TPM)

    prompt_text = CLASSIFIER_USER_PROMPT.format(
        sender=sender,
        subject=subject,
        body=body,
    )
    full_prompt = f"{CLASSIFIER_SYSTEM_PROMPT}\n\n{prompt_text}"
    est_tokens = estimate_tokens(full_prompt) + 300

    # Acquire rate limit budget (sleep up to 15s)
    acquired = limiter.acquire(est_tokens, max_wait=15.0)
    if not acquired:
        return {
            "status": "Queued",
            "rate_limited": True,
            "error": "Rate limit threshold reached, deferring to next tick",
        }, 0

    client = llm_for("classifier", temperature=0.0, max_tokens=300)

    actual_tokens = est_tokens
    parsed_output = None

    try:
        # Attempt with structured output
        structured_llm = client.with_structured_output(ClassificationResult)
        messages = [
            ("system", CLASSIFIER_SYSTEM_PROMPT),
            ("human", prompt_text),
        ]
        response = structured_llm.invoke(messages)

        if isinstance(response, ClassificationResult):
            parsed_output = response.model_dump()
        elif isinstance(response, dict):
            parsed_output = response
    except Exception as e:
        # Fallback to direct prompt + JSON parsing with one repair attempt
        try:
            raw_resp = client.invoke([
                ("system", CLASSIFIER_SYSTEM_PROMPT + "\nRespond strictly in valid JSON matching schema."),
                ("human", prompt_text),
            ])
            text = raw_resp.content.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            json_dict = json.loads(text.strip())
            validated = ClassificationResult(**json_dict)
            parsed_output = validated.model_dump()
        except Exception as fallback_err:
            parsed_output = {
                "category": "Other/Unclear",
                "confidence": 0.0,
                "urgency": "medium",
                "sentiment": "neutral",
                "language": "en",
                "multi_issue": False,
                "escalate": False,
                "escalation_reasons": [],
                "evidence_quote": "",
                "short_reasoning": f"Classifier parsing failure: {str(e)}",
            }

    # Record actual tokens
    limiter.record_usage(actual_tokens)

    # Post-LLM rules validation
    final_result = apply_post_llm_rules(
        llm_result=parsed_output,
        original_body=body,
        pre_rule_result=pre_rule,
        confidence_threshold=confidence_threshold,
    )
    final_result["tokens_used"] = actual_tokens
    return final_result, actual_tokens
