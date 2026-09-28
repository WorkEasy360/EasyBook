"""Refresh tokens can be revoked: rotation retires the old token, and logout
retires the presented one.

Before: BLACKLIST_AFTER_ROTATION was on but the token_blacklist app was not
installed, so SimpleJWT's blacklist() did not exist and the AttributeError was
swallowed — a captured refresh token stayed usable for its full lifetime, and
there was no logout endpoint at all.
"""
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from core.tests.factories import make_user

LOGIN = "/api/v1/auth/login/"
REFRESH = "/api/v1/auth/refresh/"
LOGOUT = "/api/v1/auth/logout/"


class RefreshTokenRevocationTests(TestCase):
    def setUp(self):
        cache.clear()  # the auth throttle's counters live in the (shared, in-process) cache
        make_user("revoke@example.com", "strongpassword123")
        self.client = APIClient()

    def _login(self):
        response = self.client.post(LOGIN, {"email": "revoke@example.com", "password": "strongpassword123"}, format="json")
        self.assertEqual(response.status_code, 200)
        return response.data["refresh"]

    def _refresh(self, token):
        return self.client.post(REFRESH, {"refresh": token}, format="json")

    def test_blacklist_app_is_installed(self):
        self.assertIn("rest_framework_simplejwt.token_blacklist", settings.INSTALLED_APPS)

    def test_rotated_refresh_token_cannot_be_reused(self):
        old = self._login()
        first = self._refresh(old)
        self.assertEqual(first.status_code, 200)
        self.assertNotEqual(first.data["refresh"], old)

        reuse = self._refresh(old)
        self.assertEqual(reuse.status_code, 401)
        # The replacement keeps working.
        self.assertEqual(self._refresh(first.data["refresh"]).status_code, 200)

    def test_logout_revokes_the_refresh_token(self):
        token = self._login()
        response = self.client.post(LOGOUT, {"refresh": token}, format="json")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self._refresh(token).status_code, 401)

    def test_logout_is_idempotent(self):
        token = self._login()
        for _ in range(3):
            self.assertEqual(self.client.post(LOGOUT, {"refresh": token}, format="json").status_code, 204)
        self.assertEqual(self._refresh(token).status_code, 401)

    def test_logout_does_not_reveal_whether_a_token_was_valid(self):
        for body in ({"refresh": "not-a-token"}, {"refresh": ""}, {}, {"refresh": 123}, {"refresh": "x" * 10_000}):
            response = self.client.post(LOGOUT, body, format="json")
            self.assertEqual(response.status_code, 204, body)
            self.assertFalse(response.content)

    def test_logout_after_rotation_revokes_the_current_token(self):
        rotated = self._refresh(self._login()).data["refresh"]
        self.assertEqual(self.client.post(LOGOUT, {"refresh": rotated}, format="json").status_code, 204)
        self.assertEqual(self._refresh(rotated).status_code, 401)

    def test_login_still_works_after_logout(self):
        token = self._login()
        self.client.post(LOGOUT, {"refresh": token}, format="json")
        self.assertEqual(self._refresh(self._login()).status_code, 200)
