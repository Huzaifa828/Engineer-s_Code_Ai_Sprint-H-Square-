"""Tests for auth module: HMAC state sign, verify, expiry, tamper, and malformed inputs."""
import time
from auth import generate_oauth_state, verify_oauth_state, hash_email


def test_hmac_state_valid_flow():
    secret = "my-test-secret-key-1234567890123"
    state = generate_oauth_state(secret=secret)
    assert state is not None
    assert len(state.split(":")) == 3
    assert verify_oauth_state(state, secret=secret) is True


def test_hmac_state_tampered_signature():
    secret = "my-test-secret-key-1234567890123"
    state = generate_oauth_state(secret=secret)
    parts = state.split(":")
    tampered_sig = parts[2][:-1] + ("a" if parts[2][-1] != "a" else "b")
    tampered_state = f"{parts[0]}:{parts[1]}:{tampered_sig}"
    assert verify_oauth_state(tampered_state, secret=secret) is False


def test_hmac_state_tampered_payload():
    secret = "my-test-secret-key-1234567890123"
    state = generate_oauth_state(secret=secret)
    parts = state.split(":")
    tampered_state = f"tamperednonce:{parts[1]}:{parts[2]}"
    assert verify_oauth_state(tampered_state, secret=secret) is False


def test_hmac_state_expired():
    secret = "my-test-secret-key-1234567890123"
    old_time = str(int(time.time()) - 700)  # > 600s
    import hmac, hashlib
    payload = f"oldnonce:{old_time}"
    sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    expired_state = f"{payload}:{sig}"
    assert verify_oauth_state(expired_state, secret=secret, max_age=600) is False


def test_hmac_state_malformed():
    secret = "my-test-secret-key-1234567890123"
    assert verify_oauth_state("", secret=secret) is False
    assert verify_oauth_state("invalid", secret=secret) is False
    assert verify_oauth_state("a:b", secret=secret) is False
    assert verify_oauth_state("a:notanumber:sig", secret=secret) is False
    assert verify_oauth_state(None, secret=secret) is False


def test_hash_email():
    email1 = " User.Test@Example.COM "
    email2 = "user.test@example.com"
    assert hash_email(email1) == hash_email(email2)
    assert len(hash_email(email1)) == 64
