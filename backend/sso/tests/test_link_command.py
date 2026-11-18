import uuid
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken
from workeasy360_sso.models import FederatedIdentity

from accounts.models import Membership, Organization, User
from audit.models import AuditLog
from core.tenancy import tenant_context
from core.tests.factories import make_membership, make_org_with_owner, make_organization, make_user
from sso.models import ExternalIdentity
from sso.tests.fakes import ISSUER


class LinkWorkeasy360OrganizationTests(TestCase):
    def test_links_an_unlinked_organization(self):
        organization = make_organization(name="Legacy Books")
        external = uuid.uuid4()
        call_command("link_workeasy360_organization", str(organization.id), str(external), stdout=StringIO())
        organization.refresh_from_db()
        self.assertEqual(organization.workeasy360_organization_id, external)

    def test_refuses_to_relink_either_side(self):
        first, second = make_organization(name="A"), make_organization(name="B")
        external = uuid.uuid4()
        call_command("link_workeasy360_organization", str(first.id), str(external), stdout=StringIO())
        with self.assertRaisesMessage(CommandError, "already linked to A"):
            call_command("link_workeasy360_organization", str(second.id), str(external), stdout=StringIO())
        with self.assertRaisesMessage(CommandError, "is already linked"):
            call_command("link_workeasy360_organization", str(first.id), str(uuid.uuid4()), stdout=StringIO())
        self.assertIsNone(Organization.objects.get(pk=second.id).workeasy360_organization_id)

    def test_rejects_non_uuid_ids(self):
        with self.assertRaisesMessage(CommandError, "UUIDs"):
            call_command("link_workeasy360_organization", "acme", "x", stdout=StringIO())


W360_ORG = "7b0f4f7e-2a3c-4f4e-9a59-3f5c1b2d4e6a"


