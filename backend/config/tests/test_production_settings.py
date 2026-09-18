"""config.settings.production is a fail-closed preflight.

Most scenarios are exercised directly against the pure
`validate_production_settings` function (config/settings/preflight.py) —
fast, and unaffected by the local `backend/.env` file a real production
deployment would never ship. A couple of subprocess-based smoke tests
confirm production.py actually wires that function up end to end.
"""

import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

from config.settings.preflight import validate_production_settings

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

VALID_KWARGS = {
    "secret_key": "a-real-randomly-generated-production-secret-key",
    "csrf_trusted_origins": ["https://app.example.com"],
    "allowed_hosts_raw": "app.example.com",
    "allowed_hosts": ["app.example.com"],
    "cors_allowed_origins_raw": "https://app.example.com",
    "cors_allowed_origins": ["https://app.example.com"],
    "document_storage_backend": "s3",
    "trusted_proxy_count": 1,
    "bff_proxy_secret": "b" * 48,
}


class ValidateProductionSettingsTests(SimpleTestCase):
    def test_fully_configured_values_pass(self):
        validate_production_settings(**VALID_KWARGS)  # must not raise

    def test_missing_secret_key_rejected(self):
        kwargs = {**VALID_KWARGS, "secret_key": None}
        with self.assertRaisesMessage(RuntimeError, "DJANGO_SECRET_KEY"):
            validate_production_settings(**kwargs)

    def test_placeholder_secret_key_rejected(self):
        kwargs = {**VALID_KWARGS, "secret_key": "django-insecure-change-me-in-env"}
        with self.assertRaisesMessage(RuntimeError, "DJANGO_SECRET_KEY"):
            validate_production_settings(**kwargs)

    def test_missing_csrf_trusted_origins_rejected(self):
        kwargs = {**VALID_KWARGS, "csrf_trusted_origins": []}
        with self.assertRaisesMessage(RuntimeError, "CSRF_TRUSTED_ORIGINS"):
            validate_production_settings(**kwargs)

    def test_unset_allowed_hosts_rejected(self):
        kwargs = {**VALID_KWARGS, "allowed_hosts_raw": None}
        with self.assertRaisesMessage(RuntimeError, "DJANGO_ALLOWED_HOSTS"):
            validate_production_settings(**kwargs)

    def test_wildcard_allowed_hosts_rejected(self):
        kwargs = {**VALID_KWARGS, "allowed_hosts_raw": "*", "allowed_hosts": ["*"]}
        with self.assertRaisesMessage(RuntimeError, "DJANGO_ALLOWED_HOSTS"):
            validate_production_settings(**kwargs)

    def test_unset_cors_allowed_origins_rejected(self):
        kwargs = {**VALID_KWARGS, "cors_allowed_origins_raw": None}
        with self.assertRaisesMessage(RuntimeError, "CORS_ALLOWED_ORIGINS"):
            validate_production_settings(**kwargs)

    def test_wildcard_cors_allowed_origins_rejected(self):
        kwargs = {**VALID_KWARGS, "cors_allowed_origins_raw": "*", "cors_allowed_origins": ["*"]}
        with self.assertRaisesMessage(RuntimeError, "CORS_ALLOWED_ORIGINS"):
            validate_production_settings(**kwargs)

    def test_no_trusted_proxy_rejected(self):
        kwargs = {**VALID_KWARGS, "trusted_proxy_count": 0}
        with self.assertRaisesMessage(RuntimeError, "TRUSTED_PROXY_COUNT"):
            validate_production_settings(**kwargs)

    def test_missing_or_short_bff_proxy_secret_rejected(self):
        for secret in ("", "too-short"):
            with self.subTest(secret=secret):
                kwargs = {**VALID_KWARGS, "bff_proxy_secret": secret}
                with self.assertRaisesMessage(RuntimeError, "BFF_PROXY_SECRET"):
                    validate_production_settings(**kwargs)

    def test_non_s3_document_storage_rejected(self):
        kwargs = {**VALID_KWARGS, "document_storage_backend": "local"}
        with self.assertRaisesMessage(RuntimeError, "DOCUMENT_STORAGE_BACKEND"):
            validate_production_settings(**kwargs)


def _run_check(overrides=None):
    # A real production deployment ships no `backend/.env` file — secrets and
    # config arrive as real environment variables — so only overriding
    # os.environ (rather than trying to simulate an "unset" var, which the
    # local dev .env would immediately backfill) is representative here.
    env = {
        **os.environ,
        "DJANGO_SECRET_KEY": "a-real-randomly-generated-production-secret-key",
        "DJANGO_ALLOWED_HOSTS": "app.example.com",
        "CORS_ALLOWED_ORIGINS": "https://app.example.com",
        "CSRF_TRUSTED_ORIGINS": "https://app.example.com",
        "DOCUMENT_STORAGE_BACKEND": "s3",
        "TRUSTED_PROXY_COUNT": "1",
        "BFF_PROXY_SECRET": "b" * 48,
        **(overrides or {}),
    }
    return subprocess.run(
        [sys.executable, "manage.py", "check", "--settings=config.settings.production"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


class ProductionSettingsWiringTests(SimpleTestCase):
    """End-to-end smoke tests that production.py actually calls the validator."""

    def test_fully_configured_production_env_boots(self):
        result = _run_check()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_wildcard_allowed_hosts_crashes_the_process(self):
        result = _run_check({"DJANGO_ALLOWED_HOSTS": "*"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_ALLOWED_HOSTS", result.stderr)
