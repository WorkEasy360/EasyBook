# AI provider outage

Only relevant once a real LLM/embedding provider is actually configured
(phase 12 section 67 — production must never run Ask Books on the `fake`
providers; `backend/ai/config.py::validate_ai_configuration` already refuses
to boot that combination). Until then, this runbook is dormant.

## Blast radius

Ask Books (`/api/v1/ai/...`) only. `ai/CLAUDE.md`'s module map places `ai`
above every accounting/sales/purchases/reports module and nothing below it
imports it — a real vendor outage cannot affect posting, reports, invoicing,
or any other core accounting flow. If anything outside `/api/v1/ai/` breaks
during an "AI provider outage," the AI provider is not the actual cause.

## Symptoms

- Elevated latency/errors specifically on Ask Books endpoints.
- `ai.providers.retry`'s retry/backoff logic (`AI_RETRY_MAX_ATTEMPTS`,
  `AI_RETRY_BACKOFF_SECONDS` — `config/settings/base.py`) exhausting and
  surfacing a clean "unavailable" response rather than hanging — confirm this
  is actually happening (bounded retries, not an unbounded hang) before
  assuming a code bug rather than a real vendor outage.

## Actions

1. Check the provider's own status page before assuming anything on this
   side is broken.
2. If sustained: consider temporarily setting `AI_ASK_BOOKS_ENABLED=False`
   (an ECS task-definition environment variable change + redeploy) so the
   feature cleanly reports unavailable rather than every request timing out
   through the full retry budget (`AI_REQUEST_DEADLINE_SECONDS`, default 90s
   — that's 90 seconds of held connection per request during a real outage
   if left enabled).
3. Never set `AI_ALLOW_FAKE_PROVIDERS=True` in production as a workaround —
   that flag exists specifically so a deterministic test double can never
   silently answer a real user's financial question (`ai/config.py`'s own
   validation error message says this outright).

## Recovery

Re-enable once the provider's status page confirms recovery; there is no
queued/lost work to replay — Ask Books requests are synchronous, not queued
(unlike `index_document_task`/`purge_expired_ai_data`, which are ordinary
Celery tasks and follow [redis-celery-outage.md](redis-celery-outage.md) if
Redis itself is what's down instead).
