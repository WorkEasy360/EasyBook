"""AI-specific rate and spend limits (phase sections 40-42), separate from
the general API throttles.

Fixed-window counters in the shared cache (Redis in every real
environment): per user per organization per minute, and per organization
per day. `cache.add` + `cache.incr` are atomic on Redis, so concurrent
requests cannot both slip under the limit. An optional monthly token cap per
organization is enforced from AIRequestLog.
"""

import datetime
import time

from django.core.cache import cache
from django.db.models import Sum
from django.utils import timezone

from ai.orchestration.errors import AskBooksError


def _consume(key: str, limit: int, ttl: int) -> bool:
    cache.add(key, 0, ttl)
    try:
        count = cache.incr(key)
    except ValueError:  # expired between add and incr
        cache.add(key, 1, ttl)
        count = 1
    return count <= limit


def check_and_consume(*, user, organization, config) -> None:
    now = int(time.time())
    minute_key = f"ai:rl:user:{organization.id}:{user.id}:{now // 60}"
    if not _consume(minute_key, config.user_requests_per_minute, 120):
        raise AskBooksError("rate_limit_exceeded", "Too many Ask Books requests. Please wait a minute.", 429)

    day_key = f"ai:rl:org:{organization.id}:{datetime.date.today().isoformat()}"
    if not _consume(day_key, config.org_requests_per_day, 60 * 60 * 25):
        raise AskBooksError("rate_limit_exceeded", "Your organization has reached today's Ask Books limit.", 429)

    if config.org_monthly_token_limit > 0:
        from ai.models import AIRequestLog

        month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        totals = AIRequestLog.objects.filter(created_at__gte=month_start).aggregate(
            input=Sum("input_tokens"), output=Sum("output_tokens")
        )
        used = (totals["input"] or 0) + (totals["output"] or 0)
        if used >= config.org_monthly_token_limit:
            raise AskBooksError(
                "ai_quota_exceeded", "Your organization has reached its monthly Ask Books usage limit.", 429
            )
