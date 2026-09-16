"""Provider-neutral failure taxonomy.

Every provider implementation maps its vendor SDK's exceptions onto these,
so retry policy (ai/providers/retry.py) and the API error envelope never
depend on one vendor. `retryable` is the ONLY thing retry logic reads:
invalid requests, auth failures, content-policy refusals and malformed
output are never retried — repeating them costs money and cannot succeed.
"""


class AIProviderError(Exception):
    code = "ai_unavailable"
    retryable = False
    # Safe, generic message for API clients. The vendor's own message (which
    # can include request fragments or account details) stays in the
    # exception args for server logs only.
    public_message = "The AI assistant is temporarily unavailable."


class ProviderTimeout(AIProviderError):
    code = "ai_timeout"
    retryable = True
    public_message = "The AI assistant timed out. Please try again."


class ProviderUnavailable(AIProviderError):
    retryable = True


class ProviderRateLimited(AIProviderError):
    retryable = True


class ProviderInvalidRequest(AIProviderError):
    pass


class ProviderAuthError(AIProviderError):
    pass


class ProviderContentPolicy(AIProviderError):
    code = "ai_content_refused"
    public_message = "The AI provider declined to answer this request."


class ProviderContextLimitExceeded(AIProviderError):
    code = "context_limit_exceeded"
    public_message = "The request is too large for the AI assistant. Try a narrower question."


class ProviderMalformedOutput(AIProviderError):
    public_message = "The AI assistant returned an unusable response. Please try again."
