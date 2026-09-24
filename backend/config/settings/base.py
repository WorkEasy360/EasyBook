"""
Base Django settings shared by all environments.
Environment-specific overrides live in dev.py / production.py / test.py.
"""

import ssl
from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-change-me-in-env")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "rest_framework",
    # Makes refresh tokens revocable: BLACKLIST_AFTER_ROTATION and the logout
    # endpoint (accounts.views.LogoutView) both write here. Without the app,
    # SimpleJWT's blacklist() does not exist and rotation silently kept every
    # old refresh token valid for its full lifetime.
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "core",
    "accounts",
    "authz",
    "audit",
    "documents",
    "accounting",
    "tax",
    "items",
    "inventory",
    "sales",
    "purchases",
    "projects",
    "banking",
    "compliance",
    "reports",
    # ai sits above every module it reads (reports, documents, sales, ...) —
    # nothing below it may import it. See ai/CLAUDE.md.
    "ai",
    # automation sits above ai (it calls ai.orchestration.assist for draft
    # actions) — the top of the module map. See automation/CLAUDE.md.
    "automation",
]

MIDDLEWARE = [
    # First, before host validation (CommonMiddleware) and the HTTPS redirect
    # (SecurityMiddleware): ALB and ECS probes carry neither the public Host
    # nor X-Forwarded-Proto. See core/middleware.py.
    "core.middleware.LivenessProbeMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.RequestIDMiddleware",
    "core.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

AUTH_USER_MODEL = "accounts.User"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME", default="easybook"),
        "USER": env("DB_USER", default="easybook"),
        "PASSWORD": env("DB_PASSWORD", default="easybook"),
        # 127.0.0.1, not "localhost": on Windows the latter tries ::1 first and
        # stalls ~2s per connection against an IPv4-only Docker port (.env.example).
        "HOST": env("DB_HOST", default="127.0.0.1"),
        "PORT": env("DB_PORT", default="5432"),
        # Every request is wrapped in a transaction so that PostgreSQL RLS's
        # SET LOCAL tenant GUC (see core.tenancy) stays scoped to that request
        # and financial mutations remain all-or-nothing. See backend/core/CLAUDE.md.
        "ATOMIC_REQUESTS": True,
        # Never persistent, and deliberately not env-overridable. Django's docs:
        # "When using ASGI, persistent connections should be disabled" — each
        # request's sync code runs in its own thread with its own connection,
        # and one kept open in a finished request's thread is never reused, so
        # connections accumulated toward RDS max_connections. The API process's
        # concurrency limit (config/asgi_worker.py) is what bounds how many
        # connections exist at once.
        "CONN_MAX_AGE": 0,
        "OPTIONS": {
            # Fail fast when nothing is listening. libpq's default is no
            # connect timeout at all, so a stopped Postgres (a dev machine
            # whose containers are down, an RDS failover) makes every request
            # hang forever with no error — the thread, and under ASGI the
            # concurrency slot, is held until something else kills it. Bounded,
            # a connect failure surfaces as OperationalError in seconds.
            "connect_timeout": env.int("DB_CONNECT_TIMEOUT_SECONDS", default=10),
            # Server-side guards on every connection (psycopg passes libpq
            # `options`). A runaway statement is cancelled instead of holding its
            # connection and locks; a transaction left idle — a request stalled
            # mid-ATOMIC_REQUESTS — is terminated instead of pinning a connection.
            # The idle limit must exceed the longest external call made inside a
            # transaction (AI_LLM_TIMEOUT_SECONDS, S3 uploads). Workers raise the
            # statement limit via ECS env (infrastructure/terraform/ecs.tf); a
            # one-off `migrate` task should set DB_STATEMENT_TIMEOUT_MS=0.
            "options": (
                f"-c statement_timeout={env.int('DB_STATEMENT_TIMEOUT_MS', default=30000)} "
                f"-c idle_in_transaction_session_timeout={env.int('DB_IDLE_IN_TRANSACTION_TIMEOUT_MS', default=120000)}"
            ),
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Caching / Celery -------------------------------------------------------

# 127.0.0.1 for the same reason as DB_HOST above.
REDIS_URL = env("REDIS_URL", default="redis://127.0.0.1:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=REDIS_URL)
# ElastiCache runs with transit encryption, so production URLs are rediss://
# (infrastructure/terraform/ecs.tf). Neither Celery library is safe with a bare
# rediss:// URL: the Redis result backend raises ValueError without an explicit
# ssl_cert_reqs, and kombu's broker transport silently falls back to CERT_NONE
# (no certificate verification). ElastiCache serves publicly trusted ACM
# certificates, so the system CA bundle verifies them. The TLS options are only
# set for rediss:// — Celery rejects them alongside a plain redis:// URL.
# See config/tests/test_celery_redis_tls.py.
_REDIS_TLS_OPTIONS = {"ssl_cert_reqs": ssl.CERT_REQUIRED}
CELERY_BROKER_USE_SSL = _REDIS_TLS_OPTIONS if CELERY_BROKER_URL.startswith("rediss://") else None
CELERY_REDIS_BACKEND_USE_SSL = _REDIS_TLS_OPTIONS if CELERY_RESULT_BACKEND.startswith("rediss://") else None
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE

# Execution policy. config/tests/test_celery_task_policy.py fails if a task is
# left unrouted, routed to a queue no worker in infrastructure/terraform
# consumes, or left without time limits.
#
# Routing: one queue per worker service (worker-critical: critical,
# worker-default: celery, worker-heavy: heavy, worker-ai: ai). Unrouted, every
# task landed on `celery` and the dedicated workers sat idle.
CELERY_TASK_DEFAULT_QUEUE = "celery"
CELERY_TASK_ROUTES = {
    # Financial-document generation and the automation outbox: short, and must
    # never wait behind OCR or AI work.
    "sales.tasks.generate_recurring_invoices_task": {"queue": "critical"},
    "purchases.tasks.generate_recurring_bills_task": {"queue": "critical"},
    "purchases.tasks.generate_recurring_expenses_task": {"queue": "critical"},
    "automation.tasks.dispatch_automation_events_task": {"queue": "critical"},
    # Automation runs (may wait on outbound webhooks), schedulers, sweepers.
    "automation.tasks.run_execution_task": {"queue": "celery"},
    "automation.tasks.run_due_schedules_task": {"queue": "celery"},
    "automation.tasks.run_due_scans_task": {"queue": "celery"},
    "automation.tasks.recover_stalled_executions_task": {"queue": "celery"},
    "documents.tasks.recover_stalled_ocr_task": {"queue": "celery"},
    # Extraction and bulk retention deletes.
    "documents.tasks.run_ocr_task": {"queue": "heavy"},
    "ai.rag.tasks.purge_expired_ai_data": {"queue": "heavy"},
    # Embedding calls to the AI provider.
    "ai.rag.tasks.index_document_task": {"queue": "ai"},
}

# Acknowledge after the task runs, one message per process at a time, so a
# worker container killed mid-task (deploy, scale-in, OOM) leaves the message
# to be redelivered instead of losing it. Every task is idempotent per
# occurrence. task_reject_on_worker_lost stays at its default (off): Celery
# warns it can loop a poison message; work lost that way is picked up by the
# recovery sweepers below instead.
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
# With late acks, a lost broker connection otherwise redelivers tasks that are
# still running elsewhere (Celery documents this default flipping in 6.0).
CELERY_WORKER_CANCEL_LONG_RUNNING_TASKS_ON_CONNECTION_LOSS = True

# Every task gets a soft limit (SoftTimeLimitExceeded, catchable) and a hard
# limit (process killed and replaced). Listed per task, not via "*", so a new
# task without a deliberate limit fails the policy test.
_TASK_LIMITS_SHORT = {"soft_time_limit": 300, "time_limit": 330}
_TASK_LIMITS_LONG = {"soft_time_limit": 1500, "time_limit": 1560}
CELERY_TASK_ANNOTATIONS = {
    "automation.tasks.dispatch_automation_events_task": _TASK_LIMITS_SHORT,
    "automation.tasks.run_execution_task": _TASK_LIMITS_SHORT,
    "automation.tasks.run_due_schedules_task": _TASK_LIMITS_SHORT,
    "automation.tasks.run_due_scans_task": _TASK_LIMITS_SHORT,
    "automation.tasks.recover_stalled_executions_task": _TASK_LIMITS_SHORT,
    "documents.tasks.recover_stalled_ocr_task": _TASK_LIMITS_SHORT,
    "sales.tasks.generate_recurring_invoices_task": _TASK_LIMITS_LONG,
    "purchases.tasks.generate_recurring_bills_task": _TASK_LIMITS_LONG,
    "purchases.tasks.generate_recurring_expenses_task": _TASK_LIMITS_LONG,
    "documents.tasks.run_ocr_task": _TASK_LIMITS_LONG,
    "ai.rag.tasks.index_document_task": _TASK_LIMITS_LONG,
    "ai.rag.tasks.purge_expired_ai_data": _TASK_LIMITS_LONG,
}

# Redis redelivers an unacknowledged message once this passes, and an ETA/
# countdown retry stays unacknowledged while it waits — so this must outlast
# the longest hard limit plus the longest retry countdown
# (automation run_execution_task: retry_backoff_max=3600), or work runs twice.
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 7200}

# Every entry wraps an already-idempotent, already tenant-safe task — each
# one iterates active organizations and opens its own core.tenancy.tenant_context()
# per org internally (see automation/tasks.py, sales/tasks.py, purchases/tasks.py,
# ai/rag/tasks.py); there is no request middleware inside Celery to do this for
# them (phase 12 section 19). Interval (not crontab) schedules are deliberate:
# these tasks only need "run at least this often", never exact wall-clock
# timing, and calling one again before its previous run's work was due is
# always safe by construction — so a slow run merely delays the next tick
# rather than double-processing anything.
CELERY_BEAT_SCHEDULE = {
    "automation-dispatch-events": {
        "task": "automation.tasks.dispatch_automation_events_task",
        "schedule": timedelta(seconds=env.int("BEAT_AUTOMATION_DISPATCH_EVENTS_SECONDS", default=60)),
    },
    "automation-run-due-schedules": {
        "task": "automation.tasks.run_due_schedules_task",
        "schedule": timedelta(seconds=env.int("BEAT_AUTOMATION_RUN_DUE_SCHEDULES_SECONDS", default=300)),
    },
    "automation-run-due-scans": {
        "task": "automation.tasks.run_due_scans_task",
        "schedule": timedelta(seconds=env.int("BEAT_AUTOMATION_RUN_DUE_SCANS_SECONDS", default=300)),
    },
    "sales-generate-recurring-invoices": {
        "task": "sales.tasks.generate_recurring_invoices_task",
        "schedule": timedelta(seconds=env.int("BEAT_RECURRING_INVOICES_SECONDS", default=3600)),
    },
    "purchases-generate-recurring-bills": {
        "task": "purchases.tasks.generate_recurring_bills_task",
        "schedule": timedelta(seconds=env.int("BEAT_RECURRING_BILLS_SECONDS", default=3600)),
    },
    "purchases-generate-recurring-expenses": {
        "task": "purchases.tasks.generate_recurring_expenses_task",
        "schedule": timedelta(seconds=env.int("BEAT_RECURRING_EXPENSES_SECONDS", default=3600)),
    },
    "ai-purge-expired-data": {
        "task": "ai.rag.tasks.purge_expired_ai_data",
        "schedule": timedelta(seconds=env.int("BEAT_AI_PURGE_SECONDS", default=86400)),
    },
    # Recovery sweepers: work whose task message was lost (failed enqueue,
    # worker killed mid-run) is otherwise stuck PENDING/QUEUED/RUNNING forever.
    "automation-recover-stalled-executions": {
        "task": "automation.tasks.recover_stalled_executions_task",
        "schedule": timedelta(seconds=env.int("BEAT_AUTOMATION_RECOVERY_SECONDS", default=300)),
    },
    "documents-recover-stalled-ocr": {
        "task": "documents.tasks.recover_stalled_ocr_task",
        "schedule": timedelta(seconds=env.int("BEAT_OCR_RECOVERY_SECONDS", default=300)),
    },
}

# --- Automation (Phase 11) ---------------------------------------------------
# Maximum causation chain depth an automation-triggered domain event may
# reach (phase sections 49-51). Automation A -> mutation -> event -> rule B
# -> mutation -> event -> rule A again is stopped here rather than looping
# forever; exceeding it fails that step closed (category "safety_limit").
AUTOMATION_MAX_DEPTH = env.int("AUTOMATION_MAX_DEPTH", default=5)
# "http" (real urllib POST) or "fake" (deterministic, no network I/O —
# tests must never call a real external endpoint, phase section 90).
AUTOMATION_WEBHOOK_SENDER_BACKEND = env("AUTOMATION_WEBHOOK_SENDER_BACKEND", default="http")

# --- Client identity (core/client_ip.py) ---------------------------------------
# Reverse proxies in front of Django that append to X-Forwarded-For: 1 in AWS
# (the ALB — infrastructure/terraform/ecs.tf), 0 locally where nothing is.
# Never more than actually exist: an extra trusted hop lets a client choose its
# own address.
TRUSTED_PROXY_COUNT = env.int("TRUSTED_PROXY_COUNT", default=0)
# Shared with the Next.js BFF, which forwards the browser's address with it.
# Empty = no forwarded address is trusted (every BFF call is one client).
BFF_PROXY_SECRET = env("BFF_PROXY_SECRET", default="")

# --- Django REST Framework ---------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_RENDERER_CLASSES": (
        "rest_framework.renderers.JSONRenderer",
    ),
    "DEFAULT_PARSER_CLASSES": (
        "rest_framework.parsers.JSONParser",
    ),
    "DEFAULT_PAGINATION_CLASS": "core.pagination.DefaultPagination",
    "PAGE_SIZE": 25,
    "EXCEPTION_HANDLER": "core.exceptions.api_exception_handler",
    # ScopedRateThrottle only protects the handful of views that explicitly
    # set `throttle_scope` (accounts/views.py's login/register) — every other
    # endpoint had NO rate limiting at all until User/AnonRateThrottle were
    # added here as a blanket baseline (phase 12 section 54/64 security
    # review finding). The FailOpen* wrappers (core/throttling.py) exist
    # because Django's built-in Redis cache backend does not swallow
    # connection errors — without them, a Redis outage would 500 every
    # throttled request instead of just temporarily not enforcing limits.
    "DEFAULT_THROTTLE_CLASSES": (
        "core.throttling.FailOpenScopedRateThrottle",
        "core.throttling.FailOpenUserRateThrottle",
        "core.throttling.FailOpenAnonRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "auth": env("THROTTLE_AUTH", default="20/min"),
        "burst": env("THROTTLE_BURST", default="60/min"),
        # Starting points, not load-tested SLOs (phase 12 section 59-60) —
        # tighten/loosen once real traffic gives a baseline.
        "user": env("THROTTLE_USER", default="300/min"),
        "anon": env("THROTTLE_ANON", default="60/min"),
    },
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
    # Kept consistent with core/client_ip.py for anything still using DRF's own
    # get_ident; unset, DRF keys on the entire client-written header.
    "NUM_PROXIES": TRUSTED_PROXY_COUNT,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", default=7)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# --- CORS ---------------------------------------------------------------

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=["http://localhost:3000"])
CORS_ALLOW_CREDENTIALS = True

