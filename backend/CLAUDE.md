# BACKEND

PURPOSE
Django + DRF modular monolith. One project (`config`), one app per bounded domain module at this directory level (not nested under `apps/`).

OWNS
- `config/` — settings package (base/dev/production/test), URL root, Celery app.
- Cross-app conventions: tenant isolation, error envelope, pagination, auth.

RUNNING LOCALLY
- `infrastructure/docker-compose.yml` provides Postgres 16 with pgvector (host port 5472) and Redis (host port 6390) — remapped from the defaults because other local projects already hold 5432/6379 on this machine.
- `.venv/` is the project's virtualenv (Python 3.10). Activate or call `.venv/Scripts/python.exe` directly on Windows.
- `make run` starts the dev server on **8001** (`RUN_PORT` overrides it), not
  Django's default 8000 — another local project already holds 8000, the same
  collision that moved Postgres/Redis to 5472/6390. The committed
  `frontend/.env.example` / BFF fallback point at 8001 to match.
- Start docker-compose first. Both services carry `restart: unless-stopped`
  so they come back on their own after Docker Desktop restarts or the machine
  reboots — without it Docker's default (`no`) leaves them exited (255) for
  good, which is what made the backend look like it "stopped by itself".
  If Postgres is genuinely down, the DB connect now fails in
  `DB_CONNECT_TIMEOUT_SECONDS` (default 10) with an `OperationalError`
  instead of hanging forever.
- `DB_HOST` / `REDIS_URL` must use `127.0.0.1`, never `localhost`. On Windows
  `localhost` resolves to `::1` first and Docker publishes 5472/6390 on IPv4
  only, so every new connection stalls ~2.0s before falling back (measured:
  `localhost:5472` 2.05s vs `127.0.0.1:5472` 0.007s). Django opens a fresh DB
  connection per request (`CONN_MAX_AGE=0`) plus Redis for throttling and the
  Celery broker, so `/api/v1/health/` took 12.2s and every page 12–25s
  (0.045s after the fix). Restart `runserver` after editing `.env` — the
  autoreloader watches `.py` files only.
- `manage.py` defaults to `config.settings.dev`; tests use `config.settings.test` (`manage.py test --settings=config.settings.test`).
- The Postgres role Django connects as (`easybook_app`, see `.env`) is deliberately NOT the superuser created by the official Postgres image — superusers always bypass Row Level Security. Never point `DB_USER` at the `easybook` superuser role.

DEPENDENCIES
Django 5.2 LTS, DRF 3.17, psycopg 3, djangorestframework-simplejwt, django-environ, django-cors-headers, celery, redis, boto3 (S3-compatible document storage — Phase 9, see `documents/CLAUDE.md`; lazy-imported so dev/test never require it at runtime), gunicorn + uvicorn + uvicorn-worker (production ASGI server — phase 12 slice 4/6, `Dockerfile`'s CMD; Django's own currently-documented recipe is `gunicorn config.asgi:application -k uvicorn_worker.UvicornWorker`, not the older `uvicorn.workers.UvicornWorker` which moved out of uvicorn core). The Dockerfile runs a subclass, `config.asgi_worker.BoundedUvicornWorker`, which passes uvicorn `limit_concurrency` (`ASGI_LIMIT_CONCURRENCY`, default 10 per process) — the stock class passes none, and under ASGI every concurrent request holds its own DB connection. `CONN_MAX_AGE` is fixed at 0 (Django: disable persistent connections under ASGI) and every connection carries `statement_timeout`/`idle_in_transaction_session_timeout` (`DB_STATEMENT_TIMEOUT_MS`, `DB_IDLE_IN_TRANSACTION_TIMEOUT_MS`); the connection budget arithmetic lives in `config/asgi_worker.py`. Pinned in `requirements.txt`.

