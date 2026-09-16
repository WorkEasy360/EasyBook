"""Typed, validated view over the `AI_*` Django settings.

Everything tunable about Ask Books (providers, budgets, limits, chunking)
lives in settings/environment — never hard-coded in the pipeline — and is
read through `get_ai_config()` so tests can override settings and every
call sees the current values. `validate_ai_configuration()` runs at app
startup (ai/apps.py) and refuses to boot an enabled-but-misconfigured AI
layer instead of failing on the first user request.
"""

from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

# Provider names this codebase actually implements. A live vendor provider
# (Anthropic/OpenAI/Bedrock/...) is added here only together with an
# implementation verified against that vendor's current documentation and
# real credentials — see ai/CLAUDE.md "PROVIDERS".
KNOWN_LLM_PROVIDERS = {"fake"}
KNOWN_EMBEDDING_PROVIDERS = {"fake"}
FAKE_PROVIDERS = {"fake"}


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    backoff_seconds: float
    max_backoff_seconds: float


@dataclass(frozen=True)
class ChunkingConfig:
    target_chars: int
    overlap_chars: int
    max_chars: int
    # Bumped whenever the chunking ALGORITHM changes, so an unchanged
    # document is still re-chunked after an algorithm change (it is part of
    # the index content hash — ai/rag/indexing.py).
    version: str = "chunk-v1"


@dataclass(frozen=True)
class AIConfig:
    enabled: bool
    allow_fake_providers: bool

    llm_provider: str
    llm_model: str
    llm_temperature: float
    llm_max_output_tokens: int
    llm_timeout_seconds: float

    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    embedding_timeout_seconds: float

    retry: RetryPolicy

    max_tool_calls_per_request: int
    max_llm_rounds: int
    max_tool_output_chars: int
    max_tool_rows: int
    max_context_chars: int
    max_question_chars: int
    max_retrieved_chunks: int
    retrieval_candidates: int
    vector_min_similarity: float
    rrf_k: int
    request_deadline_seconds: float

    user_requests_per_minute: int
    org_requests_per_day: int
    org_monthly_token_limit: int

    conversation_context_messages: int
    conversation_retention_days: int
    request_log_retention_days: int

    chunking: ChunkingConfig


def _setting(name, default):
    return getattr(settings, name, default)


