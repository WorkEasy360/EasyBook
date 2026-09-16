from decimal import Decimal

from django.db.utils import IntegrityError

from accounting.models.journal import JournalEntry
from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from inventory.models.stock_movement import StockMovement
from purchases.models.expense import Expense, ExpenseStatus
from purchases.services.expenses import create_expense, post_expense, update_expense, void_expense
from purchases.tests.base import ORDER_DATE, PurchasesTestsBase


class ExpenseCreationTests(PurchasesTestsBase):
    def _expense(self, **kwargs):
        defaults = {
            "organization": self.org_a,
            "expense_date": ORDER_DATE,
            "amount": Decimal("100.00"),
            "expense_account": self.office_expense_account,
            "paid_through_account": self.bank_account,
            "currency": self.currency,
        }
        return create_expense(**{**defaults, **kwargs})

    def test_create_computes_tax_and_total_server_side(self):
        with tenant_context(organization_id=self.org_a.id):
            expense = self._expense(tax_rate=Decimal("18"), tax_recoverable_account=self.input_tax_account)
        self.assertEqual(expense.status, ExpenseStatus.DRAFT)
        self.assertEqual(expense.amount, Decimal("100.00"))
        self.assertEqual(expense.tax_amount, Decimal("18.00"))
        self.assertEqual(expense.total, Decimal("118.00"))

    def test_expense_account_must_be_an_expense(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                self._expense(expense_account=self.bank_account)
        self.assertEqual(ctx.exception.detail.code, "invalid_account_type")

    def test_paid_through_may_be_a_bank_asset_or_an_ap_liability(self):
        with tenant_context(organization_id=self.org_a.id):
            paid_now = self._expense(paid_through_account=self.bank_account)
            payable_later = self._expense(paid_through_account=self.ap_account)
        self.assertEqual(paid_now.paid_through_account_id, self.bank_account.id)
        self.assertEqual(payable_later.paid_through_account_id, self.ap_account.id)

    def test_paid_through_rejects_an_income_or_expense_account(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                self._expense(paid_through_account=self.purchase_expense_account)
        self.assertEqual(ctx.exception.detail.code, "invalid_account_type")

    def test_negative_amount_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                self._expense(amount=Decimal("-1.00"))
        self.assertEqual(ctx.exception.detail.code, "expense_amount_invalid")

    def test_billable_expense_requires_a_customer(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                self._expense(is_billable=True)
        self.assertEqual(ctx.exception.detail.code, "billable_customer_required")

    def test_billable_without_customer_also_blocked_at_the_database(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(IntegrityError):
                Expense.objects.create(
                    organization=self.org_a, expense_date=ORDER_DATE, currency=self.currency,
                    expense_account=self.office_expense_account, paid_through_account=self.bank_account,
                    amount=Decimal("10.00"), is_billable=True, customer=None,
                )

    def test_billable_expense_with_customer_is_accepted(self):
        from sales.services.customers import create_customer

        with tenant_context(organization_id=self.org_a.id):
            customer = create_customer(
                organization=self.org_a, customer_code="CUST-1", display_name="Acme", currency=self.currency
            )
            expense = self._expense(is_billable=True, customer=customer)
        self.assertTrue(expense.is_billable)
        self.assertEqual(expense.customer_id, customer.id)

    def test_update_recomputes_total_and_ignores_client_supplied_total(self):
        with tenant_context(organization_id=self.org_a.id):
            expense = self._expense(tax_rate=Decimal("10"), tax_recoverable_account=self.input_tax_account)
            update_expense(expense=expense, amount=Decimal("200.00"), total=Decimal("1.00"))
            expense.refresh_from_db()
        self.assertEqual(expense.amount, Decimal("200.00"))
        self.assertEqual(expense.tax_amount, Decimal("20.00"))
        self.assertEqual(expense.total, Decimal("220.00"))


class ExpensePostingTests(PurchasesTestsBase):
    def _draft(self, **kwargs):
        defaults = {
            "organization": self.org_a,
            "expense_date": ORDER_DATE,
            "amount": Decimal("100.00"),
            "expense_account": self.office_expense_account,
            "paid_through_account": self.bank_account,
            "currency": self.currency,
        }
        return create_expense(**{**defaults, **kwargs})

    def test_post_creates_balanced_journal_and_no_stock(self):
        with tenant_context(organization_id=self.org_a.id):
            expense = self._draft()
            expense = post_expense(expense_id=expense.id, organization=self.org_a)
            journal = expense.accounting_journal

            self.assertTrue(expense.expense_number.startswith("EXP-"))
            self.assert_journal_balanced(journal)
            self.assertEqual(self.account_movement(journal, self.office_expense_account), Decimal("100.00"))
            self.assertEqual(self.account_movement(journal, self.bank_account), Decimal("-100.00"))
            self.assertEqual(StockMovement.objects.count(), 0)

    def test_post_with_tax_splits_recoverable_tax_out_of_the_expense(self):
        with tenant_context(organization_id=self.org_a.id):
            expense = self._draft(tax_rate=Decimal("18"), tax_recoverable_account=self.input_tax_account)
            expense = post_expense(expense_id=expense.id, organization=self.org_a)
            journal = expense.accounting_journal

            self.assert_journal_balanced(journal)
            self.assertEqual(self.account_movement(journal, self.office_expense_account), Decimal("100.00"))
            self.assertEqual(self.account_movement(journal, self.input_tax_account), Decimal("18.00"))
            self.assertEqual(self.account_movement(journal, self.bank_account), Decimal("-118.00"))

    def test_post_is_idempotent(self):
        with tenant_context(organization_id=self.org_a.id):
            expense = self._draft()
            first = post_expense(expense_id=expense.id, organization=self.org_a)
            second = post_expense(expense_id=expense.id, organization=self.org_a)
            self.assertEqual(first.expense_number, second.expense_number)
            self.assertEqual(JournalEntry.objects.count(), 1)

    def test_tax_without_recoverable_account_refused_at_post(self):
        with tenant_context(organization_id=self.org_a.id):
            expense = self._draft(tax_rate=Decimal("18"))
            with self.assertRaises(ApplicationError) as ctx:
                post_expense(expense_id=expense.id, organization=self.org_a)
        self.assertEqual(ctx.exception.detail.code, "tax_account_required")

    def test_posted_expense_is_immutable(self):
        with tenant_context(organization_id=self.org_a.id):
            expense = self._draft()
            post_expense(expense_id=expense.id, organization=self.org_a)
            expense.refresh_from_db()
            expense.notes = "tampered"
            with self.assertRaises(ValueError):
                expense.save()
            with self.assertRaises(ApplicationError) as ctx:
                update_expense(expense=expense, amount=Decimal("5"))
        self.assertEqual(ctx.exception.detail.code, "expense_not_draft")

    def test_void_reverses_the_journal(self):
        with tenant_context(organization_id=self.org_a.id):
            expense = self._draft()
            post_expense(expense_id=expense.id, organization=self.org_a)
            voided = void_expense(expense_id=expense.id, organization=self.org_a, reason="duplicate")
            self.assertEqual(voided.status, ExpenseStatus.VOID)
            self.assertEqual(JournalEntry.objects.count(), 2)

    def test_void_is_idempotent(self):
        with tenant_context(organization_id=self.org_a.id):
            expense = self._draft()
            post_expense(expense_id=expense.id, organization=self.org_a)
            void_expense(expense_id=expense.id, organization=self.org_a)
            void_expense(expense_id=expense.id, organization=self.org_a)
            self.assertEqual(JournalEntry.objects.count(), 2)

    def test_cannot_void_a_draft(self):
        with tenant_context(organization_id=self.org_a.id):
            expense = self._draft()
            with self.assertRaises(ApplicationError) as ctx:
                void_expense(expense_id=expense.id, organization=self.org_a)
        self.assertEqual(ctx.exception.detail.code, "expense_invalid_status")


class ExpenseTenantIsolationTests(PurchasesTestsBase):
    def test_other_org_cannot_see_or_post_expense(self):
        with tenant_context(organization_id=self.org_a.id):
            expense = create_expense(
                organization=self.org_a, expense_date=ORDER_DATE, amount=Decimal("10.00"),
                expense_account=self.office_expense_account, paid_through_account=self.bank_account,
                currency=self.currency,
            )
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(Expense.objects.count(), 0)
            with self.assertRaises(ApplicationError) as ctx:
                post_expense(expense_id=expense.id, organization=self.org_b)
        self.assertEqual(ctx.exception.detail.code, "expense_not_found")
