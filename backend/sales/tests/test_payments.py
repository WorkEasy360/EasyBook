import datetime
from decimal import Decimal

from django.test import TestCase

from accounting.models.account import AccountType
from accounting.models.journal import JournalEntry
from accounting.services.accounts import create_account
from accounts.models import FiscalYear
from core.exceptions import ApplicationError
from core.tenancy import clear_tenant_context, tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit
from sales.models.invoice import InvoiceStatus
from sales.models.payment import CustomerPayment
from sales.selectors import get_invoice_amount_due, get_invoice_amount_paid, get_payment_unapplied_amount
from sales.services.customers import create_customer
from sales.services.invoices import create_invoice, post_invoice
from sales.services.payments import record_payment


class PaymentTestsBase(TestCase):
    def setUp(self):
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "payment-owner-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "payment-owner-b@example.com")
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
            self.unit = create_unit(organization=self.org_a, code="EA", name="Each")
            self.item = create_item(
                organization=self.org_a, item_type=ItemType.SERVICE, name="Consulting", unit=self.unit,
                sales_account=self.sales_account,
            )
            self.customer = create_customer(
                organization=self.org_a, customer_code="CUST-1", display_name="Acme", currency=self.currency
            )
        with tenant_context(organization_id=self.org_b.id):
            self.customer_b = create_customer(
                organization=self.org_b, customer_code="CUST-1", display_name="Beta", currency=self.currency
            )

    def _make_invoice(self, total=Decimal("100.00")):
        invoice = create_invoice(
            organization=self.org_a, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
            due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
            lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": total}],
        )
        return post_invoice(invoice_id=invoice.id, organization=self.org_a)


