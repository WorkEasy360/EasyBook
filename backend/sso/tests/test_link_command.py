import uuid
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from accounts.models import Organization
from core.tests.factories import make_organization


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
