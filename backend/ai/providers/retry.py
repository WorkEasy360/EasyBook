"""Bounded retries and hard wall-clock timeouts for external AI calls."""

import concurrent.futures
import logging
import time

from ai.config import RetryPolicy
from ai.providers.errors import AIProviderError, ProviderTimeout, ProviderUnavailable

logger = logging.getLogger("ai.providers")

# A small, bounded pool. It exists so a vendor SDK that ignores its own
# timeout can never pin a web worker forever (phase section 43): the caller
# stops waiting after `timeout_seconds` even if the underlying thread is
# still blocked. Bounded, so a hung provider cannot spawn unlimited threads.
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="ai-provider")


def run_with_timeout(fn, *, timeout_seconds: float):
    future = _EXECUTOR.submit(fn)
    try:
        return future.result(timeout=timeout_seconds)
    except concurrent.futures.TimeoutError:
        future.cancel()
        raise ProviderTimeout(f"AI provider call exceeded {timeout_seconds}s")


def call_with_retry(fn, *, policy: RetryPolicy, timeout_seconds: float, sleep=time.sleep, deadline: float | None = None):
    """Calls `fn` (no args) with a hard timeout per attempt. Retries ONLY
    errors flagged `retryable` (timeouts, unavailability, provider rate
    limiting), at most `policy.max_attempts` times in total, with capped
    exponential backoff. Never retries past `deadline` (time.monotonic()).

    Deterministic backoff (no random jitter) — attempt counts are small and
    bounded, and deterministic timing keeps tests exact."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return run_with_timeout(fn, timeout_seconds=timeout_seconds)
        except AIProviderError as exc:
            if not exc.retryable or attempt >= policy.max_attempts:
                raise
            delay = min(policy.backoff_seconds * (2 ** (attempt - 1)), policy.max_backoff_seconds)
            if deadline is not None and time.monotonic() + delay + timeout_seconds > deadline:
                raise
            logger.warning(
                "ai_provider_retry", extra={"attempt": attempt, "error_type": type(exc).__name__}
            )
            sleep(delay)
        except Exception as exc:
            # A provider leaking a non-taxonomy exception is treated as an
            # unavailable provider: fail safely, never surface the raw error.
            logger.exception("ai_provider_unexpected_error")
            raise ProviderUnavailable(type(exc).__name__) from exc
