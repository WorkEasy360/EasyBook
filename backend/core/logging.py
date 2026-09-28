import json
import logging
import re

from core import request_context
from core.tenancy import get_current_organization_id, get_current_user_id

# Every attribute a plain LogRecord has; anything else on a record came from
# `extra={...}` (or a filter) and is emitted as a structured field.
_STANDARD_RECORD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}

# Keys whose values are never written to a log, whatever logger emits them.
# Matched case-insensitively anywhere in the key ("access_token",
# "client_secret", "X-Api-Key", ...). "refresh" and "access" alone are the
# SimpleJWT token field names. "tokens" (plural) is left alone: it names
# usage counts such as input_tokens, never a credential.
_SENSITIVE_KEY = re.compile(r"pass(word)?|secret|token(?!s)|authorization|cookie|api[_-]?key|credential|^refresh$|^access$", re.I)
REDACTED = "[redacted]"


def _redact(key: str, value):
    return REDACTED if _SENSITIVE_KEY.search(key) else value


class RequestContextFilter(logging.Filter):
    """Stamps each record with the current request/task correlation context.

    Runs at emit time in the logging caller's own context (handlers here are
    synchronous), so it sees exactly the request or task that logged. A value
    already set explicitly via `extra=` wins.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        context = {
            "request_id": request_context.get_request_id(),
            "task_id": request_context.get_task_id(),
            "task_name": request_context.get_task_name(),
            "organization_id": get_current_organization_id(),
            "user_id": get_current_user_id(),
        }
        for key, value in context.items():
            if value is not None and getattr(record, key, None) is None:
                setattr(record, key, str(value))
        return True


class JSONFormatter(logging.Formatter):
    """Dependency-free structured formatter: the base fields, then every
    structured field attached to the record (extra= payloads and the
    correlation context), with secret-looking keys redacted."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_ATTRS or key.startswith("_") or key in payload:
                continue
            payload[key] = _redact(key, value)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)