PRODUCTION CONTAINER (phase 12 slices 2/4/6)
`Dockerfile` — one multi-stage image for every ECS service (api, worker-critical, worker-default, worker-heavy, worker-ai, beat); non-api services override `command` in their own ECS task definition to run `celery -A config worker`/`celery -A config beat` against the same image. `.dockerignore` keeps `.venv/`/`.env`/`staticfiles/` out of the build context. Base image pinned by digest (see the Dockerfile's own header comment for the verification date/source); the runtime stage patches OS packages (`apt-get upgrade`) and the base image's system-wide pip/setuptools/wheel and deletes `ensurepip`'s bundled offline wheels — all found by actually running `trivy image` against the built image, not by inspection (see `.trivyignore` for the two remaining suppressions, both traced to pip's own current release, not this repo's code). `config/settings/production.py` fails closed (`config/settings/preflight.py`) without real `DJANGO_SECRET_KEY`/`DJANGO_ALLOWED_HOSTS`/`CORS_ALLOWED_ORIGINS`/`CSRF_TRUSTED_ORIGINS`/`DOCUMENT_STORAGE_BACKEND=s3` — the Dockerfile's own `collectstatic` build step deliberately uses `config.settings.dev` instead, since baking real production secrets into an image layer would defeat the point of externalizing them. `SECURE_REDIRECT_EXEMPT` exempts `/api/v1/health/` and `/api/v1/health/live/` from `SECURE_SSL_REDIRECT` — found by actually running the built image behind plain HTTP (as an ALB target-group health check does): without the exemption the probe gets a 301 to https on the same port, which the container can't serve, so the health check's own TLS handshake fails. `.github/workflows/backend-ci.yml`'s `container` job builds the image and scans it with Trivy (pinned past its March 2026 supply-chain compromise — see the workflow's own comment); it does not yet push anywhere, since no registry/environment exists (phase 12 slice 3/5).

CELERY BEAT (phase 12 slice 6)
`CELERY_BEAT_SCHEDULE` (`config/settings/base.py`) wires the automation scheduled-rule scanner/outbox dispatcher and the recurring invoice/bill/expense generators and the AI retention purge into plain interval schedules (env-overridable `BEAT_*_SECONDS`) — no `django-celery-beat` dependency needed since none of them need crontab precision, only "run at least this often" (every task involved is idempotent per occurrence). `config/tests/test_celery_beat_schedule.py` and `config/tests/test_celery_autodiscovery.py` both exist because a typo'd task path, or a task module Celery's `autodiscover_tasks()` never actually imports, silently produces a schedule entry that a real worker rejects as unregistered — see `ai/tasks.py` and `ai/CLAUDE.md` for exactly that failure mode (found only by building the Docker image and running a real `celery -A config worker` against it, since `CELERY_TASK_ALWAYS_EAGER=True` in tests never needs a task registered at all).

CELERY EXECUTION POLICY (P0 remediation)
`config/settings/base.py` routes every task explicitly (`CELERY_TASK_ROUTES`) to a queue one Terraform worker consumes (critical/celery/heavy/ai), acknowledges late with prefetch 1, gives every task a soft+hard limit (`CELERY_TASK_ANNOTATIONS`, listed per task on purpose) and sets the Redis `visibility_timeout` above the longest hard limit plus retry countdown. Work whose message is lost anyway is recovered by beat-scheduled sweepers (`automation.tasks.recover_stalled_executions_task`, `documents.tasks.recover_stalled_ocr_task`). `config/tests/test_celery_task_policy.py` fails on a new task that is unrouted, routed to an unconsumed queue, or unlimited.

INVARIANTS
- See root `CLAUDE.md` global rules — Decimal for money, tenant isolation fail-closed, audit every mutation.
- Every request runs inside one DB transaction (`ATOMIC_REQUESTS = True`) so `SET LOCAL` tenant GUCs (core/CLAUDE.md) stay scoped correctly.

