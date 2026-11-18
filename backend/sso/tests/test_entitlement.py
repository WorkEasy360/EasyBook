"""Workeasy360 entitlement, re-checked while EasyBook's own session lives on (sso/entitlement.py): an
organization whose access has ended is refused within Workeasy360's cadence, never because of an outage."""

import contextlib
import uuid
from unittest import mock

import redis.exceptions
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from workeasy360_sso import discovery
from workeasy360_sso import entitlements as connector
from workeasy360_sso.conf import ConfigurationError

from core.tests.factories import make_org_with_owner
from sso import entitlement
from sso.models import ExternalIdentity
from sso.tests.fakes import ENTITLEMENT_ENDPOINT, ISSUER
from sso.tests.test_sign_in import W360_ORG, SignInTestCase

READ = "/api/v1/organizations/members/"
WRITE = "/api/v1/accounting/accounts/"
#: The ``sub`` the fake provider signs people in with: the person the tests below ask about.
PERSON = "w360-user-1"
OTHER = "w360-user-2"


class EntitlementTests(SignInTestCase):
    def setUp(self):
        super().setUp()
        response = self.sign_in()
        self.assertEqual(response.status_code, 200, response.content)
        self.api = APIClient()
        self.api.credentials(
            HTTP_AUTHORIZATION=f"Bearer {response.data['access']}",
            HTTP_X_ORGANIZATION_ID=response.data["organization_id"],
        )

    def age_answer(self, seconds):
        self.clock.advance(seconds)

    def code_of(self, response):
        return response.data["error"]["code"]

    def test_the_organization_is_asked_about_once_per_window_and_only_by_the_client(self):
        self.assertEqual(self.api.get(READ).status_code, 200)
        self.assertEqual(self.api.get(READ).status_code, 200)
        self.assertEqual(len(self.provider.entitlement_forms), 1)
        form = self.provider.entitlement_forms[0]
        self.assertEqual(form["organization_id"], W360_ORG)
        self.assertEqual(form["subject"], PERSON)  # a signed-in request asks about the person
        self.assertNotIn("product_code", form)  # the product is the authenticated client

    def test_access_ends_when_workeasy360_says_it_has_ended(self):
        self.provider.entitlement = {"decision": "deny", "reason": "expired", "write_allowed": False}
        response = self.api.get(READ)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.code_of(response), entitlement.ACCESS_ENDED)
        # Who am I and which organizations: still answered, so the person can see why and leave.
        self.assertEqual(self.api.get("/api/v1/auth/me/").status_code, 200)
        self.assertEqual(self.api.get("/api/v1/organizations/").status_code, 200)

    def test_an_allowed_answer_is_asked_again_after_revalidate_after(self):
        self.assertEqual(self.api.get(READ).status_code, 200)
        self.provider.entitlement = {"decision": "deny", "reason": "expired", "write_allowed": False}
        self.assertEqual(self.api.get(READ).status_code, 200)  # still inside Workeasy360's window
        self.age_answer(901)
        self.assertEqual(self.code_of(self.api.get(READ)), entitlement.ACCESS_ENDED)

    def test_a_denial_is_asked_again_soon_so_a_renewal_takes_effect(self):
        self.provider.entitlement = {"decision": "deny", "reason": "expired", "write_allowed": False}
        self.assertEqual(self.api.get(READ).status_code, 403)
        self.provider.entitlement = {"decision": "allow", "reason": "entitled", "write_allowed": True}
        self.age_answer(entitlement.DENIAL_RECHECK_SECONDS + 1)
        self.assertEqual(self.api.get(READ).status_code, 200)

    def test_the_provider_cadence_is_honoured_but_bounded(self):
        self.provider.entitlement = {
            "decision": "allow", "reason": "entitled", "write_allowed": True, "revalidate_after": 86400,
        }
        self.assertEqual(self.api.get(READ).status_code, 200)
        answer = entitlement.stored_answer(W360_ORG, PERSON)
        self.assertEqual(answer.trusted_until - answer.checked_at, entitlement.DEFAULT_REVALIDATE_SECONDS)

    def test_read_only_refuses_writes_but_keeps_reads(self):
        self.provider.entitlement = {"decision": "allow", "reason": "entitled", "write_allowed": False}
        self.assertEqual(self.api.get(READ).status_code, 200)
        response = self.api.post(WRITE, {}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.code_of(response), entitlement.READ_ONLY)

    def test_a_writable_organization_reaches_the_view(self):
        response = self.api.post(WRITE, {}, format="json")
        self.assertNotEqual(response.status_code, 403, response.content)  # validation, not the gate

    def test_a_platform_failure_with_no_answer_yet_is_not_a_denial(self):
        self.provider.entitlement_status = 503
        self.assertEqual(self.api.get(READ).status_code, 200)

    def test_during_an_outage_the_last_answer_stands_and_the_provider_is_not_hammered(self):
        self.provider.entitlement = {"decision": "deny", "reason": "expired", "write_allowed": False}
        self.assertEqual(self.api.get(READ).status_code, 403)
        self.age_answer(entitlement.DENIAL_RECHECK_SECONDS + 1)
        self.provider.entitlement_status = 503
        asked = self.provider.calls.count(("POST", ENTITLEMENT_ENDPOINT))
        self.assertEqual(self.api.get(READ).status_code, 403)  # an outage never turns a denial into access...
        self.assertEqual(self.api.get(READ).status_code, 403)
        after = self.provider.calls.count(("POST", ENTITLEMENT_ENDPOINT))
        self.assertEqual(after, asked + 1)  # ...and backs off after one failed attempt

    def test_a_provider_that_does_not_offer_the_api_does_not_lock_anyone_out(self):
        self.provider.advertise_entitlement = False
        discovery.reset_cache()  # the sign-in cached the old discovery document
        cache.clear()
        self.assertEqual(self.api.get(READ).status_code, 200)
        self.assertEqual(self.provider.entitlement_forms, [])

    def test_only_one_request_asks_at_a_time(self):
        self.assertEqual(self.api.get(READ).status_code, 200)
        self.age_answer(901)
        hold_lock(PERSON)
        asked = len(self.provider.entitlement_forms)
        self.assertEqual(self.api.get(READ).status_code, 200)  # the last answer is used meanwhile
        self.assertEqual(len(self.provider.entitlement_forms), asked)


    def test_a_refused_client_is_not_an_outage_and_refuses_access(self):
        self.provider.entitlement_status = 401
        response = self.api.get(READ)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.code_of(response), entitlement.ACCESS_UNVERIFIED)
        self.provider.entitlement_status = 200  # fixed: back within the denial recheck
        self.age_answer(entitlement.CLIENT_REFUSED_SECONDS + 1)
        self.assertEqual(self.api.get(READ).status_code, 200)

    def test_a_known_denial_survives_losing_the_shared_cache(self):
        self.provider.entitlement = {"decision": "deny", "reason": "expired", "write_allowed": False}
        self.assertEqual(self.api.get(READ).status_code, 403)
        cache.clear()  # an eviction, a flush, a Redis restart
        self.provider.entitlement_status = 503
        self.assertEqual(self.code_of(self.api.get(READ)), entitlement.ACCESS_ENDED)

    def test_a_cache_outage_never_turns_a_denial_into_access(self):
        self.provider.entitlement = {"decision": "deny", "reason": "expired", "write_allowed": False}
        self.assertEqual(self.api.get(READ).status_code, 403)
        with mock.patch("workeasy360_sso.entitlements._cache", return_value=ForgetfulCache()):
            self.assertEqual(self.api.get(READ).status_code, 403)

    def test_a_first_check_is_not_waved_through_while_another_request_asks(self):
        self.provider.entitlement = {"decision": "deny", "reason": "expired", "write_allowed": False}
        hold_lock(PERSON)
        self.assertEqual(self.api.get(READ).status_code, 403)

    def test_an_unexpected_failure_is_an_outage_not_an_error(self):
        with mock.patch("workeasy360_sso.client_auth.authentication", side_effect=ValueError("bad key")):
            self.assertEqual(self.api.get(READ).status_code, 200)

    def test_configuration_that_cannot_be_loaded_is_an_outage_not_an_error(self):
        unreadable = ConfigurationError("WORKEASY360_PRIVATE_KEY_FILE could not be read.")
        with mock.patch("sso.entitlement.connector_config", side_effect=unreadable):
            self.assertEqual(self.api.get(READ).status_code, 200)

    def test_a_read_only_organization_can_still_download_and_ask(self):
        from ai.api.views import AskView
        from documents.api.views import DocumentDownloadView

        self.assertTrue(DocumentDownloadView.entitlement_read and AskView.entitlement_read)
        self.provider.entitlement = {"decision": "allow", "reason": "entitled", "write_allowed": False}
        response = self.api.post("/api/v1/ai/ask/", {"question": "x"}, format="json")
        self.assertNotEqual(response.status_code, 403, response.content)


