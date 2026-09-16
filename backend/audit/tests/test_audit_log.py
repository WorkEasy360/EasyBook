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

    def test_changes_accepts_decimal_date_and_uuid_values(self):
        """Regression: `changes` was a plain JSONField, so recording a Decimal
        money amount raised TypeError and aborted the surrounding financial
        transaction — an audit write must never be the reason a mutation
        fails. Fixed at the field definition with DjangoJSONEncoder (see
        audit/models.py), so every caller benefits, not just the one that
        first hit it (purchases.update_expense; sales.update_customer with a
        credit_limit had the same latent bug)."""
        import datetime
        import uuid
        from decimal import Decimal

        entry = audit_services.record(
            organization_id=self.organization.id,
            action=AuditLog.Action.UPDATE,
            object_type="bill",
            object_id="1",
            changes={
                "amount": Decimal("1234.56"),
                "bill_date": datetime.date(2026, 4, 10),
                "journal_id": uuid.uuid4(),
            },
        )
        with tenant_context(organization_id=self.organization.id):
            stored = AuditLog.objects.get(pk=entry.pk)
        self.assertEqual(stored.changes["amount"], "1234.56")
        self.assertEqual(stored.changes["bill_date"], "2026-04-10")
