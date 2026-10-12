"""End to end through the API the BFF calls: start, then callback, against a
fake Workeasy360 that signs real tokens."""

from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from accounts.models import Membership, Organization, User
from audit.models import AuditLog
from authz.roles import Role
from core.tenancy import tenant_context
from core.tests.factories import make_org_with_owner, make_user
from sso import config, entitlement
from sso.models import ExternalIdentity
from sso.oidc import Workeasy360Client
from sso.tests.fakes import (
    CLIENT_ID,
    CLIENT_PRIVATE_PEM,
    ISSUER,
    KEY_ID,
    REDIRECT_URI,
    FakeWorkeasy360,
    authorize_params,
)

W360_ORG = "7b0f4f7e-2a3c-4f4e-9a59-3f5c1b2d4e6a"

CONFIGURED = {
    "WORKEASY360_ISSUER": ISSUER,
    "WORKEASY360_CLIENT_ID": CLIENT_ID,
    "WORKEASY360_REDIRECT_URI": REDIRECT_URI,
    "WORKEASY360_KEY_ID": KEY_ID,
    "WORKEASY360_PRIVATE_KEY": CLIENT_PRIVATE_PEM,
    "WORKEASY360_PRIVATE_KEY_FILE": "",
    "WORKEASY360_DEFAULT_ROLE": "staff",
    "WORKEASY360_DEFAULT_CURRENCY": "INR",
}


@override_settings(**CONFIGURED)
class SignInTestCase(APITestCase):
    def setUp(self):
        self.provider = FakeWorkeasy360()
        client = Workeasy360Client(
            issuer=ISSUER, client_id=CLIENT_ID, redirect_uri=REDIRECT_URI, private_key_pem=CLIENT_PRIVATE_PEM,
            key_id=KEY_ID, transport=self.provider.transport,
        )
        for target in ("sso.flow.get_client", "sso.entitlement.get_client"):
            patcher = mock.patch(target, return_value=client)
            patcher.start()
            self.addCleanup(patcher.stop)
        config.get_client.cache_clear()
        # Every call here is throttle_scope "auth" (20/min); the bucket lives
        # in the shared locmem cache and must not leak into other suites. The
        # entitlement re-check also keeps a per-process copy of its answers.
        cache.clear()
        entitlement.clear_local()
        self.addCleanup(cache.clear)
        self.addCleanup(entitlement.clear_local)

    def start(self, organization=""):
        response = self.client.post("/api/v1/auth/workeasy360/start/", {"organization": organization})
        self.assertEqual(response.status_code, 200, response.content)
        return response.data

    def sign_in(self, *, organization=W360_ORG, **claims):
        started = self.start(organization)
        params = authorize_params(started["authorization_url"])
        code = self.provider.issue_code(
            nonce=params["nonce"], claims=self.provider.claims(organization_id=organization, **claims)
        )
        return self.client.post(
            "/api/v1/auth/workeasy360/callback/",
            {"code": code, "state": params["state"], "flow": started["flow"]},
        )

    def membership(self, user, organization):
        with tenant_context(user_id=user.id):
            return Membership.all_objects.get(user=user, organization=organization)


class MethodsAndStartTests(SignInTestCase):
    def test_methods_report_what_the_login_page_offers(self):
        response = self.client.get("/api/v1/auth/methods/")
        self.assertEqual(response.data, {"password": True, "workeasy360": True, "workeasy360_sign_out": False})
        with override_settings(WORKEASY360_SSO_ONLY=True, WORKEASY360_ISSUER=""):
            self.assertEqual(
                self.client.get("/api/v1/auth/methods/").data,
                {"password": False, "workeasy360": False, "workeasy360_sign_out": False},
            )
        with override_settings(WORKEASY360_POST_LOGOUT_REDIRECT_URI="https://book.workeasy360.test/login"):
            self.assertTrue(self.client.get("/api/v1/auth/methods/").data["workeasy360_sign_out"])

    def test_start_forwards_a_uuid_organization_and_drops_anything_else(self):
        self.assertEqual(authorize_params(self.start(W360_ORG)["authorization_url"])["organization"], W360_ORG)
        self.assertNotIn("organization", authorize_params(self.start("not-a-uuid")["authorization_url"]))
        self.assertNotIn("organization", authorize_params(self.start()["authorization_url"]))

    def test_start_without_configuration_is_unavailable(self):
        with override_settings(WORKEASY360_ISSUER=""):
            response = self.client.post("/api/v1/auth/workeasy360/start/", {})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["error"]["code"], "workeasy360_unavailable")


