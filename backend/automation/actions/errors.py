class AutomationTransientError(Exception):
    """A retryable action failure — network timeout, rate limit, a
    provider/webhook endpoint 5xx. Everything else is permanent
    (phase section 28)."""
