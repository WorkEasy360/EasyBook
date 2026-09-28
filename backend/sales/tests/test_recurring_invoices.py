import datetime
from decimal import Decimal

from django.test import TestCase, TransactionTestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from accounts.models import FiscalYear
from core.exceptions import ApplicationError
from core.tenancy import clear_tenant_context, tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit
from sales.models.invoice import InvoiceStatus
from sales.models.recurring_invoice import RecurringFrequency, RecurringInvoiceRun, RecurringInvoiceTemplate
from sales.services.customers import create_customer
from sales.services.recurring_invoices import (
    activate_template,
    create_recurring_template,
    deactivate_template,
    generate_due_invoices,
    update_recurring_template,
)


class RecurringInvoiceTestsBase:
    """Plain mixin, not a TestCase subclass — concrete test classes below
    combine it with either TestCase (fast, most tests) or TransactionTestCase
    (only where genuine top-level transaction boundaries matter — see
    MultiOrgGenerationTests)."""

    def setUp(self):
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "recur-owner-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "recur-owner-b@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org_a.id):
            FiscalYear.objects.create(
                organization=self.org_a, start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 12, 31)
            )
            self.ar_account = create_account(
                organization=self.org_a, code="1100", name="AR", account_type=AccountType.ASSET
            )
            self.sales_account = create_account(
                organization=self.org_a, code="4000", name="Sales", account_type=AccountType.INCOME
            )
            self.unit = create_unit(organization=self.org_a, code="EA", name="Each")
            self.item = create_item(
                organization=self.org_a, item_type=ItemType.SERVICE, name="Retainer", unit=self.unit,
                sales_account=self.sales_account,
            )
            self.customer = create_customer(
                organization=self.org_a, customer_code="CUST-1", display_name="Acme", currency=self.currency
            )
        with tenant_context(organization_id=self.org_b.id):
            self.customer_b = create_customer(
                organization=self.org_b, customer_code="CUST-1", display_name="Beta", currency=self.currency
            )

    def _make_template(self, frequency=RecurringFrequency.MONTHLY, start_date=datetime.date(2026, 4, 1), **kwargs):
        return create_recurring_template(
            organization=self.org_a, customer=self.customer, frequency=frequency, start_date=start_date,
            receivable_account=self.ar_account,
            lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("500.00")}],
            **kwargs,
        )


class RecurringTemplateTests(RecurringInvoiceTestsBase, TestCase):
    def test_create_template_initializes_next_run_at_to_start_date(self):
        with tenant_context(organization_id=self.org_a.id):
            template = self._make_template(start_date=datetime.date(2026, 4, 1))
        self.assertEqual(template.next_run_at, datetime.date(2026, 4, 1))
        self.assertTrue(template.is_active)

    def test_update_template_lines(self):
        with tenant_context(organization_id=self.org_a.id):
            template = self._make_template()
            updated = update_recurring_template(
                template=template,
                lines=[{"item": self.item, "quantity": Decimal("2"), "unit_price": Decimal("100.00")}],
            )
            self.assertEqual(updated.lines.count(), 1)
            self.assertEqual(updated.lines.first().quantity, Decimal("2"))

    def test_deactivate_and_activate_template(self):
        with tenant_context(organization_id=self.org_a.id):
            template = self._make_template()
            deactivated = deactivate_template(template=template)
            self.assertFalse(deactivated.is_active)
            reactivated = activate_template(template=deactivated)
            self.assertTrue(reactivated.is_active)

    def test_update_rejects_end_date_before_start_date(self):
        # Regression: create checked this, update did not.
        with tenant_context(organization_id=self.org_a.id):
            template = self._make_template(start_date=datetime.date(2026, 4, 1))
            with self.assertRaises(ApplicationError) as ctx:
                update_recurring_template(template=template, end_date=datetime.date(2026, 3, 1))
            self.assertEqual(ctx.exception.get_codes(), "recurring_end_before_start")
            template.refresh_from_db()
            self.assertIsNone(template.end_date)

    def test_tracked_product_line_requires_a_warehouse(self):
        # Regression: accepted here, then every scheduled occurrence failed
        # with warehouse_required in the background job.
        with tenant_context(organization_id=self.org_a.id):
            product = create_item(
                organization=self.org_a, item_type=ItemType.PRODUCT, name="Boxed widget", unit=self.unit,
                sku="BOX-1", track_inventory=True, sales_account=self.sales_account,
            )
            with self.assertRaises(ApplicationError) as ctx:
                create_recurring_template(
                    organization=self.org_a, customer=self.customer, frequency=RecurringFrequency.MONTHLY,
                    start_date=datetime.date(2026, 4, 1), receivable_account=self.ar_account,
                    lines=[{"item": product, "quantity": Decimal("1"), "unit_price": Decimal("50.00")}],
                )
            self.assertEqual(ctx.exception.get_codes(), "warehouse_required")

    def test_cross_org_customer_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError):
                create_recurring_template(
                    organization=self.org_a, customer=self.customer_b, frequency=RecurringFrequency.MONTHLY,
                    start_date=datetime.date(2026, 4, 1), receivable_account=self.ar_account,
                    lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("100.00")}],
                )


