"""`get_storage()` is the ONE place that decides which `DocumentStorage`
implementation is live. Everything else (services, API views, Celery tasks)
depends only on the `DocumentStorage` interface — see base.py.

Deliberately NOT cached: tests flip `DOCUMENT_STORAGE_BACKEND` per-case via
`override_settings`, and constructing either backend is cheap (no network
call happens until a method is actually invoked)."""

from django.conf import settings

from documents.storage.base import DocumentStorage


def get_storage() -> DocumentStorage:
    backend = getattr(settings, "DOCUMENT_STORAGE_BACKEND", "local")
    if backend == "s3":
        from documents.storage.s3 import S3DocumentStorage

        return S3DocumentStorage()
    if backend == "local":
        from documents.storage.local import LocalDocumentStorage

        return LocalDocumentStorage()
    raise ValueError(f"Unknown DOCUMENT_STORAGE_BACKEND: {backend!r}")
