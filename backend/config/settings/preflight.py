"""Pure validation for config/settings/production.py's fail-closed checks.

Kept separate from production.py — which is itself a Django settings module,
executed as a side effect of importing it against whatever `.env` file and
process environment happen to be present — so this logic can be unit-tested
directly with crafted inputs instead of via subprocess + environment
juggling. See config/tests/test_production_settings.py.
"""

import re
from urllib.parse import urlsplit

INSECURE_SECRET_KEYS = {None, "", "django-insecure-change-me-in-env"}

# A bare DNS hostname: no scheme, port, path, leading dot (Django's subdomain
# wildcard) or "*". Case-insensitive, as DNS is.
_HOSTNAME = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", re.I)


def derive_csrf_trusted_origins(allowed_hosts):
    """https://<host> for each allowed host: the only origins a single-hostname,
    HTTPS-only deployment serves its forms from."""
    return [f"https://{host}" for host in allowed_hosts]


def _https_origin_host(origin):
    """The host of a bare https:// origin, or None if `origin` is anything else."""
    parts = urlsplit(origin)
    if parts.scheme != "https" or parts.path not in ("", "/") or parts.query or parts.fragment:
        return None
    if parts.username or parts.password or parts.port is not None:
        return None
    host = parts.hostname or ""
    return host if _HOSTNAME.match(host) else None


def validate_production_settings(
    *,
    secret_key,
    csrf_trusted_origins,
    allowed_hosts_raw,
    allowed_hosts,
    cors_allowed_origins_raw,
    cors_allowed_origins,
    document_storage_backend,
    trusted_proxy_count,
    bff_proxy_secret,
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
    if not allowed_hosts:
        raise RuntimeError("DJANGO_ALLOWED_HOSTS must name the deployment's hostname (it is empty).")
    for host in allowed_hosts:
        if not _HOSTNAME.match(host):
            raise RuntimeError(
                f"DJANGO_ALLOWED_HOSTS entry {host!r} must be a bare hostname such as app.example.com "
                "(no scheme, port, path, leading dot or wildcard)."
            )
    allowed = {host.lower() for host in allowed_hosts}
    for origin in csrf_trusted_origins:
        host = _https_origin_host(origin)
        if host is None or host.lower() not in allowed:
            raise RuntimeError(
                f"CSRF_TRUSTED_ORIGINS entry {origin!r} must be https://<one of DJANGO_ALLOWED_HOSTS> "
                "(HTTPS only, no wildcard, port or path)."
            )

    if cors_allowed_origins_raw is None:
        raise RuntimeError("CORS_ALLOWED_ORIGINS must be set explicitly in production.")
    if "*" in cors_allowed_origins:
        raise RuntimeError("CORS_ALLOWED_ORIGINS must not contain a wildcard in production.")
    for origin in cors_allowed_origins:
        if _https_origin_host(origin) is None:
            raise RuntimeError(f"CORS_ALLOWED_ORIGINS entry {origin!r} must be a bare https:// origin in production.")

    if trusted_proxy_count < 1:
        raise RuntimeError(
            "TRUSTED_PROXY_COUNT must be at least 1 in production (the load balancer) — with 0 every "
            "client is attributed to the load balancer's address and shares one rate limit."
        )

    if not bff_proxy_secret or len(bff_proxy_secret) < 32:
        raise RuntimeError(
            "BFF_PROXY_SECRET must be set (32+ characters) in production — without it every browser user "
            "is attributed to the frontend server's address and shares one rate limit."
        )

    if document_storage_backend != "s3":
        raise RuntimeError(
            "DOCUMENT_STORAGE_BACKEND must be 's3' in production — local filesystem "
            "storage is a dev/test-only default (see documents/CLAUDE.md)."
        )
