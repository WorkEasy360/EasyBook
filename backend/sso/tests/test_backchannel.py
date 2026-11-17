"""OpenID Connect Back-Channel Logout: Workeasy360 POSTs a signed logout token, EasyBook ends the sessions it
names (sso/backchannel.py, sso/sessions.py; the receiver itself is the connector's).

A "device" below is one Workeasy360 sign-in (one ``sid``) and the token pair EasyBook issued for it, used as
the BFF uses it: the access token on API calls, the refresh token to rotate."""

import io
import json
import time
from unittest import mock
from urllib.parse import urlencode

from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken
from workeasy360_sso.models import EndedProviderSession, ProductSession, ReceivedLogoutToken

from accounts.models import Organization, User
from audit.models import AuditLog
from core.tenancy import tenant_context
from sso.tests.fakes import CLIENT_ID, FORGER_PRIVATE_PEM, REDIRECT_URI
from sso.tests.test_sign_in import W360_ORG, SignInTestCase

URL = "/api/v1/auth/workeasy360/backchannel-logout/"
PERSON = "w360-user-1"


class Device:
    def __init__(self, case, response):
        case.assertEqual(response.status_code, 200, response.content)
        self.case = case
        self.access = response.data["access"]
        self.refresh = response.data["refresh"]

    def api_works(self):
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")
        return api.get("/api/v1/auth/me/").status_code == 200

    def refreshes(self):
        response = APIClient().post("/api/v1/auth/refresh/", {"refresh": self.refresh}, format="json")
        if response.status_code == 200:
            self.access, self.refresh = response.data["access"], response.data["refresh"]
            return True
        return False


class BackchannelTestCase(SignInTestCase):
    def device(self, *, sid, sub=PERSON, email="asha@example.com"):
        return Device(self, self.sign_in(sid=sid, sub=sub, email=email))

    def deliver(self, token):
        """POST the logout token as Workeasy360 does: form-encoded, no cookies, no CSRF token."""
        return self.client_class(enforce_csrf_checks=True).post(
            URL, urlencode({"logout_token": token}), content_type="application/x-www-form-urlencoded"
        )

    def logout(self, **claims):
        return self.deliver(self.provider.logout_token(**claims))


class SignOutOfOneDeviceTests(BackchannelTestCase):
    """A token naming ``sid``: the EasyBook sessions that Workeasy360 sign-in opened, nothing else."""

    def test_only_the_named_sign_in_ends(self):
        phone, laptop = self.device(sid="sid-phone"), self.device(sid="sid-laptop")
        self.assertEqual(self.logout(sub=PERSON, sid="sid-phone").status_code, 200)

        self.assertFalse(phone.api_works())  # at once, not when its access token expires
        self.assertFalse(phone.refreshes())
        self.assertTrue(laptop.api_works())
        self.assertTrue(laptop.refreshes())
        ended = ProductSession.objects.get(provider_sid="sid-phone")
        self.assertEqual(ended.end_reason, ProductSession.EndReason.BACKCHANNEL)
        self.assertIsNone(ProductSession.objects.get(provider_sid="sid-laptop").ended_at)

    def test_a_token_naming_only_the_sid_ends_that_sign_in(self):
        phone, laptop = self.device(sid="sid-phone"), self.device(sid="sid-laptop")
        self.assertEqual(self.logout(sid="sid-phone").status_code, 200)
        self.assertFalse(phone.api_works())
        self.assertTrue(laptop.api_works())

    def test_tokens_rotated_after_the_sign_in_end_with_it(self):
        phone = self.device(sid="sid-phone")
        self.assertTrue(phone.refreshes())  # a new pair, the same session
        self.assertTrue(phone.refreshes())
        self.logout(sub=PERSON, sid="sid-phone")
        self.assertFalse(phone.api_works())
        self.assertFalse(phone.refreshes())

    def test_another_persons_sid_ends_nothing_here(self):
        phone = self.device(sid="sid-phone")
        self.assertEqual(self.logout(sub="w360-someone-else", sid="sid-phone").status_code, 200)
        self.assertTrue(phone.api_works())

    def test_it_is_audited_in_the_organization_signed_in_to(self):
        self.device(sid="sid-phone")
        self.logout(sub=PERSON, sid="sid-phone")
        organization = Organization.objects.get(workeasy360_organization_id=W360_ORG)
        with tenant_context(organization_id=organization.id):
            row = AuditLog.objects.get(object_type="sign_in", action=AuditLog.Action.DELETE)
        self.assertEqual(row.object_id, str(User.objects.get(email="asha@example.com").id))
        self.assertEqual(row.changes["method"], "workeasy360_backchannel")
        self.assertEqual(row.changes["scope"], "session")
        self.assertEqual(row.changes["workeasy360_subject"], PERSON)


