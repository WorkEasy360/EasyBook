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