class FirstSignInTests(SignInTestCase):
    def test_first_sign_in_provisions_user_organization_and_owner(self):
        response = self.sign_in()
        self.assertEqual(response.status_code, 200, response.content)

        user = User.objects.get(email="asha@example.com")
        self.assertFalse(user.has_usable_password())
        self.assertEqual((user.first_name, user.last_name), ("Asha", "Rao"))
        organization = Organization.objects.get(workeasy360_organization_id=W360_ORG)
        self.assertEqual(organization.name, "Acme Traders")
        self.assertEqual(organization.default_currency_id, "INR")
        self.assertEqual(self.membership(user, organization).role, Role.OWNER)
        self.assertTrue(ExternalIdentity.objects.filter(issuer=ISSUER, subject="w360-user-1", user=user).exists())

        self.assertEqual(response.data["organization_id"], str(organization.id))
        self.assertTrue(response.data["organization_created"])
        self.assertEqual(AccessToken(response.data["access"])["user_id"], str(user.id))
        with tenant_context(organization_id=organization.id):
            self.assertEqual(
                set(AuditLog.objects.filter(organization=organization).values_list("object_type", flat=True)),
                {"organization", "sign_in"},
            )

    def test_tokens_work_against_the_api(self):
        response = self.sign_in()
        me = self.client.get("/api/v1/auth/me/", HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.data["email"], "asha@example.com")

    def test_a_colleague_joins_the_existing_organization_with_the_default_role(self):
        self.sign_in()
        response = self.sign_in(sub="w360-user-2", email="ravi@example.com", name="Ravi")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.data["organization_created"])
        organization = Organization.objects.get(workeasy360_organization_id=W360_ORG)
        self.assertEqual(self.membership(User.objects.get(email="ravi@example.com"), organization).role, Role.STAFF)

    def test_returning_user_is_the_same_account(self):
        first = self.sign_in()
        again = self.sign_in(email="asha.new@example.com")
        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(AccessToken(first.data["access"])["user_id"], AccessToken(again.data["access"])["user_id"])
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(Organization.objects.count(), 1)

    def test_one_person_in_two_workeasy360_organizations_gets_two_here(self):
        self.sign_in()
        other = "0d6c0a3e-8e52-4a63-8b0e-5a1d2c3b4f50"
        response = self.sign_in(organization=other, organization_name="Second Co")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Organization.objects.get(workeasy360_organization_id=other).name, "Second Co")

    def test_without_organization_name_a_neutral_label_is_used(self):
        self.sign_in(organization_name=None)
        self.assertEqual(Organization.objects.get(workeasy360_organization_id=W360_ORG).name, "My organization")


class LinkingTests(SignInTestCase):
    def test_existing_account_is_linked_by_a_verified_address_and_its_password_retired(self):
        _, owner, _ = make_org_with_owner(email="asha@example.com")
        old_refresh = RefreshToken.for_user(owner)  # a session the password opened earlier
        response = self.sign_in(email="Asha@Example.com")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(AccessToken(response.data["access"])["user_id"], str(owner.id))
        self.assertTrue(ExternalIdentity.objects.filter(user=owner).exists())
        owner.refresh_from_db()
        # EasyBook never verified the address, so the password may belong to
        # someone who pre-registered it: it goes, with every session it opened.
        self.assertFalse(owner.has_usable_password())
        refreshed = self.client.post("/api/v1/auth/refresh/", {"refresh": str(old_refresh)})
        self.assertEqual(refreshed.status_code, 401)
        # The session the sign-in itself issued still works.
        self.assertEqual(
            self.client.post("/api/v1/auth/refresh/", {"refresh": response.data["refresh"]}).status_code, 200
        )

    def test_an_unverified_address_never_takes_over_an_account(self):
        owner = make_user(email="asha@example.com")
        response = self.sign_in(email_verified=False)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "email_unverified")
        self.assertFalse(ExternalIdentity.objects.filter(user=owner).exists())
        self.assertFalse(Organization.objects.filter(workeasy360_organization_id=W360_ORG).exists())

    def test_an_account_linked_to_another_workeasy360_user_is_not_relinked(self):
        self.sign_in()
        response = self.sign_in(sub="w360-impostor")  # same verified address, different subject
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "account_conflict")

    def test_an_unverified_address_never_creates_an_account(self):
        response = self.sign_in(email_verified=False)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "email_unverified")
        self.assertFalse(User.objects.exists())

    def test_email_claim_is_required(self):
        response = self.sign_in(email=None)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "email_required")

    def test_a_removed_member_stays_removed(self):
        self.sign_in()
        user = User.objects.get(email="asha@example.com")
        organization = Organization.objects.get(workeasy360_organization_id=W360_ORG)
        membership = self.membership(user, organization)
        with tenant_context(user_id=user.id):
            Membership.all_objects.filter(id=membership.id).update(is_active=False)
        response = self.sign_in()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "membership_inactive")

    def test_a_deactivated_account_cannot_sign_in(self):
        self.sign_in()
        User.objects.filter(email="asha@example.com").update(is_active=False)
        response = self.sign_in()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "account_disabled")

    def test_existing_organization_linked_by_staff_is_joined_not_duplicated(self):
        organization, _owner, _ = make_org_with_owner(org_name="Legacy Books", email="owner@example.com")
        Organization.objects.filter(id=organization.id).update(workeasy360_organization_id=W360_ORG)
        response = self.sign_in()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["organization_id"], str(organization.id))
        self.assertEqual(self.membership(User.objects.get(email="asha@example.com"), organization).role, Role.STAFF)