DENY = {"decision": "deny", "write_allowed": False}


class PerPersonTests(SignInTestCase):
    """A signed-in request asks per (Workeasy360 organization, subject): design section 4."""

    def setUp(self):
        super().setUp()
        self.api = self.api_for(self.sign_in())

    def api_for(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        api = APIClient()
        api.credentials(
            HTTP_AUTHORIZATION=f"Bearer {response.data['access']}",
            HTTP_X_ORGANIZATION_ID=response.data["organization_id"],
        )
        return api

    def sign_in_other(self):
        return self.api_for(self.sign_in(sub=OTHER, email="other@example.com"))

    def code_of(self, response):
        return response.data["error"]["code"]

    def age(self, seconds):
        self.clock.advance(seconds)

    def test_a_person_in_good_standing_is_allowed(self):
        self.assertEqual(self.api.get(READ).status_code, 200)
        self.assertEqual(self.api.post(WRITE, {}, format="json").status_code, 400)  # reaches the view
        self.assertEqual([form["subject"] for form in self.provider.entitlement_forms], [PERSON])

    def test_a_removed_membership_ends_that_persons_access_with_the_reason(self):
        self.provider.person_entitlements[PERSON] = {**DENY, "reason": "membership_inactive"}
        response = self.api.get(READ)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.code_of(response), entitlement.ACCESS_ENDED)
        self.assertEqual(response.data["error"]["details"], {"reason": "membership_inactive"})
        self.assertIn("removed", response.data["error"]["message"])
        self.assertEqual(self.api.get("/api/v1/auth/me/").status_code, 200)  # still able to see why and sign out

    def test_a_removed_seat_ends_that_persons_access_with_the_reason(self):
        self.provider.person_entitlements[PERSON] = {**DENY, "reason": "no_seat"}
        response = self.api.get(READ)
        self.assertEqual(self.code_of(response), entitlement.ACCESS_ENDED)
        self.assertEqual(response.data["error"]["details"], {"reason": "no_seat"})
        self.assertIn("seat", response.data["error"]["message"])

    def test_an_organization_level_denial_carries_no_person_reason(self):
        self.provider.person_entitlements[PERSON] = {**DENY, "reason": "expired"}
        response = self.api.get(READ)
        self.assertEqual(self.code_of(response), entitlement.ACCESS_ENDED)
        self.assertIsNone(response.data["error"]["details"])

    def test_one_persons_denial_does_not_touch_a_colleague(self):
        other = self.sign_in_other()
        self.provider.person_entitlements[PERSON] = {**DENY, "reason": "no_seat"}
        self.assertEqual(self.api.get(READ).status_code, 403)
        self.assertEqual(other.get(READ).status_code, 200)
        self.assertEqual(sorted(form["subject"] for form in self.provider.entitlement_forms), [PERSON, OTHER])

    def test_per_person_keys_do_not_collide(self):
        another = "0d6c0a3e-8e52-4a63-8b0e-5a1d2c3b4f50"
        keys = {
            question_key(W360_ORG),
            question_key(W360_ORG, PERSON),
            question_key(W360_ORG, OTHER),
            question_key(another, PERSON),
        }
        self.assertEqual(len(keys), 4)
        self.api.get(READ)
        self.assertIsNotNone(entitlement.stored_answer(W360_ORG, PERSON))
        self.assertIsNone(entitlement.stored_answer(W360_ORG))  # the organization's own answer is not touched

    def test_a_denial_is_asked_again_after_a_minute_for_that_person_only(self):
        self.provider.person_entitlements[PERSON] = {**DENY, "reason": "no_seat"}
        self.assertEqual(self.api.get(READ).status_code, 403)
        self.provider.person_entitlements[PERSON] = {"decision": "allow", "reason": "entitled", "write_allowed": True}
        self.assertEqual(self.api.get(READ).status_code, 403)  # inside the minute: the last answer
        self.age(entitlement.DENIAL_RECHECK_SECONDS + 1)
        self.assertEqual(self.api.get(READ).status_code, 200)

    def test_an_outage_keeps_the_persons_last_answer_and_backs_off_per_person(self):
        self.provider.person_entitlements[PERSON] = {**DENY, "reason": "no_seat"}
        self.assertEqual(self.api.get(READ).status_code, 403)
        other = self.sign_in_other()
        self.assertEqual(other.get(READ).status_code, 200)
        self.age(901)  # the person's denial and the colleague's allow are both due again
        self.provider.entitlement_status = 503
        self.assertEqual(self.api.get(READ).status_code, 403)  # an outage never turns a denial into access
        self.assertEqual(other.get(READ).status_code, 200)  # ...nor an allow into one
        asked = len(self.provider.entitlement_forms)
        self.assertEqual(self.api.get(READ).status_code, 403)
        self.assertEqual(len(self.provider.entitlement_forms), asked)  # PERSON backs off
        self.assertEqual(other.get(READ).status_code, 200)
        self.assertEqual(len(self.provider.entitlement_forms), asked)  # so does OTHER, on its own key

    def test_an_outage_with_no_answer_yet_lets_the_person_in(self):
        self.provider.entitlement_status = 503
        self.assertEqual(self.api.get(READ).status_code, 200)

    def test_a_refusal_of_the_client_fails_closed_for_the_person(self):
        self.provider.entitlement_status = 401
        response = self.api.get(READ)
        self.assertEqual(self.code_of(response), entitlement.ACCESS_UNVERIFIED)
        self.assertIsNone(response.data["error"]["details"])

    def test_the_persons_answer_is_single_flight_on_the_persons_key(self):
        self.assertEqual(self.api.get(READ).status_code, 200)
        self.age(901)
        hold_lock(PERSON)
        asked = len(self.provider.entitlement_forms)
        self.assertEqual(self.api.get(READ).status_code, 200)  # the last answer meanwhile
        self.assertEqual(len(self.provider.entitlement_forms), asked)
        # The colleague's key is not locked by it.
        other = self.sign_in_other()
        self.assertEqual(other.get(READ).status_code, 200)
        self.assertEqual(self.provider.entitlement_forms[-1]["subject"], OTHER)

    def test_without_a_workeasy360_identity_the_organization_is_asked(self):
        organization, user, _membership = make_org_with_owner()
        organization.workeasy360_organization_id = "11111111-2222-4333-8444-555555555555"
        organization.save(update_fields=["workeasy360_organization_id"])
        api = APIClient()
        api.force_authenticate(user=user)
        api.credentials(HTTP_X_ORGANIZATION_ID=str(organization.id))
        self.assertEqual(api.get(READ).status_code, 200)
        self.assertNotIn("subject", self.provider.entitlement_forms[-1])
        self.assertEqual(
            self.provider.entitlement_forms[-1]["organization_id"], str(organization.workeasy360_organization_id)
        )

    def test_the_subject_lookup_costs_no_query_after_the_first(self):
        self.assertEqual(self.api.get(READ).status_code, 200)  # the first request caches the subject
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.api.get(READ).status_code, 200)
        statements = [query["sql"] for query in queries.captured_queries]
        self.assertFalse([statement for statement in statements if "sso_externalidentity" in statement], statements)

    def test_a_new_link_is_seen_at_once_not_after_the_lookup_expires(self):
        _organization, user, _membership = make_org_with_owner()
        self.assertEqual(entitlement.subject_for(user), "")  # cached as "no identity"
        ExternalIdentity.objects.create(user=user, issuer=ISSUER, subject="late-link")
        entitlement.forget_subject(user.pk)
        self.assertEqual(entitlement.subject_for(user), "late-link")

    def test_an_identity_at_another_issuer_is_not_the_persons_subject(self):
        _organization, user, _membership = make_org_with_owner()
        ExternalIdentity.objects.create(user=user, issuer="https://elsewhere.example", subject="x")
        self.assertEqual(entitlement.subject_for(user), "")


