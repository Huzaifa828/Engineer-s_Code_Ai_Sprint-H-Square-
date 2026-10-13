"""Tests for limiter: rolling 60s window, token estimation, TPM target ratio, and max_wait timeout."""
import time
from limiter import estimate_tokens, ModelRateLimiter


def test_estimate_tokens_ceil():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
    assert estimate_tokens("a" * 100) == 25


def test_limiter_immediate_acquire():
    limiter = ModelRateLimiter(model_name="test-model", tpm=1000, target_ratio=0.8)
    # Effective TPM is 800
    assert limiter.acquire(200, max_wait=1.0) is True
    assert limiter.current_usage(time.time()) == 200
    assert limiter.acquire(300, max_wait=1.0) is True
    assert limiter.current_usage(time.time()) == 500


def test_limiter_rejects_exceeding_without_waiting_forever():
    limiter = ModelRateLimiter(model_name="test-model-small", tpm=100, target_ratio=1.0)
    assert limiter.acquire(90, max_wait=0.5) is True

    # Attempt to acquire another 50 when limit is 100 with short max_wait
    start = time.time()
    res = limiter.acquire(50, max_wait=0.3)
    duration = time.time() - start

    assert res is False
    assert duration < 1.0  # Must return False rather than blocking forever
