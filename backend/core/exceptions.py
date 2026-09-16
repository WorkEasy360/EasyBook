import contextlib
import logging
import uuid

from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler

logger = logging.getLogger("django.request")


class ApplicationError(APIException):
    """Base for domain errors that should surface a stable machine-readable code."""

    status_code = 400
    default_code = "application_error"

    def __init__(self, detail=None, code=None, status_code=None):
        if status_code is not None:
            self.status_code = status_code
        super().__init__(detail=detail, code=code or self.default_code)


def api_exception_handler(exc, context):
    """Renders every DRF error as {"error": {"code", "message", "details", "request_id"}}."""
    response = exception_handler(exc, context)
    if response is None:
        return None

    request = context.get("request")
    request_id = getattr(request, "request_id", None) or str(uuid.uuid4())

    if isinstance(response.data, dict) and "detail" in response.data and len(response.data) == 1:
        message = str(response.data["detail"])
        details = None
    else:
        message = "Request failed validation."
        details = response.data

    code = getattr(exc, "default_code", None) or getattr(exc, "code", None) or "error"
    if hasattr(exc, "get_codes"):
        # Defensive: a malformed/partially-built DRF exception can raise here.
        # The envelope must still render, so fall back to the `code` resolved
        # above rather than letting the error handler itself error.
        with contextlib.suppress(Exception):
            code = exc.get_codes()

    response.data = {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": request_id,
        }
    }
    return response