class CallbackHardeningTests(SignInTestCase):
    def test_state_mismatch_is_refused(self):
        started = self.start(W360_ORG)
        params = authorize_params(started["authorization_url"])
        code = self.provider.issue_code(nonce=params["nonce"])
        response = self.client.post(
            "/api/v1/auth/workeasy360/callback/", {"code": code, "state": "forged", "flow": started["flow"]}
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "state_mismatch")

    def test_a_tampered_flow_is_refused(self):
        started = self.start(W360_ORG)
        params = authorize_params(started["authorization_url"])
        response = self.client.post(
            "/api/v1/auth/workeasy360/callback/",
            {"code": "x", "state": params["state"], "flow": started["flow"][:-2] + "AA"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "flow_expired")

    def test_an_expired_flow_is_refused(self):
        started = self.start(W360_ORG)
        params = authorize_params(started["authorization_url"])
        with mock.patch("sso.flow.FLOW_MAX_AGE_SECONDS", -1):
            response = self.client.post(
                "/api/v1/auth/workeasy360/callback/",
                {"code": "x", "state": params["state"], "flow": started["flow"]},
            )
        self.assertEqual(response.data["error"]["code"], "flow_expired")

    def test_a_token_for_another_product_is_refused(self):
        response = self.sign_in(product_code="easy_crm")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "wrong_product")

    def test_a_token_for_another_organization_than_the_launcher_asked_is_refused(self):
        started = self.start(W360_ORG)
        params = authorize_params(started["authorization_url"])
        code = self.provider.issue_code(
            nonce=params["nonce"],
            claims=self.provider.claims(organization_id="0d6c0a3e-8e52-4a63-8b0e-5a1d2c3b4f50"),
        )
        response = self.client.post(
            "/api/v1/auth/workeasy360/callback/", {"code": code, "state": params["state"], "flow": started["flow"]}
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "wrong_organization")
        self.assertFalse(Organization.objects.exists())

    def test_malformed_callback_is_rejected_before_any_work(self):
        response = self.client.post("/api/v1/auth/workeasy360/callback/", {"code": "x" * 5000, "state": "s", "flow": "f"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.provider.calls, [])

    def test_provider_outage_is_503_not_a_refusal(self):
        self.provider.token_status = (503, {"error": "temporarily_unavailable"})
        response = self.sign_in()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["error"]["code"], "workeasy360_unavailable")


@override_settings(WORKEASY360_SSO_ONLY=True)
class Workeasy360OnlyTests(SignInTestCase):
    def test_password_sign_in_is_refused(self):
        make_user(email="old@example.com")
        response = self.client.post("/api/v1/auth/login/", {"email": "old@example.com", "password": "strongpassword123"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "managed_by_workeasy360")

    def test_registration_is_refused(self):
        response = self.client.post("/api/v1/auth/register/", {"email": "n@example.com", "password": "strongpassword123"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(email="n@example.com").exists())

    def test_creating_an_organization_here_is_refused(self):
        access = self.sign_in().data["access"]
        response = self.client.post(
            "/api/v1/organizations/", {"name": "Side Co", "default_currency": "INR"},
            HTTP_AUTHORIZATION=f"Bearer {access}",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Organization.objects.filter(name="Side Co").exists())

    def test_workeasy360_sign_in_still_works(self):
        self.assertEqual(self.sign_in().status_code, 200)


class SignOutOfWorkeasy360Tests(SignInTestCase):
    """RP-initiated logout: where "sign out of EasyBook and Workeasy360" sends the browser."""

    def end_session(self):
        with mock.patch("sso.views.get_client", return_value=self._client()):
            return self.client.get("/api/v1/auth/workeasy360/end-session/")

    def _client(self):
        return Workeasy360Client(
            issuer=ISSUER, client_id=CLIENT_ID, redirect_uri=REDIRECT_URI, private_key_pem=CLIENT_PRIVATE_PEM,
            key_id=KEY_ID, transport=self.provider.transport,
        )

    @override_settings(WORKEASY360_POST_LOGOUT_REDIRECT_URI="https://book.workeasy360.test/login")
    def test_names_only_our_client_and_our_registered_uri(self):
        from urllib.parse import parse_qs, urlsplit

        url = self.end_session().data["url"]
        self.assertTrue(url.startswith(f"{ISSUER}/oauth/logout?"))
        self.assertEqual(
            parse_qs(urlsplit(url).query),
            {"client_id": [CLIENT_ID], "post_logout_redirect_uri": ["https://book.workeasy360.test/login"]},
        )

    def test_is_empty_without_the_setting(self):
        self.assertEqual(self.end_session().data, {"url": ""})

    @override_settings(WORKEASY360_POST_LOGOUT_REDIRECT_URI="https://book.workeasy360.test/login")
    def test_is_empty_when_the_provider_offers_no_end_session(self):
        self.provider.advertise_end_session = False
        self.assertEqual(self.end_session().data, {"url": ""})
