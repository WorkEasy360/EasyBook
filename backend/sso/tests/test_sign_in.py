"""End to end through the API the BFF calls: start, then callback, against a
fake Workeasy360 that signs real tokens, with the workeasy360-sso connector
doing the protocol."""

import io
from unittest import mock

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core import signing
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import SystemCheckError
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APITestCase
from rest_framework.throttling import SimpleRateThrottle
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
from workeasy360_sso import discovery
from workeasy360_sso.models import FederatedIdentity, PendingSignIn, ProductSession

from accounts.models import Membership, Organization, User
from audit.models import AuditLog
from authz.roles import Role
from core.tenancy import tenant_context
from core.tests.factories import make_membership, make_org_with_owner, make_organization, make_user
from sso import checks, entitlement
from sso.flow import FLOW_MAX_AGE_SECONDS
from sso.models import ExternalIdentity
from sso.sessions import SESSION_CLAIM
from sso.tests.fakes import (
    CLIENT_ID,
    CLIENT_PRIVATE_PEM,
    ISSUER,
    KEY_ID,
    REDIRECT_URI,
    Clock,
    FakeWorkeasy360,
    authorize_params,
)
from sso.views import Workeasy360CallbackView, Workeasy360StartView

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


def behind_fake_provider(case):
    """Point the connector at a fresh ``FakeWorkeasy360`` for one test, with its entitlement clock in the
    test's hands and nothing cached from another test. Returns (provider, clock)."""
    provider = FakeWorkeasy360()
    transport = override_settings(WORKEASY360_HTTP_TRANSPORT=provider.httpx_transport)
    transport.enable()
    case.addCleanup(transport.disable)
    clock = Clock()
    patcher = mock.patch("workeasy360_sso.entitlements._clock", clock)
    patcher.start()
    case.addCleanup(patcher.stop)
    # Every call here is throttle_scope "auth" (20/min); the bucket lives in the shared locmem cache and must
    # not leak into other suites. The entitlement re-check also keeps a per-process copy of its answers, and
    # the connector caches discovery and the key set per process.
    for reset in (cache.clear, entitlement.clear_local, discovery.reset_cache):
        reset()
        case.addCleanup(reset)
    return provider, clock


@override_settings(**CONFIGURED)
class SignInTestCase(APITestCase):
    def setUp(self):
        self.provider, self.clock = behind_fake_provider(self)

    def start(self, organization=""):
        response = self.client.post("/api/v1/auth/workeasy360/start/", {"organization": organization})
        self.assertEqual(response.status_code, 200, response.content)
        return response.data

    def complete(self, started, *, code=None, state=None, **claims):
        params = authorize_params(started["authorization_url"])
        if code is None:
            code = self.provider.issue_code(
                nonce=params["nonce"], code_challenge=params["code_challenge"],
                claims=self.provider.claims(**claims),
            )
        return self.client.post(
            "/api/v1/auth/workeasy360/callback/",
            {"code": code, "state": params["state"] if state is None else state, "flow": started["flow"]},
        )

    def sign_in(self, *, organization=W360_ORG, **claims):
        return self.complete(self.start(organization), organization_id=organization, **claims)

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

    def test_start_with_an_unreachable_provider_is_unavailable(self):
        self.provider.discovery_issuer = "https://evil.example"  # a document describing someone else
        response = self.client.post("/api/v1/auth/workeasy360/start/", {})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["error"]["code"], "workeasy360_unavailable")
        self.assertFalse(PendingSignIn.objects.exists())

    def test_the_flow_the_bff_keeps_names_no_state_nonce_or_verifier(self):
        started = self.start(W360_ORG)
        params = authorize_params(started["authorization_url"])
        kept = signing.loads(started["flow"], salt="sso.workeasy360.flow", max_age=FLOW_MAX_AGE_SECONDS)
        self.assertEqual(set(kept), {"b"})  # only this browser's binding: the rest stays server-side
        for secret in (params["state"], params["nonce"]):
            self.assertNotIn(secret, str(kept))
        pending = PendingSignIn.objects.get()
        self.assertNotEqual(pending.state_hash, params["state"])  # hashed at rest
        self.assertEqual(str(pending.organization_hint), W360_ORG)
        self.assertEqual((pending.expires_at - pending.created_at).total_seconds(), FLOW_MAX_AGE_SECONDS)


