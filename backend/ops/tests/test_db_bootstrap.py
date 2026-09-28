"""ops.db_bootstrap — the first-environment database bootstrap.

The integration tests need an administrative connection (they create a
scratch database and role), which the application's own test role must never
have. They run when DB_BOOTSTRAP_TEST_ADMIN_DSN is set (CI sets it to the
Postgres service's superuser) and are skipped otherwise.
"""
import io
import os
import secrets
import unittest
from contextlib import redirect_stderr, redirect_stdout

import psycopg
from django.test import SimpleTestCase
from psycopg import sql

from ops import db_bootstrap

ADMIN_DSN = os.environ.get("DB_BOOTSTRAP_TEST_ADMIN_DSN", "")


class ConfigTests(SimpleTestCase):
    def test_missing_environment_fails_without_touching_a_database(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertEqual(db_bootstrap.main({"DB_HOST": "db"}), 1)
        self.assertIn("DB_MASTER_PASSWORD", stderr.getvalue())

    def test_app_role_may_not_be_the_admin_role(self):
        env = dict.fromkeys(db_bootstrap.REQUIRED_ENV, "x")
        with self.assertRaises(db_bootstrap.BootstrapError):
            db_bootstrap._config(env)


@unittest.skipUnless(ADMIN_DSN, "set DB_BOOTSTRAP_TEST_ADMIN_DSN to run the bootstrap against a real server")
class BootstrapIntegrationTests(SimpleTestCase):
    def setUp(self):
        suffix = secrets.token_hex(4)
        self.dbname = f"easybook_bootstrap_{suffix}"
        self.role = f"easybook_bootstrap_app_{suffix}"
        self.password = secrets.token_urlsafe(24)
        self.admin = psycopg.conninfo.conninfo_to_dict(ADMIN_DSN)
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.dbname)))

    def tearDown(self):
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(self.dbname)))
            conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(self.role)))

    def _env(self, password=None):
        return {
            "DB_HOST": self.admin.get("host", "127.0.0.1"),
            "DB_PORT": str(self.admin.get("port", 5432)),
            "DB_NAME": self.dbname,
            "DB_MASTER_USER": self.admin["user"],
            "DB_MASTER_PASSWORD": self.admin.get("password", ""),
            "DB_APP_USER": self.role,
            "DB_APP_PASSWORD": password or self.password,
            "DB_SSLMODE": "disable",
        }

    def _run(self, env):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = db_bootstrap.main(env)
        return code, out.getvalue() + err.getvalue()

    def _as_app(self, password=None):
        return psycopg.connect(
            host=self.admin.get("host", "127.0.0.1"),
            port=self.admin.get("port", 5432),
            dbname=self.dbname,
            user=self.role,
            password=password or self.password,
        )

    def test_bootstrap_creates_a_restricted_role_and_the_extension(self):
        code, output = self._run(self._env())
        self.assertEqual(code, 0, output)
        self.assertNotIn(self.password, output)

        with self._as_app() as conn, conn.cursor() as cur:
            cur.execute("SELECT rolsuper, rolbypassrls, rolreplication, rolcreaterole FROM pg_roles WHERE rolname = current_user")
            self.assertEqual(cur.fetchone(), (False, False, False, False))
            cur.execute("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
            self.assertEqual(cur.fetchone()[0], 1)
            # The app role owns what it migrates: it can create in `public`.
            cur.execute("CREATE TABLE bootstrap_probe (id int)")
            cur.execute("SELECT '[1,2,3]'::vector")

    def test_bootstrap_is_idempotent_and_applies_password_rotation(self):
        self.assertEqual(self._run(self._env())[0], 0)
        rotated = secrets.token_urlsafe(24)
        code, output = self._run(self._env(password=rotated))
        self.assertEqual(code, 0, output)
        self.assertIn('"app_role_created": false', output)
        with self._as_app(password=rotated) as conn:
            self.assertEqual(conn.execute("SELECT 1").fetchone()[0], 1)

    def test_bootstrap_refuses_an_existing_superuser_app_role(self):
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE ROLE {} WITH LOGIN SUPERUSER").format(sql.Identifier(self.role)))
        code, output = self._run(self._env())
        self.assertEqual(code, 1)
        self.assertIn("superuser", output)
        self.assertNotIn(self.password, output)

    def test_bootstrap_refuses_an_existing_bypassrls_app_role(self):
        with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE ROLE {} WITH LOGIN BYPASSRLS").format(sql.Identifier(self.role)))
        code, output = self._run(self._env())
        self.assertEqual(code, 1)
        self.assertIn("BYPASSRLS", output)