class ClientRefusalTests(SignInTestCase):
    """A refusal of EasyBook's own client is client-wide: nobody can be confirmed until one question is
    answered again (design section 4)."""

    def setUp(self):
        super().setUp()
        self.api = self.api_for(self.sign_in())
        self.other = self.api_for(self.sign_in(sub=OTHER, email="other@example.com"))

    def api_for(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        api = APIClient()
        api.credentials(
            HTTP_AUTHORIZATION=f"Bearer {response.data['access']}",
            HTTP_X_ORGANIZATION_ID=response.data["organization_id"],
        )
        return api

    def code_of(self, response):
        return response.data["error"]["code"]

    def age_both(self, seconds):
        self.clock.advance(seconds)

    def refuse_through_first_person(self):
        self.assertEqual(self.api.get(READ).status_code, 200)
        self.assertEqual(self.other.get(READ).status_code, 200)  # both hold a fresh allow
        self.age_both(901)
        self.provider.entitlement_status = 401
        return self.api.get(READ)

    def test_a_refusal_closes_the_client_for_everyone_without_asking(self):
        first = self.refuse_through_first_person()
        self.assertEqual(self.code_of(first), entitlement.ACCESS_UNVERIFIED)
        asked = len(self.provider.entitlement_forms)
        # The colleague's own answer had expired too, but they are not asked: the client is refused.
        second = self.other.get(READ)
        self.assertEqual(self.code_of(second), entitlement.ACCESS_UNVERIFIED)
        self.assertEqual(len(self.provider.entitlement_forms), asked)

    def test_a_fresh_allow_does_not_survive_a_refusal(self):
        self.assertEqual(self.api.get(READ).status_code, 200)
        self.clock.advance(901)  # the person's answer is due again...
        self.assertEqual(self.other.get(READ).status_code, 200)  # ...while the colleague's allow is fresh
        self.provider.entitlement_status = 401
        self.assertEqual(self.code_of(self.api.get(READ)), entitlement.ACCESS_UNVERIFIED)
        self.assertEqual(self.code_of(self.other.get(READ)), entitlement.ACCESS_UNVERIFIED)

    def test_after_a_minute_one_question_is_asked_again(self):
        self.refuse_through_first_person()
        self.provider.entitlement_status = 200  # the credential was restored
        self.age_both(entitlement.CLIENT_REFUSED_SECONDS + 1)  # the hold, and the person's own refused answer
        asked = len(self.provider.entitlement_forms)
        self.assertEqual(self.api.get(READ).status_code, 200)
        self.assertEqual(len(self.provider.entitlement_forms), asked + 1)

    def test_a_429_soon_after_a_refusal_is_the_limiter_not_an_outage(self):
        self.refuse_through_first_person()
        self.age_both(entitlement.CLIENT_REFUSED_SECONDS + 1)  # the minute is over; the limiter is not
        self.provider.entitlement_status = 429
        response = self.other.get(READ)
        self.assertEqual(self.code_of(response), entitlement.ACCESS_UNVERIFIED)
        self.assertEqual(self.code_of(self.api.get(READ)), entitlement.ACCESS_UNVERIFIED)  # closed again

    def test_a_429_with_no_recent_refusal_is_an_outage(self):
        self.provider.entitlement_status = 429
        self.assertEqual(self.api.get(READ).status_code, 200)

    def test_a_429_long_after_a_refusal_is_an_outage_again(self):
        self.refuse_through_first_person()
        self.age_both(entitlement.RATE_LIMIT_AFTER_REFUSAL_SECONDS + 1)
        self.provider.entitlement_status = 429
        self.assertEqual(self.other.get(READ).status_code, 200)

    def test_the_refusal_survives_a_redis_outage(self):
        self.refuse_through_first_person()
        with dead_cache():
            self.provider.entitlement_status = 200
            self.assertEqual(self.code_of(self.other.get(READ)), entitlement.ACCESS_UNVERIFIED)

    def test_a_refusal_is_visible_to_other_processes_through_the_shared_cache(self):
        self.refuse_through_first_person()
        entitlement.clear_local()  # another process: no local copy, only the shared cache
        self.assertEqual(self.code_of(self.other.get(READ)), entitlement.ACCESS_UNVERIFIED)


class SsoOnlyTests(SignInTestCase):
    """With SSO-only on, a session with no Workeasy360 account cannot be checked per person, so it is refused."""

    def linked_unfederated(self):
        organization, user, _membership = make_org_with_owner()
        organization.workeasy360_organization_id = W360_ORG
        organization.save(update_fields=["workeasy360_organization_id"])
        api = APIClient()
        api.force_authenticate(user=user)
        api.credentials(HTTP_X_ORGANIZATION_ID=str(organization.id))
        return api

    def test_an_unfederated_user_is_asked_to_sign_in_with_workeasy360(self):
        api = self.linked_unfederated()
        with self.settings(WORKEASY360_SSO_ONLY=True):
            response = api.get(READ)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.data["error"]["code"], entitlement.SIGN_IN_REQUIRED)
            self.assertEqual(api.post(WRITE, {}, format="json").data["error"]["code"], entitlement.SIGN_IN_REQUIRED)
            # Who am I, which organizations, and signing out stay reachable so the page can explain.
            self.assertEqual(api.get("/api/v1/auth/me/").status_code, 200)
            self.assertEqual(api.get("/api/v1/organizations/").status_code, 200)
        self.assertEqual(self.provider.entitlement_forms, [])  # nothing to ask about

    def test_a_federated_user_is_checked_per_person_as_before(self):
        response = self.sign_in()
        api = APIClient()
        api.credentials(
            HTTP_AUTHORIZATION=f"Bearer {response.data['access']}",
            HTTP_X_ORGANIZATION_ID=response.data["organization_id"],
        )
        with self.settings(WORKEASY360_SSO_ONLY=True):
            self.assertEqual(api.get(READ).status_code, 200)
        self.assertEqual(self.provider.entitlement_forms[-1]["subject"], PERSON)

    def test_with_sso_only_off_an_unfederated_user_is_checked_per_organization(self):
        api = self.linked_unfederated()
        with self.settings(WORKEASY360_SSO_ONLY=False):
            self.assertEqual(api.get(READ).status_code, 200)
        self.assertNotIn("subject", self.provider.entitlement_forms[-1])

    def test_an_organization_not_linked_to_workeasy360_is_never_affected(self):
        organization, user, _membership = make_org_with_owner()
        api = APIClient()
        api.force_authenticate(user=user)
        api.credentials(HTTP_X_ORGANIZATION_ID=str(organization.id))
        with self.settings(WORKEASY360_SSO_ONLY=True):
            self.assertEqual(api.get(READ).status_code, 200)


