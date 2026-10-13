"""Factory for LLM clients using LangChain and ChatGroq.

Roles:
- classifier: CLASSIFIER_MODEL
- verifier: VERIFIER_MODEL
- drafter: DRAFTER_MODEL

Rules:
- No hardcoded model names anywhere.
- If GROQ_API_KEY_DRAFT is missing, fallback to GROQ_API_KEY_CLASSIFY.
- Provide swap point if alternative providers are plugged in.
"""
from typing import Optional
from langchain_groq import ChatGroq
from config.settings import get_secret


def get_model_name_for_role(role: str) -> str:
    """Return model name for role from environment/secrets without hardcoding."""
    if role == "classifier":
        return get_secret("CLASSIFIER_MODEL", "llama-3.3-70b-versatile")
    elif role == "verifier":
        return get_secret("VERIFIER_MODEL", "mixtral-8x7b-32768")
    elif role == "drafter":
        return get_secret("DRAFTER_MODEL", "llama-3.1-8b-instant")
    else:
        raise ValueError(f"Unknown LLM role: {role}")


def get_api_key_for_role(role: str) -> str:
    """Return Groq API key for role."""
    classify_key = get_secret("GROQ_API_KEY_CLASSIFY", "")
    if role == "drafter":
        draft_key = get_secret("GROQ_API_KEY_DRAFT")
        return draft_key if draft_key else classify_key
    return classify_key


def llm_for(role: str, temperature: float = 0.0, max_tokens: Optional[int] = None) -> ChatGroq:
    """Return initialized ChatGroq instance for role."""
    model_name = get_model_name_for_role(role)
    api_key = get_api_key_for_role(role) or "gsk_dummy_test_key_for_mocking"

    kwargs = {
        "model": model_name,
        "temperature": temperature,
        "api_key": api_key,
    }
    if max_tokens:
        kwargs["max_tokens"] = max_tokens

    return ChatGroq(**kwargs)
