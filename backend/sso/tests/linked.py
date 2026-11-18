"""A mixin that puts any test case behind a configured, fake Workeasy360: used by the tests that prove a
background job honours the organization's entitlement (``sso/tests/test_background.py``)."""

from django.test import override_settings

from accounts.models import Organization
from sso.tests.test_sign_in import CONFIGURED, W360_ORG, behind_fake_provider

ALLOW = {"decision": "allow", "reason": "entitled", "write_allowed": True, "revalidate_after": 900}
EXPIRED = {"decision": "deny", "reason": "expired", "write_allowed": False}


class LinkedToWorkeasy360:
    """Put it first in the bases: ``setUp`` runs the case's own first, then configures the fake provider."""

    def setUp(self):
        super().setUp()
        settings_override = override_settings(**CONFIGURED)
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        self.provider, self.clock = behind_fake_provider(self)

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
        """Let ``seconds`` pass for the refusal of EasyBook's own client (and everything else the entitlement
        check remembers: the connector keeps one clock)."""
        self.clock.advance(seconds)

    def age_organization_answer(self, seconds):
        """Make the answer for the linked organization ``seconds`` older (see ``age_client_refusal``)."""
        self.clock.advance(seconds)