class DeadCache:
    """The shared cache with Redis down: every operation raises, as Django's RedisCache does."""

    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise redis.exceptions.ConnectionError("redis is down")

        return fail


class ForgetfulCache:
    """A cache that answers but keeps nothing: every read misses, and ``add`` cannot say."""

    def get(self, *args, **kwargs):
        return None

    def add(self, *args, **kwargs):
        return None

    def set(self, *args, **kwargs):
        return None

    def delete(self, *args, **kwargs):
        return None


@contextlib.contextmanager
def dead_cache():
    """Redis down for both caches the entitlement check uses: the connector's (answers, back-offs, refusal
    marks) and EasyBook's own subject lookup."""
    with mock.patch("workeasy360_sso.entitlements._cache", return_value=DeadCache()), mock.patch.object(
        entitlement, "cache", DeadCache()
    ):
        yield


def question_key(workeasy360_id, subject=None):
    """The connector's cache key of one entitlement question."""
    return connector._key(entitlement.connector_config(), uuid.UUID(str(workeasy360_id)), subject)


def hold_lock(subject=None):
    """Another request is asking this question right now (the connector's single-flight lock)."""
    cache.add(f"{question_key(W360_ORG, subject)}:lock", 1, connector.LOCK_TIMEOUT)


class CacheOutageTests(SignInTestCase):
    """A Redis outage is never a 500 and never a change of answer: the per-process copy stands in."""

    def setUp(self):
        super().setUp()
        response = self.sign_in()
        self.assertEqual(response.status_code, 200, response.content)
        self.api = APIClient()
        self.api.credentials(
            HTTP_AUTHORIZATION=f"Bearer {response.data['access']}",
            HTTP_X_ORGANIZATION_ID=response.data["organization_id"],
        )

    def dead(self):
        return dead_cache()

    def test_a_known_allow_keeps_serving_without_asking_again(self):
        self.assertEqual(self.api.get(READ).status_code, 200)
        asked = len(self.provider.entitlement_forms)
        with self.dead():
            self.assertEqual(self.api.get(READ).status_code, 200)
            self.assertEqual(self.api.post(WRITE, {}, format="json").status_code, 400)  # reaches the view
        self.assertEqual(len(self.provider.entitlement_forms), asked)

    def test_a_known_denial_is_still_a_denial(self):
        self.provider.entitlement = {"decision": "deny", "reason": "expired", "write_allowed": False}
        self.assertEqual(self.api.get(READ).status_code, 403)
        self.provider.entitlement = {"decision": "allow", "reason": "entitled", "write_allowed": True}
        with self.dead():
            response = self.api.get(READ)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["error"]["code"], entitlement.ACCESS_ENDED)

    def test_an_old_denial_survives_an_outage_of_both_the_cache_and_the_provider(self):
        self.provider.entitlement = {"decision": "deny", "reason": "expired", "write_allowed": False}
        self.assertEqual(self.api.get(READ).status_code, 403)
        self.clock.advance(entitlement.DENIAL_RECHECK_SECONDS + 1)
        self.provider.entitlement_status = 503
        with self.dead():
            self.assertEqual(self.api.get(READ).status_code, 403)
            self.assertEqual(self.api.get(READ).status_code, 403)  # and the back-off holds, locally

    def test_with_nothing_known_the_provider_is_asked_and_its_answer_used(self):
        entitlement.clear_local()
        cache.clear()
        with self.dead():
            self.assertEqual(self.api.get(READ).status_code, 200)
            self.assertEqual(self.provider.entitlement_forms[-1]["subject"], PERSON)
            entitlement.clear_local()
            self.provider.entitlement = {"decision": "deny", "reason": "no_seat", "write_allowed": False}
            self.assertEqual(self.api.get(READ).status_code, 403)

    def test_the_person_is_still_identified_without_the_cache(self):
        user = ExternalIdentity.objects.get(subject=PERSON).user
        with self.dead():
            self.assertEqual(entitlement.subject_for(user), PERSON)
            entitlement.forget_subject(user.pk)  # a no-op, not an error

    def test_an_outage_with_nothing_known_lets_the_request_through(self):
        entitlement.clear_local()
        cache.clear()
        self.provider.entitlement_status = 503
        with self.dead():
            self.assertEqual(self.api.get(READ).status_code, 200)


