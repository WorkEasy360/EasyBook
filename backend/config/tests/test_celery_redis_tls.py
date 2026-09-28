"""Celery against TLS Redis — the only shape production uses.

infrastructure/terraform/ecs.tf sets REDIS_URL=rediss://... because
ElastiCache has transit encryption on. Passed straight through to Celery,
that URL broke in two different ways (confirmed against celery 5.6 / kombu
5.6 source, then reproduced here):

- the Redis result backend refuses a rediss:// URL without ssl_cert_reqs and
  raises ValueError the first time anything touches `app.backend` — which
  every `.delay()` does;
- kombu's broker transport does not refuse it: it logs a warning and
  silently connects with ssl_cert_reqs=CERT_NONE, i.e. no certificate
  verification at all.

Run in a subprocess so the settings module is evaluated fresh against the
URL under test, exactly as a worker process starting in ECS would.
"""

import json
import os
import ssl
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

_SCRIPT = """
import json
import django
django.setup()
from config.celery import app

result = {}
try:
    backend = app.backend
    result["backend_error"] = None
    connection_class = backend.connparams.get("connection_class")
    result["backend_connection_class"] = connection_class.__name__ if connection_class else None
    result["backend_cert_reqs"] = int(backend.connparams.get("ssl_cert_reqs", -1))
except ValueError as exc:
    result["backend_error"] = str(exc).strip()
connection = app.connection_for_write()
broker_ssl = connection.ssl
result["broker_cert_reqs"] = int(broker_ssl["ssl_cert_reqs"]) if broker_ssl else None
print(json.dumps(result))
"""


def _celery_config_for(redis_url: str) -> dict:
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.test", "REDIS_URL": redis_url}
    # Both default to REDIS_URL; an explicit value in the developer's .env or
    # shell would otherwise mask the URL under test.
    env.pop("CELERY_BROKER_URL", None)
    env.pop("CELERY_RESULT_BACKEND", None)
    completed = subprocess.run(  # noqa: S603 — fixed interpreter path and hardcoded script constant, no untrusted input
        [sys.executable, "-c", _SCRIPT],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return json.loads(completed.stdout.strip().splitlines()[-1])


class CeleryRedisTLSTests(SimpleTestCase):
    def test_rediss_result_backend_initialises_with_certificate_verification(self):
        config = _celery_config_for("rediss://easybook.example.cache.amazonaws.com:6379/0")
        self.assertIsNone(config["backend_error"])
        self.assertEqual(config["backend_connection_class"], "SSLConnection")
        self.assertEqual(config["backend_cert_reqs"], int(ssl.CERT_REQUIRED))

    def test_rediss_broker_verifies_certificates_instead_of_silently_skipping(self):
        config = _celery_config_for("rediss://easybook.example.cache.amazonaws.com:6379/0")
        self.assertEqual(config["broker_cert_reqs"], int(ssl.CERT_REQUIRED))

    def test_plain_redis_url_stays_plaintext_for_local_development(self):
        config = _celery_config_for("redis://localhost:6390/0")
        self.assertIsNone(config["backend_error"])
        self.assertNotEqual(config["backend_connection_class"], "SSLConnection")
        self.assertIsNone(config["broker_cert_reqs"])
