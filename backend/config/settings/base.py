"""
Base Django settings shared by all environments.
Environment-specific overrides live in dev.py / production.py / test.py.
"""

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
        "HOST": env("DB_HOST", default="localhost"),
        "PORT": env("DB_PORT", default="5432"),
        # Every request is wrapped in a transaction so that PostgreSQL RLS's
        # SET LOCAL tenant GUC (see core.tenancy) stays scoped to that request
        # and financial mutations remain all-or-nothing. See backend/core/CLAUDE.md.
        "ATOMIC_REQUESTS": True,
        "CONN_MAX_AGE": env.int("DB_CONN_MAX_AGE", default=60),
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

REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=REDIS_URL)
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE

# --- Automation (Phase 11) ---------------------------------------------------
# Maximum causation chain depth an automation-triggered domain event may
# reach (phase sections 49-51). Automation A -> mutation -> event -> rule B
# -> mutation -> event -> rule A again is stopped here rather than looping
# forever; exceeding it fails that step closed (category "safety_limit").
AUTOMATION_MAX_DEPTH = env.int("AUTOMATION_MAX_DEPTH", default=5)
# "http" (real urllib POST) or "fake" (deterministic, no network I/O —
# tests must never call a real external endpoint, phase section 90).
AUTOMATION_WEBHOOK_SENDER_BACKEND = env("AUTOMATION_WEBHOOK_SENDER_BACKEND", default="http")

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
    "DEFAULT_THROTTLE_CLASSES": (
        "rest_framework.throttling.ScopedRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "auth": env("THROTTLE_AUTH", default="20/min"),
        "burst": env("THROTTLE_BURST", default="60/min"),
    },
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
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