class GenerateDueInvoicesTests(RecurringInvoiceTestsBase, TransactionTestCase):
    """TransactionTestCase, not TestCase: generate_due_invoices spans every
    organization (it enumerates accounts.Organization, then opens its own
    tenant_context()/transaction.atomic() per org — see
    services/recurring_invoices.py) — including org_b, created in setUp for
    the isolation tests even though most tests here only care about org_a.
    Under TestCase's outer wrapping transaction, each tenant_context() call
    becomes a SAVEPOINT rather than a real transaction, and Postgres only
    reverts a SET LOCAL GUC on ROLLBACK, not on a savepoint RELEASE — so
    the last org processed (org_b, with no templates of its own) would
    leak its GUC and break every assertion scoped to org_a. A real
    top-level transaction per tenant_context() call (what
    TransactionTestCase and production Celery execution both give you)
    doesn't have this problem. Calls happen OUTSIDE any test-level
    tenant_context() block, matching how this is actually invoked in
    production (a Celery task with no ambient tenant context)."""

    def test_scheduled_invoice_generated_when_due(self):
        with tenant_context(organization_id=self.org_a.id):
            template = self._make_template(start_date=datetime.date(2026, 4, 1))
        generated = generate_due_invoices(as_of=datetime.date(2026, 4, 1))
        self.assertEqual(len(generated), 1)
        invoice = generated[0]
        self.assertEqual(invoice.status, InvoiceStatus.DRAFT)
        self.assertEqual(invoice.customer_id, self.customer.id)
        self.assertEqual(invoice.total, Decimal("500.00"))
        with tenant_context(organization_id=self.org_a.id):
            template.refresh_from_db()
            self.assertEqual(template.next_run_at, datetime.date(2026, 5, 1))

    def test_one_failing_template_does_not_stop_the_others(self):
        # Regression: an exception from one template aborted the whole run.
        with tenant_context(organization_id=self.org_a.id):
            doomed_item = create_item(
                organization=self.org_a, item_type=ItemType.SERVICE, name="Discontinued", unit=self.unit,
                sales_account=self.sales_account,
            )
            doomed = create_recurring_template(
                organization=self.org_a, customer=self.customer, frequency=RecurringFrequency.MONTHLY,
                start_date=datetime.date(2026, 4, 1), receivable_account=self.ar_account,
                lines=[{"item": doomed_item, "quantity": Decimal("1"), "unit_price": Decimal("10.00")}],
            )
            healthy = self._make_template(start_date=datetime.date(2026, 4, 1))
            # Generation re-validates items, and inactive items are refused.
            type(doomed_item).objects.filter(pk=doomed_item.pk).update(is_active=False)

        with self.assertLogs("sales.recurring", level="ERROR"):
            generated = generate_due_invoices(as_of=datetime.date(2026, 4, 1))

        self.assertEqual(len(generated), 1)
        with tenant_context(organization_id=self.org_a.id):
            healthy.refresh_from_db()
            doomed.refresh_from_db()
            self.assertEqual(healthy.next_run_at, datetime.date(2026, 5, 1))
            # Not advanced: it is retried on the next run.
            self.assertEqual(doomed.next_run_at, datetime.date(2026, 4, 1))

    def test_not_yet_due_generates_nothing(self):
        with tenant_context(organization_id=self.org_a.id):
            self._make_template(start_date=datetime.date(2026, 5, 1))
        generated = generate_due_invoices(as_of=datetime.date(2026, 4, 1))
        self.assertEqual(generated, [])

    def test_duplicate_schedule_execution_idempotent(self):
        with tenant_context(organization_id=self.org_a.id):
            self._make_template(start_date=datetime.date(2026, 4, 1))
        first_run = generate_due_invoices(as_of=datetime.date(2026, 4, 1))
        second_run = generate_due_invoices(as_of=datetime.date(2026, 4, 1))
        self.assertEqual(len(first_run), 1)
        self.assertEqual(second_run, [])
        with tenant_context(organization_id=self.org_a.id):
            self.assertEqual(RecurringInvoiceRun.objects.count(), 1)

    def test_catch_up_generates_multiple_missed_occurrences(self):
        with tenant_context(organization_id=self.org_a.id):
            self._make_template(frequency=RecurringFrequency.MONTHLY, start_date=datetime.date(2026, 1, 1))
        generated = generate_due_invoices(as_of=datetime.date(2026, 4, 1))
        # Jan, Feb, Mar, Apr occurrences all due.
        self.assertEqual(len(generated), 4)
        with tenant_context(organization_id=self.org_a.id):
            self.assertEqual(RecurringInvoiceRun.objects.count(), 4)

    def test_inactive_template_not_generated(self):
        with tenant_context(organization_id=self.org_a.id):
            template = self._make_template(start_date=datetime.date(2026, 4, 1))
            deactivate_template(template=template)
        generated = generate_due_invoices(as_of=datetime.date(2026, 4, 1))
        self.assertEqual(generated, [])

    def test_end_date_stops_generation(self):
        with tenant_context(organization_id=self.org_a.id):
            self._make_template(
                frequency=RecurringFrequency.MONTHLY, start_date=datetime.date(2026, 1, 1),
                end_date=datetime.date(2026, 2, 15),
            )
        generated = generate_due_invoices(as_of=datetime.date(2026, 6, 1))
        # Only Jan and Feb occurrences fall on/before end_date.
        self.assertEqual(len(generated), 2)

    def test_weekly_frequency_advances_by_seven_days(self):
        with tenant_context(organization_id=self.org_a.id):
            template = self._make_template(frequency=RecurringFrequency.WEEKLY, start_date=datetime.date(2026, 4, 1))
        generate_due_invoices(as_of=datetime.date(2026, 4, 1))
        with tenant_context(organization_id=self.org_a.id):
            template.refresh_from_db()
            self.assertEqual(template.next_run_at, datetime.date(2026, 4, 8))

    def test_yearly_frequency_advances_by_one_year(self):
        with tenant_context(organization_id=self.org_a.id):
            template = self._make_template(frequency=RecurringFrequency.YEARLY, start_date=datetime.date(2026, 4, 1))
        generate_due_invoices(as_of=datetime.date(2026, 4, 1))
        with tenant_context(organization_id=self.org_a.id):
            template.refresh_from_db()
            self.assertEqual(template.next_run_at, datetime.date(2027, 4, 1))

    def test_generated_invoice_due_date_uses_due_days(self):
        with tenant_context(organization_id=self.org_a.id):
            self._make_template(start_date=datetime.date(2026, 4, 1), due_days=15)
        generated = generate_due_invoices(as_of=datetime.date(2026, 4, 1))
        self.assertEqual(generated[0].due_date, datetime.date(2026, 4, 16))

    def test_generation_across_multiple_organizations(self):
        with tenant_context(organization_id=self.org_a.id):
            self._make_template(start_date=datetime.date(2026, 4, 1))
        with tenant_context(organization_id=self.org_b.id):
            FiscalYear.objects.create(
                organization=self.org_b, start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 12, 31)
            )
            ar_account_b = create_account(
                organization=self.org_b, code="1100", name="AR", account_type=AccountType.ASSET
            )
            sales_account_b = create_account(
                organization=self.org_b, code="4000", name="Sales", account_type=AccountType.INCOME
            )
            unit_b = create_unit(organization=self.org_b, code="EA", name="Each")
            item_b = create_item(
                organization=self.org_b, item_type=ItemType.SERVICE, name="Retainer", unit=unit_b,
                sales_account=sales_account_b,
            )
            create_recurring_template(
                organization=self.org_b, customer=self.customer_b, frequency=RecurringFrequency.MONTHLY,
                start_date=datetime.date(2026, 4, 1), receivable_account=ar_account_b,
                lines=[{"item": item_b, "quantity": Decimal("1"), "unit_price": Decimal("50.00")}],
            )
        # Not tenant-scoped — spans every organization, self-scoping per template.
        generated = generate_due_invoices(as_of=datetime.date(2026, 4, 1))
        self.assertEqual(len(generated), 2)


class RecurringInvoiceTenantIsolationTests(RecurringInvoiceTestsBase, TestCase):
    def test_tenant_isolation(self):
        with tenant_context(organization_id=self.org_a.id):
            self._make_template()
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(list(RecurringInvoiceTemplate.objects.all()), [])

    def test_no_tenant_context_fails_closed(self):
        with tenant_context(organization_id=self.org_a.id):
            self._make_template()
        clear_tenant_context()
        self.assertEqual(list(RecurringInvoiceTemplate.objects.all()), [])