def get_ai_config() -> AIConfig:
    return AIConfig(
        enabled=bool(_setting("AI_ASK_BOOKS_ENABLED", False)),
        allow_fake_providers=bool(_setting("AI_ALLOW_FAKE_PROVIDERS", False)),
        llm_provider=_setting("AI_LLM_PROVIDER", "fake"),
        llm_model=_setting("AI_LLM_MODEL", "fake-llm-1"),
        llm_temperature=float(_setting("AI_LLM_TEMPERATURE", 0.0)),
        llm_max_output_tokens=int(_setting("AI_LLM_MAX_OUTPUT_TOKENS", 1024)),
        llm_timeout_seconds=float(_setting("AI_LLM_TIMEOUT_SECONDS", 30.0)),
        embedding_provider=_setting("AI_EMBEDDING_PROVIDER", "fake"),
        embedding_model=_setting("AI_EMBEDDING_MODEL", "fake-hash-embedding-1"),
        embedding_dimensions=int(_setting("AI_EMBEDDING_DIMENSIONS", 768)),
        embedding_timeout_seconds=float(_setting("AI_EMBEDDING_TIMEOUT_SECONDS", 15.0)),
        retry=RetryPolicy(
            max_attempts=int(_setting("AI_RETRY_MAX_ATTEMPTS", 3)),
            backoff_seconds=float(_setting("AI_RETRY_BACKOFF_SECONDS", 0.5)),
            max_backoff_seconds=float(_setting("AI_RETRY_MAX_BACKOFF_SECONDS", 4.0)),
        ),
        max_tool_calls_per_request=int(_setting("AI_MAX_TOOL_CALLS_PER_REQUEST", 6)),
        max_llm_rounds=int(_setting("AI_MAX_LLM_ROUNDS", 4)),
        max_tool_output_chars=int(_setting("AI_MAX_TOOL_OUTPUT_CHARS", 12_000)),
        max_tool_rows=int(_setting("AI_MAX_TOOL_ROWS", 25)),
        max_context_chars=int(_setting("AI_MAX_CONTEXT_CHARS", 48_000)),
        max_question_chars=int(_setting("AI_MAX_QUESTION_CHARS", 2_000)),
        max_retrieved_chunks=int(_setting("AI_MAX_RETRIEVED_CHUNKS", 6)),
        retrieval_candidates=int(_setting("AI_RETRIEVAL_CANDIDATES", 20)),
        vector_min_similarity=float(_setting("AI_VECTOR_MIN_SIMILARITY", 0.15)),
        rrf_k=int(_setting("AI_RRF_K", 60)),
        request_deadline_seconds=float(_setting("AI_REQUEST_DEADLINE_SECONDS", 90.0)),
        user_requests_per_minute=int(_setting("AI_USER_REQUESTS_PER_MINUTE", 10)),
        org_requests_per_day=int(_setting("AI_ORG_REQUESTS_PER_DAY", 1_000)),
        org_monthly_token_limit=int(_setting("AI_ORG_MONTHLY_TOKEN_LIMIT", 0)),
        conversation_context_messages=int(_setting("AI_CONVERSATION_CONTEXT_MESSAGES", 6)),
        conversation_retention_days=int(_setting("AI_CONVERSATION_RETENTION_DAYS", 90)),
        request_log_retention_days=int(_setting("AI_REQUEST_LOG_RETENTION_DAYS", 365)),
        chunking=ChunkingConfig(
            target_chars=int(_setting("AI_CHUNK_TARGET_CHARS", 1_200)),
            overlap_chars=int(_setting("AI_CHUNK_OVERLAP_CHARS", 200)),
            max_chars=int(_setting("AI_CHUNK_MAX_CHARS", 2_000)),
        ),
    )


def validate_ai_configuration(config: AIConfig | None = None) -> None:
    """Fail closed at startup. Called from AiConfig.ready()."""
    config = config or get_ai_config()
    if config.llm_provider not in KNOWN_LLM_PROVIDERS:
        raise ImproperlyConfigured(f"AI_LLM_PROVIDER {config.llm_provider!r} is not an implemented provider.")
    if config.embedding_provider not in KNOWN_EMBEDDING_PROVIDERS:
        raise ImproperlyConfigured(
            f"AI_EMBEDDING_PROVIDER {config.embedding_provider!r} is not an implemented provider."
        )
    if config.enabled and not config.allow_fake_providers and (
        config.llm_provider in FAKE_PROVIDERS or config.embedding_provider in FAKE_PROVIDERS
    ):
        # A deterministic test double must never silently answer real users'
        # financial questions in production.
        raise ImproperlyConfigured(
            "AI_ASK_BOOKS_ENABLED is on but a fake AI provider is configured and "
            "AI_ALLOW_FAKE_PROVIDERS is off. Configure a real provider or disable Ask Books."
        )
    if not 1 <= config.embedding_dimensions <= 16_000:
        # pgvector's `vector` type stores at most 16,000 dimensions.
        raise ImproperlyConfigured("AI_EMBEDDING_DIMENSIONS must be between 1 and 16000.")
    chunking = config.chunking
    if not (0 <= chunking.overlap_chars < chunking.target_chars <= chunking.max_chars):
        raise ImproperlyConfigured("AI chunking requires 0 <= OVERLAP < TARGET <= MAX characters.")
    for name in (
        "llm_timeout_seconds", "embedding_timeout_seconds", "max_tool_calls_per_request", "max_llm_rounds",
        "max_tool_output_chars", "max_tool_rows", "max_context_chars", "max_retrieved_chunks",
        "retrieval_candidates", "request_deadline_seconds", "max_question_chars",
    ):
        if getattr(config, name) <= 0:
            raise ImproperlyConfigured(f"AI setting {name} must be positive (bounded, never unlimited).")
    if config.retry.max_attempts < 1 or config.retry.max_attempts > 5:
        raise ImproperlyConfigured("AI_RETRY_MAX_ATTEMPTS must be between 1 and 5.")
