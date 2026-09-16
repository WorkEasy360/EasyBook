import datetime
from decimal import Decimal

from django.test import TestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from accounts.models import FiscalYear
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit
from reports.selectors.receivables import (
    ageing_bucket,
    get_ar_ageing,
    get_customer_balances,
    get_outstanding_invoices_report,
    get_overdue_invoices_report,
    get_sales_by_customer,
    get_sales_by_item,
)
from sales.services.customers import create_customer
from sales.services.invoices import create_invoice, post_invoice
from sales.services.payments import record_payment


class ReceivablesTestsBase(TestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Acme", "ar-owner@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            FiscalYear.objects.create(
                organization=self.org, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            FiscalYear.objects.create(
                organization=self.org, start_date=datetime.date(2025, 4, 1), end_date=datetime.date(2026, 3, 31)
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
            self.unit = create_unit(organization=self.org, code="EA", name="Each")
            self.item_a = create_item(
                organization=self.org, item_type=ItemType.SERVICE, name="Consulting", unit=self.unit,
                sales_account=self.sales_account,
            )
            self.item_b = create_item(
                organization=self.org, item_type=ItemType.SERVICE, name="Support", unit=self.unit,
                sales_account=self.sales_account,
            )
            self.customer_1 = create_customer(
                organization=self.org, customer_code="CUST-1", display_name="Alpha Co", currency=self.currency
            )
            self.customer_2 = create_customer(
                organization=self.org, customer_code="CUST-2", display_name="Beta Co", currency=self.currency
            )

    def _make_invoice(self, customer, item, unit_price, invoice_date, due_date):
        invoice = create_invoice(
            organization=self.org, customer=customer, invoice_date=invoice_date, due_date=due_date,
            receivable_account=self.ar_account,
            lines=[{"item": item, "quantity": Decimal("1"), "unit_price": unit_price}],
        )
        return post_invoice(invoice_id=invoice.id, organization=self.org)


class ARAgeingTests(ReceivablesTestsBase):
    def test_ageing_bucket_boundaries(self):
        self.assertEqual(ageing_bucket(0), "current")
        self.assertEqual(ageing_bucket(-5), "current")
        self.assertEqual(ageing_bucket(1), "1-30")
        self.assertEqual(ageing_bucket(30), "1-30")
        self.assertEqual(ageing_bucket(31), "31-60")
        self.assertEqual(ageing_bucket(60), "31-60")
        self.assertEqual(ageing_bucket(61), "61-90")
        self.assertEqual(ageing_bucket(90), "61-90")
        self.assertEqual(ageing_bucket(91), "90+")

    def test_ar_ageing_places_invoices_in_correct_buckets(self):
        with tenant_context(organization_id=self.org.id):
            as_of = datetime.date(2026, 7, 1)
            # Current: due in the future.
            self._make_invoice(self.customer_1, self.item_a, Decimal("100"), datetime.date(2026, 6, 20), datetime.date(2026, 7, 10))
            # 1-30 days overdue: due 2026-06-15, 16 days before as_of.
            self._make_invoice(self.customer_1, self.item_a, Decimal("200"), datetime.date(2026, 5, 15), datetime.date(2026, 6, 15))
            # 31-60 days overdue: due 2026-05-20, 42 days before as_of.
            self._make_invoice(self.customer_2, self.item_b, Decimal("300"), datetime.date(2026, 4, 20), datetime.date(2026, 5, 20))
            # 90+ days overdue: due 2026-01-01.
            self._make_invoice(self.customer_2, self.item_b, Decimal("400"), datetime.date(2025, 12, 1), datetime.date(2026, 1, 1))

            result = get_ar_ageing(organization=self.org, as_of=as_of)

        self.assertEqual(result["totals"]["current"], Decimal("100"))
        self.assertEqual(result["totals"]["1-30"], Decimal("200"))
        self.assertEqual(result["totals"]["31-60"], Decimal("300"))
        self.assertEqual(result["totals"]["61-90"], Decimal("0"))
        self.assertEqual(result["totals"]["90+"], Decimal("400"))
        self.assertEqual(result["grand_total"], Decimal("1000"))

    def test_partial_payment_reduces_ageing_amount(self):
        with tenant_context(organization_id=self.org.id):
            invoice = self._make_invoice(self.customer_1, self.item_a, Decimal("100"), datetime.date(2026, 4, 1), datetime.date(2026, 4, 10))
            record_payment(
                organization=self.org, customer=self.customer_1, payment_date=datetime.date(2026, 4, 12),
                amount=Decimal("40"), destination_account=self.bank_account,
                allocations=[{"invoice": invoice, "amount": Decimal("40")}],
            )
            result = get_ar_ageing(organization=self.org, as_of=datetime.date(2026, 4, 15))
        self.assertEqual(result["grand_total"], Decimal("60"))

    def test_credit_note_removes_invoice_from_ageing(self):
        from sales.services.credit_notes import create_credit_note, issue_credit_note

        with tenant_context(organization_id=self.org.id):
            invoice = self._make_invoice(self.customer_1, self.item_a, Decimal("100"), datetime.date(2026, 4, 1), datetime.date(2026, 4, 10))
            credit_note = create_credit_note(
                organization=self.org, customer=self.customer_1, credit_note_date=datetime.date(2026, 4, 12),
                source_invoice=invoice,
                lines=[{"item": self.item_a, "quantity": Decimal("1"), "unit_price": Decimal("100")}],
            )
            issue_credit_note(credit_note_id=credit_note.id, organization=self.org)
            result = get_ar_ageing(organization=self.org, as_of=datetime.date(2026, 4, 15))
        self.assertEqual(result["grand_total"], Decimal("0"))


class CustomerBalancesAndInvoiceReportsTests(ReceivablesTestsBase):
    def test_customer_balances_grouped_correctly(self):
        with tenant_context(organization_id=self.org.id):
            self._make_invoice(self.customer_1, self.item_a, Decimal("100"), datetime.date(2026, 4, 1), datetime.date(2026, 5, 1))
            self._make_invoice(self.customer_1, self.item_a, Decimal("50"), datetime.date(2026, 4, 5), datetime.date(2026, 5, 5))
            self._make_invoice(self.customer_2, self.item_b, Decimal("200"), datetime.date(2026, 4, 10), datetime.date(2026, 5, 10))
            balances = get_customer_balances(organization=self.org)
        by_customer = {row["customer_id"]: row["balance"] for row in balances}
        self.assertEqual(by_customer[self.customer_1.id], Decimal("150"))
        self.assertEqual(by_customer[self.customer_2.id], Decimal("200"))

    def test_outstanding_and_overdue_invoices(self):
        with tenant_context(organization_id=self.org.id):
            self._make_invoice(self.customer_1, self.item_a, Decimal("100"), datetime.date(2026, 4, 1), datetime.date(2026, 4, 5))
            self._make_invoice(self.customer_2, self.item_b, Decimal("200"), datetime.date(2026, 4, 1), datetime.date(2099, 1, 1))
            outstanding = get_outstanding_invoices_report(organization=self.org)
            overdue = get_overdue_invoices_report(organization=self.org, as_of=datetime.date(2026, 4, 20))
        self.assertEqual(len(outstanding), 2)
        self.assertEqual(len(overdue), 1)
        self.assertEqual(overdue[0]["customer_id"], self.customer_1.id)


class SalesByCustomerAndItemTests(ReceivablesTestsBase):
    def test_sales_by_customer(self):
        with tenant_context(organization_id=self.org.id):
            self._make_invoice(self.customer_1, self.item_a, Decimal("100"), datetime.date(2026, 4, 5), datetime.date(2026, 5, 5))
            self._make_invoice(self.customer_1, self.item_a, Decimal("50"), datetime.date(2026, 4, 6), datetime.date(2026, 5, 6))
            self._make_invoice(self.customer_2, self.item_b, Decimal("200"), datetime.date(2026, 4, 7), datetime.date(2026, 5, 7))
            rows = get_sales_by_customer(
                organization=self.org, from_date=datetime.date(2026, 4, 1), to_date=datetime.date(2026, 4, 30)
            )
        by_customer = {row["customer_id"]: row for row in rows}
        self.assertEqual(by_customer[self.customer_1.id]["total_sales"], Decimal("150"))
        self.assertEqual(by_customer[self.customer_1.id]["invoice_count"], 2)
        self.assertEqual(by_customer[self.customer_2.id]["total_sales"], Decimal("200"))

    def test_sales_by_item(self):
        with tenant_context(organization_id=self.org.id):
            self._make_invoice(self.customer_1, self.item_a, Decimal("100"), datetime.date(2026, 4, 5), datetime.date(2026, 5, 5))
            self._make_invoice(self.customer_2, self.item_a, Decimal("50"), datetime.date(2026, 4, 6), datetime.date(2026, 5, 6))
            self._make_invoice(self.customer_2, self.item_b, Decimal("200"), datetime.date(2026, 4, 7), datetime.date(2026, 5, 7))
            rows = get_sales_by_item(
                organization=self.org, from_date=datetime.date(2026, 4, 1), to_date=datetime.date(2026, 4, 30)
            )
        by_item = {row["item_id"]: row for row in rows}
        self.assertEqual(by_item[self.item_a.id]["total_sales"], Decimal("150"))
        self.assertEqual(by_item[self.item_a.id]["quantity"], Decimal("2"))
        self.assertEqual(by_item[self.item_b.id]["total_sales"], Decimal("200"))

    def test_sales_reports_exclude_draft_and_out_of_range(self):
        with tenant_context(organization_id=self.org.id):
            create_invoice(
                organization=self.org, customer=self.customer_1, invoice_date=datetime.date(2026, 4, 1),
                due_date=datetime.date(2026, 5, 1), receivable_account=self.ar_account,
                lines=[{"item": self.item_a, "quantity": Decimal("1"), "unit_price": Decimal("999")}],
            )  # left DRAFT
            self._make_invoice(self.customer_1, self.item_a, Decimal("100"), datetime.date(2026, 3, 1), datetime.date(2026, 4, 1))
            rows = get_sales_by_customer(
                organization=self.org, from_date=datetime.date(2026, 4, 1), to_date=datetime.date(2026, 4, 30)
            )
        self.assertEqual(rows, [])

    def test_receivables_are_tenant_scoped(self):
        other_org, _other_user, _ = make_org_with_owner("Other Org", "ar-other@example.com")
        with tenant_context(organization_id=other_org.id):
            FiscalYear.objects.create(
                organization=other_org, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            ar_account = create_account(
                organization=other_org, code="1100", name="AR", account_type=AccountType.ASSET
            )
            sales_account = create_account(
                organization=other_org, code="4000", name="Sales", account_type=AccountType.INCOME
            )
            unit = create_unit(organization=other_org, code="EA", name="Each")
            item = create_item(
                organization=other_org, item_type=ItemType.SERVICE, name="Other", unit=unit,
                sales_account=sales_account,
            )
            customer = create_customer(
                organization=other_org, customer_code="CUST-1", display_name="Other Co", currency=self.currency
            )
            invoice = create_invoice(
                organization=other_org, customer=customer, invoice_date=datetime.date(2026, 4, 1),
                due_date=datetime.date(2026, 5, 1), receivable_account=ar_account,
                lines=[{"item": item, "quantity": Decimal("1"), "unit_price": Decimal("999")}],
            )
            post_invoice(invoice_id=invoice.id, organization=other_org)
            other_balances = get_customer_balances(organization=other_org)
        self.assertEqual(len(other_balances), 1)
        self.assertEqual(other_balances[0]["balance"], Decimal("999"))

        with tenant_context(organization_id=self.org.id):
            self._make_invoice(self.customer_1, self.item_a, Decimal("100"), datetime.date(2026, 4, 1), datetime.date(2026, 5, 1))
            own_balances = get_customer_balances(organization=self.org)
        self.assertEqual(len(own_balances), 1)
        self.assertEqual(own_balances[0]["balance"], Decimal("100"))