class FirstSignInTests(SignInTestCase):
    def test_first_sign_in_provisions_user_organization_and_owner(self):
        response = self.sign_in()
        self.assertEqual(response.status_code, 200, response.content)

        user = User.objects.get(email="asha@example.com")
        self.assertFalse(user.has_usable_password())
        self.assertIsNotNone(user.last_login)
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


class SessionRecordTests(SignInTestCase):
    """What the connector records about a sign-in, so back-channel logout can end it (sso/sessions.py)."""

    def test_the_sign_in_is_recorded_and_its_tokens_name_the_record(self):
        response = self.sign_in(sid="w360-sign-in-1")
        self.assertEqual(response.status_code, 200, response.content)
        record = ProductSession.objects.get()
        self.assertEqual(record.provider_sid, "w360-sign-in-1")
        self.assertEqual(str(record.organization_id), W360_ORG)  # the Workeasy360 organization, not ours
        self.assertEqual((record.identity.issuer, record.identity.subject), (ISSUER, "w360-user-1"))
        self.assertIsNone(record.ended_at)
        self.assertEqual(AccessToken(response.data["access"])[SESSION_CLAIM], str(record.id))
        refresh = RefreshToken(response.data["refresh"])
        self.assertEqual(refresh[SESSION_CLAIM], str(record.id))
        # The outstanding-token record holds exactly what the BFF holds.
        self.assertEqual(OutstandingToken.objects.get(jti=refresh["jti"]).token, response.data["refresh"])

    def test_a_refresh_keeps_the_session(self):
        response = self.sign_in()
        refreshed = self.client.post("/api/v1/auth/refresh/", {"refresh": response.data["refresh"]})
        self.assertEqual(refreshed.status_code, 200, refreshed.content)
        record = ProductSession.objects.get()
        self.assertEqual(RefreshToken(refreshed.data["refresh"])[SESSION_CLAIM], str(record.id))
        self.assertEqual(AccessToken(refreshed.data["access"])[SESSION_CLAIM], str(record.id))

    def test_without_a_sid_the_sign_in_is_still_recorded(self):
        # Workeasy360 issues sid only to an integration that registered a back-channel logout URI.
        self.assertEqual(self.sign_in().status_code, 200)
        self.assertEqual(ProductSession.objects.get().provider_sid, "")

    def test_no_django_session_is_ever_stored(self):
        self.assertEqual(self.sign_in().status_code, 200)
        self.assertEqual(Session.objects.count(), 0)

    def test_the_identity_mirror_follows_the_authoritative_link(self):
        self.sign_in()
        user = User.objects.get(email="asha@example.com")
        mirror = FederatedIdentity.objects.get()
        self.assertEqual((mirror.user_id, mirror.issuer, mirror.subject), (user.id, ISSUER, "w360-user-1"))
        # A mirror that disagrees (written by an earlier release, say) is corrected by the next sign-in.
        other = make_user(email="someone@example.com")
        FederatedIdentity.objects.filter(pk=mirror.pk).update(user=other)
        self.assertEqual(self.sign_in().status_code, 200)
        self.assertEqual(FederatedIdentity.objects.get().user_id, user.id)
        self.assertEqual(ExternalIdentity.objects.get().user_id, user.id)


def linked_organization(name="Legacy Books", external=W360_ORG, email="asha@example.com"):
    """An EasyBook organization linked to Workeasy360 organization ``external``, owned by an existing (password)
    account with ``email``. Returns (organization, owner)."""
    organization, owner, _ = make_org_with_owner(org_name=name, email=email)
    if external:
        Organization.objects.filter(id=organization.id).update(workeasy360_organization_id=external)
    return organization, owner


def link_audit(organization):
    with tenant_context(organization_id=organization.id):
        return list(AuditLog.objects.filter(object_type="external_identity"))


