"""One-time (and safely re-runnable) database bootstrap for a new environment.

    python -m ops.db_bootstrap

Runs as the database's administrative user (the RDS master user), ONCE per
environment before the first `migrate`, as its own one-off task — never as
part of API/worker startup, and never with credentials any service holds. See
infrastructure/runbooks/database-bootstrap.md.

It does exactly what the application role cannot do for itself:

1. `CREATE EXTENSION vector` — pgvector is not a trusted extension, so the
   non-superuser application role cannot install it (ai/checks.py fails the
   ai migration closed when it is missing).
2. Creates (or re-asserts) the application role: LOGIN, NOCREATEDB,
   NOCREATEROLE, and the password from the app credentials secret. New roles
   default to NOSUPERUSER / NOBYPASSRLS / NOREPLICATION; those attributes are
   NOT set explicitly because on RDS the master user is not a real superuser
   and may not name them — instead step 4 verifies them and fails loudly.
3. Grants CONNECT on the database and USAGE + CREATE on schema `public`: the
   application role owns the schema objects it migrates. FORCE ROW LEVEL
   SECURITY (core/rls.py) keeps RLS binding on it as owner.
4. Verifies the result and refuses to finish if the application role is a
   superuser, can bypass RLS, can replicate, or is a member of a role that
   can (e.g. rds_superuser).

Everything is idempotent: re-running changes nothing except re-setting the
password to the value in the secret (how rotation is applied). No secret is
ever printed. Dependencies: psycopg only (already in requirements.txt), so
it runs from the backend image without adding psql to it.

Environment:
    DB_HOST, DB_PORT (default 5432), DB_NAME
    DB_MASTER_USER, DB_MASTER_PASSWORD   administrative credentials
    DB_APP_USER, DB_APP_PASSWORD         the role to create for the app
    DB_SSLMODE                           default "require"
"""

from __future__ import annotations

import json
import os
import sys

import psycopg
from psycopg import sql

REQUIRED_ENV = ("DB_HOST", "DB_NAME", "DB_MASTER_USER", "DB_MASTER_PASSWORD", "DB_APP_USER", "DB_APP_PASSWORD")

# Membership in any of these would hand the app role superuser-equivalent
# powers. Checked only if the role exists in this cluster.
PRIVILEGED_ROLES = ("rds_superuser", "pg_write_server_files", "pg_execute_server_program", "pg_read_server_files")


class BootstrapError(RuntimeError):
    pass


def _config(env) -> dict:
    missing = [name for name in REQUIRED_ENV if not env.get(name)]
    if missing:
        raise BootstrapError(f"Missing required environment variables: {', '.join(missing)}")
    if env["DB_APP_USER"] == env["DB_MASTER_USER"]:
        raise BootstrapError("DB_APP_USER must not be the administrative user.")
    return {
        "host": env["DB_HOST"],
        "port": int(env.get("DB_PORT") or 5432),
        "dbname": env["DB_NAME"],
        "master_user": env["DB_MASTER_USER"],
        "master_password": env["DB_MASTER_PASSWORD"],
        "app_user": env["DB_APP_USER"],
        "app_password": env["DB_APP_PASSWORD"],
        "sslmode": env.get("DB_SSLMODE") or "require",
    }


def _role_exists(cur, role: str) -> bool:
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role])
    return cur.fetchone() is not None


def verify_app_role(cur, app_user: str) -> list[str]:
    """Problems that make `app_user` unsafe as the RLS-bound runtime role."""
    cur.execute(
        "SELECT rolsuper, rolbypassrls, rolreplication, rolcreaterole, rolcanlogin FROM pg_roles WHERE rolname = %s",
        [app_user],
    )
    row = cur.fetchone()
    if row is None:
        return [f"role {app_user!r} does not exist"]
    is_super, bypass_rls, replication, create_role, can_login = row
    problems = []
    if is_super:
        problems.append("is a superuser (bypasses RLS unconditionally)")
    if bypass_rls:
        problems.append("has BYPASSRLS")
    if replication:
        problems.append("has REPLICATION")
    if create_role:
        problems.append("has CREATEROLE")
    if not can_login:
        problems.append("cannot log in")
    for privileged in PRIVILEGED_ROLES:
        if _role_exists(cur, privileged):
            cur.execute("SELECT pg_has_role(%s, %s, 'MEMBER')", [app_user, privileged])
            if cur.fetchone()[0]:
                problems.append(f"is a member of {privileged}")
    return problems


def bootstrap(config: dict) -> dict:
    app = sql.Identifier(config["app_user"])
    password = sql.Literal(config["app_password"])
    with psycopg.connect(
        host=config["host"],
        port=config["port"],
        dbname=config["dbname"],
        user=config["master_user"],
        password=config["master_password"],
        sslmode=config["sslmode"],
        connect_timeout=15,
        application_name="easybook-db-bootstrap",
    ) as conn, conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")

        created = not _role_exists(cur, config["app_user"])
        verb = "CREATE" if created else "ALTER"
        # Client-side composition (sql.Literal): utility statements cannot take
        # server-side parameters. The statement is never logged by this script.
        cur.execute(sql.SQL(verb + " ROLE {} WITH LOGIN NOCREATEDB NOCREATEROLE PASSWORD {}").format(app, password))

        cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(config["dbname"]), app))
        cur.execute(sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(app))

        problems = verify_app_role(cur, config["app_user"])
        cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        vector = cur.fetchone()
        if vector is None:
            problems.append("pgvector extension is not installed")
        if problems:
            conn.rollback()
            raise BootstrapError(f"Application role {config['app_user']!r} is unsafe: " + "; ".join(problems))
        conn.commit()

    return {
        "database": config["dbname"],
        "app_role": config["app_user"],
        "app_role_created": created,
        "pgvector_version": vector[0],
        "verified": ["NOSUPERUSER", "NOBYPASSRLS", "NOREPLICATION", "NOCREATEROLE", "LOGIN"],
    }


def main(env=None) -> int:
    env = os.environ if env is None else env
    try:
        summary = bootstrap(_config(env))
    except BootstrapError as exc:
        print(json.dumps({"event": "db_bootstrap_failed", "error": str(exc)}), file=sys.stderr)
        return 1
    except psycopg.Error as exc:
        # The driver's message never contains the password; the class name and
        # SQLSTATE are enough to diagnose connectivity vs. permission problems.
        print(
            json.dumps({"event": "db_bootstrap_failed", "error": type(exc).__name__, "sqlstate": exc.sqlstate}),
            file=sys.stderr,
        )
        return 1
    print(json.dumps({"event": "db_bootstrap_succeeded", **summary}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