# --- Security headers ------------------------------------------------------

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

# --- Structured logging ------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "core.logging.JSONFormatter"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "json"},
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
    "loggers": {
        "django": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO"), "propagate": False},
    },
}

# --- Documents / OCR (Phase 9) ----------------------------------------------
# See documents/CLAUDE.md. `local` (FileSystemStorage under private_media/) is
# the dev/test default since no S3-compatible service is provisioned in
# infrastructure/docker-compose.yml yet; production sets DOCUMENT_STORAGE_BACKEND=s3.

DOCUMENT_STORAGE_BACKEND = env("DOCUMENT_STORAGE_BACKEND", default="local")
DOCUMENT_LOCAL_STORAGE_ROOT = env("DOCUMENT_LOCAL_STORAGE_ROOT", default=str(BASE_DIR / "private_media" / "documents"))
DOCUMENT_STORAGE_S3_BUCKET = env("DOCUMENT_STORAGE_S3_BUCKET", default="")
DOCUMENT_STORAGE_S3_ENDPOINT_URL = env("DOCUMENT_STORAGE_S3_ENDPOINT_URL", default="")
DOCUMENT_STORAGE_S3_REGION = env("DOCUMENT_STORAGE_S3_REGION", default="")

# Short-lived by design (phase section 3/21) — never a long-lived or
# permanent link, regardless of backend.
DOCUMENT_SIGNED_URL_TTL_SECONDS = env.int("DOCUMENT_SIGNED_URL_TTL_SECONDS", default=300)