class ExistingAccountRefusedByDefaultTests(SignInTestCase):
    """An existing EasyBook account is never merged with a new Workeasy360 account because the addresses match
    (WORKEASY360_LINK_BY_VERIFIED_EMAIL is off by default): an operator links them (link_workeasy360_user)."""

    def test_the_default_is_off(self):
        self.assertFalse(settings.WORKEASY360_LINK_BY_VERIFIED_EMAIL)

    def test_a_verified_address_match_in_the_same_organization_is_refused_not_merged(self):
        organization, owner = linked_organization()
        old_refresh = RefreshToken.for_user(owner)
        response = self.sign_in(email="Asha@Example.com")
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(response.data["error"]["code"], "account_conflict")
        self.assertFalse(ExternalIdentity.objects.exists())
        self.assertFalse(FederatedIdentity.objects.exists())
        self.assertFalse(ProductSession.objects.exists())
        self.assertEqual(User.objects.count(), 1)
        owner.refresh_from_db()
        self.assertTrue(owner.has_usable_password())
        self.assertEqual(self.client.post("/api/v1/auth/refresh/", {"refresh": str(old_refresh)}).status_code, 200)
        self.assertEqual(link_audit(organization), [])

    def test_a_match_in_an_easybook_only_organization_creates_nothing(self):
        linked_organization(external=None)
        response = self.sign_in()
        self.assertEqual(response.data["error"]["code"], "account_conflict")
        self.assertFalse(Organization.objects.filter(workeasy360_organization_id=W360_ORG).exists())
        self.assertEqual(User.objects.count(), 1)

    def test_after_the_operator_links_the_account_the_person_signs_in_to_it(self):
        organization, owner = linked_organization()
        call_command(
            "link_workeasy360_user", "asha@example.com", subject="w360-user-1", organization=W360_ORG,
            actor="ops: Priya", reason="ticket 42", stdout=io.StringIO(),
        )
        response = self.sign_in(email="asha.renamed@example.com")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(AccessToken(response.data["access"])["user_id"], str(owner.id))
        self.assertEqual(response.data["organization_id"], str(organization.id))
        self.assertEqual(User.objects.count(), 1)
        owner.refresh_from_db()
        self.assertTrue(owner.has_usable_password())  # left as it is; SSO-only retires password sign-in
        self.assertEqual(FederatedIdentity.objects.get().user_id, owner.id)
        # The operator's record only: the sign-in found the link on (issuer, sub), it made none.
        self.assertEqual([entry.changes["link"] for entry in link_audit(organization)], ["operator"])


