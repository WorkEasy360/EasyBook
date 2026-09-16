"""Deterministic hashing embedding — a TEST DOUBLE, not a semantic model.

Feature hashing over word tokens plus 5-character prefixes (so "terminate"
and "termination" land close together), L2-normalised. It gives stable,
reproducible vectors for the automated suite with no network or credentials.
It does NOT understand synonyms ("cancel" vs "terminate"); retrieval quality
numbers measured with it are a floor for pipeline correctness, not a
statement about a production embedding model (ai/CLAUDE.md "EVALUATION").
"""

import hashlib
import math
import re

from ai.embeddings.base import EmbeddingProvider, EmbeddingResult

_STOPWORDS = frozenset(
    "a an and are as at be by does did do for from has have how in is it its of on or our "
    "say says than that the their this to was we what when where which who why will with you your".split()
)
_TOKEN = re.compile(r"[a-z0-9]+")


def _bucket(feature: str, dimensions: int) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % dimensions, (1.0 if (value >> 63) & 1 else -1.0)


def hash_embed(text: str, dimensions: int) -> list[float]:
    vector = [0.0] * dimensions
    for token in _TOKEN.findall(text.lower()):
        if token in _STOPWORDS:
            continue
        index, sign = _bucket(f"w:{token}", dimensions)
        vector[index] += sign
        if len(token) >= 6:
            # Crude deterministic stemming: a shared 5-character prefix
            # ("termi"nate/"termi"nation) pulls word variants together.
            # Character trigrams were tried first and rejected: short
            # fragments like "ice" (office/invoice/notice) created
            # cross-topic noise that the retrieval evaluation caught.
            index, sign = _bucket(f"p:{token[:5]}", dimensions)
            vector[index] += 0.7 * sign
    norm = math.sqrt(sum(component * component for component in vector))
    if norm == 0.0:
        # All-stopword text: an all-zero vector. pgvector's cosine distance
        # against it is NaN, which the similarity threshold filter excludes.
        return vector
    return [round(component / norm, 6) for component in vector]


class FakeEmbeddingProvider(EmbeddingProvider):
    def __init__(self, *, spec, fail_with: Exception | None = None):
        super().__init__(spec=spec)
        self.fail_with = fail_with
        self.calls = 0

    def _embed(self, texts: list[str]) -> EmbeddingResult:
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        vectors = [hash_embed(text, self.spec.dimensions) for text in texts]
        return EmbeddingResult(vectors=vectors, spec=self.spec, usage_tokens=sum(max(1, len(t) // 4) for t in texts))

    def embed_documents(self, texts: list[str]) -> EmbeddingResult:
        return self._embed(texts)

    def embed_query(self, text: str) -> EmbeddingResult:
        return self._embed([text])
