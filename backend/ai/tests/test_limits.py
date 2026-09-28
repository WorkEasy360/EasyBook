from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from redis.exceptions import ConnectionError as RedisConnectionError

from ai.config import get_ai_config
from ai.orchestration.errors import AskBooksError
from ai.orchestration.limits import check_and_consume
from core.tests.factories import make_org_with_owner


class CheckAndConsumeTests(TestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Org", "ai-limits@example.com")
        cache.clear()

    @override_settings(AI_USER_REQUESTS_PER_MINUTE=2, AI_ORG_REQUESTS_PER_DAY=1000, AI_ORG_MONTHLY_TOKEN_LIMIT=0)
    def test_allows_requests_within_the_per_minute_limit(self):
        config = get_ai_config()
        check_and_consume(user=self.user, organization=self.org, config=config)
        check_and_consume(user=self.user, organization=self.org, config=config)  # must not raise

    @override_settings(AI_USER_REQUESTS_PER_MINUTE=1, AI_ORG_REQUESTS_PER_DAY=1000, AI_ORG_MONTHLY_TOKEN_LIMIT=0)
    def test_raises_once_the_per_minute_limit_is_exceeded(self):
        config = get_ai_config()
        check_and_consume(user=self.user, organization=self.org, config=config)
        with self.assertRaises(AskBooksError):
            check_and_consume(user=self.user, organization=self.org, config=config)

    @override_settings(AI_USER_REQUESTS_PER_MINUTE=1, AI_ORG_REQUESTS_PER_DAY=1000, AI_ORG_MONTHLY_TOKEN_LIMIT=0)
    def test_fails_open_when_the_cache_backend_is_unavailable(self):
        """A Redis outage must degrade Ask Books to "not rate-limited", not
        make it unavailable — matching core/throttling.py's fix for the
        general API throttles (same rationale: rate limiting is a
        protective layer, not a correctness-critical one). Without the fix
        this raises RedisConnectionError instead of returning normally."""
        config = get_ai_config()
        with patch("ai.orchestration.limits.cache.add", side_effect=RedisConnectionError("down")):
            check_and_consume(user=self.user, organization=self.org, config=config)  # must not raise
