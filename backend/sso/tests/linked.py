"""A mixin that puts any test case behind a configured, fake Workeasy360: used by the tests that prove a
background job honours the organization's entitlement (``sso/tests/test_background.py``)."""

from unittest import mock

from django.core.cache import cache
from django.test import override_settings

from accounts.models import Organization
from sso import entitlement
from sso.oidc import Workeasy360Client
from sso.tests.fakes import CLIENT_ID, CLIENT_PRIVATE_PEM, ISSUER, KEY_ID, REDIRECT_URI, FakeWorkeasy360
from sso.tests.test_sign_in import CONFIGURED, W360_ORG

ALLOW = {"decision": "allow", "reason": "entitled", "write_allowed": True, "revalidate_after": 900}
EXPIRED = {"decision": "deny", "reason": "expired", "write_allowed": False}


def age_client_refusal(seconds):
    """Make the last refusal of EasyBook's own client ``seconds`` older (this process and the shared cache)."""
    entitlement._client_refused_at -= seconds
    shared = cache.get(entitlement.CLIENT_REFUSED_KEY)
    if shared:
        cache.set(entitlement.CLIENT_REFUSED_KEY, shared - seconds, entitlement.RATE_LIMIT_AFTER_REFUSAL_SECONDS)


class LinkedToWorkeasy360:
    """Put it first in the bases: ``setUp`` runs the case's own first, then configures the fake provider."""

    def setUp(self):
        super().setUp()
        self.provider = FakeWorkeasy360()
        client = Workeasy360Client(
            issuer=ISSUER, client_id=CLIENT_ID, redirect_uri=REDIRECT_URI, private_key_pem=CLIENT_PRIVATE_PEM,
            key_id=KEY_ID, transport=self.provider.transport,
        )
        settings_override = override_settings(**CONFIGURED)
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        patcher = mock.patch("sso.entitlement.get_client", return_value=client)
        patcher.start()
        self.addCleanup(patcher.stop)
        cache.clear()
        entitlement.clear_local()
        self.addCleanup(cache.clear)
        self.addCleanup(entitlement.clear_local)

    def link(self, organization, workeasy360_id=W360_ORG):
        Organization.objects.filter(pk=organization.pk).update(workeasy360_organization_id=workeasy360_id)
        organization.workeasy360_organization_id = workeasy360_id

    def allow(self):
        self.provider.entitlement = dict(ALLOW)

    def deny(self, reason="expired"):
        self.provider.entitlement = {**EXPIRED, "reason": reason}

    def asked(self):
        return len(self.provider.entitlement_forms)

    def age_client_refusal(self, seconds):
        age_client_refusal(seconds)

    def age_organization_answer(self, seconds):
        """Make the shared answer for the linked organization ``seconds`` older."""
        key = entitlement._key(W360_ORG)
        record = cache.get(key)
        record["checked_at"] -= seconds
        cache.set(key, record, entitlement.LAST_KNOWN_KEEP_SECONDS)
        entitlement.clear_local()
