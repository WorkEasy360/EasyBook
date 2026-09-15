from rest_framework.test import APITestCase

from accounts.models import User
from core.tests.factories import make_currency, make_user


class RegistrationAndLoginTests(APITestCase):
    def test_register_creates_user_with_hashed_password(self):
        response = self.client.post(
            "/api/v1/auth/register/",
            {"email": "new@example.com", "password": "strongpassword123", "first_name": "New"},
        )
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(email="new@example.com")
        self.assertNotEqual(user.password, "strongpassword123")
        self.assertTrue(user.check_password("strongpassword123"))

    def test_register_rejects_duplicate_email(self):
        make_user(email="dupe@example.com")
        response = self.client.post(
            "/api/v1/auth/register/",
            {"email": "dupe@example.com", "password": "strongpassword123"},
        )
        self.assertEqual(response.status_code, 400)

    def test_login_returns_jwt_pair(self):
        make_user(email="login@example.com", password="strongpassword123")
        response = self.client.post(
            "/api/v1/auth/login/", {"email": "login@example.com", "password": "strongpassword123"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

    def test_login_rejects_wrong_password(self):
        make_user(email="login2@example.com", password="strongpassword123")
        response = self.client.post(
            "/api/v1/auth/login/", {"email": "login2@example.com", "password": "wrong"}
        )
        self.assertEqual(response.status_code, 401)

    def test_me_requires_authentication(self):
        response = self.client.get("/api/v1/auth/me/")
        self.assertEqual(response.status_code, 401)

    def test_me_returns_current_user(self):
        user = make_user(email="me@example.com", password="strongpassword123")
        self.client.force_authenticate(user=user)
        response = self.client.get("/api/v1/auth/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["email"], "me@example.com")


class OrganizationCreationTests(APITestCase):
    def test_creating_organization_makes_creator_owner(self):
        user = make_user(email="founder@example.com", password="strongpassword123")
        currency = make_currency("USD")
        self.client.force_authenticate(user=user)
        response = self.client.post(
            "/api/v1/organizations/",
            {"name": "Founder Co", "default_currency": currency.code},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["role"], "owner")
