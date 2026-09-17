"""Rate limiting that survives a cache-backend (Redis) outage.

Django's built-in `django.core.cache.backends.redis.RedisCache` does not
swallow connection errors (confirmed against its source — see
infrastructure/runbooks/redis-celery-outage.md) — DRF's throttle classes call
`cache.get`/`cache.incr` directly, so an unavailable cache backend would
otherwise turn "Redis is down" into "every throttled endpoint returns 500"
rather than just "rate limiting is temporarily not enforced." Rate limiting
is a protective, non-correctness-critical layer, unlike tenant isolation or
authentication (which must fail closed per root CLAUDE.md) — failing OPEN
here is the standard resilience-engineering default: an outage should
degrade a safety net, not take the whole API down with it.
"""

import logging

from redis.exceptions import RedisError
from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle, UserRateThrottle

logger = logging.getLogger("django.request")


class FailOpenThrottleMixin:
    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except RedisError:
            logger.warning(f"throttle_backend_unavailable throttle={type(self).__name__}")
            return True


class FailOpenScopedRateThrottle(FailOpenThrottleMixin, ScopedRateThrottle):
    pass


class FailOpenUserRateThrottle(FailOpenThrottleMixin, UserRateThrottle):
    pass


class FailOpenAnonRateThrottle(FailOpenThrottleMixin, AnonRateThrottle):
    pass