class SubjectCacheTests(SignInTestCase):
    def test_no_identity_is_never_cached(self):
        """A cached "no identity" could outlive the sign-in that creates one."""
        _organization, user, _membership = make_org_with_owner()
        self.assertEqual(entitlement.subject_for(user), "")
        ExternalIdentity.objects.create(user=user, issuer=ISSUER, subject="just-linked")
        self.assertEqual(entitlement.subject_for(user), "just-linked")  # no forget needed

    def test_a_first_sign_in_forgets_the_subject_only_after_commit(self):
        with mock.patch.object(entitlement, "forget_subject") as forget:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                self.assertEqual(self.sign_in().status_code, 200)
            forget.assert_not_called()  # the identity row is not committed yet
            for callback in callbacks:
                callback()
        user = ExternalIdentity.objects.get(subject=PERSON).user
        forget.assert_called_once_with(user.pk)


class NotLinkedTests(SignInTestCase):
    def test_an_easybook_only_organization_is_never_checked(self):
        organization, user, _membership = make_org_with_owner()
        api = APIClient()
        api.force_authenticate(user=user)
        api.credentials(HTTP_X_ORGANIZATION_ID=str(organization.id))
        self.assertEqual(api.get(READ).status_code, 200)
        self.assertEqual(self.provider.entitlement_forms, [])