class SignOutEverywhereTests(BackchannelTestCase):
    """A token naming only ``sub``: every EasyBook session of that person."""

    def test_every_session_of_the_person_ends_and_nobody_elses(self):
        phone, laptop = self.device(sid="sid-phone"), self.device(sid="sid-laptop")
        colleague = self.device(sid="sid-colleague", sub="w360-user-2", email="ravi@example.com")
        self.assertEqual(self.logout(sub=PERSON).status_code, 200)
        for device in (phone, laptop):
            self.assertFalse(device.api_works())
            self.assertFalse(device.refreshes())
        self.assertTrue(colleague.api_works())
        self.assertTrue(colleague.refreshes())
        self.assertEqual(
            ProductSession.objects.filter(identity__subject=PERSON, end_reason="backchannel_logout").count(), 2
        )

    def test_sessions_from_before_the_connector_are_revoked_too(self):
        self.device(sid="sid-phone")
        user = User.objects.get(email="asha@example.com")
        # A session no Workeasy360 sign-in of this release opened (an earlier release's, or a password's):
        # its tokens name no session record.
        older = RefreshToken.for_user(user)
        self.assertEqual(self.logout(sub=PERSON).status_code, 200)
        response = APIClient().post("/api/v1/auth/refresh/", {"refresh": str(older)}, format="json")
        self.assertEqual(response.status_code, 401)  # it ends at its next refresh

    def test_a_sid_names_one_sign_in_so_older_sessions_are_left_alone(self):
        self.device(sid="sid-phone")
        older = RefreshToken.for_user(User.objects.get(email="asha@example.com"))
        self.logout(sub=PERSON, sid="sid-phone")
        response = APIClient().post("/api/v1/auth/refresh/", {"refresh": str(older)}, format="json")
        self.assertEqual(response.status_code, 200)

    def test_it_is_audited(self):
        self.device(sid="sid-phone")
        self.logout(sub=PERSON)
        organization = Organization.objects.get(workeasy360_organization_id=W360_ORG)
        with tenant_context(organization_id=organization.id):
            row = AuditLog.objects.get(object_type="sign_in", action=AuditLog.Action.DELETE)
        self.assertEqual(row.changes["scope"], "everywhere")
        self.assertGreaterEqual(row.changes["refresh_tokens_revoked"], 1)


class LogoutTokenVerificationTests(BackchannelTestCase):
    """Verified exactly as an ID token is, plus Back-Channel Logout 1.0 section 2.6; anything else is 400 and
    ends nothing."""

    def assert_refused(self, token):
        phone = self.device(sid="sid-phone")
        response = self.deliver(token)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(json.loads(response.content), {"error": "invalid_request"})
        self.assertTrue(phone.api_works())
        self.assertFalse(ReceivedLogoutToken.objects.exists())
        self.assertFalse(ProductSession.objects.filter(ended_at__isnull=False).exists())

    def test_a_forged_signature_is_refused(self):
        self.assert_refused(self.provider.logout_token(sub=PERSON, sid="sid-phone", key=FORGER_PRIVATE_PEM))

    def test_an_unknown_key_is_refused(self):
        self.assert_refused(self.provider.logout_token(sub=PERSON, sid="sid-phone", kid="provider-2"))

    def test_another_audience_is_refused(self):
        self.assert_refused(self.provider.logout_token(sub=PERSON, sid="sid-phone", aud="another-product"))

    def test_another_issuer_is_refused(self):
        self.assert_refused(self.provider.logout_token(sub=PERSON, sid="sid-phone", iss="https://evil.example"))

    def test_a_token_carrying_a_nonce_is_refused(self):
        self.assert_refused(self.provider.logout_token(sub=PERSON, sid="sid-phone", nonce="n"))

    def test_a_token_without_the_logout_event_is_refused(self):
        self.assert_refused(self.provider.logout_token(sub=PERSON, sid="sid-phone", events={}))

    def test_a_token_not_typed_as_a_logout_token_is_refused(self):
        self.assert_refused(self.provider.logout_token(sub=PERSON, sid="sid-phone", headers={"typ": "JWT"}))

    def test_an_id_token_is_not_a_logout_token(self):
        self.assert_refused(self.provider.sign(self.provider.claims(sid="sid-phone")))

    def test_a_token_naming_neither_sub_nor_sid_is_refused(self):
        self.assert_refused(self.provider.logout_token())

    def test_an_expired_token_is_refused(self):
        now = int(time.time())
        self.assert_refused(self.provider.logout_token(sub=PERSON, iat=now - 900, exp=now - 600))

    def test_an_old_token_is_refused_even_if_not_expired(self):
        now = int(time.time())
        self.assert_refused(self.provider.logout_token(sub=PERSON, iat=now - 700, exp=now + 120))

    def test_garbage_is_refused(self):
        self.assert_refused("not-a-token")

    def test_a_replayed_token_is_refused_and_ends_nothing_new(self):
        self.device(sid="sid-phone")
        token = self.provider.logout_token(sub=PERSON)
        self.assertEqual(self.deliver(token).status_code, 200)
        later = self.device(sid="sid-later")  # signed in again since
        replayed = self.deliver(token)
        self.assertEqual(replayed.status_code, 400)
        self.assertTrue(later.api_works())
        self.assertEqual(ReceivedLogoutToken.objects.count(), 1)