@override_settings(WORKEASY360_LINK_BY_VERIFIED_EMAIL=True)
class AutomaticLinkByVerifiedEmailTests(SignInTestCase):
    """WORKEASY360_LINK_BY_VERIFIED_EMAIL on: a verified address links an account of the signing-in organization
    only, never across organizations, never an account already linked, always audited."""

    def test_an_account_of_this_organization_alone_is_linked_and_audited_and_keeps_its_password(self):
        organization, owner = linked_organization()
        old_refresh = RefreshToken.for_user(owner)  # a session the password opened earlier
        response = self.sign_in(email="Asha@Example.com")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(AccessToken(response.data["access"])["user_id"], str(owner.id))
        self.assertEqual(response.data["organization_id"], str(organization.id))
        self.assertTrue(ExternalIdentity.objects.filter(user=owner, issuer=ISSUER, subject="w360-user-1").exists())
        self.assertTrue(FederatedIdentity.objects.filter(user=owner).exists())
        owner.refresh_from_db()
        self.assertTrue(owner.has_usable_password())  # left as it is; SSO-only retires password sign-in
        # Sessions opened before the link end; the one the sign-in issued works.
        self.assertEqual(self.client.post("/api/v1/auth/refresh/", {"refresh": str(old_refresh)}).status_code, 401)
        self.assertEqual(
            self.client.post("/api/v1/auth/refresh/", {"refresh": response.data["refresh"]}).status_code, 200
        )
        [entry] = link_audit(organization)
        self.assertEqual(entry.actor_id, owner.id)
        self.assertEqual(entry.object_id, str(owner.id))
        self.assertEqual(entry.changes["link"], "automatic_verified_email")
        self.assertEqual(entry.changes["workeasy360_subject"], "w360-user-1")
        self.assertEqual(entry.changes["workeasy360_organization_id"], W360_ORG)
        self.assertEqual(entry.changes["issuer"], ISSUER)
        self.assertTrue(entry.changes["password_kept"])

    def test_an_unverified_address_still_never_links(self):
        _, owner = linked_organization()
        response = self.sign_in(email_verified=False)
        self.assertEqual(response.data["error"]["code"], "email_unverified")
        self.assertFalse(ExternalIdentity.objects.filter(user=owner).exists())

    def test_an_account_of_another_organization_only_is_never_linked(self):
        # The signing-in organization is linked here, but the account belongs to a different one.
        linked_organization(name="Theirs", external=W360_ORG, email="owner@example.com")
        other, asha = linked_organization(name="Elsewhere", external="0d6c0a3e-8e52-4a63-8b0e-5a1d2c3b4f50")
        response = self.sign_in()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "account_conflict")
        self.assertFalse(ExternalIdentity.objects.exists())
        self.assertEqual(link_audit(other), [])
        with tenant_context(user_id=asha.id):
            self.assertEqual(Membership.all_objects.filter(user=asha).count(), 1)

    def test_an_account_of_an_easybook_only_organization_is_never_linked(self):
        # The global lookup used to link this one, and the new Workeasy360 organization then opened it.
        _, owner = linked_organization(external=None)
        response = self.sign_in()
        self.assertEqual(response.data["error"]["code"], "account_conflict")
        self.assertFalse(ExternalIdentity.objects.filter(user=owner).exists())
        self.assertFalse(Organization.objects.filter(workeasy360_organization_id=W360_ORG).exists())

    def test_an_account_of_this_organization_and_another_is_never_linked(self):
        organization, owner = linked_organization()
        side = make_organization(name="Side Books")
        make_membership(side, owner)
        response = self.sign_in()
        self.assertEqual(response.data["error"]["code"], "account_conflict")
        self.assertFalse(ExternalIdentity.objects.filter(user=owner).exists())
        self.assertEqual(link_audit(organization), [])

    def test_a_removed_member_of_this_organization_is_never_linked(self):
        organization, owner = linked_organization()
        with tenant_context(user_id=owner.id):
            Membership.all_objects.filter(user=owner, organization=organization).update(is_active=False)
        self.assertEqual(self.sign_in().data["error"]["code"], "account_conflict")
        self.assertFalse(ExternalIdentity.objects.filter(user=owner).exists())

    def test_an_account_already_linked_is_never_relinked(self):
        organization, _owner = linked_organization()
        self.assertEqual(self.sign_in().status_code, 200)
        response = self.sign_in(sub="w360-impostor")  # same verified address, different subject
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "account_conflict")
        self.assertFalse(ExternalIdentity.objects.filter(subject="w360-impostor").exists())
        self.assertFalse(FederatedIdentity.objects.filter(subject="w360-impostor").exists())
        self.assertEqual(len(link_audit(organization)), 1)

    def test_an_account_linked_under_another_issuer_is_never_relinked(self):
        _, owner = linked_organization()
        ExternalIdentity.objects.create(user=owner, issuer="https://app.workeasy360.test", subject="w360-old")
        response = self.sign_in()
        self.assertEqual(response.data["error"]["code"], "account_conflict")
        self.assertFalse(ExternalIdentity.objects.filter(issuer=ISSUER).exists())


