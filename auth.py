"""Google Web OAuth Flow and HMAC State Management.

Features:
- Stateless HMAC-SHA256 OAuth state generator and verifier with 10-minute TTL.
- Flow.from_client_config configured for confidential Web applications.
- Credentials live only in st.session_state; never persisted to disk or logs.
- Safe profile fetching to identify user by email address.
"""
import hmac
import hashlib
import secrets
import time
from typing import Tuple, Optional, Dict, Any
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from config.settings import get_secret

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
STATE_TTL_SECONDS = 600  # 10 minutes


def generate_oauth_state(secret: Optional[str] = None) -> str:
    """Generate state parameter as nonce:timestamp:signature using HMAC-SHA256."""
    secret_key = secret or get_secret("STATE_SECRET", "default-dev-state-secret-string-32b")
    nonce = secrets.token_hex(16)
    timestamp = str(int(time.time()))
    payload = f"{nonce}:{timestamp}"
    signature = hmac.new(
        secret_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return f"{payload}:{signature}"


def verify_oauth_state(state: str, secret: Optional[str] = None, max_age: int = STATE_TTL_SECONDS) -> bool:
    """Verify state token integrity and TTL. Rejects malformed values without crashing."""
    if not state or not isinstance(state, str):
        return False

    parts = state.split(":")
    if len(parts) != 3:
        return False

    nonce, timestamp_str, signature = parts
    try:
        ts = int(timestamp_str)
    except (ValueError, TypeError):
        return False

    # Check expiration
    now = int(time.time())
    if now - ts > max_age or ts > now + 60:  # Allow 60s clock skew forward
        return False

    secret_key = secret or get_secret("STATE_SECRET", "default-dev-state-secret-string-32b")
    payload = f"{nonce}:{timestamp_str}"
    expected_sig = hmac.new(
        secret_key.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(signature, expected_sig)


def get_client_config() -> Dict[str, Any]:
    """Build client_config dictionary for Google Flow from settings."""
    client_id = get_secret("GOOGLE_CLIENT_ID", "")
    client_secret = get_secret("GOOGLE_CLIENT_SECRET", "")
    redirect_uri = get_secret("REDIRECT_URI", "http://localhost:8501")

    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def create_oauth_flow(state: Optional[str] = None) -> Flow:
    """Create google_auth_oauthlib.flow.Flow configured for web app with offline access."""
    client_config = get_client_config()
    redirect_uri = get_secret("REDIRECT_URI", "http://localhost:8501")

    flow = Flow.from_client_config(
        client_config=client_config,
        scopes=SCOPES,
        state=state,
        autogenerate_code_verifier=False,
    )
    flow.redirect_uri = redirect_uri
    flow.code_verifier = None
    return flow


def get_authorization_url() -> Tuple[str, str]:
    """Get Google OAuth authorization URL and signed state token."""
    state = generate_oauth_state()
    flow = create_oauth_flow(state=state)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
    )
    return auth_url, state


def exchange_code_for_credentials(code: str, state: str) -> Credentials:
    """Exchange authorization code for Credentials using the Flow."""
    flow = create_oauth_flow(state=state)
    flow.fetch_token(code=code)
    return flow.credentials


def get_user_email(credentials: Credentials) -> str:
    """Identify the user with Gmail users().getProfile (emailAddress) without requesting extra scopes."""
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    profile = service.users().getProfile(userId="me").execute()
    return profile.get("emailAddress", "").strip().lower()


def hash_email(email: str) -> str:
    """Generate sha256 hex digest of the lowercased, trimmed email address."""
    clean_email = email.strip().lower()
    return hashlib.sha256(clean_email.encode("utf-8")).hexdigest()