class RecordPaymentTests(PaymentTestsBase):
    def test_full_payment_marks_invoice_paid(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(Decimal("100.00"))
            record_payment(
                organization=self.org_a, customer=self.customer, payment_date=datetime.date(2026, 4, 15),
                amount=Decimal("100.00"), destination_account=self.bank_account,
                allocations=[{"invoice": invoice, "amount": Decimal("100.00")}],
            )
            invoice.refresh_from_db()
            self.assertEqual(invoice.status, InvoiceStatus.PAID)
            self.assertEqual(get_invoice_amount_paid(invoice=invoice), Decimal("100.00"))
            self.assertEqual(get_invoice_amount_due(invoice=invoice), Decimal("0.00"))

    def test_partial_payment_marks_invoice_partially_paid(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(Decimal("100.00"))
            record_payment(
                organization=self.org_a, customer=self.customer, payment_date=datetime.date(2026, 4, 15),
                amount=Decimal("40.00"), destination_account=self.bank_account,
                allocations=[{"invoice": invoice, "amount": Decimal("40.00")}],
            )
            invoice.refresh_from_db()
            self.assertEqual(invoice.status, InvoiceStatus.PARTIALLY_PAID)
            self.assertEqual(get_invoice_amount_due(invoice=invoice), Decimal("60.00"))

    def test_multiple_payments_fully_settle_invoice(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(Decimal("100.00"))
            record_payment(
                organization=self.org_a, customer=self.customer, payment_date=datetime.date(2026, 4, 15),
                amount=Decimal("40.00"), destination_account=self.bank_account,
                allocations=[{"invoice": invoice, "amount": Decimal("40.00")}],
            )
            invoice.refresh_from_db()
            record_payment(
                organization=self.org_a, customer=self.customer, payment_date=datetime.date(2026, 4, 20),
                amount=Decimal("60.00"), destination_account=self.bank_account,
                allocations=[{"invoice": invoice, "amount": Decimal("60.00")}],
            )
            invoice.refresh_from_db()
            self.assertEqual(invoice.status, InvoiceStatus.PAID)

    def test_one_payment_allocated_across_multiple_invoices(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice1 = self._make_invoice(Decimal("50.00"))
            invoice2 = self._make_invoice(Decimal("70.00"))
            record_payment(
                organization=self.org_a, customer=self.customer, payment_date=datetime.date(2026, 4, 15),
                amount=Decimal("120.00"), destination_account=self.bank_account,
                allocations=[
                    {"invoice": invoice1, "amount": Decimal("50.00")},
                    {"invoice": invoice2, "amount": Decimal("70.00")},
                ],
            )
            invoice1.refresh_from_db()
            invoice2.refresh_from_db()
            self.assertEqual(invoice1.status, InvoiceStatus.PAID)
            self.assertEqual(invoice2.status, InvoiceStatus.PAID)

    def test_ar_reduced_correctly_in_journal(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(Decimal("100.00"))
            payment = record_payment(
                organization=self.org_a, customer=self.customer, payment_date=datetime.date(2026, 4, 15),
                amount=Decimal("100.00"), destination_account=self.bank_account,
                allocations=[{"invoice": invoice, "amount": Decimal("100.00")}],
            )
            journal = JournalEntry.objects.get(pk=payment.accounting_journal_id)
            lines = list(journal.lines.all())
            total_debit = sum((jl.debit for jl in lines), Decimal("0"))
            total_credit = sum((jl.credit for jl in lines), Decimal("0"))
            self.assertEqual(total_debit, total_credit)
            bank_line = next(jl for jl in lines if jl.account_id == self.bank_account.id)
            ar_line = next(jl for jl in lines if jl.account_id == self.ar_account.id)
            self.assertEqual(bank_line.debit, Decimal("100.00"))
            self.assertEqual(ar_line.credit, Decimal("100.00"))

    def test_overpayment_creates_unapplied_credit(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(Decimal("100.00"))
            payment = record_payment(
                organization=self.org_a, customer=self.customer, payment_date=datetime.date(2026, 4, 15),
                amount=Decimal("150.00"), destination_account=self.bank_account,
                unapplied_credit_account=self.credit_account,
                allocations=[{"invoice": invoice, "amount": Decimal("100.00")}],
            )
            invoice.refresh_from_db()
            self.assertEqual(invoice.status, InvoiceStatus.PAID)
            self.assertEqual(get_payment_unapplied_amount(payment=payment), Decimal("50.00"))

    def test_overpayment_without_credit_account_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(Decimal("100.00"))
            with self.assertRaises(ApplicationError):
                record_payment(
                    organization=self.org_a, customer=self.customer, payment_date=datetime.date(2026, 4, 15),
                    amount=Decimal("150.00"), destination_account=self.bank_account,
                    allocations=[{"invoice": invoice, "amount": Decimal("100.00")}],
                )

    def test_over_allocation_prevented(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(Decimal("100.00"))
            with self.assertRaises(ApplicationError):
                record_payment(
                    organization=self.org_a, customer=self.customer, payment_date=datetime.date(2026, 4, 15),
                    amount=Decimal("100.00"), destination_account=self.bank_account,
                    allocations=[{"invoice": invoice, "amount": Decimal("150.00")}],
                )

    def test_allocation_sum_exceeding_payment_amount_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice1 = self._make_invoice(Decimal("100.00"))
            invoice2 = self._make_invoice(Decimal("100.00"))
            with self.assertRaises(ApplicationError):
                record_payment(
                    organization=self.org_a, customer=self.customer, payment_date=datetime.date(2026, 4, 15),
                    amount=Decimal("50.00"), destination_account=self.bank_account,
                    allocations=[
                        {"invoice": invoice1, "amount": Decimal("30.00")},
                        {"invoice": invoice2, "amount": Decimal("30.00")},
                    ],
                )

    def test_second_over_allocation_after_partial_payment_prevented(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(Decimal("100.00"))
            record_payment(
                organization=self.org_a, customer=self.customer, payment_date=datetime.date(2026, 4, 15),
                amount=Decimal("60.00"), destination_account=self.bank_account,
                allocations=[{"invoice": invoice, "amount": Decimal("60.00")}],
            )
            with self.assertRaises(ApplicationError):
                record_payment(
                    organization=self.org_a, customer=self.customer, payment_date=datetime.date(2026, 4, 20),
                    amount=Decimal("50.00"), destination_account=self.bank_account,
                    allocations=[{"invoice": invoice, "amount": Decimal("50.00")}],
                )

    def test_cannot_allocate_to_draft_invoice(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = create_invoice(
                organization=self.org_a, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("100.00")}],
            )
            with self.assertRaises(ApplicationError):
                record_payment(
                    organization=self.org_a, customer=self.customer, payment_date=datetime.date(2026, 4, 15),
                    amount=Decimal("100.00"), destination_account=self.bank_account,
                    allocations=[{"invoice": invoice, "amount": Decimal("100.00")}],
                )

    def test_cross_org_customer_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(Decimal("100.00"))
            with self.assertRaises(ApplicationError):
                record_payment(
                    organization=self.org_a, customer=self.customer_b, payment_date=datetime.date(2026, 4, 15),
                    amount=Decimal("100.00"), destination_account=self.bank_account,
                    allocations=[{"invoice": invoice, "amount": Decimal("100.00")}],
                )

    def test_payment_is_append_only(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(Decimal("100.00"))
            payment = record_payment(
                organization=self.org_a, customer=self.customer, payment_date=datetime.date(2026, 4, 15),
                amount=Decimal("100.00"), destination_account=self.bank_account,
                allocations=[{"invoice": invoice, "amount": Decimal("100.00")}],
            )
            payment.notes = "changed"
            with self.assertRaises(ValueError):
                payment.save()

    def test_tenant_isolation(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(Decimal("100.00"))
            record_payment(
                organization=self.org_a, customer=self.customer, payment_date=datetime.date(2026, 4, 15),
                amount=Decimal("100.00"), destination_account=self.bank_account,
                allocations=[{"invoice": invoice, "amount": Decimal("100.00")}],
            )
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(list(CustomerPayment.objects.all()), [])

    def test_no_tenant_context_fails_closed(self):
        with tenant_context(organization_id=self.org_a.id):
            invoice = self._make_invoice(Decimal("100.00"))
            record_payment(
                organization=self.org_a, customer=self.customer, payment_date=datetime.date(2026, 4, 15),
                amount=Decimal("100.00"), destination_account=self.bank_account,
                allocations=[{"invoice": invoice, "amount": Decimal("100.00")}],
            )
        clear_tenant_context()
        self.assertEqual(list(CustomerPayment.objects.all()), [])
