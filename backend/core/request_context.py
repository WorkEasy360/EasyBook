"""Per-request / per-task correlation context for logs and audit records.

Context variables, so each request (sync thread or ASGI task) and each Celery
task sees only its own values. Tenant and user ids are NOT duplicated here —
core.tenancy already owns them and the logging filter reads them from there.

Every binder returns a token; callers reset with it in a `finally`, so a
value can never survive into the next request handled by the same thread.
"""

import contextvars
import re
import uuid

_request_id = contextvars.ContextVar("request_id", default=None)
_task_id = contextvars.ContextVar("task_id", default=None)
_task_name = contextvars.ContextVar("task_name", default=None)

# What a caller-supplied X-Request-ID may look like. Bounded to the audit
# column (AuditLog.request_id is varchar(64)); a conservative character set
# keeps it safe to echo in a response header and to write into logs. Anything
# else is replaced, never truncated or escaped: a correlation id that was
# altered no longer correlates.
MAX_REQUEST_ID_LENGTH = 64
_REQUEST_ID_PATTERN = re.compile(rf"[A-Za-z0-9._:-]{{1,{MAX_REQUEST_ID_LENGTH}}}")


def new_request_id() -> str:
    return str(uuid.uuid4())


def sanitize_request_id(raw) -> str:
    """The supplied id if it is well-formed, else a freshly generated one."""
    if isinstance(raw, str) and _REQUEST_ID_PATTERN.fullmatch(raw):
        return raw
    return new_request_id()


def get_request_id():
    return _request_id.get()


def get_task_id():
    return _task_id.get()


def get_task_name():
    return _task_name.get()


def bind_request_id(request_id: str):
    return _request_id.set(request_id)


def reset_request_id(token) -> None:
    _request_id.reset(token)


def bind_task(task_id, task_name):
    """Returns the tokens to pass to reset_task()."""
    return (_task_id.set(task_id), _task_name.set(task_name), _request_id.set(task_id))


def reset_task(tokens) -> None:
    task_token, name_token, request_token = tokens
    _request_id.reset(request_token)
    _task_name.reset(name_token)
    _task_id.reset(task_token)
