# Redis / Celery outage

Redis backs the Celery broker/result backend, DRF rate limiting, and
`django.core.cache` (`backend/config/settings/base.py`) — never authoritative
financial storage (root CLAUDE.md), so a Redis outage degrades the app rather
than corrupting it. It does not affect `/api/v1/health/live/` (deliberately
dependency-free — `backend/core/views.py`); it will fail
`/api/v1/health/`'s cache check, so the ALB will correctly stop routing to
otherwise-healthy `api` tasks.

## Symptoms

- `/api/v1/health/` reports `"cache": false`.
- ECS Container Insights / `cloudwatch.tf`'s `redis_cpu` alarm, or a flat/zero
  ElastiCache CPU metric (node down rather than overloaded).
- Celery workers log connection errors; `CELERY_BEAT_SCHEDULE` entries
  (`config/settings/base.py`) stop firing.

## Immediate actions

1. Check `aws_elasticache_replication_group.main` (terraform/elasticache.tf)
   status in the ElastiCache console. If `var.redis_multi_az = true`,
   automatic failover should already be underway — confirm rather than assume.
2. If the primary node is down and failover isn't happening (e.g.
   `redis_multi_az = false`, a real single-node deployment), this is a
   restart/replace of the ElastiCache node, not something to fix from inside
   the application.
3. **Fixed, not just documented** (was a verified gap as of the phase 12
   security review; closed the same day). `core/throttling.py`'s
   `FailOpen{Scoped,User,Anon}RateThrottle` wrap DRF's throttle classes to
   catch `redis.exceptions.RedisError` and allow the request through rather
   than 500 it — wired in as `DEFAULT_THROTTLE_CLASSES`
   (`config/settings/base.py`). Login (`accounts/views.py`'s `throttle_scope
   = "auth"`) and Ask Books (`ai/orchestration/limits.py`'s direct
   `cache.add`/`cache.incr` calls, which the DRF throttle classes don't cover
   — that one is NOT wrapped, since it isn't a DRF throttle) behave
   differently:
   - **Login and every other DRF-throttled endpoint: unaffected.** A Redis
     outage degrades to "rate limiting not enforced" (logged as
     `throttle_backend_unavailable`), not an outage of the endpoint itself.
   - **Ask Books (`/api/v1/ai/...`): also fixed.** `ai/orchestration/limits.py::_consume()`
     catches the same `redis.exceptions.RedisError` and fails open (logged as
     `ai_rate_limit_backend_unavailable`) — it isn't a DRF throttle class, so
     it needed its own fix rather than reusing `core/throttling.py`'s
     wrapper, but the reasoning and outcome are identical.
   - Everything else that doesn't call `cache.*` directly is unaffected by
     this specific failure mode (it may still depend on Redis via Celery for
     asynchronous work, per the rest of this runbook).

## Celery-specific

- Queued-but-undelivered tasks are held in Redis; a Redis data loss event
  (not just downtime) loses anything queued but not yet executed. Everything
  in `CELERY_BEAT_SCHEDULE` is idempotent per-occurrence by design
  (`config/settings/base.py`'s header comment) — once Redis and workers are
  back, the next scheduled tick re-evaluates "what's actually due" from
  Postgres, not from whatever was lost in the queue. No manual replay needed.
- `automation.tasks.dispatch_automation_events_task`'s outbox pattern
  (`SELECT ... FOR UPDATE SKIP LOCKED` against `AutomationEvent` rows in
  Postgres, not Redis) means undispatched automation events are NOT lost in
  a Redis outage — they're durably in Postgres already, just not yet picked
  up. Confirm this specifically after recovery: check for an
  `AutomationEvent` backlog rather than assuming "queue drained = fully caught up."

## After recovery

Watch the Celery Beat single-instance service (`ecs.tf`'s `beat` entry) come
back with `desired_count = 1` — never manually start a second Beat task
"to catch up faster." See that service's comment for why.
