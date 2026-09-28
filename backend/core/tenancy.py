"""
Tenant-context propagation.

The current organization/user is held in a contextvar for app-level query
scoping (see core.managers.TenantManager) AND mirrored into PostgreSQL session
GUCs via SET LOCAL so Row Level Security policies enforce the same boundary
at the database layer, independent of application code correctness.

SET LOCAL only has effect inside a transaction; DATABASES['default']['ATOMIC_REQUESTS']
is True so every request already runs inside one (see config/settings/base.py).
"""

import contextvars
from contextlib import contextmanager

from django.db import Error as DjangoDBError
from django.db import connection, transaction

_current_organization_id = contextvars.ContextVar("current_organization_id", default=None)
_current_user_id = contextvars.ContextVar("current_user_id", default=None)


def get_current_organization_id():
    return _current_organization_id.get()


def get_current_user_id():
    return _current_user_id.get()


def _set_guc(name: str, value) -> None:
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config(%s, %s, true)", [name, str(value) if value else ""])


def set_current_user_id(user_id) -> None:
    _current_user_id.set(str(user_id) if user_id else None)
    _set_guc("app.current_user_id", user_id)


def set_current_organization_id(organization_id) -> None:
    _current_organization_id.set(str(organization_id) if organization_id else None)
    _set_guc("app.current_organization_id", organization_id)


def clear_tenant_context() -> None:
    """Reset both the contextvar scope and the mirrored session GUCs.

    Always clears the DB-side GUC unconditionally, regardless of the current
    Python contextvar state: `tenant_context()` below can leave the two out
    of sync (its `finally` resets the contextvar via `.reset()` but does not
    re-clear the GUC), so skipping the DB call whenever the contextvar
    happens to read as unset would leave a stale GUC value from an earlier
    `with tenant_context(...)` block active for the rest of the transaction —
    exactly the RLS bypass core/tests/test_tenant_isolation.py guards against.
    The DjangoDBError guard is pure resilience for a genuinely broken
    connection (e.g. during a DB outage): SET LOCAL is scoped to the
    request's own transaction and is discarded the instant that transaction
    ends, so there is nothing security-relevant left to clear once the
    connection itself is unusable. RequestIDMiddleware additionally skips
    calling this at all for routes that never touch tenant context in the
    first place (e.g. the liveness probe), rather than this function trying
    to infer that itself.
    """
    _current_organization_id.set(None)
    _current_user_id.set(None)
    try:
        _set_guc("app.current_organization_id", None)
        _set_guc("app.current_user_id", None)
    except DjangoDBError:
        pass


@contextmanager
def tenant_context(*, organization_id=None, user_id=None):
    """Explicitly scope a block of code (management commands, Celery tasks,
    services) to a tenant.

    SET LOCAL only survives for the life of an open transaction — outside of
    request handling (where DATABASES['default']['ATOMIC_REQUESTS'] already
    keeps one open) there is no ambient transaction, so this opens one itself
    to guarantee the GUC actually sticks for the query it's meant to scope.
    """
    org_token = _current_organization_id.set(str(organization_id) if organization_id else None)
    user_token = _current_user_id.set(str(user_id) if user_id else None)
    try:
        with transaction.atomic():
            _set_guc("app.current_organization_id", organization_id)
            _set_guc("app.current_user_id", user_id)
            yield
    finally:
        _current_organization_id.reset(org_token)
        _current_user_id.reset(user_token)
