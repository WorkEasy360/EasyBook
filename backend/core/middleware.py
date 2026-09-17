import uuid

from core.tenancy import clear_tenant_context

# View names that never touch tenant context and must stay independent of the
# database even during an outage (core/views.py:LivenessCheckView) — skipped
# here rather than having clear_tenant_context() try to infer "was anything
# set this request", which core/tenancy.py explains is unsafe to infer from
# contextvar state alone.
_TENANT_CONTEXT_EXEMPT_VIEW_NAMES = frozenset({"liveness-check"})


class RequestIDMiddleware:
    """Attaches a request_id (from X-Request-ID or generated) for log correlation."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        try:
            response = self.get_response(request)
        finally:
            # Contextvars persist per-thread across requests under some servers;
            # always drop tenant scope once the response is built.
            view_name = getattr(getattr(request, "resolver_match", None), "view_name", None)
            if view_name not in _TENANT_CONTEXT_EXEMPT_VIEW_NAMES:
                clear_tenant_context()
        response["X-Request-ID"] = request.request_id
        return response


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
