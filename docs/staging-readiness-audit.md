# Staging readiness audit — 2026-09-18

Branch: `p0-remediation` (13 commits on top of `6a3ca32`). Nothing pushed —
this repository has **no git remote configured**.

## Verdict

**STAGING READINESS: NO-GO.**

The eleven P0 items on the remediation list are closed, each reproduced before
it was fixed and covered by a regression test. The re-run architecture audit
then found **eight further P0 findings** — including two defects in the
remediation work itself, which are now fixed, and six pre-existing ones that
are not. Three of the remaining six need a product or infrastructure decision
before they can be fixed, so they are reported rather than guessed at.

The verdict turns on one plain fact: **a freshly deployed environment cannot be
brought up, and if it were, no organization could post a single journal.**

---

## Part 1 — the eleven P0 items (all closed)

Every item was reproduced first (a failing test or an observed failure against
the built container), then fixed with the smallest change that holds, then
gated: ruff, bandit, pip-audit, migration drift, the full backend suite,
frontend typecheck/lint/unit tests/production build, and `terraform
fmt`/`validate`/`test`.

| # | Item | What was actually wrong | Evidence it is closed |
|---|------|--------------------------|------------------------|
| 1 | Ledger reversal / report correctness | `reverse_journal` marks the original REVERSED and posts a mirror as POSTED. Every ledger figure filtered `status=POSTED`, so the original vanished and only its mirror counted: balances showed *minus* the original, and reversing in a later period silently rewrote an earlier period. | 7 regression tests (P&L revenue 800→1000, balance-sheet assets 8200→13200, bank book balance 4500→0). `LEDGER_STATUSES` now used by every ledger read. `67b915b` |
| 2 | Commit deployment/frontend/infra artifacts | The entire frontend, Terraform stack, Dockerfile and runbooks existed only on one disk; the 62 tracked files already modified alongside them imported those untracked modules. | Committed as-is after a secret scan. CI added for frontend and Terraform — which immediately caught a real defect: `package-lock.json` was out of sync with `package.json`, so every clean `npm ci` failed. `0da73a1`, `2830118` |
| 3 | Celery TLS Redis | Production `REDIS_URL` is `rediss://`. Celery's result backend raises `ValueError` on it without `ssl_cert_reqs` (every `.delay()`), and kombu silently connected with `CERT_NONE` — no certificate verification. | Verified end to end against a TLS-only Redis with a local CA: round trip and worker ping succeed with `CERT_REQUIRED`; with the CA untrusted the connection is refused. `1b3da2a` |
| 4 | Task routing, late acks, limits, sweepers | No task was routed anywhere: all 12 landed on the default queue while the dedicated workers idled. Tasks acked before running (a killed container lost the work), no time limits, no visibility timeout sized for late acks. | Real-broker check: 12 tasks now split critical 4 / celery 5 / heavy 2 / ai 1. Policy test fails CI on an unrouted task, an unconsumed queue, or a task without limits. `3fba9ee` |
| 5 | Automation retry boundaries / on_commit dispatch | The retry marker was raised *inside* `tenant_context()`'s transaction, so every transient failure rolled back the attempt's own bookkeeping and repeated its side effects (webhooks fired again per retry). Run/retry endpoints and `request_ocr` enqueued inside the request transaction, so a worker could see pre-commit state and skip the work forever. | Regression tests: succeeded step now runs once, failing step stops at its 5-attempt budget, execution ends PARTIAL; `delay` is not called until commit. `161d37e` |
| 6 | API connection budget | `CONN_MAX_AGE=60` under ASGI (Django's docs: disable persistent connections under ASGI), no concurrency limit per process, and `statement_timeout`/`idle_in_transaction_session_timeout` both `0`. | Verified in the built image: with a limit of 2, two idle connections make the next request 503 and it recovers; a real connection reports `30s` / `2min` / `CONN_MAX_AGE=0`. Budget documented and the RDS alarm resized to match. `7b26b1b` |
| 7 | Health-check redesign | Worse than "wrong endpoint": ALB and ECS probes carry the task IP or `127.0.0.1` as Host, and production `ALLOWED_HOSTS` is the domain — **both probes got 400**, so every API task would have been killed in a loop. The ALB also probed readiness, so one Redis blip would fail every target at once. | In the built image both probes now return 200 (400 before), POST and other paths still 400, container reports healthy. ALB probes liveness; circuit breaker + rollback + grace period + deployment-failure alert added. `d2cdb67` |
| 8 | WAF request-body handling and logging | An ALB web ACL inspects only the first 8 KB of a body, and the managed `SizeRestrictions_BODY` rule **blocks** anything larger — document uploads, bank statement imports and large invoices were all rejected outright. Nothing was logged. | Size rule switched to count; oversize bodies still blocked where no credentials are needed (`/api/v1/auth/*`, `/admin/*`), ahead of the managed groups. Logging to `aws-waf-logs-*` with Authorization and Cookie redacted. `722be4e` |
| 9 | Reject unsupported FX | Documents carried a currency and an exchange rate, but posting never passed that rate to the journal: a USD 1,000 invoice at 83.10 posted **1,000** in base currency. | Refused at the ledger (so nothing can reach the books) and at every document/party service. 10 regression tests. `b10e97a` |
| 10 | Remote Terraform state + locking | The backend block was commented out, so state lived on whoever ran apply; the commented block also still described deprecated DynamoDB locking. | S3 backend with native lockfiles, per-environment partial config, bootstrap module. `terraform init` without a backend config now **fails** instead of silently using local state; CI asserts it. `131d48c` |
| 11 | Trusted client IP / BFF / rate limits | DRF keyed throttles on the entire client-written `X-Forwarded-For`: rotating one byte gave a fresh bucket, so sign-in throttling never engaged. And every browser user shared the BFF's single bucket — twenty failed sign-ins anywhere locked out everyone. | Single client-identity resolver (proxy-count from the right, plus a secret-authenticated BFF assertion). Tests show the forged-prefix rotation now hits 429 and two BFF users no longer share a bucket. `4937462` |

Suite size over the work: 1601 → 1663 backend tests, all passing, plus 288
frontend unit tests and 7 Terraform plan tests.

---

## Part 2 — findings from the re-run audit

Six parallel reviews (accounting, tenant isolation/security, async/failure,
frontend/BFF, infrastructure/deploy, observability/performance). Every finding
below was re-verified directly against the code before being listed.

### P0-A — introduced by the remediation work, now fixed

**A1. The recovery sweeper could never give up.** Its budget was
`attempt_count`, which `run_execution` increments *inside* the run's own
transaction. Any exception that escapes the task — a statement timeout, a lost
connection, or a `KeyError` from `allowed_fields(trigger_type)[field]` when a
rule's trigger is edited while an execution is pending — rolls that increment
back. Proven against the pre-fix code: after a failed run the row read
`{status: pending, attempt_count: 0, error_summary: ''}`. Nothing durable
survived, so the sweeper re-enqueued the same execution every 10 minutes
forever, invisibly. Fixed: the budget is now `recovery_attempts`, incremented
by the sweeper in its own committed transaction, and a failed attempt records
itself on the execution from a fresh transaction.

**A2. The OCR sweeper had no budget at all.** A document that could not be
processed was re-enqueued every 10 minutes indefinitely, re-fetching the file
from storage and re-invoking the paid OCR provider each cycle. Fixed:
`Document.ocr_recovery_attempts` against `OCR_RECOVERY_MAX_ATTEMPTS`, reset by
a fresh `request_ocr`, failing visibly (`ocr_recovery_exhausted`) past it.

### P0-B — pre-existing, still open

**B1. No organization can post anything: `FiscalYear` has no creation path.**
`post_journal` requires a fiscal year covering the posting date. `FiscalYear`
has no serializer, no view, no admin registration and no management command —
only the model, the lookup service and test fixtures. After signing up, every
invoice, bill, expense and journal fails with `fiscal_year_not_found`. The
period-close control is equally unreachable. *Decision needed: where fiscal
years are created (onboarding step, settings screen, or seeded on organization
creation).*

**B2. Refresh tokens can never be revoked.** `BLACKLIST_AFTER_ROTATION` is on,
but `rest_framework_simplejwt.token_blacklist` is not in `INSTALLED_APPS`, so
`blacklist()` does not exist and SimpleJWT swallows the resulting
`AttributeError`. The frontend's entire sign-out is "call refresh once so the
old token is blacklisted" — it is not. A captured refresh token stays valid for
its full 7 days; sign-out and password change do not stop it. Needs the app,
its migration, and a real logout endpoint.

**B3. A staging environment cannot be brought up.** The mandatory one-time
bootstrap (`CREATE EXTENSION vector`, `CREATE ROLE easybook_app`) has no
executable path: no bastion, `enable_execute_command` appears nowhere in the
Terraform, the task role has no `ssmmessages:*`, the image has no `psql`, and
no principal in the VPC may read the RDS master secret. Without the app role,
all six services fail authentication and crash-loop. *Decision needed: ECS Exec
vs a one-off bootstrap task vs a bastion.*

**B4. No image ever reaches ECR, and the first apply is circular.** CI builds
and scans the image but never pushes; there is no OIDC provider or CI role to
push with. `container_image` is required while the repository is created by the
same apply. *Decision needed: CI deploy identity and a documented two-phase
first apply.*

**B5. With the documented defaults the environment serves nothing.** With no
ACM certificate there is no HTTPS listener, but `SECURE_SSL_REDIRECT` is on and
the HTTP listener stamps `X-Forwarded-Proto: http`, so every non-health request
301s to a port nothing listens on. `DJANGO_ALLOWED_HOSTS` is set to
`var.domain_name`, so the ALB's own DNS name cannot be used either. The README
claims plain-HTTP smoke testing works; it does not. *Decision needed: domain
and certificate, or an explicit HTTP-only staging mode.*

**B6. Nothing that happens in staging can be diagnosed.** The JSON log
formatter emits only timestamp/level/logger/message, discarding every
`extra={...}` payload the code carefully builds (AI errors, sweeper counts,
step failures). No logging filter attaches `request_id`, so the correlation id
shown to users appears in no log line. The BFF logs nothing at all and mints
fresh ids for its own error responses. There are no application metrics, and a
worker service at zero running tasks raises no alarm.

**B7. Any client can 500 every audited financial mutation.** `RequestIDMiddleware`
takes `X-Request-ID` verbatim and unbounded; `AuditLog.request_id` is
`varchar(64)`; the BFF forwards the header from the browser. A 65-character
header makes the audit insert fail inside `ATOMIC_REQUESTS`, rolling back the
whole mutation. One-line fix (truncate, as the AI telemetry path already does),
listed here rather than fixed because it was outside the agreed scope.

**B8. A posted journal's lines can be replaced.** `replace_draft_lines` checks
the status without locking the journal and deletes lines via a queryset (which
bypasses `JournalLine.delete()`'s guard), and it never writes the header, so
the immutability guard in `JournalEntry.save()` never fires. A `PATCH` racing a
`POST /post` can rewrite the lines of a posted, numbered journal. Verified by
code inspection; a concurrency test like the existing post/post one would
confirm it.

### P1 (selected — full detail in the review notes)

- The BFF path guard is bypassable with double-encoded dot segments:
  `/api/bff/.%252e/.%252e/admin` resolves to `http://api-host/admin/` with the
  user's bearer token attached. Verified by running the route's own logic.
  Bounded (host fixed, cookies not forwarded) but the stated containment
  boundary is broken.
- Session cookies are `Secure` only when `SESSION_COOKIE_SECURE=1`, which
  nothing in the repo sets, and there is no frontend deployment or startup
  preflight to set it. The same gap makes the new client-IP forwarding inert
  until the frontend's own env vars are set.
- The execution role can read the RDS **master** secret, which no container
  references.
- No `stopTimeout`: Celery workers are SIGKILLed 30s into a task on every
  deploy; with late acks the work returns only after the 2-hour visibility
  timeout.
- Org-iterating beat tasks are unbounded under a 300s soft limit with no
  ordering, so at scale the tail of the organization list is systematically
  starved — including the automation outbox dispatcher.
- Withholding/reverse-charge documents make AR/AP selectors disagree with the
  posted ledger (not reachable over HTTP today: those fields are on no
  serializer).
- Read-path scalability: no index can restrict a balance query by date
  (`JournalLine` has neither `posting_date` nor `status`); AR/AP ageing is
  ~7 queries per outstanding invoice; the account ledger endpoint is
  unpaginated and defaults to all time; CSV export builds the file three times
  in memory.
- `/api/v1/auth/refresh/` is not on the `auth` throttle scope; the member
  directory has no permission gate; a malformed `X-Organization-Id` returns 500
  instead of the error envelope.
- Several runbook steps do not match the code (the Redis-outage runbook expects
  the ALB to drain on a readiness failure, which it no longer does; the deploy
  runbook watches the readiness endpoint and omits `DB_STATEMENT_TIMEOUT_MS=0`
  for migrations).
- `tests/load/api-smoke.js` logs in once globally and shares one token across
  all virtual users, so it measures the rate limiter rather than the system.

### P2

Recorded in the review notes: rounding-policy inconsistencies, GSTR-3B
double-counting reverse-charge ITC and GSTR-1 B2C-large having no threshold
(both compliance-report-only), inventory journal skipped when accounts net to
zero, missing VPC endpoints/flow logs/ALB access logs, no autoscaling, S3
bucket name without a suffix, secrets without a recovery window, and the stale
`recharts` dependency.

---

## What "GO" would require

1. B1 and B2 — an organization must be able to post, and a session must be
   revocable.
2. B3, B4 and B5 — a decision on the bootstrap path, a CI deploy identity, and
   TLS/domain, so the environment can actually be created and reached.
3. B6 and B7 — structured logs that keep their fields, a request id on every
   record, and a bounded `X-Request-ID`.
4. B8 — lock the journal in `replace_draft_lines`, with a concurrency test.

Items 1, 3 and 4 are mechanical. Item 2 needs product/infrastructure decisions
that are not mine to make.