@override_settings(WORKEASY360_ISSUER=ISSUER)
class LinkWorkeasy360UserTests(TestCase):
    """manage.py link_workeasy360_user: the operator-vouched link of an existing account (sso/provisioning.py
    refuses to merge one by address)."""

    def setUp(self):
        self.organization, self.asha, _ = make_org_with_owner(org_name="Legacy Books", email="asha@example.com")
        Organization.objects.filter(id=self.organization.id).update(workeasy360_organization_id=W360_ORG)

    def link(self, user="asha@example.com", **options):
        arguments = {
            "subject": "w360-user-1", "organization": W360_ORG, "actor": "ops: Priya", "reason": "ticket 42",
            **options,
        }
        out = StringIO()
        call_command("link_workeasy360_user", user, stdout=out, **arguments)
        return out.getvalue()

    def audit(self, organization=None):
        with tenant_context(organization_id=(organization or self.organization).id):
            return list(AuditLog.objects.filter(object_type="external_identity"))

    def assert_nothing_written(self):
        self.assertFalse(ExternalIdentity.objects.filter(user=self.asha, issuer=ISSUER).exists())
        self.assertFalse(FederatedIdentity.objects.filter(user=self.asha, issuer=ISSUER).exists())
        self.assertEqual(self.audit(), [])

    def test_links_audits_and_leaves_the_password(self):
        before = RefreshToken.for_user(self.asha)
        side = make_organization(name="Side Books")
        make_membership(side, self.asha)
        out = self.link()
        identity = ExternalIdentity.objects.get(user=self.asha)
        self.assertEqual((identity.issuer, identity.subject), (ISSUER, "w360-user-1"))
        self.assertIsNone(identity.last_login_at)  # linked, not yet signed in
        mirror = FederatedIdentity.objects.get(user=self.asha)
        self.assertEqual(
            (mirror.issuer, mirror.subject, mirror.email_at_link), (ISSUER, "w360-user-1", "asha@example.com")
        )
        self.asha.refresh_from_db()
        self.assertTrue(self.asha.has_usable_password())
        self.assertTrue(BlacklistedToken.objects.filter(token__jti=before["jti"]).exists())
        [entry] = self.audit()
        self.assertIsNone(entry.actor_id)  # an operator, not an EasyBook user: named in changes
        self.assertEqual(entry.action, AuditLog.Action.CREATE)
        self.assertEqual(entry.object_id, str(self.asha.id))
        keys = (
            "link", "operator", "reason", "issuer", "workeasy360_subject", "workeasy360_organization_id", "email",
            "other_organizations", "password_kept",
        )
        self.assertEqual(
            {key: entry.changes[key] for key in keys},
            {
                "link": "operator", "operator": "ops: Priya", "reason": "ticket 42", "issuer": ISSUER,
                "workeasy360_subject": "w360-user-1", "workeasy360_organization_id": W360_ORG,
                "email": "asha@example.com", "other_organizations": [str(side.id)], "password_kept": True,
            },
        )
        self.assertIsNotNone(entry.created_at)
        self.assertIn(str(side.id), out)  # the operator is told the link opens that organization too

    def test_the_account_can_be_named_by_id(self):
        self.link(user=str(self.asha.id))
        self.assertTrue(ExternalIdentity.objects.filter(user=self.asha).exists())

    def test_refuses_an_account_already_linked(self):
        self.link()
        with self.assertRaisesMessage(CommandError, "already linked to a Workeasy360 account"):
            self.link(subject="w360-someone-else")
        self.assertEqual(ExternalIdentity.objects.get(user=self.asha).subject, "w360-user-1")
        self.assertEqual(len(self.audit()), 1)

    def test_refuses_an_account_linked_under_another_issuer(self):
        ExternalIdentity.objects.create(user=self.asha, issuer="https://app.workeasy360.test", subject="w360-old")
        with self.assertRaisesMessage(CommandError, "already linked"):
            self.link()
        self.assert_nothing_written()

    def test_refuses_a_workeasy360_account_linked_to_someone_else(self):
        ravi = make_user(email="ravi@example.com")
        make_membership(self.organization, ravi)
        ExternalIdentity.objects.create(user=ravi, issuer=ISSUER, subject="w360-user-1")
        with self.assertRaisesMessage(CommandError, "already linked to another EasyBook account (ravi@example.com)"):
            self.link()
        self.assert_nothing_written()

    def test_refuses_when_the_connector_mirror_names_someone_else(self):
        ravi = make_user(email="ravi@example.com")
        FederatedIdentity.objects.create(user=ravi, issuer=ISSUER, subject="w360-user-1")
        with self.assertRaisesMessage(CommandError, "already linked to another EasyBook account"):
            self.link()
        self.assert_nothing_written()

    def test_refuses_an_account_outside_the_stated_organization(self):
        outsider = make_user(email="outsider@example.com")
        make_membership(make_organization(name="Elsewhere"), outsider)
        with self.assertRaisesMessage(CommandError, "is not an active member of Legacy Books"):
            self.link(user="outsider@example.com")
        self.assertFalse(ExternalIdentity.objects.exists())
        self.assertEqual(self.audit(), [])

    def test_refuses_a_removed_member(self):
        with tenant_context(user_id=self.asha.id):
            Membership.all_objects.filter(user=self.asha).update(is_active=False)
        with self.assertRaisesMessage(CommandError, "is not an active member"):
            self.link()
        self.assert_nothing_written()

    def test_refuses_a_disabled_account(self):
        User.objects.filter(pk=self.asha.pk).update(is_active=False)
        with self.assertRaisesMessage(CommandError, "is disabled"):
            self.link()
        self.assert_nothing_written()

    def test_refuses_an_organization_not_linked_to_workeasy360(self):
        with self.assertRaisesMessage(CommandError, "is not linked to an EasyBook organization"):
            self.link(organization=str(uuid.uuid4()))
        self.assert_nothing_written()

    def test_refuses_an_unknown_account(self):
        with self.assertRaisesMessage(CommandError, "No EasyBook account nobody@example.com"):
            self.link(user="nobody@example.com")

    def test_needs_an_actor_and_a_reason(self):
        with self.assertRaisesMessage(CommandError, "--actor and --reason must not be empty"):
            self.link(actor="  ")
        with self.assertRaisesMessage(CommandError, "--actor and --reason must not be empty"):
            self.link(reason="")
        with self.assertRaises(CommandError):  # argparse: the following arguments are required
            call_command(
                "link_workeasy360_user", "asha@example.com", subject="w360-user-1", organization=W360_ORG,
                stdout=StringIO(),
            )
        self.assert_nothing_written()

    def test_only_the_configured_issuer(self):
        with self.assertRaisesMessage(CommandError, "--issuer must be the configured issuer"):
            self.link(issuer="https://evil.example")
        with override_settings(WORKEASY360_ISSUER=""), self.assertRaisesMessage(CommandError, "not configured"):
            self.link()
        self.assert_nothing_written()

    def test_rejects_a_non_uuid_organization(self):
        with self.assertRaisesMessage(CommandError, "UUID"):
            self.link(organization="acme")
