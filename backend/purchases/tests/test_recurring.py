"""Recurring bill/expense generation.

TEST GOTCHA (inherited from sales/tests/test_recurring_invoices.py): any test
calling `generate_due_bills`/`generate_due_expenses` must use
`TransactionTestCase`, not `TestCase`. Under TestCase's outer wrapping
transaction each `tenant_context()` becomes a SAVEPOINT, and Postgres only
reverts a `SET LOCAL` GUC on ROLLBACK, never on a savepoint RELEASE — so the
last organization the sweep touches leaks its GUC into whatever runs next.
TransactionTestCase gives real transaction boundaries, matching production
Celery execution.
"""

import datetime
from decimal import Decimal

from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from purchases.models.bill import Bill, BillStatus
from purchases.models.expense import Expense, ExpenseStatus
from purchases.models.recurring import RecurringBillRun, RecurringExpenseRun
from purchases.services.recurring import (
    create_recurring_bill_template,
    create_recurring_expense_template,
    deactivate_bill_template,
    generate_due_bills,
    generate_due_expenses,
)
from purchases.tests.base import ORDER_DATE, PurchasesTestsBase, PurchasesTransactionTestsBase


class RecurringTemplateValidationTests(PurchasesTestsBase):
    def test_inventoried_item_rejected_on_a_recurring_bill_template(self):
        """A DRAFT bill generated on a schedule has no goods receipt behind
        it, so an inventoried line would inflate stock every period."""
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_recurring_bill_template(
                    organization=self.org_a, vendor=self.vendor, frequency="monthly",
                    start_date=ORDER_DATE, payable_account=self.ap_account,
                    lines=self._product_lines(),
                )
        self.assertEqual(ctx.exception.detail.code, "recurring_item_inventoried")

    def test_service_item_accepted(self):
        with tenant_context(organization_id=self.org_a.id):
            template = create_recurring_bill_template(
                organization=self.org_a, vendor=self.vendor, frequency="monthly",
                start_date=ORDER_DATE, payable_account=self.ap_account, lines=self._service_lines(),
            )
        self.assertEqual(template.next_run_at, ORDER_DATE)
        self.assertTrue(template.is_active)

    def test_unknown_frequency_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_recurring_bill_template(
                    organization=self.org_a, vendor=self.vendor, frequency="fortnightly",
                    start_date=ORDER_DATE, payable_account=self.ap_account, lines=self._service_lines(),
                )
        self.assertEqual(ctx.exception.detail.code, "recurring_frequency_invalid")

    def test_end_date_before_start_date_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_recurring_bill_template(
                    organization=self.org_a, vendor=self.vendor, frequency="monthly",
                    start_date=ORDER_DATE, end_date=ORDER_DATE - datetime.timedelta(days=1),
                    payable_account=self.ap_account, lines=self._service_lines(),
                )
        self.assertEqual(ctx.exception.detail.code, "recurring_end_before_start")


