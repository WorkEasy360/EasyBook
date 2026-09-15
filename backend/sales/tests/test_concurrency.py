import datetime
import threading
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import TestCase, TransactionTestCase

from accounting.models.account import AccountType
from accounting.models.journal import JournalEntry
from accounting.services.accounts import create_account
from accounts.models import FiscalYear
from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from inventory.models.stock_movement import StockMovement
from inventory.selectors import get_stock_on_hand
from inventory.services.opening_stock import post_opening_stock
from inventory.services.warehouses import create_warehouse
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit
from sales.models.invoice import Invoice, InvoiceStatus
from sales.services.customers import create_customer
from sales.services.invoices import create_invoice, post_invoice
from sales.services.payments import record_payment


class SalesConcurrencyTestsBase:
    """Shared fixture setup. Not a TestCase subclass itself — concrete
    classes below combine it with TransactionTestCase (for real
    thread/connection contention — TestCase's outer wrapping transaction
    would serialize everything on a single connection and defeat the
    point) or TestCase (for the single-threaded rollback proof)."""

    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Org", "sales-concurrency@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            FiscalYear.objects.create(
                organization=self.org, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            self.ar_account = create_account(
                organization=self.org, code="1100", name="AR", account_type=AccountType.ASSET
            )
            self.bank_account = create_account(
                organization=self.org, code="1000", name="Bank", account_type=AccountType.ASSET
            )
            self.sales_account = create_account(
                organization=self.org, code="4000", name="Sales", account_type=AccountType.INCOME
            )
            self.cogs_account = create_account(
                organization=self.org, code="5000", name="COGS", account_type=AccountType.EXPENSE
            )
            self.inventory_account = create_account(
                organization=self.org, code="1200", name="Inventory Asset", account_type=AccountType.ASSET
            )
            self.warehouse = create_warehouse(organization=self.org, code="MAIN", name="Main")
            self.unit = create_unit(organization=self.org, code="EA", name="Each")
            self.service_item = create_item(
                organization=self.org, item_type=ItemType.SERVICE, name="Consulting", unit=self.unit,
                sales_account=self.sales_account,
            )
            self.product_item = create_item(
                organization=self.org, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit,
                sku="WID-1", track_inventory=True, sales_account=self.sales_account,
                inventory_account=self.inventory_account, cogs_account=self.cogs_account,
            )
            self.customer = create_customer(
                organization=self.org, customer_code="CUST-1", display_name="Acme", currency=self.currency
            )


class ConcurrentInvoicePostTests(SalesConcurrencyTestsBase, TransactionTestCase):
    """§26: 'Two concurrent invoice-post requests -> one posting'."""

    def test_concurrent_post_of_same_invoice_posts_exactly_once(self):
        with tenant_context(organization_id=self.org.id):
            invoice = create_invoice(
                organization=self.org, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                lines=[{"item": self.service_item, "quantity": Decimal("1"), "unit_price": Decimal("100.00")}],
            )
        invoice_id = invoice.id

        results = []
        errors = []
        lock = threading.Lock()

        def do_post():
            try:
                with tenant_context(organization_id=self.org.id):
                    posted = post_invoice(invoice_id=invoice_id, organization=self.org)
                with lock:
                    results.append(posted.invoice_number)
            except Exception as exc:
                with lock:
                    errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=do_post) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # post_invoice is idempotent-by-construction (locked no-op once
        # already SENT) — every thread succeeds, but only ONE journal exists.
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 5)
        self.assertEqual(len(set(results)), 1, "all callers must observe the same invoice_number")

        with tenant_context(organization_id=self.org.id):
            journal_count = JournalEntry.objects.filter(
                source_type="sales.Invoice", source_id=str(invoice_id)
            ).count()
            self.assertEqual(journal_count, 1)


class ConcurrentPaymentAllocationTests(SalesConcurrencyTestsBase, TransactionTestCase):
    """§26: 'Two concurrent full payments -> cannot overpay invoice
    unintentionally' and 'Two concurrent payment requests -> no duplicate
    allocation'."""

    def test_concurrent_full_payments_cannot_overpay_invoice(self):
        with tenant_context(organization_id=self.org.id):
            invoice = create_invoice(
                organization=self.org, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                lines=[{"item": self.service_item, "quantity": Decimal("1"), "unit_price": Decimal("100.00")}],
            )
            invoice = post_invoice(invoice_id=invoice.id, organization=self.org)
        invoice_id = invoice.id

        results = []
        errors = []
        lock = threading.Lock()

        def do_pay():
            try:
                with tenant_context(organization_id=self.org.id):
                    invoice_ref = Invoice.objects.get(pk=invoice_id)
                    payment = record_payment(
                        organization=self.org, customer=self.customer, payment_date=datetime.date(2026, 4, 15),
                        amount=Decimal("100.00"), destination_account=self.bank_account,
                        allocations=[{"invoice": invoice_ref, "amount": Decimal("100.00")}],
                    )
                with lock:
                    results.append(payment.id)
            except ApplicationError as exc:
                with lock:
                    errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=do_pay) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one payment succeeds (the invoice can only absorb 100.00
        # once). record_payment locks the invoice row via select_for_update,
        # so every other thread only acquires the lock AFTER the winner has
        # already committed and marked the invoice PAID — they are rejected
        # as "not payable" (a fully-paid invoice), not "over-allocated"
        # (which would fire only if a thread raced in while the invoice
        # still showed SENT/PARTIALLY_PAID with insufficient room). Either
        # way, the outcome root CLAUDE.md §26 cares about holds: never
        # silently double-allocated.
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 4)
        for exc in errors:
            self.assertIn(exc.get_codes(), ("over_allocation", "invoice_not_payable"))

        with tenant_context(organization_id=self.org.id):
            invoice.refresh_from_db()
            self.assertEqual(invoice.status, InvoiceStatus.PAID)
            from sales.selectors import get_invoice_amount_paid

            self.assertEqual(get_invoice_amount_paid(invoice=invoice), Decimal("100.00"))


