import datetime
from decimal import Decimal

from django.test import TestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from accounts.models import FiscalYear
from automation.models.event import AutomationEvent
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit
from sales.services.customers import create_customer
from sales.services.invoices import create_invoice, post_invoice


class InvoicePostedEventTests(TestCase):
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("Org", "automation-events@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            FiscalYear.objects.create(
                organization=self.org, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            self.ar_account = create_account(
                organization=self.org, code="1100", name="Accounts Receivable", account_type=AccountType.ASSET
            )
            self.sales_account = create_account(
                organization=self.org, code="4000", name="Sales Revenue", account_type=AccountType.INCOME
            )
            self.unit = create_unit(organization=self.org, code="EA", name="Each")
            self.item = create_item(
                organization=self.org, item_type=ItemType.SERVICE, name="Consulting", unit=self.unit,
                sales_account=self.sales_account,
            )
            self.customer = create_customer(
                organization=self.org, customer_code="CUST-1", display_name="Acme", currency=self.currency
            )

    def _create_and_post_invoice(self):
        invoice = create_invoice(
            organization=self.org, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
            due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
            lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("100.00")}],
        )
        return post_invoice(invoice_id=invoice.id, organization=self.org, actor=self.owner)

    def test_posting_invoice_emits_automation_event(self):
        with tenant_context(organization_id=self.org.id):
            invoice = self._create_and_post_invoice()
            events = list(AutomationEvent.objects.filter(event_type="invoice.posted", entity_id=str(invoice.id)))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload["invoice_number"], invoice.invoice_number)
        self.assertIsNone(events[0].causation_id)
        self.assertEqual(events[0].depth, 0)
        self.assertIsNone(events[0].dispatched_at)

    def test_reposting_already_sent_invoice_does_not_duplicate_event(self):
        """post_invoice is idempotent-by-construction (returns early for an
        already-SENT invoice, sales/CLAUDE.md) — the post_save receiver only
        fires from a real status-transition save, so calling post_invoice
        again must not create a second AutomationEvent."""
        with tenant_context(organization_id=self.org.id):
            invoice = self._create_and_post_invoice()
            post_invoice(invoice_id=invoice.id, organization=self.org, actor=self.owner)
            events = AutomationEvent.objects.filter(event_type="invoice.posted", entity_id=str(invoice.id))
            self.assertEqual(events.count(), 1)

    def test_creating_a_draft_invoice_does_not_emit_an_event(self):
        with tenant_context(organization_id=self.org.id):
            create_invoice(
                organization=self.org, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("50.00")}],
            )
            self.assertEqual(AutomationEvent.objects.filter(event_type="invoice.posted").count(), 0)


class AutomationEventTenantIsolationTests(TestCase):
    def test_event_from_other_org_is_not_visible(self):
        org_a, _, _ = make_org_with_owner("Org A", "automation-events-a@example.com")
        org_b, _, _ = make_org_with_owner("Org B", "automation-events-b@example.com")

        with tenant_context(organization_id=org_a.id):
            AutomationEvent.objects.create(
                organization=org_a, event_type="manual.test", entity_id="1",
                occurred_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
            )
        with tenant_context(organization_id=org_b.id):
            self.assertEqual(AutomationEvent.objects.count(), 0)
        with tenant_context(organization_id=org_a.id):
            self.assertEqual(AutomationEvent.objects.count(), 1)


class AutomationLoopPreventionTests(TestCase):
    """Phase sections 49-51: an automation-triggered domain event inherits
    causation_id/correlation_id/depth from the execution that caused it, and
    exceeding AUTOMATION_MAX_DEPTH fails the new event closed rather than
    ever chaining forever. No Phase 11 default action actually mutates
    domain state yet (Level 2 is deferred), so this exercises the guard
    directly via `causation_context` — the same mechanism a future
    domain-mutating action would run under."""

    def test_event_inherits_causation_lineage(self):
        import uuid

        from automation.services.events import causation_context, record_event

        org, _, _ = make_org_with_owner("Org", "automation-loop-a@example.com")
        execution_id = uuid.uuid4()
        correlation_id = uuid.uuid4()

        with tenant_context(organization_id=org.id), causation_context(
            execution_id=execution_id, correlation_id=correlation_id, depth=2
        ):
            event = record_event(organization=org, event_type="manual.test", entity_id="1")

        self.assertEqual(event.causation_id, execution_id)
        self.assertEqual(event.correlation_id, correlation_id)
        self.assertEqual(event.depth, 2)

    def test_exceeding_max_depth_fails_closed(self):
        import uuid

        from django.test import override_settings

        from automation.services.events import causation_context, record_event
        from core.exceptions import ApplicationError

        org, _, _ = make_org_with_owner("Org", "automation-loop-b@example.com")

        with override_settings(AUTOMATION_MAX_DEPTH=3), tenant_context(organization_id=org.id), causation_context(
            execution_id=uuid.uuid4(), correlation_id=uuid.uuid4(), depth=4
        ):
            with self.assertRaises(ApplicationError):
                record_event(organization=org, event_type="manual.test", entity_id="1")
            self.assertEqual(AutomationEvent.objects.filter(event_type="manual.test").count(), 0)
