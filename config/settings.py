"""Configuration settings and secret retrieval."""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load local .env if present
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()


def get_secret(name: str, default: any = None) -> any:
    """Retrieve secret from Streamlit secrets first, then os.environ."""
    # Check Streamlit secrets if running inside streamlit
    try:
        import streamlit as st
        if hasattr(st, "secrets") and name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass

    val = os.getenv(name)
    if val is not None:
        return val
    return default


# Pipeline & sweep tunables
POLL_SECONDS: int = 10
MAX_PER_TICK: int = 5
MAX_SCAN: int = 300
CONFIDENCE_THRESHOLD: float = 0.6
MAX_BODY_CHARS: int = 3000

# Default TPM limits
DEFAULT_CLASSIFIER_TPM: int = int(get_secret("CLASSIFIER_TPM", 8000))
DEFAULT_VERIFIER_TPM: int = int(get_secret("VERIFIER_TPM", 8000))
DEFAULT_DRAFTER_TPM: int = int(get_secret("DRAFTER_TPM", 8000))
