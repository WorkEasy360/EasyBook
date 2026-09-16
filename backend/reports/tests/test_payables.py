import datetime
from decimal import Decimal

from django.test import TestCase

from core.tenancy import tenant_context
from purchases.services.bills import create_bill, post_bill
from purchases.services.payments import record_vendor_payment
from purchases.tests.base import PurchasesFixtureMixin
from reports.selectors.payables import (
    get_ap_ageing,
    get_expenses_by_category,
    get_outstanding_bills_report,
    get_overdue_bills_report,
    get_purchases_by_item,
    get_purchases_by_vendor,
    get_vendor_balances,
)


class PayablesTestsBase(PurchasesFixtureMixin, TestCase):
    def _make_bill(self, unit_price, bill_date, due_date, item=None):
        item = item or self.service
        bill = create_bill(
            organization=self.org_a, vendor=self.vendor, bill_date=bill_date, due_date=due_date,
            payable_account=self.ap_account,
            lines=[{"item": item, "quantity": Decimal("1"), "unit_price": unit_price}],
        )
        return post_bill(bill_id=bill.id, organization=self.org_a)


class APAgeingTests(PayablesTestsBase):
    def test_ap_ageing_places_bills_in_correct_buckets(self):
        with tenant_context(organization_id=self.org_a.id):
            as_of = datetime.date(2026, 7, 1)
            self._make_bill(Decimal("100"), datetime.date(2026, 6, 20), datetime.date(2026, 7, 10))
            self._make_bill(Decimal("200"), datetime.date(2026, 5, 15), datetime.date(2026, 6, 15))
            self._make_bill(Decimal("300"), datetime.date(2026, 4, 20), datetime.date(2026, 5, 20))

            result = get_ap_ageing(organization=self.org_a, as_of=as_of)
        self.assertEqual(result["totals"]["current"], Decimal("100"))
        self.assertEqual(result["totals"]["1-30"], Decimal("200"))
        self.assertEqual(result["totals"]["31-60"], Decimal("300"))
        self.assertEqual(result["grand_total"], Decimal("600"))

    def test_partial_payment_reduces_ap_ageing_amount(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = self._make_bill(Decimal("100"), datetime.date(2026, 4, 1), datetime.date(2026, 4, 10))
            record_vendor_payment(
                organization=self.org_a, vendor=self.vendor, payment_date=datetime.date(2026, 4, 12),
                amount=Decimal("40"), source_account=self.bank_account,
                allocations=[{"bill": bill, "amount": Decimal("40")}],
            )
            result = get_ap_ageing(organization=self.org_a, as_of=datetime.date(2026, 4, 15))
        self.assertEqual(result["grand_total"], Decimal("60"))


class VendorBalancesAndBillReportsTests(PayablesTestsBase):
    def test_vendor_balances_grouped_correctly(self):
        with tenant_context(organization_id=self.org_a.id):
            self._make_bill(Decimal("100"), datetime.date(2026, 4, 1), datetime.date(2026, 5, 1))
            self._make_bill(Decimal("50"), datetime.date(2026, 4, 5), datetime.date(2026, 5, 5))
            balances = get_vendor_balances(organization=self.org_a)
        self.assertEqual(len(balances), 1)
        self.assertEqual(balances[0]["balance"], Decimal("150"))

    def test_outstanding_and_overdue_bills(self):
        with tenant_context(organization_id=self.org_a.id):
            self._make_bill(Decimal("100"), datetime.date(2026, 4, 1), datetime.date(2026, 4, 5))
            self._make_bill(Decimal("200"), datetime.date(2026, 4, 1), datetime.date(2099, 1, 1))
            outstanding = get_outstanding_bills_report(organization=self.org_a)
            overdue = get_overdue_bills_report(organization=self.org_a, as_of=datetime.date(2026, 4, 20))
        self.assertEqual(len(outstanding), 2)
        self.assertEqual(len(overdue), 1)


class PurchasesByVendorItemAndExpenseCategoryTests(PayablesTestsBase):
    def test_purchases_by_vendor(self):
        with tenant_context(organization_id=self.org_a.id):
            self._make_bill(Decimal("100"), datetime.date(2026, 4, 5), datetime.date(2026, 5, 5))
            self._make_bill(Decimal("50"), datetime.date(2026, 4, 6), datetime.date(2026, 5, 6))
            rows = get_purchases_by_vendor(
                organization=self.org_a, from_date=datetime.date(2026, 4, 1), to_date=datetime.date(2026, 4, 30)
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["total_purchases"], Decimal("150"))
        self.assertEqual(rows[0]["bill_count"], 2)

    def test_purchases_by_item(self):
        with tenant_context(organization_id=self.org_a.id):
            self._make_bill(Decimal("100"), datetime.date(2026, 4, 5), datetime.date(2026, 5, 5), item=self.service)
            self._make_bill(Decimal("200"), datetime.date(2026, 4, 6), datetime.date(2026, 5, 6), item=self.untracked_product)
            rows = get_purchases_by_item(
                organization=self.org_a, from_date=datetime.date(2026, 4, 1), to_date=datetime.date(2026, 4, 30)
            )
        by_item = {row["item_id"]: row for row in rows}
        self.assertEqual(by_item[self.service.id]["total_purchases"], Decimal("100"))
        self.assertEqual(by_item[self.untracked_product.id]["total_purchases"], Decimal("200"))

    def test_expenses_by_category(self):
        from purchases.services.expenses import create_expense, post_expense

        with tenant_context(organization_id=self.org_a.id):
            expense1 = create_expense(
                organization=self.org_a, expense_date=datetime.date(2026, 4, 5), amount=Decimal("75"),
                expense_account=self.office_expense_account, paid_through_account=self.bank_account,
            )
            post_expense(expense_id=expense1.id, organization=self.org_a)
            expense2 = create_expense(
                organization=self.org_a, expense_date=datetime.date(2026, 4, 6), amount=Decimal("25"),
                expense_account=self.office_expense_account, paid_through_account=self.bank_account,
            )
            post_expense(expense_id=expense2.id, organization=self.org_a)
            rows = get_expenses_by_category(
                organization=self.org_a, from_date=datetime.date(2026, 4, 1), to_date=datetime.date(2026, 4, 30)
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["account_id"], self.office_expense_account.id)
        self.assertEqual(rows[0]["total_amount"], Decimal("100"))
        self.assertEqual(rows[0]["expense_count"], 2)

    def test_payables_are_tenant_scoped(self):
        with tenant_context(organization_id=self.org_a.id):
            self._make_bill(Decimal("100"), datetime.date(2026, 4, 1), datetime.date(2026, 5, 1))
            own = get_vendor_balances(organization=self.org_a)
        self.assertEqual(own[0]["balance"], Decimal("100"))

        with tenant_context(organization_id=self.org_b.id):
            other = get_vendor_balances(organization=self.org_b)
        self.assertEqual(other, [])
