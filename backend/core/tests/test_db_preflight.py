"""The runtime database role must never defeat RLS (core/db_preflight.py)."""
import io
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import TestCase

from core import db_preflight


class RuntimeRolePreflightTests(TestCase):
    def test_the_test_suites_own_role_is_restricted(self):
        # The suite runs as the application role (never the postgres
        # superuser); if this ever fails, every RLS test in the suite is void.
        role = db_preflight.assert_runtime_db_role_is_restricted()
        with connection.cursor() as cursor:
            cursor.execute("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s", [role])
            self.assertEqual(cursor.fetchone(), (False, False))

    def test_superuser_role_refuses_to_start(self):
        with mock.patch.object(db_preflight, "verify_app_role", return_value=["is a superuser (bypasses RLS unconditionally)"]):
            with self.assertRaisesMessage(ImproperlyConfigured, "Refusing to start"):
                db_preflight.assert_runtime_db_role_is_restricted()

    def test_bypassrls_role_refuses_to_start(self):
        with mock.patch.object(db_preflight, "verify_app_role", return_value=["has BYPASSRLS"]):
            with self.assertRaisesMessage(ImproperlyConfigured, "BYPASSRLS"):
                db_preflight.assert_runtime_db_role_is_restricted()

    def test_management_command_passes_and_fails(self):
        out = io.StringIO()
        call_command("db_preflight", stdout=out)
        self.assertIn("is restricted", out.getvalue())
        with mock.patch.object(db_preflight, "verify_app_role", return_value=["has BYPASSRLS"]):
            with self.assertRaises(CommandError):
                call_command("db_preflight", stdout=io.StringIO())

    def test_production_always_enables_the_preflight(self):
        source = open("config/settings/production.py", encoding="utf-8").read()
        self.assertIn("DB_ROLE_PREFLIGHT = True", source)
        self.assertNotIn('env.bool("DB_ROLE_PREFLIGHT"', source)


class CeleryBootRefusalTests(TestCase):
    """Celery swallows exceptions from signal handlers, so the worker/beat
    guard must terminate the process itself (config/celery.py)."""

    def test_unrestricted_role_terminates_worker_and_beat(self):
        from django.test import override_settings

        from config import celery as celery_config

        with override_settings(DB_ROLE_PREFLIGHT=True), mock.patch.object(
            db_preflight, "verify_app_role", return_value=["has BYPASSRLS"]
        ), mock.patch.object(celery_config.os, "_exit") as exit_:
            celery_config.refuse_unrestricted_db_role()
        exit_.assert_called_once_with(1)

    def test_restricted_role_lets_workers_start(self):
        from django.test import override_settings

        from config import celery as celery_config

        with override_settings(DB_ROLE_PREFLIGHT=True), mock.patch.object(celery_config.os, "_exit") as exit_:
            celery_config.refuse_unrestricted_db_role()
        exit_.assert_not_called()

    def test_both_worker_and_beat_are_guarded(self):
        from celery.signals import beat_init, worker_init

        from config import celery as celery_config

        for signal in (worker_init, beat_init):
            receivers = [r[1]() if callable(r[1]) else r[1] for r in signal.receivers]
            self.assertIn(celery_config.refuse_unrestricted_db_role, receivers, signal)
