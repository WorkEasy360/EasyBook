import logging
import time

from django.http import JsonResponse

from core.request_context import bind_request_id, reset_request_id, sanitize_request_id
from core.tenancy import clear_tenant_context

request_logger = logging.getLogger("easybook.request")

LIVENESS_PATH = "/api/v1/health/live/"


class LivenessProbeMiddleware:
    """Answers the liveness probe before any other middleware runs.

    Must be first in MIDDLEWARE. The ALB target-group health check sends the
    task's private IP as Host and the ECS container healthCheck sends
    127.0.0.1:8000, neither of which is in production's DJANGO_ALLOWED_HOSTS —
    CommonMiddleware's host validation answered both with 400, so every API
    task failed its health checks. Answering here also keeps the probe clear of
    SECURE_SSL_REDIRECT (the probe is plain HTTP inside the VPC), the database
    (ATOMIC_REQUESTS wraps views, not middleware), sessions, auth and
    throttling: liveness means "this process can answer", nothing more.
    Readiness (/api/v1/health/, database + cache) stays an ordinary view for
    monitoring and alerts, never for replacing tasks.

    Only GET/HEAD on the exact path, so nothing else can use it to skip host
    validation.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == LIVENESS_PATH and request.method in ("GET", "HEAD"):
            return JsonResponse({"status": "ok"})
        return self.get_response(request)

# View names that never touch tenant context and must stay independent of the
# database even during an outage (core/views.py:LivenessCheckView) — skipped
# here rather than having clear_tenant_context() try to infer "was anything
# set this request", which core/tenancy.py explains is unsafe to infer from
# contextvar state alone.
_TENANT_CONTEXT_EXEMPT_VIEW_NAMES = frozenset({"liveness-check"})


class RequestIDMiddleware:
    """Binds the request's correlation id and logs one structured line per request.

    The id is the caller's X-Request-ID only when it is well-formed (bounded
    to the 64-character audit column, a conservative character set); anything
    else — oversized, malformed, missing — is replaced by a generated UUID,
    never echoed. It is bound into core.request_context for the logging filter
    and for audit.services.record, and reset in `finally` together with the
    tenant context, so nothing survives into the next request on this thread.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = sanitize_request_id(request.headers.get("X-Request-ID"))
        token = bind_request_id(request.request_id)
        started = time.monotonic()
        response = None
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = request.request_id
            return response
        finally:
            # Logged before the tenant context is cleared, so the line carries
            # the organization and user the request acted as.
            request_logger.info(
                "request_finished",
                extra={
                    "method": request.method,
                    "path": request.path,
                    "status": response.status_code if response is not None else 500,
                    "duration_ms": round((time.monotonic() - started) * 1000, 1),
                },
            )
            # Contextvars persist per-thread across requests under some servers;
            # drop tenant scope once the response is built. Tenant context is
            # only ever set inside a view, so a request that never resolved
            # one (refused earlier: unknown Host, HTTPS redirect) has nothing
            # to clear — and must not open a database connection to clear it.
            resolver_match = getattr(request, "resolver_match", None)
            if resolver_match is not None and resolver_match.view_name not in _TENANT_CONTEXT_EXEMPT_VIEW_NAMES:
                clear_tenant_context()
            reset_request_id(token)


class SecurityHeadersMiddleware:
    """Headers not already covered by Django's SecurityMiddleware settings."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("X-Content-Type-Options", "nosniff")
        response.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        if request.path.startswith("/admin/"):
            # Django admin renders HTML with its own CSS/JS; the API's
            # zero-trust "default-src none" would break it.
            response.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
            )
        else:
            response.setdefault(
                "Content-Security-Policy",
                "default-src 'none'; frame-ancestors 'none'",
            )
        return response
