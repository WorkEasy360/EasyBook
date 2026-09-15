import uuid

from core.tenancy import clear_tenant_context


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
