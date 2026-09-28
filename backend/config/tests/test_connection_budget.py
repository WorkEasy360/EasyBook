"""API database connection budget.

Regressions (phase 12 P0 remediation):

- CONN_MAX_AGE defaulted to 60 under ASGI. Django's own docs: "When using
  ASGI, persistent connections should be disabled" — each request's sync code
  runs in its own thread with its own connection, and a persistent connection
  left in a finished request's thread is never reused, so connections piled
  up toward RDS max_connections.
- Nothing bounded how many requests one API process ran at once
  (uvicorn_worker.UvicornWorker never passes uvicorn a concurrency limit), so
  nothing bounded how many connections it opened either.
- No server-side statement or idle-in-transaction timeout: one runaway query,
  or a request stalled while holding its ATOMIC_REQUESTS transaction open,
  held its connection and locks indefinitely.
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.db import connection
from django.test import SimpleTestCase, TestCase

from config.asgi_limits import DEFAULT_ASGI_LIMIT_CONCURRENCY, asgi_limit_concurrency

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

_SETTINGS_SCRIPT = """
import json
from django.conf import settings
import django
django.setup()
print(json.dumps({"conn_max_age": settings.DATABASES["default"]["CONN_MAX_AGE"]}))
"""


class PersistentConnectionTests(SimpleTestCase):
    def test_persistent_connections_are_disabled(self):
        self.assertEqual(settings.DATABASES["default"]["CONN_MAX_AGE"], 0)

    def test_environment_cannot_re_enable_persistent_connections(self):
        env = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.test", "DB_CONN_MAX_AGE": "60"}
        completed = subprocess.run(  # noqa: S603 — fixed interpreter path and hardcoded script constant, no untrusted input
            [sys.executable, "-c", _SETTINGS_SCRIPT], cwd=BACKEND_DIR, env=env,
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout.strip().splitlines()[-1])["conn_max_age"], 0)


class ServerSideTimeoutTests(TestCase):
    def _show(self, name):
        with connection.cursor() as cursor:
            cursor.execute(f"SHOW {name}")
            return cursor.fetchone()[0]

    def test_every_connection_has_a_statement_timeout(self):
        self.assertEqual(self._show("statement_timeout"), "30s")

    def test_every_connection_has_an_idle_in_transaction_timeout(self):
        self.assertEqual(self._show("idle_in_transaction_session_timeout"), "2min")


class BoundedAsgiConcurrencyTests(SimpleTestCase):
    def test_concurrency_limit_defaults_to_a_bounded_value(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ASGI_LIMIT_CONCURRENCY", None)
            self.assertEqual(asgi_limit_concurrency(), DEFAULT_ASGI_LIMIT_CONCURRENCY)

    def test_concurrency_limit_refuses_unbounded_or_invalid_values(self):
        for raw in ("0", "-1", "unlimited", ""):
            with self.subTest(raw=raw), patch.dict(os.environ, {"ASGI_LIMIT_CONCURRENCY": raw}):
                with self.assertRaises(RuntimeError):
                    asgi_limit_concurrency()

    # gunicorn imports fcntl, so the worker class only loads where the
    # container runs (Linux CI, the image itself) — not on a Windows dev box.
    @skipUnless(importlib.util.find_spec("fcntl"), "gunicorn needs fcntl (POSIX only)")
    def test_api_worker_class_passes_the_limit_to_uvicorn(self):
        from uvicorn_worker import UvicornWorker

        from config.asgi_worker import BoundedUvicornWorker

        self.assertTrue(issubclass(BoundedUvicornWorker, UvicornWorker))
        self.assertEqual(BoundedUvicornWorker.CONFIG_KWARGS["limit_concurrency"], asgi_limit_concurrency())

    def test_api_container_runs_the_bounded_worker(self):
        dockerfile = (BACKEND_DIR / "Dockerfile").read_text(encoding="utf-8")
        cmd = re.search(r"^CMD \[(.*?)\]\s*$", dockerfile, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(cmd)
        self.assertIn('"config.asgi_worker.BoundedUvicornWorker"', cmd.group(1))
        self.assertNotIn('"uvicorn_worker.UvicornWorker"', cmd.group(1))