# Per-category ceilings (phase section 5) — deliberately not one blanket
# limit: an image or PDF legitimately needs more room than a CSV/receipt scan.
DOCUMENT_MAX_UPLOAD_SIZES = {
    "default": env.int("DOCUMENT_MAX_UPLOAD_SIZE_DEFAULT", default=10 * 1024 * 1024),
    "image": env.int("DOCUMENT_MAX_UPLOAD_SIZE_IMAGE", default=15 * 1024 * 1024),
    "pdf": env.int("DOCUMENT_MAX_UPLOAD_SIZE_PDF", default=25 * 1024 * 1024),
}

# Malware scan hook (phase section 7) — see documents/services/malware_scan.py
# for why this is a mock scanner and what replacing it for production requires.
DOCUMENT_MALWARE_SCANNER_BACKEND = env("DOCUMENT_MALWARE_SCANNER_BACKEND", default="eicar_mock")

DOCUMENT_OCR_PROVIDER = env("DOCUMENT_OCR_PROVIDER", default="manual")

# OCR confidence thresholds (phase section 14). >= HIGH can prefill without a
# warning; >= LOW but < HIGH shows a warning; below LOW forces NEEDS_REVIEW.
DOCUMENT_OCR_CONFIDENCE_HIGH = env.float("DOCUMENT_OCR_CONFIDENCE_HIGH", default=0.90)
DOCUMENT_OCR_CONFIDENCE_LOW = env.float("DOCUMENT_OCR_CONFIDENCE_LOW", default=0.60)