class ConcurrentInvoiceStockIssueTests(SalesConcurrencyTestsBase, TransactionTestCase):
    """§26: 'Concurrent delivery/invoice stock issue -> cannot double-reduce
    inventory'. Two separate DRAFT invoices each need more of the same
    product than is jointly available — posting races the shared
    (item, warehouse) negative-stock guard in
    inventory.services.movements.record_stock_movement."""

    def test_concurrent_invoice_posts_cannot_oversell_shared_stock(self):
        with tenant_context(organization_id=self.org.id):
            post_opening_stock(
                organization=self.org, warehouse=self.warehouse, currency=self.currency,
                lines=[{"item": self.product_item, "quantity": Decimal("10"), "unit_cost": Decimal("10.00")}],
                opening_date=datetime.date(2026, 4, 1),
            )
            invoice_ids = []
            for _ in range(2):
                invoice = create_invoice(
                    organization=self.org, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                    due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                    warehouse=self.warehouse,
                    lines=[{"item": self.product_item, "quantity": Decimal("8"), "unit_price": Decimal("50.00")}],
                )
                invoice_ids.append(invoice.id)

        results = []
        errors = []
        lock = threading.Lock()

        def do_post(invoice_id):
            try:
                with tenant_context(organization_id=self.org.id):
                    posted = post_invoice(invoice_id=invoice_id, organization=self.org)
                with lock:
                    results.append(posted.id)
            except ApplicationError as exc:
                with lock:
                    errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=do_post, args=(iid,)) for iid in invoice_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Only 10 units exist and each invoice wants 8 — both cannot post.
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].get_codes(), "insufficient_stock")

        with tenant_context(organization_id=self.org.id):
            on_hand = get_stock_on_hand(item=self.product_item, warehouse=self.warehouse)
            self.assertEqual(on_hand, Decimal("2"))
            self.assertGreaterEqual(on_hand, Decimal("0"))


class InvoicePostRollbackTests(SalesConcurrencyTestsBase, TestCase):
    """Force a failure AFTER accounting/inventory work has happened inside
    post_invoice's atomic block and prove nothing partial survives — no
    orphan journal, no orphan stock movement, invoice still DRAFT."""

    def test_failure_after_stock_issue_rolls_back_everything(self):
        with tenant_context(organization_id=self.org.id):
            post_opening_stock(
                organization=self.org, warehouse=self.warehouse, currency=self.currency,
                lines=[{"item": self.product_item, "quantity": Decimal("50"), "unit_cost": Decimal("10.00")}],
                opening_date=datetime.date(2026, 4, 1),
            )
            invoice = create_invoice(
                organization=self.org, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account, warehouse=self.warehouse,
                lines=[{"item": self.product_item, "quantity": Decimal("10"), "unit_price": Decimal("50.00")}],
            )

            # post_sales_journal runs AFTER record_stock_movement inside
            # post_invoice — forcing it to raise proves the stock movement
            # (already inserted earlier in the same atomic block) is rolled
            # back too, not left as an orphan.
            with mock.patch("sales.services.invoices.post_sales_journal", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    post_invoice(invoice_id=invoice.id, organization=self.org)

            invoice.refresh_from_db()
            self.assertEqual(invoice.status, InvoiceStatus.DRAFT)
            self.assertEqual(invoice.invoice_number, "")
            self.assertIsNone(invoice.accounting_journal_id)

            self.assertEqual(
                StockMovement.objects.filter(source_type="sales.Invoice", source_id=str(invoice.id)).count(), 0
            )
            self.assertEqual(get_stock_on_hand(item=self.product_item, warehouse=self.warehouse), Decimal("50"))
            self.assertEqual(
                JournalEntry.objects.filter(source_type="sales.Invoice", source_id=str(invoice.id)).count(), 0
            )
