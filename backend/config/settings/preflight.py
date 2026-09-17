"""Pure validation for config/settings/production.py's fail-closed checks.

Kept separate from production.py — which is itself a Django settings module,
executed as a side effect of importing it against whatever `.env` file and
process environment happen to be present — so this logic can be unit-tested
directly with crafted inputs instead of via subprocess + environment
juggling. See config/tests/test_production_settings.py.
"""

INSECURE_SECRET_KEYS = {None, "", "django-insecure-change-me-in-env"}


def validate_production_settings(
    *,
    secret_key,
    csrf_trusted_origins,
    allowed_hosts_raw,
    allowed_hosts,
    cors_allowed_origins_raw,
    cors_allowed_origins,
    document_storage_backend,
):
    """Raise RuntimeError on the first insecure/missing value found.

    `*_raw` parameters are the unparsed environment value (None if the
    variable was never set) — needed because an unset variable and an
    explicitly-empty one both parse to the same falsy list, but only the
    former means "left at the dev default" and deserves its own message.
    """
    if secret_key in INSECURE_SECRET_KEYS:
        raise RuntimeError("DJANGO_SECRET_KEY must be set to a real secret in production.")

    if not csrf_trusted_origins:
        raise RuntimeError("CSRF_TRUSTED_ORIGINS must be set in production (e.g. https://app.example.com).")

    if allowed_hosts_raw is None:
        raise RuntimeError("DJANGO_ALLOWED_HOSTS must be set explicitly in production.")
    if "*" in allowed_hosts:
        raise RuntimeError("DJANGO_ALLOWED_HOSTS must not contain a wildcard in production.")

    if cors_allowed_origins_raw is None:
        raise RuntimeError("CORS_ALLOWED_ORIGINS must be set explicitly in production.")
    if "*" in cors_allowed_origins:
        raise RuntimeError("CORS_ALLOWED_ORIGINS must not contain a wildcard in production.")

    if document_storage_backend != "s3":
        raise RuntimeError(
            "DOCUMENT_STORAGE_BACKEND must be 's3' in production — local filesystem "
            "storage is a dev/test-only default (see documents/CLAUDE.md)."
        )
