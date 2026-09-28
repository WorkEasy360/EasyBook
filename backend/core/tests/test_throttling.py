from unittest.mock import patch

from django.test import SimpleTestCase
from redis.exceptions import ConnectionError as RedisConnectionError
from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle, UserRateThrottle

from core.throttling import FailOpenAnonRateThrottle, FailOpenScopedRateThrottle, FailOpenUserRateThrottle


class FailOpenThrottleTests(SimpleTestCase):
    def test_scoped_throttle_allows_request_when_cache_backend_is_down(self):
        with patch.object(ScopedRateThrottle, "allow_request", side_effect=RedisConnectionError("down")):
            throttle = FailOpenScopedRateThrottle()
            self.assertTrue(throttle.allow_request(request=None, view=None))

    def test_user_throttle_allows_request_when_cache_backend_is_down(self):
        with patch.object(UserRateThrottle, "allow_request", side_effect=RedisConnectionError("down")):
            throttle = FailOpenUserRateThrottle()
            self.assertTrue(throttle.allow_request(request=None, view=None))

    def test_anon_throttle_allows_request_when_cache_backend_is_down(self):
        with patch.object(AnonRateThrottle, "allow_request", side_effect=RedisConnectionError("down")):
            throttle = FailOpenAnonRateThrottle()
            self.assertTrue(throttle.allow_request(request=None, view=None))

    def test_scoped_throttle_still_enforces_normally_when_cache_is_up(self):
        with patch.object(ScopedRateThrottle, "allow_request", return_value=False):
            throttle = FailOpenScopedRateThrottle()
            self.assertFalse(throttle.allow_request(request=None, view=None))

    def test_non_redis_exceptions_are_not_swallowed(self):
        with patch.object(ScopedRateThrottle, "allow_request", side_effect=ValueError("unrelated bug")):
            throttle = FailOpenScopedRateThrottle()
            with self.assertRaises(ValueError):
                throttle.allow_request(request=None, view=None)