QUALITY GATE
`make check` runs what CI runs, in the order that fails fastest: `lint` (ruff)
-> `security` (bandit) -> `audit` (pip-audit) -> `migrations` (drift check)
-> `test`. Config lives in `pyproject.toml`; dev tooling is pinned in
`requirements-dev.txt`, separate from `requirements.txt` so production images
never install a linter. CI is `.github/workflows/backend-ci.yml`, with actions
pinned to commit SHAs rather than tags.

Deliberately NOT adopted yet, each with a reason in `requirements-dev.txt`:
pytest (the suite depends on Django's TransactionTestCase semantics), mypy
(needs django-stubs and an annotation pass — its own slice), semgrep
(overlaps bandit/ruff for this codebase's shape). `ruff format` is configured
but NOT enforced: adopting it reformats ~120 files, which belongs in its own
commit rather than mixed into feature work.

DJANGO 6.0 READINESS
`models.CheckConstraint` takes `condition=`, not `check=`. The `check` keyword
was deprecated in Django 5.1 and is REMOVED in 6.0; all 36 occurrences were
renamed in the Phase 6 slice. The rename is source-only — Django's
`deconstruct()` already emitted `condition`, so no migration was generated and
none is needed. New constraints must use `condition=`; `python -W
error::DeprecationWarning -c "import django; django.setup()"` catches a
regression.

Bandit excludes `tests/` while ruff does not. Skipping B105/B106/B107 globally
to quiet fixture passwords would blind bandit to a real hardcoded credential
in shipping code, which is the one thing it is here for.

TESTS
`manage.py test --settings=config.settings.test`. Requires the Postgres container running (RLS tests execute real SQL against Postgres, not SQLite — do not switch the test DB engine).

MODULE MAP
`core` (tenancy, shared money/enum/recurrence helpers) -> `accounts`/`authz`/`audit` -> `accounting` -> `tax` (GST engine: state master, determination, component split — see `tax/CLAUDE.md`) -> `items`/`inventory` -> `sales`, `purchases` -> `projects`, `banking`, `compliance` (registers, GSTR-1/3B, e-Invoice/e-Way Bill — see `compliance/CLAUDE.md`) -> `reports` (read-only reporting layer over every module above — see `reports/CLAUDE.md`) -> `documents` (tenant-scoped document management + OCR foundation — see `documents/CLAUDE.md`; sits at the same top layer as `reports`, importing lazily across modules only to validate a `DocumentLink`'s target) -> `ai` (Ask Books: read-only tools over existing selectors + RAG over `documents`; see `ai/CLAUDE.md`) -> `automation` (tenant-safe event/schedule-driven workflow engine — trigger/condition/action, calls `ai.orchestration.assist` for draft actions; the very top layer — nothing imports it; see `automation/CLAUDE.md`). Modules below `automation` never import it either — it listens to their existing `post_save` signals instead (`automation/receivers.py`), the same inversion `ai/rag/signals.py` uses for `documents`. `ai` requires the pgvector extension, created by a superuser before `migrate` (backend role cannot).
Peer modules at the same level (sales and purchases; projects, banking and compliance) must not import each other: anything two of them need is promoted to a lower layer instead (see `core/CLAUDE.md`, and `tax/services/party.py` for the same pattern one layer up — shared between `sales.Customer` and `purchases.Vendor` tax fields). `projects`, `banking` and `compliance` sit above `sales`/`purchases` and may import them; the reverse is forbidden, which is why `purchases.Expense.project` is a string FK reference. `tax` sits below `items` (`Item.tax_rate` FKs into it) and below `sales`/`purchases` and must NEVER import either — it is called, it does not call out.

READ FIRST
- `config/settings/base.py`
- `core/tenancy.py`, `core/models.py`, `core/rls.py`
- The specific app's `CLAUDE.md` you're working in

DO NOT READ BY DEFAULT
- `.venv/`, `staticfiles/`, `**/migrations/*` (except when a migration is the actual subject of the change)
