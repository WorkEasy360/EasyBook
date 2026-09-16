from ai.providers.errors import AIProviderError


class AskBooksError(Exception):
    """A safe, client-presentable failure. `message` never contains provider
    internals, prompts, stack traces or other tenants' data."""

    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


_PROVIDER_STATUS = {
    "ai_timeout": 504,
    "ai_unavailable": 503,
    "context_limit_exceeded": 413,
    "ai_content_refused": 422,
}


def from_provider_error(exc: AIProviderError) -> AskBooksError:
    return AskBooksError(exc.code, exc.public_message, _PROVIDER_STATUS.get(exc.code, 503))


def ai_disabled() -> AskBooksError:
    return AskBooksError("ai_disabled", "Ask Books is not enabled.", 403)


def forbidden() -> AskBooksError:
    return AskBooksError("forbidden", "You do not have permission to use Ask Books.", 403)
