from unittest.mock import MagicMock, patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from ai.checks import check_pgvector_extension_installed


class _FakeAppConfig:
    def __init__(self, label):
        self.label = label


class PgvectorPreflightTests(SimpleTestCase):
    def test_ignores_apps_other_than_ai(self):
        # No DB access attempted at all for an unrelated app — patching
        # connection would make a wrongly-scoped check obvious via MagicMock
        # never being called.
        with patch("django.db.connection") as connection:
            check_pgvector_extension_installed(sender=None, app_config=_FakeAppConfig("accounting"))
            connection.cursor.assert_not_called()

    def test_passes_when_extension_is_installed(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (1,)
        with patch("django.db.connection") as connection:
            connection.cursor.return_value.__enter__.return_value = cursor
            check_pgvector_extension_installed(sender=None, app_config=_FakeAppConfig("ai"))

    def test_raises_clearly_when_extension_is_missing(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        with patch("django.db.connection") as connection:
            connection.cursor.return_value.__enter__.return_value = cursor
            with self.assertRaisesMessage(ImproperlyConfigured, "CREATE EXTENSION vector"):
                check_pgvector_extension_installed(sender=None, app_config=_FakeAppConfig("ai"))