class RecurringBillGenerationTests(PurchasesTransactionTestsBase):
    def _template(self, **kwargs):
        defaults = {
            "organization": self.org_a, "vendor": self.vendor, "frequency": "monthly",
            "start_date": ORDER_DATE, "payable_account": self.ap_account,
            "lines": self._service_lines(quantity=Decimal("1"), unit_price=Decimal("100.00")),
            "due_days": 15,
        }
        return create_recurring_bill_template(**{**defaults, **kwargs})

    def test_generates_a_draft_bill_never_a_posted_one(self):
        with tenant_context(organization_id=self.org_a.id):
            self._template()
        bills = generate_due_bills(as_of=ORDER_DATE)
        self.assertEqual(len(bills), 1)
        self.assertEqual(bills[0].status, BillStatus.DRAFT)
        self.assertEqual(bills[0].bill_number, "")
        self.assertIsNone(bills[0].accounting_journal)

    def test_due_date_derives_from_due_days(self):
        with tenant_context(organization_id=self.org_a.id):
            self._template(due_days=15)
        bill = generate_due_bills(as_of=ORDER_DATE)[0]
        self.assertEqual(bill.due_date, ORDER_DATE + datetime.timedelta(days=15))

    def test_same_occurrence_never_generates_twice(self):
        with tenant_context(organization_id=self.org_a.id):
            self._template()
        self.assertEqual(len(generate_due_bills(as_of=ORDER_DATE)), 1)
        self.assertEqual(len(generate_due_bills(as_of=ORDER_DATE)), 0)
        with tenant_context(organization_id=self.org_a.id):
            self.assertEqual(Bill.objects.count(), 1)
            self.assertEqual(RecurringBillRun.objects.count(), 1)

    def test_catches_up_on_missed_occurrences(self):
        with tenant_context(organization_id=self.org_a.id):
            self._template()
        # Three months later: April, May and June occurrences are all due.
        bills = generate_due_bills(as_of=ORDER_DATE + datetime.timedelta(days=70))
        self.assertEqual(len(bills), 3)
        with tenant_context(organization_id=self.org_a.id):
            self.assertEqual(
                sorted(RecurringBillRun.objects.values_list("occurrence_date", flat=True)),
                [datetime.date(2026, 4, 10), datetime.date(2026, 5, 10), datetime.date(2026, 6, 10)],
            )

    def test_stops_at_end_date(self):
        with tenant_context(organization_id=self.org_a.id):
            self._template(end_date=ORDER_DATE + datetime.timedelta(days=40))
        bills = generate_due_bills(as_of=ORDER_DATE + datetime.timedelta(days=200))
        self.assertEqual(len(bills), 2)  # April and May only

    def test_inactive_template_generates_nothing(self):
        with tenant_context(organization_id=self.org_a.id):
            template = self._template()
            deactivate_bill_template(template=template)
        self.assertEqual(len(generate_due_bills(as_of=ORDER_DATE)), 0)

    def test_generated_bills_carry_no_vendor_bill_number(self):
        """The vendor's own document number is on paper we have not received
        — inventing one would trip the duplicate-bill constraint next period."""
        with tenant_context(organization_id=self.org_a.id):
            self._template()
        bills = generate_due_bills(as_of=ORDER_DATE + datetime.timedelta(days=40))
        self.assertEqual(len(bills), 2)
        self.assertTrue(all(bill.vendor_bill_number == "" for bill in bills))

    def test_each_organization_is_swept_in_its_own_tenant_context(self):
        with tenant_context(organization_id=self.org_a.id):
            self._template()
        with tenant_context(organization_id=self.org_b.id):
            from accounting.models.account import AccountType
            from accounting.services.accounts import create_account
            from accounts.models import FiscalYear

            FiscalYear.objects.create(
                organization=self.org_b, start_date=datetime.date(2026, 4, 1),
                end_date=datetime.date(2027, 3, 31),
            )
            ap_b = create_account(
                organization=self.org_b, code="2000", name="AP", account_type=AccountType.LIABILITY
            )
            expense_b = create_account(
                organization=self.org_b, code="5100", name="Purchases", account_type=AccountType.EXPENSE
            )
            from items.services.items import update_item

            update_item(item=self.item_b, purchase_account=expense_b)
            create_recurring_bill_template(
                organization=self.org_b, vendor=self.vendor_b, frequency="monthly",
                start_date=ORDER_DATE, payable_account=ap_b,
                lines=[{"item": self.item_b, "quantity": Decimal("1"), "unit_price": Decimal("10.00")}],
            )

        bills = generate_due_bills(as_of=ORDER_DATE)
        self.assertEqual(len(bills), 2)
        self.assertEqual({bill.organization_id for bill in bills}, {self.org_a.id, self.org_b.id})

    def test_runs_are_append_only(self):
        with tenant_context(organization_id=self.org_a.id):
            self._template()
        generate_due_bills(as_of=ORDER_DATE)
        with tenant_context(organization_id=self.org_a.id):
            run = RecurringBillRun.objects.get()
            with self.assertRaises(ValueError):
                run.save()
            with self.assertRaises(ValueError):
                run.delete()


class RecurringExpenseGenerationTests(PurchasesTransactionTestsBase):
    def _template(self, **kwargs):
        defaults = {
            "organization": self.org_a, "frequency": "monthly", "start_date": ORDER_DATE,
            "amount": Decimal("500.00"), "expense_account": self.office_expense_account,
            "paid_through_account": self.bank_account, "currency": self.currency,
            "description": "Office rent",
        }
        return create_recurring_expense_template(**{**defaults, **kwargs})

    def test_generates_a_draft_expense_never_a_posted_one(self):
        with tenant_context(organization_id=self.org_a.id):
            self._template()
        expenses = generate_due_expenses(as_of=ORDER_DATE)
        self.assertEqual(len(expenses), 1)
        self.assertEqual(expenses[0].status, ExpenseStatus.DRAFT)
        self.assertEqual(expenses[0].amount, Decimal("500.00"))
        self.assertIsNone(expenses[0].accounting_journal)

    def test_same_occurrence_never_generates_twice(self):
        with tenant_context(organization_id=self.org_a.id):
            self._template()
        self.assertEqual(len(generate_due_expenses(as_of=ORDER_DATE)), 1)
        self.assertEqual(len(generate_due_expenses(as_of=ORDER_DATE)), 0)
        with tenant_context(organization_id=self.org_a.id):
            self.assertEqual(Expense.objects.count(), 1)
            self.assertEqual(RecurringExpenseRun.objects.count(), 1)

    def test_catches_up_and_computes_tax_per_occurrence(self):
        with tenant_context(organization_id=self.org_a.id):
            self._template(tax_rate=Decimal("18"), tax_recoverable_account=self.input_tax_account)
        expenses = generate_due_expenses(as_of=ORDER_DATE + datetime.timedelta(days=40))
        self.assertEqual(len(expenses), 2)
        for expense in expenses:
            self.assertEqual(expense.tax_amount, Decimal("90.00"))
            self.assertEqual(expense.total, Decimal("590.00"))

    def test_quarterly_frequency_advances_three_months(self):
        with tenant_context(organization_id=self.org_a.id):
            self._template(frequency="quarterly")
        generate_due_expenses(as_of=ORDER_DATE)
        with tenant_context(organization_id=self.org_a.id):
            from purchases.models.recurring import RecurringExpenseTemplate

            self.assertEqual(
                RecurringExpenseTemplate.objects.get().next_run_at, datetime.date(2026, 7, 10)
            )