class LinkingTests(SignInTestCase):

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
        self.assertFalse(FederatedIdentity.objects.filter(subject="w360-impostor").exists())

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

    def test_a_refused_membership_leaves_no_new_account_behind(self):
        # The user and organization adapters share one transaction: a refusal in the second undoes the first.
        organization, _owner, _ = make_org_with_owner(org_name="Legacy Books", email="owner@example.com")
        Organization.objects.filter(id=organization.id).update(workeasy360_organization_id=W360_ORG, is_active=False)
        response = self.sign_in()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "organization_inactive")
        self.assertFalse(User.objects.filter(email="asha@example.com").exists())
        self.assertFalse(ExternalIdentity.objects.exists())
        self.assertFalse(FederatedIdentity.objects.exists())
        self.assertFalse(ProductSession.objects.exists())

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
        response = self.complete(started, state="forged")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "state_mismatch")
        self.assertFalse(any(url.endswith("/oauth/token") for _, url in self.provider.calls))

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

    def test_a_flow_from_the_previous_release_ends_in_expired(self):
        # A sign-in mid-flight at the deploy: its flow carried state, nonce and verifier, and no binding.
        started = self.start(W360_ORG)
        params = authorize_params(started["authorization_url"])
        old = signing.dumps(
            {"s": params["state"], "n": params["nonce"], "v": "v" * 43, "o": W360_ORG},
            salt="sso.workeasy360.flow", compress=True,
        )
        response = self.client.post(
            "/api/v1/auth/workeasy360/callback/", {"code": "x", "state": params["state"], "flow": old}
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "flow_expired")

    def test_a_flow_is_single_use(self):
        started = self.start(W360_ORG)
        self.assertEqual(self.complete(started).status_code, 200)
        replayed = self.complete(started)  # the same state again: that flow is spent
        self.assertEqual(replayed.status_code, 403)
        self.assertEqual(replayed.data["error"]["code"], "state_mismatch")
        self.assertEqual(ProductSession.objects.count(), 1)

    def test_a_flow_is_bound_to_the_browser_that_started_it(self):
        mine, theirs = self.start(W360_ORG), self.start(W360_ORG)
        # Their state with my flow (a callback URL opened in another browser): refused, and their flow is
        # burnt rather than left for a second attempt.
        response = self.complete(mine, state=authorize_params(theirs["authorization_url"])["state"])
        self.assertEqual(response.data["error"]["code"], "state_mismatch")
        self.assertEqual(self.complete(theirs).data["error"]["code"], "state_mismatch")
        self.assertEqual(self.complete(mine).status_code, 200)

    def test_a_token_for_another_product_is_refused(self):
        response = self.sign_in(product_code="easy_crm")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "wrong_product")

    def test_a_token_naming_no_product_is_refused(self):
        response = self.sign_in(product_code=None)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "wrong_product")

    def test_a_token_for_another_organization_than_the_launcher_asked_is_refused(self):
        started = self.start(W360_ORG)
        response = self.complete(started, organization_id="0d6c0a3e-8e52-4a63-8b0e-5a1d2c3b4f50")
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

    def test_a_rate_limited_token_endpoint_is_503_not_a_refusal(self):
        self.provider.token_status = (429, {"error": "temporarily_unavailable"})
        response = self.sign_in()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["error"]["code"], "workeasy360_unavailable")

    def test_a_refused_code_exchange_is_sign_in_refused(self):
        self.provider.token_status = (400, {"error": "invalid_grant"})
        response = self.sign_in()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], "sign_in_refused")


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
        return self.client.get("/api/v1/auth/workeasy360/end-session/")

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
        self.assertEqual(self.provider.calls, [])

    @override_settings(WORKEASY360_POST_LOGOUT_REDIRECT_URI="https://book.workeasy360.test/login")
    def test_is_empty_when_the_provider_offers_no_end_session(self):
        self.provider.advertise_end_session = False
        self.assertEqual(self.end_session().data, {"url": ""})

    @override_settings(WORKEASY360_POST_LOGOUT_REDIRECT_URI="https://book.workeasy360.test/login")
    def test_is_empty_when_the_provider_cannot_be_reached(self):
        self.provider.discovery_issuer = "https://evil.example"
        self.assertEqual(self.end_session().data, {"url": ""})


class ProductBindingTests(SignInTestCase):
    def test_a_token_naming_the_configured_product_signs_in(self):
        self.assertEqual(self.sign_in(product_code="easybook").status_code, 200)

    def test_no_configured_product_code_fails_closed_instead_of_skipping_the_check(self):
        started = self.start(W360_ORG)  # begun while configured
        with override_settings(WORKEASY360_PRODUCT_CODE=""):
            # Nothing starts...
            response = self.client.post("/api/v1/auth/workeasy360/start/", {"organization": W360_ORG})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.data["error"]["code"], "workeasy360_unavailable")
            # ...and nothing in flight completes, whatever product the token names.
            for claimed in ("easybook", "easy_crm", None):
                with self.subTest(claimed=claimed):
                    response = self.complete(started, product_code=claimed)
                    self.assertIn(response.status_code, (403, 503))
                    self.assertNotIn("access", response.data)
        self.assertFalse(User.objects.filter(email="asha@example.com").exists())
        self.assertFalse(Organization.objects.exists())


