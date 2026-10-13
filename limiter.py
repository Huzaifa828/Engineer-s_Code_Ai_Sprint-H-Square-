"""Token-aware rate limiter.

Rules:
- No tokenizer library: estimate tokens as ceil(len(text)/4).
- Rolling 60-second window of estimated tokens (prompt estimate + max_tokens) per model.
- Thread-safe, targeting ~85% of the role's TPM setting.
- acquire(tokens, max_wait=15.0): sleeps up to max_wait, else returns False (defer without freezing UI).
- On HTTP 429, honor retry-after (capped), retry at most twice, then defer.
"""
import math
import threading
import time
from typing import Dict, List, Tuple, Optional


def estimate_tokens(text: str) -> int:
    """Estimate token count as ceil(len(text) / 4) with zero external dependencies."""
    if not text:
        return 0
    return math.ceil(len(text) / 4)


class ModelRateLimiter:
    """Thread-safe rolling 60-second window rate limiter for a specific model."""

    def __init__(self, model_name: str, tpm: int = 8000, target_ratio: float = 0.85):
        self.model_name = model_name
        self.tpm = tpm
        self.effective_tpm = int(tpm * target_ratio)
        self.lock = threading.Lock()
        # List of (timestamp, token_count)
        self.history: List[Tuple[float, int]] = []
        self.total_tokens_logged: int = 0

    def _prune(self, now: float):
        cutoff = now - 60.0
        self.history = [(t, count) for (t, count) in self.history if t > cutoff]

    def current_usage(self, now: float) -> int:
        self._prune(now)
        return sum(count for (_, count) in self.history)

    def can_acquire(self, tokens: int, now: Optional[float] = None) -> bool:
        t = now if now is not None else time.time()
        with self.lock:
            usage = self.current_usage(t)
            return (usage + tokens) <= self.effective_tpm

    def acquire(self, tokens: int, max_wait: float = 15.0) -> bool:
        """Attempt to acquire tokens within max_wait seconds. Returns False if waiting exceeds max_wait."""
        start_time = time.time()
        while True:
            now = time.time()
            with self.lock:
                usage = self.current_usage(now)
                if (usage + tokens) <= self.effective_tpm:
                    self.history.append((now, tokens))
                    self.total_tokens_logged += tokens
                    return True

                # Calculate how long until oldest tokens expire
                if self.history:
                    oldest_t, _ = self.history[0]
                    wait_needed = max(0.1, (oldest_t + 60.0) - now)
                else:
                    wait_needed = 1.0

            elapsed = time.time() - start_time
            remaining_budget = max_wait - elapsed
            if wait_needed > remaining_budget:
                return False

            sleep_duration = min(wait_needed, remaining_budget, 1.0)
            if sleep_duration <= 0:
                return False
            time.sleep(sleep_duration)

    def record_usage(self, actual_tokens: int):
        """Record real usage if returned by LLM metadata."""
        with self.lock:
            self.total_tokens_logged += actual_tokens


# Global registry of limiters per model
_LIMITERS: Dict[str, ModelRateLimiter] = {}
_REGISTRY_LOCK = threading.Lock()


def get_limiter_for_model(model_name: str, tpm: int = 8000) -> ModelRateLimiter:
    with _REGISTRY_LOCK:
        if model_name not in _LIMITERS:
            _LIMITERS[model_name] = ModelRateLimiter(model_name=model_name, tpm=tpm)
        return _LIMITERS[model_name]
