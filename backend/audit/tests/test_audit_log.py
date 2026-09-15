from django.test import TestCase

from audit import services as audit_services
from audit.models import AuditLog
from core.tenancy import tenant_context
from core.tests.factories import make_organization, make_user


class AuditLogTests(TestCase):
    def setUp(self):
        self.organization = make_organization("Acme")
        self.user = make_user("actor@example.com")

    def test_record_creates_entry(self):
        entry = audit_services.record(
            organization_id=self.organization.id,
            action=AuditLog.Action.CREATE,
            object_type="invoice",
            object_id="123",
            actor=self.user,
            changes={"total": "100.00"},
        )
        self.assertEqual(entry.action, AuditLog.Action.CREATE)
        self.assertEqual(entry.object_type, "invoice")

    def test_entries_are_append_only(self):
        entry = audit_services.record(
            organization_id=self.organization.id,
            action=AuditLog.Action.CREATE,
            object_type="invoice",
            object_id="123",
        )
        entry.action = AuditLog.Action.UPDATE
        with self.assertRaises(ValueError):
            entry.save()

    def test_entries_cannot_be_deleted(self):
        entry = audit_services.record(
            organization_id=self.organization.id,
            action=AuditLog.Action.CREATE,
            object_type="invoice",
            object_id="123",
        )
        with self.assertRaises(ValueError):
            entry.delete()

    def test_entries_are_isolated_per_organization(self):
        other_org = make_organization("Other Co")
        audit_services.record(
            organization_id=self.organization.id, action=AuditLog.Action.CREATE,
            object_type="invoice", object_id="1",
        )
        audit_services.record(
            organization_id=other_org.id, action=AuditLog.Action.CREATE,
            object_type="invoice", object_id="2",
        )
        with tenant_context(organization_id=self.organization.id):
            visible = list(AuditLog.objects.all())
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0].object_id, "1")
