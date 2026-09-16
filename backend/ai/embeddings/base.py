"""Embedding provider interface — deliberately separate from the chat LLM
(phase section 7): the embedding model is its own explicit configuration
and is NEVER assumed to be the chat model."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class EmbeddingSpec:
    """Identity of a vector space. Vectors from two different specs are
    never compared (ai/retrieval/vector.py filters on `config_key`)."""

    provider: str
    model: str
    dimensions: int
    version: str

    @property
    def config_key(self) -> str:
        return f"{self.provider}:{self.model}:{self.dimensions}:{self.version}"


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    spec: EmbeddingSpec
    # None when the provider does not report usage.
    usage_tokens: int | None = None


class EmbeddingProvider(ABC):
    def __init__(self, *, spec: EmbeddingSpec):
        self.spec = spec

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> EmbeddingResult:
        """Embed chunk texts for storage. Must honour the configured timeout
        and raise only ai.providers.errors.AIProviderError subclasses."""

    @abstractmethod
    def embed_query(self, text: str) -> EmbeddingResult:
        """Embed a search query (some models embed queries differently)."""
