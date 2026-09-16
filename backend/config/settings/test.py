import tempfile

from .base import *

DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CELERY_TASK_ALWAYS_EAGER = True

CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
}

# Never write test uploads into the repo tree (see documents/storage/local.py).
DOCUMENT_LOCAL_STORAGE_ROOT = tempfile.mkdtemp(prefix="easybook-test-documents-")

# Ask Books runs entirely on the deterministic fake providers in tests — no
# credentials, no paid API calls (ai/CLAUDE.md). Automatic indexing on
# document state changes is off by default so Phase 9 document tests stay
# isolated; ai/tests enable it explicitly where they test that hook.
AI_ASK_BOOKS_ENABLED = True
AI_ALLOW_FAKE_PROVIDERS = True
AI_AUTO_INDEX_DOCUMENTS = False
AI_RETRY_BACKOFF_SECONDS = 0.0
AI_RETRY_MAX_BACKOFF_SECONDS = 0.0

# Automated tests must never call a real external endpoint (phase section 90).
AUTOMATION_WEBHOOK_SENDER_BACKEND = "fake"
