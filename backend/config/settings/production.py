from .base import *
from .base import env
from .preflight import validate_production_settings

DEBUG = False

SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# The ALB's target-group health check (and a plain `docker run` HEALTHCHECK)
# hits the container directly over HTTP inside the VPC — TLS already ended at
# the ALB, so there is no X-Forwarded-Proto header on that probe for
# SECURE_PROXY_SSL_HEADER to see. Without this exemption SecurityMiddleware
# 301s the probe to https on the same port, which the container cannot serve,
# so the prober's TLS handshake fails and the target is wrongly marked
# unhealthy — reproduced by actually running the built image (Dockerfile).
SECURE_REDIRECT_EXEMPT = [
    r"^api/v1/health/$",
    r"^api/v1/health/live/$",
]

# The frontend/admin origin(s) allowed to make unsafe (POST/PUT/PATCH/DELETE)
# requests. Django's CSRF default (same-origin only) is wrong for a separately
# hosted Next.js frontend, but there is no safe default to fall back to here —
# an empty list must fail closed, not silently accept every origin.
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# base.py's defaults (localhost hosts, localhost:3000 CORS, local document
# storage, an insecure placeholder secret key) exist so `manage.py runserver`
# works with zero configuration. None of them are safe in production, and a
# missing/wildcard/dev-default value here is exactly the kind of misconfig
# that must crash the process at startup rather than silently open the app up.
validate_production_settings(
    secret_key=env("DJANGO_SECRET_KEY", default=None),
    csrf_trusted_origins=CSRF_TRUSTED_ORIGINS,
    allowed_hosts_raw=env("DJANGO_ALLOWED_HOSTS", default=None),
    allowed_hosts=ALLOWED_HOSTS,
    cors_allowed_origins_raw=env("CORS_ALLOWED_ORIGINS", default=None),
    cors_allowed_origins=CORS_ALLOWED_ORIGINS,
    document_storage_backend=DOCUMENT_STORAGE_BACKEND,
)
