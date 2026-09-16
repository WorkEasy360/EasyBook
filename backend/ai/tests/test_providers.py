import threading
import time

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from ai.config import RetryPolicy, get_ai_config, validate_ai_configuration
from ai.embeddings import EmbeddingResult, embed_documents, embed_query, override_embedding_provider
from ai.embeddings.base import EmbeddingSpec
from ai.embeddings.fake import FakeEmbeddingProvider, hash_embed
from ai.fields import parse_pgvector_text, to_pgvector_text
from ai.providers.base import LLMRequest, LLMResponse, Message
from ai.providers.errors import (
    ProviderAuthError,
    ProviderContentPolicy,
    ProviderInvalidRequest,
    ProviderMalformedOutput,
    ProviderTimeout,
    ProviderUnavailable,
)
from ai.providers.fake import FakeLLMProvider
from ai.providers.retry import call_with_retry, run_with_timeout

POLICY = RetryPolicy(max_attempts=3, backoff_seconds=0.0, max_backoff_seconds=0.0)


def _request(**kwargs):
    defaults = {
        "system": "s", "messages": (Message(role="user", text="hi"),), "purpose": "synthesis",
        "max_output_tokens": 10, "temperature": 0.0, "timeout_seconds": 1.0,
    }
    defaults.update(kwargs)
    return LLMRequest(**defaults)


class RetryAndTimeoutTests(SimpleTestCase):
    def test_transient_errors_are_retried_up_to_the_bound(self):
        attempts = []

        def flaky():
            attempts.append(1)
            raise ProviderUnavailable("down")

        with self.assertRaises(ProviderUnavailable):
            call_with_retry(flaky, policy=POLICY, timeout_seconds=1, sleep=lambda _: None)
        self.assertEqual(len(attempts), 3)

    def test_transient_error_then_success(self):
        attempts = []

        def flaky():
            attempts.append(1)
            if len(attempts) < 2:
                raise ProviderUnavailable("blip")
            return "ok"

        self.assertEqual(call_with_retry(flaky, policy=POLICY, timeout_seconds=1, sleep=lambda _: None), "ok")
        self.assertEqual(len(attempts), 2)

    def test_non_retryable_errors_are_never_retried(self):
        for error in (ProviderInvalidRequest, ProviderAuthError, ProviderContentPolicy, ProviderMalformedOutput):
            attempts = []

            def fail(error=error, attempts=attempts):
                attempts.append(1)
                raise error("no")

            with self.assertRaises(error):
                call_with_retry(fail, policy=POLICY, timeout_seconds=1, sleep=lambda _: None)
            self.assertEqual(len(attempts), 1, error.__name__)

    def test_hung_call_times_out_instead_of_blocking(self):
        release = threading.Event()
        started = time.monotonic()
        with self.assertRaises(ProviderTimeout):
            run_with_timeout(lambda: release.wait(5), timeout_seconds=0.05)
        release.set()
        self.assertLess(time.monotonic() - started, 2)

    def test_unexpected_exception_becomes_safe_unavailable(self):
        def boom():
            raise KeyError("vendor internals")

        with self.assertRaises(ProviderUnavailable):
            call_with_retry(boom, policy=POLICY, timeout_seconds=1, sleep=lambda _: None)

    def test_retry_stops_at_deadline(self):
        attempts = []

        def flaky():
            attempts.append(1)
            raise ProviderUnavailable("down")

        policy = RetryPolicy(max_attempts=5, backoff_seconds=10, max_backoff_seconds=10)
        with self.assertRaises(ProviderUnavailable):
            call_with_retry(flaky, policy=policy, timeout_seconds=1, sleep=lambda _: None, deadline=time.monotonic() + 1)
        self.assertEqual(len(attempts), 1)


class ProviderInterfaceTests(SimpleTestCase):
    def test_structured_output_rejects_missing_object(self):
        provider = FakeLLMProvider(responder=lambda request: LLMResponse(text="not json"))
        with self.assertRaises(ProviderMalformedOutput):
            provider.structured_output(_request(response_schema={"type": "object"}))

    def test_fake_provider_is_deterministic_and_reports_usage(self):
        provider = FakeLLMProvider()
        first = provider.generate(_request(purpose="route", messages=(Message(role="user", text="What is our profit?"),)))
        second = provider.generate(_request(purpose="route", messages=(Message(role="user", text="What is our profit?"),)))
        self.assertEqual(first.structured, second.structured)
        self.assertEqual(first.structured["intent"], "structured")
        self.assertIsNotNone(first.usage.input_tokens)

    def test_streaming_is_not_enabled(self):
        with self.assertRaises(NotImplementedError):
            FakeLLMProvider().stream(_request())


class ConfigValidationTests(SimpleTestCase):
    def test_default_test_configuration_is_valid(self):
        validate_ai_configuration()

    @override_settings(AI_ASK_BOOKS_ENABLED=True, AI_ALLOW_FAKE_PROVIDERS=False)
    def test_enabled_with_fake_provider_fails_closed(self):
        with self.assertRaises(ImproperlyConfigured):
            validate_ai_configuration()

    @override_settings(AI_LLM_PROVIDER="some-unverified-vendor")
    def test_unknown_provider_is_refused(self):
        with self.assertRaises(ImproperlyConfigured):
            validate_ai_configuration()

    @override_settings(AI_CHUNK_OVERLAP_CHARS=5000)
    def test_invalid_chunking_is_refused(self):
        with self.assertRaises(ImproperlyConfigured):
            validate_ai_configuration()

    @override_settings(AI_MAX_TOOL_CALLS_PER_REQUEST=0)
    def test_unbounded_limits_are_refused(self):
        with self.assertRaises(ImproperlyConfigured):
            validate_ai_configuration()


class EmbeddingTests(SimpleTestCase):
    def test_hash_embedding_is_deterministic_and_normalized(self):
        a = hash_embed("Termination of this agreement requires notice", 64)
        self.assertEqual(a, hash_embed("Termination of this agreement requires notice", 64))
        self.assertAlmostEqual(sum(x * x for x in a), 1.0, places=3)

    def test_embedding_spec_carries_full_identity(self):
        spec = EmbeddingSpec(provider="fake", model="m", dimensions=8, version="v")
        self.assertEqual(spec.config_key, "fake:m:8:v")

    def test_wrong_dimension_output_is_rejected(self):
        spec = EmbeddingSpec(provider="fake", model="m", dimensions=get_ai_config().embedding_dimensions, version="v")

        class Broken(FakeEmbeddingProvider):
            def embed_documents(self, texts):
                return EmbeddingResult(vectors=[[0.1, 0.2]], spec=self.spec)

        with override_embedding_provider(Broken(spec=spec)), self.assertRaises(ProviderMalformedOutput):
            embed_documents(["x"])

    def test_embedding_timeout_fails_safely(self):
        spec = EmbeddingSpec(provider="fake", model="m", dimensions=8, version="v")
        provider = FakeEmbeddingProvider(spec=spec, fail_with=ProviderTimeout("slow"))
        with override_embedding_provider(provider), self.assertRaises(ProviderTimeout):
            embed_query("anything")
        self.assertEqual(provider.calls, get_ai_config().retry.max_attempts)

    def test_vector_text_round_trip(self):
        self.assertEqual(parse_pgvector_text(to_pgvector_text([1, -0.5, 0.25])), [1.0, -0.5, 0.25])