# --- AI / Ask Books (Phase 10) ---------------------------------------------
# See ai/CLAUDE.md. Only the deterministic `fake` providers are implemented;
# a live vendor provider is added together with a verified implementation.
# ai/config.py::validate_ai_configuration refuses to start with Ask Books
# enabled on a fake provider unless AI_ALLOW_FAKE_PROVIDERS is set, so the
# test double can never silently answer production users.
AI_ASK_BOOKS_ENABLED = env.bool("AI_ASK_BOOKS_ENABLED", default=False)
AI_ALLOW_FAKE_PROVIDERS = env.bool("AI_ALLOW_FAKE_PROVIDERS", default=False)
AI_AUTO_INDEX_DOCUMENTS = env.bool("AI_AUTO_INDEX_DOCUMENTS", default=True)

AI_LLM_PROVIDER = env("AI_LLM_PROVIDER", default="fake")
AI_LLM_MODEL = env("AI_LLM_MODEL", default="fake-llm-1")
AI_LLM_TEMPERATURE = env.float("AI_LLM_TEMPERATURE", default=0.0)
AI_LLM_MAX_OUTPUT_TOKENS = env.int("AI_LLM_MAX_OUTPUT_TOKENS", default=1024)
AI_LLM_TIMEOUT_SECONDS = env.float("AI_LLM_TIMEOUT_SECONDS", default=30.0)

