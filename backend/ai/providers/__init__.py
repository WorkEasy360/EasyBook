"""LLM provider factory. See ai/CLAUDE.md "PROVIDERS"."""

import contextlib
import threading

from ai.config import get_ai_config
from ai.providers.base import LLMProvider

_override = threading.local()


def get_llm_provider() -> LLMProvider:
    provider = getattr(_override, "provider", None)
    if provider is not None:
        return provider
    config = get_ai_config()
    if config.llm_provider == "fake":
        from ai.providers.fake import FakeLLMProvider

        return FakeLLMProvider(model=config.llm_model)
    # Unreachable when validate_ai_configuration() ran at startup.
    raise ValueError(f"Unknown AI_LLM_PROVIDER: {config.llm_provider!r}")


@contextlib.contextmanager
def override_llm_provider(provider: LLMProvider):
    """Test hook: route every get_llm_provider() call on this thread to
    `provider` (e.g. a FakeLLMProvider with a scripted responder)."""
    previous = getattr(_override, "provider", None)
    _override.provider = provider
    try:
        yield provider
    finally:
        _override.provider = previous
