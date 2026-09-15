import datetime
from decimal import Decimal

from django.test import TestCase

from accounting.models.account import AccountType
from accounting.models.journal import JournalEntry, JournalStatus
from accounting.services.accounts import create_account
from accounts.models import FiscalYear
from core.exceptions import ApplicationError
from core.tenancy import clear_tenant_context, tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from inventory.models.stock_movement import MovementType, StockMovement
from inventory.selectors import get_stock_on_hand
from inventory.services.opening_stock import post_opening_stock
from inventory.services.warehouses import create_warehouse
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit
from sales.models.invoice import Invoice, InvoiceStatus
from sales.services.customers import create_customer
from sales.services.deliveries import create_delivery_challan, dispatch_delivery
from sales.services.invoices import (
    create_invoice,
    post_invoice,
    replace_invoice_lines,
    void_invoice,
)
from sales.services.sales_orders import confirm_order, create_sales_order


class InvoiceTestsBase(TestCase):
    def setUp(self):
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "invoice-owner-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "invoice-owner-b@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org_a.id):
            FiscalYear.objects.create(
                organization=self.org_a, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            self.ar_account = create_account(
                organization=self.org_a, code="1100", name="Accounts Receivable", account_type=AccountType.ASSET
            )
            self.sales_account = create_account(
                organization=self.org_a, code="4000", name="Sales Revenue", account_type=AccountType.INCOME
            )
            self.tax_account = create_account(
                organization=self.org_a, code="2100", name="Output Tax Payable", account_type=AccountType.LIABILITY
            )
            self.cogs_account = create_account(
                organization=self.org_a, code="5000", name="COGS", account_type=AccountType.EXPENSE
            )
            self.inventory_account = create_account(
                organization=self.org_a, code="1200", name="Inventory Asset", account_type=AccountType.ASSET
            )
            self.warehouse = create_warehouse(organization=self.org_a, code="MAIN", name="Main")
            self.unit = create_unit(organization=self.org_a, code="EA", name="Each")
            self.service_item = create_item(
                organization=self.org_a, item_type=ItemType.SERVICE, name="Consulting", unit=self.unit,
                sales_account=self.sales_account,
            )
            self.product_item = create_item(
                organization=self.org_a, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit,
                sku="WID-1", track_inventory=True, sales_account=self.sales_account,
                inventory_account=self.inventory_account, cogs_account=self.cogs_account,
            )
            post_opening_stock(
                organization=self.org_a, warehouse=self.warehouse, currency=self.currency,
                lines=[{"item": self.product_item, "quantity": Decimal("100"), "unit_cost": Decimal("10.00")}],
                opening_date=datetime.date(2026, 4, 1),
            )
            self.customer = create_customer(
                organization=self.org_a, customer_code="CUST-1", display_name="Acme", currency=self.currency
            )
        with tenant_context(organization_id=self.org_b.id):
            self.customer_b = create_customer(
                organization=self.org_b, customer_code="CUST-1", display_name="Beta", currency=self.currency
            )
            self.unit_b = create_unit(organization=self.org_b, code="EA", name="Each")
            self.item_b = create_item(
                organization=self.org_b, item_type=ItemType.SERVICE, name="Other", unit=self.unit_b
            )

    def _service_lines(self, quantity=Decimal("2"), unit_price=Decimal("100.00"), **kwargs):
        return [{"item": self.service_item, "quantity": quantity, "unit_price": unit_price, **kwargs}]


class InvoiceCreationTests(InvoiceTestsBase):
    def test_create_draft_invoice_with_correct_totals(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = create_invoice(
                organization=self.org_a, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                lines=self._service_lines(tax_rate=Decimal("18")),
            )
            self.assertEqual(invoice.lines.count(), 1)
        self.assertEqual(invoice.status, InvoiceStatus.DRAFT)
        self.assertEqual(invoice.invoice_number, "")
        self.assertEqual(invoice.subtotal, Decimal("200.00"))
        self.assertEqual(invoice.tax_total, Decimal("36.00"))
        self.assertEqual(invoice.total, Decimal("236.00"))

    def test_item_without_sales_account_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            bare_item = create_item(
                organization=self.org_a, item_type=ItemType.SERVICE, name="Bare", unit=self.unit
            )
            with self.assertRaises(ApplicationError):
                create_invoice(
                    organization=self.org_a, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                    due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                    lines=[{"item": bare_item, "quantity": Decimal("1"), "unit_price": Decimal("10")}],
                )

    def test_wrong_account_type_for_receivable_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError):
                create_invoice(
                    organization=self.org_a, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                    due_date=datetime.date(2026, 5, 10), receivable_account=self.sales_account,
                    lines=self._service_lines(),
                )

    def test_tracked_product_without_warehouse_or_challan_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError):
                create_invoice(
                    organization=self.org_a, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                    due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                    lines=[{"item": self.product_item, "quantity": Decimal("1"), "unit_price": Decimal("50")}],
                )

    def test_cross_org_customer_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError):
                create_invoice(
                    organization=self.org_a, customer=self.customer_b, invoice_date=datetime.date(2026, 4, 10),
                    due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                    lines=self._service_lines(),
                )

    def test_replace_lines_recomputes_totals(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = create_invoice(
                organization=self.org_a, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                lines=self._service_lines(),
            )
            updated = replace_invoice_lines(
                invoice=invoice, lines=self._service_lines(quantity=Decimal("1"), unit_price=Decimal("30.00"))
            )
        self.assertEqual(updated.total, Decimal("30.00"))


class InvoicePostingTests(InvoiceTestsBase):
    def _post(self, **overrides):
        kwargs = dict(
            organization=self.org_a, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
            due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
            tax_payable_account=self.tax_account, lines=self._service_lines(tax_rate=Decimal("18")),
        )
        kwargs.update(overrides)
        with tenant_context(organization_id=self.org_a.id):
            invoice = create_invoice(**kwargs)
            posted = post_invoice(invoice_id=invoice.id, organization=self.org_a)
        return posted

    def test_post_allocates_number_and_sets_sent(self):
        posted = self._post()
        self.assertEqual(posted.status, InvoiceStatus.SENT)
        self.assertTrue(posted.invoice_number.startswith("INV-"))
        self.assertIsNotNone(posted.posted_at)

    def test_balanced_journal_created_with_correct_ar_and_revenue_and_tax(self):
        posted = self._post()
        with tenant_context(organization_id=self.org_a.id):
            journal = JournalEntry.objects.get(pk=posted.accounting_journal_id)
            self.assertEqual(journal.status, JournalStatus.POSTED)
            lines = list(journal.lines.all())
            total_debit = sum((l.debit for l in lines), Decimal("0"))
            total_credit = sum((l.credit for l in lines), Decimal("0"))
            self.assertEqual(total_debit, total_credit)
            ar_line = next(l for l in lines if l.account_id == self.ar_account.id)
            self.assertEqual(ar_line.debit, Decimal("236.00"))
            revenue_line = next(l for l in lines if l.account_id == self.sales_account.id)
            self.assertEqual(revenue_line.credit, Decimal("200.00"))
            tax_line = next(l for l in lines if l.account_id == self.tax_account.id)
            self.assertEqual(tax_line.credit, Decimal("36.00"))

    def test_tax_snapshot_preserved_on_line(self):
        posted = self._post()
        with tenant_context(organization_id=self.org_a.id):
            line = posted.lines.first()
            self.assertEqual(line.tax_rate, Decimal("18.00"))
            self.assertEqual(line.tax_amount, Decimal("36.00"))

    def test_historical_line_data_preserved_after_item_changes(self):
        posted = self._post()
        with tenant_context(organization_id=self.org_a.id):
            line = posted.lines.first()
            original_description = line.description

            from items.services.items import update_item

            update_item(item=self.service_item, name="Renamed Consulting")
            line.refresh_from_db()
            self.assertEqual(line.description, original_description)

    def test_posted_invoice_immutable_at_model_layer(self):
        posted = self._post()
        posted.reference = "changed"
        with self.assertRaises(ValueError):
            posted.save()

    def test_posted_invoice_line_immutable_at_model_layer(self):
        posted = self._post()
        with tenant_context(organization_id=self.org_a.id):
            line = posted.lines.first()
            line.quantity = Decimal("99")
            with self.assertRaises(ValueError):
                line.save()

    def test_duplicate_post_is_idempotent(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = create_invoice(
                organization=self.org_a, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                tax_payable_account=self.tax_account, lines=self._service_lines(tax_rate=Decimal("18")),
            )
            first = post_invoice(invoice_id=invoice.id, organization=self.org_a)
            second = post_invoice(invoice_id=invoice.id, organization=self.org_a)
            journal_count = JournalEntry.objects.filter(source_type="sales.Invoice", source_id=str(invoice.id)).count()
        self.assertEqual(first.invoice_number, second.invoice_number)
        self.assertEqual(journal_count, 1)

    def test_posting_without_tax_account_when_tax_due_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = create_invoice(
                organization=self.org_a, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                lines=self._service_lines(tax_rate=Decimal("18")),
            )
            with self.assertRaises(ApplicationError):
                post_invoice(invoice_id=invoice.id, organization=self.org_a)

    def test_cannot_replace_lines_on_posted_invoice(self):
        posted = self._post()
        with self.assertRaises(ApplicationError):
            replace_invoice_lines(invoice=posted, lines=self._service_lines())


class InvoiceStockIntegrationTests(InvoiceTestsBase):
    def test_direct_invoice_issues_stock_and_posts_cogs(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = create_invoice(
                organization=self.org_a, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account, warehouse=self.warehouse,
                lines=[{"item": self.product_item, "quantity": Decimal("10"), "unit_price": Decimal("50.00")}],
            )
            posted = post_invoice(invoice_id=invoice.id, organization=self.org_a)

            on_hand = get_stock_on_hand(item=self.product_item, warehouse=self.warehouse)
            self.assertEqual(on_hand, Decimal("90"))

            issue_movements = StockMovement.objects.filter(
                movement_type=MovementType.ISSUE, source_type="sales.Invoice", source_id=str(invoice.id)
            )
            self.assertEqual(issue_movements.count(), 1)

            journal = JournalEntry.objects.get(pk=posted.accounting_journal_id)
            lines = list(journal.lines.all())
            cogs_line = next(l for l in lines if l.account_id == self.cogs_account.id)
            inv_line = next(l for l in lines if l.account_id == self.inventory_account.id)
            self.assertEqual(cogs_line.debit, Decimal("100.00"))  # 10 units * 10.00 cost
            self.assertEqual(inv_line.credit, Decimal("100.00"))

    def test_no_double_inventory_movement_when_already_delivered(self):
        with tenant_context(organization_id=self.org_a.id):
            order = create_sales_order(
                organization=self.org_a, customer=self.customer, order_date=datetime.date(2026, 4, 1),
                lines=[{"item": self.product_item, "quantity": Decimal("10"), "unit_price": Decimal("50.00")}],
            )
            confirm_order(order_id=order.id, organization=self.org_a)
            order.refresh_from_db()
            order_line = order.lines.first()

            challan = create_delivery_challan(
                organization=self.org_a, customer=self.customer, warehouse=self.warehouse,
                challan_date=datetime.date(2026, 4, 5), source_sales_order=order,
                lines=[{"item": self.product_item, "quantity": Decimal("10"), "source_order_line": order_line}],
            )
            dispatch_delivery(challan_id=challan.id, organization=self.org_a)
            challan_line = challan.lines.first()

            self.assertEqual(get_stock_on_hand(item=self.product_item, warehouse=self.warehouse), Decimal("90"))

            invoice = create_invoice(
                organization=self.org_a, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                lines=[{
                    "item": self.product_item, "quantity": Decimal("10"), "unit_price": Decimal("50.00"),
                    "source_delivery_challan_line": challan_line,
                }],
            )
            posted = post_invoice(invoice_id=invoice.id, organization=self.org_a)

            # No NEW issue movement from the invoice — stock stays at 90.
            self.assertEqual(get_stock_on_hand(item=self.product_item, warehouse=self.warehouse), Decimal("90"))
            invoice_issue_movements = StockMovement.objects.filter(
                movement_type=MovementType.ISSUE, source_type="sales.Invoice", source_id=str(invoice.id)
            )
            self.assertEqual(invoice_issue_movements.count(), 0)

            # COGS is still posted, using the historical cost basis.
            journal = JournalEntry.objects.get(pk=posted.accounting_journal_id)
            cogs_line = next(l for l in journal.lines.all() if l.account_id == self.cogs_account.id)
            self.assertEqual(cogs_line.debit, Decimal("100.00"))

    def test_invoice_quantity_exceeding_delivered_quantity_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            order = create_sales_order(
                organization=self.org_a, customer=self.customer, order_date=datetime.date(2026, 4, 1),
                lines=[{"item": self.product_item, "quantity": Decimal("10"), "unit_price": Decimal("50.00")}],
            )
            confirm_order(order_id=order.id, organization=self.org_a)
            order.refresh_from_db()
            order_line = order.lines.first()
            challan = create_delivery_challan(
                organization=self.org_a, customer=self.customer, warehouse=self.warehouse,
                challan_date=datetime.date(2026, 4, 5), source_sales_order=order,
                lines=[{"item": self.product_item, "quantity": Decimal("4"), "source_order_line": order_line}],
            )
            dispatch_delivery(challan_id=challan.id, organization=self.org_a)
            challan_line = challan.lines.first()

            with self.assertRaises(ApplicationError):
                create_invoice(
                    organization=self.org_a, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                    due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                    lines=[{
                        "item": self.product_item, "quantity": Decimal("5"), "unit_price": Decimal("50.00"),
                        "source_delivery_challan_line": challan_line,
                    }],
                )


class InvoiceVoidTests(InvoiceTestsBase):
    def test_void_reverses_accounting_journal(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = create_invoice(
                organization=self.org_a, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                tax_payable_account=self.tax_account, lines=self._service_lines(tax_rate=Decimal("18")),
            )
            posted = post_invoice(invoice_id=invoice.id, organization=self.org_a)
            journal_id = posted.accounting_journal_id
            voided = void_invoice(invoice_id=invoice.id, organization=self.org_a, reason="customer cancelled")
        self.assertEqual(voided.status, InvoiceStatus.VOID)
        with tenant_context(organization_id=self.org_a.id):
            original = JournalEntry.objects.get(pk=journal_id)
            self.assertEqual(original.status, JournalStatus.REVERSED)
            self.assertTrue(hasattr(original, "reversal"))

    def test_void_reverses_directly_issued_stock(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = create_invoice(
                organization=self.org_a, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account, warehouse=self.warehouse,
                lines=[{"item": self.product_item, "quantity": Decimal("10"), "unit_price": Decimal("50.00")}],
            )
            post_invoice(invoice_id=invoice.id, organization=self.org_a)
            self.assertEqual(get_stock_on_hand(item=self.product_item, warehouse=self.warehouse), Decimal("90"))
            void_invoice(invoice_id=invoice.id, organization=self.org_a)
            self.assertEqual(get_stock_on_hand(item=self.product_item, warehouse=self.warehouse), Decimal("100"))

    def test_cannot_void_draft_invoice(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = create_invoice(
                organization=self.org_a, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                lines=self._service_lines(),
            )
            with self.assertRaises(ApplicationError):
                void_invoice(invoice_id=invoice.id, organization=self.org_a)

    def test_duplicate_void_is_idempotent(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = create_invoice(
                organization=self.org_a, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                lines=self._service_lines(),
            )
            post_invoice(invoice_id=invoice.id, organization=self.org_a)
            first = void_invoice(invoice_id=invoice.id, organization=self.org_a)
            second = void_invoice(invoice_id=invoice.id, organization=self.org_a)
        self.assertEqual(first.status, second.status)


class InvoiceTenantIsolationTests(InvoiceTestsBase):
    def test_tenant_isolation(self):
        with tenant_context(organization_id=self.org_a.id):
            create_invoice(
                organization=self.org_a, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                lines=self._service_lines(),
            )
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(list(Invoice.objects.all()), [])

    def test_no_tenant_context_fails_closed(self):
        with tenant_context(organization_id=self.org_a.id):
            create_invoice(
                organization=self.org_a, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                lines=self._service_lines(),
            )
        clear_tenant_context()
        self.assertEqual(list(Invoice.objects.all()), [])