AI_EMBEDDING_PROVIDER = env("AI_EMBEDDING_PROVIDER", default="fake")
AI_EMBEDDING_MODEL = env("AI_EMBEDDING_MODEL", default="fake-hash-embedding-1")
AI_EMBEDDING_DIMENSIONS = env.int("AI_EMBEDDING_DIMENSIONS", default=768)
AI_EMBEDDING_TIMEOUT_SECONDS = env.float("AI_EMBEDDING_TIMEOUT_SECONDS", default=15.0)

AI_RETRY_MAX_ATTEMPTS = env.int("AI_RETRY_MAX_ATTEMPTS", default=3)
AI_RETRY_BACKOFF_SECONDS = env.float("AI_RETRY_BACKOFF_SECONDS", default=0.5)
AI_RETRY_MAX_BACKOFF_SECONDS = env.float("AI_RETRY_MAX_BACKOFF_SECONDS", default=4.0)

# Budgets — every one bounded; there is no "unlimited" setting.
AI_MAX_TOOL_CALLS_PER_REQUEST = env.int("AI_MAX_TOOL_CALLS_PER_REQUEST", default=6)
AI_MAX_LLM_ROUNDS = env.int("AI_MAX_LLM_ROUNDS", default=4)
AI_MAX_TOOL_OUTPUT_CHARS = env.int("AI_MAX_TOOL_OUTPUT_CHARS", default=12000)
AI_MAX_TOOL_ROWS = env.int("AI_MAX_TOOL_ROWS", default=25)
AI_MAX_CONTEXT_CHARS = env.int("AI_MAX_CONTEXT_CHARS", default=48000)
AI_MAX_QUESTION_CHARS = env.int("AI_MAX_QUESTION_CHARS", default=2000)
AI_MAX_RETRIEVED_CHUNKS = env.int("AI_MAX_RETRIEVED_CHUNKS", default=6)
AI_RETRIEVAL_CANDIDATES = env.int("AI_RETRIEVAL_CANDIDATES", default=20)
# Model-specific: 0.15 is calibrated for the fake embedding on ai/evaluations/datasets.py
# (irrelevant matches <= 0.11, relevant >= 0.179). Recalibrate with the evaluation
# suite whenever a real embedding model is configured.
AI_VECTOR_MIN_SIMILARITY = env.float("AI_VECTOR_MIN_SIMILARITY", default=0.15)
AI_RRF_K = env.int("AI_RRF_K", default=60)
AI_REQUEST_DEADLINE_SECONDS = env.float("AI_REQUEST_DEADLINE_SECONDS", default=90.0)