class DeliveryTests(BackchannelTestCase):
    def test_a_person_unknown_here_is_acknowledged(self):
        self.assertEqual(self.logout(sub="w360-nobody-here").status_code, 200)

    def test_an_ended_sign_in_still_in_flight_is_refused(self):
        # The browser was at Workeasy360 when that sign-in ended: its ID token names the ended sid.
        started = self.start(W360_ORG)
        self.assertEqual(self.logout(sub=PERSON, sid="sid-ended").status_code, 200)
        self.assertTrue(EndedProviderSession.objects.filter(provider_sid="sid-ended").exists())
        response = self.complete(started, sid="sid-ended")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "flow_expired")  # the BFF says "start again"
        self.assertFalse(User.objects.exists())

    def test_a_failure_records_nothing_and_asks_for_the_token_again(self):
        phone = self.device(sid="sid-phone")
        token = self.provider.logout_token(sub=PERSON)
        with mock.patch("sso.backchannel.revoke_refresh_tokens", side_effect=RuntimeError("database hiccup")):
            response = self.deliver(token)
        self.assertEqual(response.status_code, 503)
        self.assertTrue(phone.api_works())  # nothing ended...
        self.assertFalse(ReceivedLogoutToken.objects.exists())  # ...and the jti is not spent
        self.assertEqual(self.deliver(token).status_code, 200)  # so the retry is accepted
        self.assertFalse(phone.api_works())

    def test_only_post_is_served(self):
        self.assertEqual(self.client.get(URL).status_code, 405)

    @override_settings(WORKEASY360_BACKCHANNEL_LOGOUT=False)
    def test_switched_off_it_is_not_served(self):
        phone = self.device(sid="sid-phone")
        self.assertEqual(self.logout(sub=PERSON).status_code, 404)
        self.assertTrue(phone.api_works())

    def test_the_route_is_under_the_prefix_the_edge_routes_to_django_on_the_redirect_uris_origin(self):
        self.assertEqual(reverse("workeasy360_sso:backchannel_logout"), URL)
        self.assertTrue(URL.startswith("/api/v1/"))  # infrastructure/terraform/alb.tf
        out = io.StringIO()
        call_command("w360sso_manifest", "--login-url", "https://book.workeasy360.test/login", stdout=out)
        manifest = json.loads(out.getvalue())
        self.assertEqual(manifest["redirect_uris"], [REDIRECT_URI])
        self.assertEqual(manifest["backchannel_logout_uri"], f"https://book.workeasy360.test{URL}")
        self.assertEqual(manifest["product_code"], "easybook")

    def test_tokens_of_a_session_with_a_tampered_session_claim_are_refused(self):
        phone = self.device(sid="sid-phone")
        token = RefreshToken(phone.refresh)
        token["workeasy360_session"] = "not-a-session-id"
        phone.refresh, phone.access = str(token), str(token.access_token)
        # Re-signed with EasyBook's own key, so only a claim EasyBook never writes is wrong.
        self.assertFalse(phone.api_works())
        self.assertFalse(phone.refreshes())

    def test_the_client_id_is_the_audience(self):
        self.device(sid="sid-phone")
        self.assertEqual(self.logout(sub=PERSON, aud=[CLIENT_ID]).status_code, 200)
