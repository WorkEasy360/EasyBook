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
from inventory.selectors import get_stock_on_hand
from inventory.services.opening_stock import post_opening_stock
from inventory.services.warehouses import create_warehouse
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit
from sales.models.credit_note import CreditNote, CreditNoteStatus
from sales.selectors import get_invoice_amount_due
from sales.services.credit_notes import create_credit_note, issue_credit_note, void_credit_note
from sales.services.customers import create_customer
from sales.services.invoices import create_invoice, post_invoice
from sales.services.payments import record_payment


class CreditNoteTestsBase(TestCase):
    def setUp(self):
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "cn-owner-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "cn-owner-b@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org_a.id):
            FiscalYear.objects.create(
                organization=self.org_a, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            self.ar_account = create_account(
                organization=self.org_a, code="1100", name="AR", account_type=AccountType.ASSET
            )
            self.bank_account = create_account(
                organization=self.org_a, code="1000", name="Bank", account_type=AccountType.ASSET
            )
            self.credit_account = create_account(
                organization=self.org_a, code="2200", name="Customer Advances", account_type=AccountType.LIABILITY
            )
            self.sales_account = create_account(
                organization=self.org_a, code="4000", name="Sales", account_type=AccountType.INCOME
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

    def _make_invoice(self, item=None, quantity=Decimal("1"), unit_price=Decimal("100.00")):
        item = item or self.service_item
        invoice = create_invoice(
            organization=self.org_a, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
            due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
            warehouse=self.warehouse if item.track_inventory else None,
            lines=[{"item": item, "quantity": quantity, "unit_price": unit_price}],
        )
        return post_invoice(invoice_id=invoice.id, organization=self.org_a)


class CreditNoteAgainstInvoiceTests(CreditNoteTestsBase):
    def test_partial_credit_nets_against_ar(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(unit_price=Decimal("100.00"))
            invoice_line = invoice.lines.first()
            credit_note = create_credit_note(
                organization=self.org_a, customer=self.customer, credit_note_date=datetime.date(2026, 4, 15),
                source_invoice=invoice,
                lines=[{
                    "item": self.service_item, "quantity": Decimal("1"), "unit_price": Decimal("40.00"),
                    "source_invoice_line": invoice_line,
                }],
            )
            issued = issue_credit_note(credit_note_id=credit_note.id, organization=self.org_a)
            invoice.refresh_from_db()
            self.assertEqual(issued.status, CreditNoteStatus.ISSUED)
            self.assertTrue(issued.credit_note_number.startswith("CN-"))
            self.assertEqual(get_invoice_amount_due(invoice=invoice), Decimal("60.00"))

    def test_full_credit_zeroes_invoice_due(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(unit_price=Decimal("100.00"))
            invoice_line = invoice.lines.first()
            credit_note = create_credit_note(
                organization=self.org_a, customer=self.customer, credit_note_date=datetime.date(2026, 4, 15),
                source_invoice=invoice,
                lines=[{
                    "item": self.service_item, "quantity": Decimal("1"), "unit_price": Decimal("100.00"),
                    "source_invoice_line": invoice_line,
                }],
            )
            issue_credit_note(credit_note_id=credit_note.id, organization=self.org_a)
            invoice.refresh_from_db()
            self.assertEqual(get_invoice_amount_due(invoice=invoice), Decimal("0.00"))

    def test_accounting_reversal_correct(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(unit_price=Decimal("100.00"))
            invoice_line = invoice.lines.first()
            credit_note = create_credit_note(
                organization=self.org_a, customer=self.customer, credit_note_date=datetime.date(2026, 4, 15),
                source_invoice=invoice,
                lines=[{
                    "item": self.service_item, "quantity": Decimal("1"), "unit_price": Decimal("40.00"),
                    "source_invoice_line": invoice_line,
                }],
            )
            issued = issue_credit_note(credit_note_id=credit_note.id, organization=self.org_a)
            journal = JournalEntry.objects.get(pk=issued.accounting_journal_id)
            lines = list(journal.lines.all())
            total_debit = sum((jl.debit for jl in lines), Decimal("0"))
            total_credit = sum((jl.credit for jl in lines), Decimal("0"))
            self.assertEqual(total_debit, total_credit)
            revenue_line = next(jl for jl in lines if jl.account_id == self.sales_account.id)
            ar_line = next(jl for jl in lines if jl.account_id == self.ar_account.id)
            self.assertEqual(revenue_line.debit, Decimal("40.00"))
            self.assertEqual(ar_line.credit, Decimal("40.00"))

    def test_credit_exceeding_paid_invoice_due_becomes_unapplied_credit(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(unit_price=Decimal("100.00"))
            record_payment(
                organization=self.org_a, customer=self.customer, payment_date=datetime.date(2026, 4, 12),
                amount=Decimal("100.00"), destination_account=self.bank_account,
                allocations=[{"invoice": invoice, "amount": Decimal("100.00")}],
            )
            invoice.refresh_from_db()
            self.assertEqual(get_invoice_amount_due(invoice=invoice), Decimal("0.00"))

            invoice_line = invoice.lines.first()
            credit_note = create_credit_note(
                organization=self.org_a, customer=self.customer, credit_note_date=datetime.date(2026, 4, 20),
                source_invoice=invoice, unapplied_credit_account=self.credit_account,
                lines=[{
                    "item": self.service_item, "quantity": Decimal("1"), "unit_price": Decimal("30.00"),
                    "source_invoice_line": invoice_line,
                }],
            )
            issued = issue_credit_note(credit_note_id=credit_note.id, organization=self.org_a)
            journal = JournalEntry.objects.get(pk=issued.accounting_journal_id)
            credit_line = next(jl for jl in journal.lines.all() if jl.account_id == self.credit_account.id)
            self.assertEqual(credit_line.credit, Decimal("30.00"))
            # AR untouched below zero — invoice due stays exactly at 0, not negative.
            self.assertEqual(get_invoice_amount_due(invoice=invoice), Decimal("0.00"))

    def test_missing_unapplied_credit_account_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(unit_price=Decimal("100.00"))
            record_payment(
                organization=self.org_a, customer=self.customer, payment_date=datetime.date(2026, 4, 12),
                amount=Decimal("100.00"), destination_account=self.bank_account,
                allocations=[{"invoice": invoice, "amount": Decimal("100.00")}],
            )
            invoice.refresh_from_db()
            invoice_line = invoice.lines.first()
            credit_note = create_credit_note(
                organization=self.org_a, customer=self.customer, credit_note_date=datetime.date(2026, 4, 20),
                source_invoice=invoice,
                lines=[{
                    "item": self.service_item, "quantity": Decimal("1"), "unit_price": Decimal("30.00"),
                    "source_invoice_line": invoice_line,
                }],
            )
            with self.assertRaises(ApplicationError):
                issue_credit_note(credit_note_id=credit_note.id, organization=self.org_a)

    def test_original_invoice_lines_preserved(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(unit_price=Decimal("100.00"))
            original_total = invoice.total
            invoice_line = invoice.lines.first()
            credit_note = create_credit_note(
                organization=self.org_a, customer=self.customer, credit_note_date=datetime.date(2026, 4, 15),
                source_invoice=invoice,
                lines=[{
                    "item": self.service_item, "quantity": Decimal("1"), "unit_price": Decimal("40.00"),
                    "source_invoice_line": invoice_line,
                }],
            )
            issue_credit_note(credit_note_id=credit_note.id, organization=self.org_a)
            invoice.refresh_from_db()
            self.assertEqual(invoice.total, original_total)
            self.assertEqual(invoice.lines.count(), 1)

    def test_credit_quantity_exceeding_invoiced_quantity_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(quantity=Decimal("1"), unit_price=Decimal("100.00"))
            invoice_line = invoice.lines.first()
            with self.assertRaises(ApplicationError):
                create_credit_note(
                    organization=self.org_a, customer=self.customer, credit_note_date=datetime.date(2026, 4, 15),
                    source_invoice=invoice,
                    lines=[{
                        "item": self.service_item, "quantity": Decimal("2"), "unit_price": Decimal("100.00"),
                        "source_invoice_line": invoice_line,
                    }],
                )


class CreditNoteRestockTests(CreditNoteTestsBase):
    def test_restocked_product_returns_stock_and_posts_cogs_reversal(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(item=self.product_item, quantity=Decimal("10"), unit_price=Decimal("50.00"))
            # posting the invoice issued 10 units — confirm baseline first.
            self.assertEqual(get_stock_on_hand(item=self.product_item, warehouse=self.warehouse), Decimal("90"))
            invoice_line = invoice.lines.first()

            credit_note = create_credit_note(
                organization=self.org_a, customer=self.customer, credit_note_date=datetime.date(2026, 4, 15),
                source_invoice=invoice, warehouse=self.warehouse,
                lines=[{
                    "item": self.product_item, "quantity": Decimal("10"), "unit_price": Decimal("50.00"),
                    "source_invoice_line": invoice_line, "restock": True, "unit_cost": Decimal("10.00"),
                }],
            )
            issued = issue_credit_note(credit_note_id=credit_note.id, organization=self.org_a)

            self.assertEqual(get_stock_on_hand(item=self.product_item, warehouse=self.warehouse), Decimal("100"))
            journal = JournalEntry.objects.get(pk=issued.accounting_journal_id)
            lines = list(journal.lines.all())
            inv_line = next(jl for jl in lines if jl.account_id == self.inventory_account.id)
            cogs_line = next(jl for jl in lines if jl.account_id == self.cogs_account.id)
            self.assertEqual(inv_line.debit, Decimal("100.00"))
            self.assertEqual(cogs_line.credit, Decimal("100.00"))

    def test_service_item_cannot_be_restocked(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(unit_price=Decimal("100.00"))
            invoice_line = invoice.lines.first()
            with self.assertRaises(ApplicationError):
                create_credit_note(
                    organization=self.org_a, customer=self.customer, credit_note_date=datetime.date(2026, 4, 15),
                    source_invoice=invoice, warehouse=self.warehouse,
                    lines=[{
                        "item": self.service_item, "quantity": Decimal("1"), "unit_price": Decimal("100.00"),
                        "source_invoice_line": invoice_line, "restock": True, "unit_cost": Decimal("1.00"),
                    }],
                )

    def test_restock_without_unit_cost_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(item=self.product_item, quantity=Decimal("10"), unit_price=Decimal("50.00"))
            invoice_line = invoice.lines.first()
            with self.assertRaises(ApplicationError):
                create_credit_note(
                    organization=self.org_a, customer=self.customer, credit_note_date=datetime.date(2026, 4, 15),
                    source_invoice=invoice, warehouse=self.warehouse,
                    lines=[{
                        "item": self.product_item, "quantity": Decimal("10"), "unit_price": Decimal("50.00"),
                        "source_invoice_line": invoice_line, "restock": True,
                    }],
                )

    def test_non_restock_product_credit_does_not_move_stock(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(item=self.product_item, quantity=Decimal("10"), unit_price=Decimal("50.00"))
            invoice_line = invoice.lines.first()
            credit_note = create_credit_note(
                organization=self.org_a, customer=self.customer, credit_note_date=datetime.date(2026, 4, 15),
                source_invoice=invoice,
                lines=[{
                    "item": self.product_item, "quantity": Decimal("10"), "unit_price": Decimal("50.00"),
                    "source_invoice_line": invoice_line,
                }],
            )
            issue_credit_note(credit_note_id=credit_note.id, organization=self.org_a)
            self.assertEqual(get_stock_on_hand(item=self.product_item, warehouse=self.warehouse), Decimal("90"))


class CreditNoteStandaloneTests(CreditNoteTestsBase):
    def test_standalone_customer_credit_has_no_source_invoice(self):
        with tenant_context(organization_id=self.org_a.id):
            credit_note = create_credit_note(
                organization=self.org_a, customer=self.customer, credit_note_date=datetime.date(2026, 4, 15),
                unapplied_credit_account=self.credit_account,
                lines=[{"item": self.service_item, "quantity": Decimal("1"), "unit_price": Decimal("25.00")}],
            )
            issued = issue_credit_note(credit_note_id=credit_note.id, organization=self.org_a)
            self.assertIsNone(issued.source_invoice_id)
            journal = JournalEntry.objects.get(pk=issued.accounting_journal_id)
            credit_line = next(jl for jl in journal.lines.all() if jl.account_id == self.credit_account.id)
            self.assertEqual(credit_line.credit, Decimal("25.00"))


class CreditNoteVoidTests(CreditNoteTestsBase):
    def test_void_reverses_journal(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(unit_price=Decimal("100.00"))
            invoice_line = invoice.lines.first()
            credit_note = create_credit_note(
                organization=self.org_a, customer=self.customer, credit_note_date=datetime.date(2026, 4, 15),
                source_invoice=invoice,
                lines=[{
                    "item": self.service_item, "quantity": Decimal("1"), "unit_price": Decimal("40.00"),
                    "source_invoice_line": invoice_line,
                }],
            )
            issued = issue_credit_note(credit_note_id=credit_note.id, organization=self.org_a)
            journal_id = issued.accounting_journal_id
            voided = void_credit_note(credit_note_id=credit_note.id, organization=self.org_a)
            self.assertEqual(voided.status, CreditNoteStatus.VOID)
            original = JournalEntry.objects.get(pk=journal_id)
            self.assertEqual(original.status, JournalStatus.REVERSED)

    def test_void_reverses_restock(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(item=self.product_item, quantity=Decimal("10"), unit_price=Decimal("50.00"))
            invoice_line = invoice.lines.first()
            credit_note = create_credit_note(
                organization=self.org_a, customer=self.customer, credit_note_date=datetime.date(2026, 4, 15),
                source_invoice=invoice, warehouse=self.warehouse,
                lines=[{
                    "item": self.product_item, "quantity": Decimal("10"), "unit_price": Decimal("50.00"),
                    "source_invoice_line": invoice_line, "restock": True, "unit_cost": Decimal("10.00"),
                }],
            )
            issue_credit_note(credit_note_id=credit_note.id, organization=self.org_a)
            self.assertEqual(get_stock_on_hand(item=self.product_item, warehouse=self.warehouse), Decimal("100"))
            void_credit_note(credit_note_id=credit_note.id, organization=self.org_a)
            self.assertEqual(get_stock_on_hand(item=self.product_item, warehouse=self.warehouse), Decimal("90"))

    def test_duplicate_issue_idempotent(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(unit_price=Decimal("100.00"))
            invoice_line = invoice.lines.first()
            credit_note = create_credit_note(
                organization=self.org_a, customer=self.customer, credit_note_date=datetime.date(2026, 4, 15),
                source_invoice=invoice,
                lines=[{
                    "item": self.service_item, "quantity": Decimal("1"), "unit_price": Decimal("40.00"),
                    "source_invoice_line": invoice_line,
                }],
            )
            first = issue_credit_note(credit_note_id=credit_note.id, organization=self.org_a)
            second = issue_credit_note(credit_note_id=credit_note.id, organization=self.org_a)
            self.assertEqual(first.credit_note_number, second.credit_note_number)
            journal_count = JournalEntry.objects.filter(
                source_type="sales.CreditNote", source_id=str(credit_note.id)
            ).count()
            self.assertEqual(journal_count, 1)


class CreditNoteTenantIsolationTests(CreditNoteTestsBase):
    def test_tenant_isolation(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(unit_price=Decimal("100.00"))
            invoice_line = invoice.lines.first()
            create_credit_note(
                organization=self.org_a, customer=self.customer, credit_note_date=datetime.date(2026, 4, 15),
                source_invoice=invoice,
                lines=[{
                    "item": self.service_item, "quantity": Decimal("1"), "unit_price": Decimal("40.00"),
                    "source_invoice_line": invoice_line,
                }],
            )
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(list(CreditNote.objects.all()), [])

    def test_no_tenant_context_fails_closed(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(unit_price=Decimal("100.00"))
            invoice_line = invoice.lines.first()
            create_credit_note(
                organization=self.org_a, customer=self.customer, credit_note_date=datetime.date(2026, 4, 15),
                source_invoice=invoice,
                lines=[{
                    "item": self.service_item, "quantity": Decimal("1"), "unit_price": Decimal("40.00"),
                    "source_invoice_line": invoice_line,
                }],
            )
        clear_tenant_context()
        self.assertEqual(list(CreditNote.objects.all()), [])
