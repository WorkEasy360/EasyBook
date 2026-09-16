"""Embedding provider factory. Every embedding call goes through
`embed_documents`/`embed_query` here so the timeout + bounded retry policy
is applied uniformly (phase sections 43/44)."""

import contextlib
import threading

from ai.config import get_ai_config
from ai.embeddings.base import EmbeddingProvider, EmbeddingResult, EmbeddingSpec
from ai.providers.retry import call_with_retry

_override = threading.local()

# Bumped when the fake hashing scheme changes, so stored vectors from the
# old scheme stop matching the active spec.
FAKE_EMBEDDING_VERSION = "hash-v2"


def get_embedding_provider() -> EmbeddingProvider:
    provider = getattr(_override, "provider", None)
    if provider is not None:
        return provider
    config = get_ai_config()
    if config.embedding_provider == "fake":
        from ai.embeddings.fake import FakeEmbeddingProvider

        return FakeEmbeddingProvider(
            spec=EmbeddingSpec(
                provider="fake", model=config.embedding_model, dimensions=config.embedding_dimensions,
                version=FAKE_EMBEDDING_VERSION,
            )
        )
    raise ValueError(f"Unknown AI_EMBEDDING_PROVIDER: {config.embedding_provider!r}")


def active_embedding_spec() -> EmbeddingSpec:
    return get_embedding_provider().spec


def _validated(result: EmbeddingResult, expected_count: int, spec: EmbeddingSpec) -> EmbeddingResult:
    from ai.providers.errors import ProviderMalformedOutput

    if len(result.vectors) != expected_count:
        raise ProviderMalformedOutput("embedding count mismatch")
    for vector in result.vectors:
        if len(vector) != spec.dimensions:
            raise ProviderMalformedOutput("embedding dimension mismatch")
    return result


def embed_documents(texts: list[str]) -> EmbeddingResult:
    config = get_ai_config()
    provider = get_embedding_provider()
    result = call_with_retry(
        lambda: provider.embed_documents(texts), policy=config.retry, timeout_seconds=config.embedding_timeout_seconds
    )
    return _validated(result, len(texts), provider.spec)


def embed_query(text: str, *, deadline: float | None = None) -> EmbeddingResult:
    config = get_ai_config()
    provider = get_embedding_provider()
    result = call_with_retry(
        lambda: provider.embed_query(text), policy=config.retry,
        timeout_seconds=config.embedding_timeout_seconds, deadline=deadline,
    )
    return _validated(result, 1, provider.spec)


@contextlib.contextmanager
def override_embedding_provider(provider: EmbeddingProvider):
    previous = getattr(_override, "provider", None)
    _override.provider = provider
    try:
        yield provider
    finally:
        _override.provider = previous
