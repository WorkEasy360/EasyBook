"""Refuse to run the application as a database role that defeats RLS.

Row Level Security is the second tenant-isolation layer (core/rls.py). A
superuser bypasses it unconditionally, and so does any role with BYPASSRLS —
FORCE ROW LEVEL SECURITY does not help. If a deployment ever points the API or
a worker at such a role (the RDS master user, say), every tenant can read
every other tenant's rows the moment an application-level scope is missed.
This check makes that deployment fail at process start instead.

Called once per process (config/asgi.py, the Celery worker_init signal) in
production, and by `manage.py db_preflight` before migrations. The rules are
the same ones ops.db_bootstrap verifies when it creates the role.
"""

from django.core.exceptions import ImproperlyConfigured
from django.db import connections

from ops.db_bootstrap import verify_app_role


def runtime_role_problems(using: str = "default") -> tuple[str, list[str]]:
    connection = connections[using]
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user")
            role = cursor.fetchone()[0]
            return role, verify_app_role(cursor, role)
    finally:
        # Checked from process start-up code, outside any request: never leave
        # this connection open for whatever thread runs next. (Inside an
        # atomic block — only ever a caller's own transaction, e.g. a test —
        # the connection belongs to that caller and is left alone.)
        if not connection.in_atomic_block:
            connection.close()


def assert_runtime_db_role_is_restricted(using: str = "default") -> str:
    role, problems = runtime_role_problems(using)
    if problems:
        raise ImproperlyConfigured(
            f"Refusing to start: database role {role!r} " + "; ".join(problems)
            + ". The application must connect as the non-superuser, non-BYPASSRLS role created by "
            "ops.db_bootstrap (infrastructure/runbooks/database-bootstrap.md)."
        )
    return role