AI_USER_REQUESTS_PER_MINUTE = env.int("AI_USER_REQUESTS_PER_MINUTE", default=10)
AI_ORG_REQUESTS_PER_DAY = env.int("AI_ORG_REQUESTS_PER_DAY", default=1000)
# 0 disables the monthly token cap (request-count limits above still apply).
AI_ORG_MONTHLY_TOKEN_LIMIT = env.int("AI_ORG_MONTHLY_TOKEN_LIMIT", default=0)

AI_CONVERSATION_CONTEXT_MESSAGES = env.int("AI_CONVERSATION_CONTEXT_MESSAGES", default=6)
AI_CONVERSATION_RETENTION_DAYS = env.int("AI_CONVERSATION_RETENTION_DAYS", default=90)
AI_REQUEST_LOG_RETENTION_DAYS = env.int("AI_REQUEST_LOG_RETENTION_DAYS", default=365)

AI_CHUNK_TARGET_CHARS = env.int("AI_CHUNK_TARGET_CHARS", default=1200)
AI_CHUNK_OVERLAP_CHARS = env.int("AI_CHUNK_OVERLAP_CHARS", default=200)
AI_CHUNK_MAX_CHARS = env.int("AI_CHUNK_MAX_CHARS", default=2000)

# Optional, for the usage API's cost ESTIMATE only — never stored, never
# authoritative. JSON: {"<model>": {"input_per_million": "3.00", "output_per_million": "15.00"}}
AI_MODEL_PRICING = env.json("AI_MODEL_PRICING", default={})