class ProductCodeCheckTests(SimpleTestCase):
    """The process refuses to start with no product code, in every environment (production also
    refuses at import: config/tests/test_production_settings.py)."""

    def test_the_default_configuration_passes(self):
        self.assertEqual(checks.product_code_is_set(None), [])

    def test_an_empty_or_blank_product_code_is_a_system_check_error(self):
        for blank in ("", "  "):
            with self.subTest(repr(blank)), override_settings(WORKEASY360_PRODUCT_CODE=blank):
                errors = checks.product_code_is_set(None)
                self.assertEqual([error.id for error in errors], ["sso.E001"])
                self.assertIn("WORKEASY360_PRODUCT_CODE", errors[0].msg)

    def test_manage_py_check_stops_on_it(self):
        with override_settings(WORKEASY360_PRODUCT_CODE=""), self.assertRaises(SystemCheckError):
            call_command("check", stdout=io.StringIO(), stderr=io.StringIO())


class RedirectUriCheckTests(SimpleTestCase):
    """The registered redirect URI is the BFF's callback route; Django serves no route at it, so the connector's
    own check (workeasy360_sso.E005) is silenced and this one stands in for it."""

    def test_the_bff_callback_passes_and_unset_passes(self):
        for value in ("", REDIRECT_URI, "http://localhost:3000/api/auth/callback/workeasy360"):
            with self.subTest(value), override_settings(WORKEASY360_REDIRECT_URI=value):
                self.assertEqual(checks.redirect_uri_is_the_bff_callback(None), [])

    def test_any_other_path_is_a_system_check_error(self):
        for value in (
            "https://book.workeasy360.test/api/auth/callback/workeasy360/",  # a trailing slash breaks the match
            "https://book.workeasy360.test/api/v1/auth/workeasy360/callback/",  # Django's relay endpoint
        ):
            with self.subTest(value), override_settings(WORKEASY360_REDIRECT_URI=value):
                self.assertEqual([error.id for error in checks.redirect_uri_is_the_bff_callback(None)], ["sso.E002"])

    def test_only_the_explained_connector_checks_are_silenced(self):
        self.assertEqual(sorted(settings.SILENCED_SYSTEM_CHECKS), ["workeasy360_sso.E005", "workeasy360_sso.W003"])


class ThrottleScopeTests(SignInTestCase):
    """The start endpoint has its own bucket: auto-start makes starts routine, and they must not use up the
    callback's 20 a minute (an office behind one address, an uptime check)."""

    RATES = {"auth": "20/min", "sso_start": "60/min"}

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(SimpleRateThrottle.THROTTLE_RATES, self.RATES)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post_start(self):
        return self.client.post("/api/v1/auth/workeasy360/start/", {})

    def post_callback(self):
        # A flow nobody signed: refused (403 flow_expired) after the throttle has counted it.
        return self.client.post(
            "/api/v1/auth/workeasy360/callback/", {"code": "x", "state": "s", "flow": "not-a-signed-flow"}
        )

    def test_scopes_and_default_rates(self):
        self.assertEqual(Workeasy360StartView.throttle_scope, "sso_start")
        self.assertEqual(Workeasy360CallbackView.throttle_scope, "auth")
        rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        self.assertIn("sso_start", rates)
        self.assertIn("auth", rates)

    def test_starts_beyond_the_auth_limit_still_work_and_leave_the_callbacks_budget_alone(self):
        for number in range(25):  # more than the 20 an "auth" bucket allows
            self.assertEqual(self.post_start().status_code, 200, f"start {number + 1}")
        self.assertEqual(self.post_callback().status_code, 403)  # refused as a flow, not throttled

    def test_the_callback_keeps_its_auth_limit_and_starts_do_not_notice(self):
        for number in range(20):
            self.assertEqual(self.post_callback().status_code, 403, f"callback {number + 1}")
        self.assertEqual(self.post_callback().status_code, 429)
        self.assertEqual(self.post_start().status_code, 200)

    def test_password_sign_in_still_shares_the_auth_bucket_with_the_callback(self):
        for _ in range(20):
            self.post_callback()
        response = self.client.post("/api/v1/auth/login/", {"email": "x@example.com", "password": "nope-nope-nope"})
        self.assertEqual(response.status_code, 429)

    def test_start_is_limited_at_its_own_rate(self):
        for number in range(60):
            self.assertEqual(self.post_start().status_code, 200, f"start {number + 1}")
        self.assertEqual(self.post_start().status_code, 429)
