"""Shared fixture for banking tests.

One organization with a bank account and a credit card, a customer and a
vendor so real documents exist to match against, and a second organization
for cross-tenant assertions.
"""

import datetime
from decimal import Decimal

from django.test import TestCase, TransactionTestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from accounts.models import FiscalYear, Membership
from authz.roles import Role
from banking.services.bank_accounts import create_bank_account
from banking.services.parsers import AmountMode, CsvColumnMapping
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner, make_user
from purchases.services.vendors import create_vendor
from sales.services.customers import create_customer

PERIOD_START = datetime.date(2026, 4, 1)
DAY = datetime.date(2026, 4, 10)
PERIOD_END = datetime.date(2026, 4, 30)

SIGNED_MAPPING = CsvColumnMapping(
    date_column="Date",
    amount_mode=AmountMode.SIGNED,
    amount_column="Amount",
    description_column="Narration",
    reference_column="Ref",
)

DEBIT_CREDIT_MAPPING = CsvColumnMapping(
    date_column="Txn Date",
    amount_mode=AmountMode.DEBIT_CREDIT,
    debit_column="Withdrawal Amt.",
    credit_column="Deposit Amt.",
    description_column="Narration",
    reference_column="Chq No",
)


class BankingFixtureMixin:
    """Split from the Django base class for the same reason
    `purchases.tests.base` is: `TestCase` subclasses `TransactionTestCase`, so
    mixing them directly silently yields `TestCase` semantics."""

    def setUp(self):
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "bank-owner-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "bank-owner-b@example.com")
        self.currency = make_currency("INR")

        self.accountant_user = make_user("bank-accountant@example.com")
        with tenant_context(user_id=self.accountant_user.id):
            Membership.objects.create(
                organization=self.org_a, user=self.accountant_user, role=Role.ACCOUNTANT
            )
        self.staff_user = make_user("bank-staff@example.com")
        with tenant_context(user_id=self.staff_user.id):
            Membership.objects.create(organization=self.org_a, user=self.staff_user, role=Role.STAFF)

        with tenant_context(organization_id=self.org_a.id):
            FiscalYear.objects.create(
                organization=self.org_a, start_date=PERIOD_START, end_date=datetime.date(2027, 3, 31)
            )
            self.gl_bank = create_account(
                organization=self.org_a, code="1000", name="Bank", account_type=AccountType.ASSET
            )
            self.gl_bank_two = create_account(
                organization=self.org_a, code="1001", name="Savings", account_type=AccountType.ASSET
            )
            self.gl_card = create_account(
                organization=self.org_a, code="2200", name="Credit Card", account_type=AccountType.LIABILITY
            )
            self.gl_ar = create_account(
                organization=self.org_a, code="1100", name="Accounts Receivable", account_type=AccountType.ASSET
            )
            self.gl_ap = create_account(
                organization=self.org_a, code="2000", name="Accounts Payable", account_type=AccountType.LIABILITY
            )
            self.gl_unapplied = create_account(
                organization=self.org_a, code="2300", name="Customer Advances",
                account_type=AccountType.LIABILITY,
            )
            self.gl_vendor_advance = create_account(
                organization=self.org_a, code="1400", name="Vendor Advances", account_type=AccountType.ASSET
            )
            self.gl_revenue = create_account(
                organization=self.org_a, code="4000", name="Sales", account_type=AccountType.INCOME
            )
            self.gl_charges = create_account(
                organization=self.org_a, code="5100", name="Bank Charges", account_type=AccountType.EXPENSE
            )
            self.gl_office = create_account(
                organization=self.org_a, code="5200", name="Office Expenses", account_type=AccountType.EXPENSE
            )

            self.bank = create_bank_account(
                organization=self.org_a, name="HDFC Current", account=self.gl_bank,
                currency=self.currency, bank_name="HDFC Bank", account_number="1234567890123456",
                branch_identifier="HDFC0001234",
            )
            self.savings = create_bank_account(
                organization=self.org_a, name="HDFC Savings", account=self.gl_bank_two,
                currency=self.currency,
            )
            self.card = create_bank_account(
                organization=self.org_a, name="Amex Corporate", account=self.gl_card,
                currency=self.currency, kind="credit_card", account_number="4111111111111111",
            )

            self.customer = create_customer(
                organization=self.org_a, customer_code="CUST-1", display_name="Acme Ltd",
                currency=self.currency,
            )
            self.vendor = create_vendor(
                organization=self.org_a, vendor_code="VEN-1", display_name="Beta Supplies",
                currency=self.currency,
            )

        with tenant_context(organization_id=self.org_b.id):
            self.gl_bank_b = create_account(
                organization=self.org_b, code="1000", name="Bank", account_type=AccountType.ASSET
            )
            self.bank_b = create_bank_account(
                organization=self.org_b, name="Other Bank", account=self.gl_bank_b, currency=self.currency
            )

    # -------------------------------------------------------- helpers

    def _txn(self, *, amount, bank_account=None, transaction_date=DAY, description="", **kwargs):
        from banking.services.transactions import add_manual_transaction

        return add_manual_transaction(
            organization=self.org_a,
            bank_account=bank_account or self.bank,
            transaction_date=transaction_date,
            amount=Decimal(amount),
            description=description,
            **kwargs,
        )

    def _customer_payment(self, *, amount=Decimal("1000.00"), payment_date=DAY, reference=""):
        from sales.services.payments import record_payment

        return record_payment(
            organization=self.org_a,
            customer=self.customer,
            payment_date=payment_date,
            amount=amount,
            destination_account=self.gl_bank,
            allocations=[],
            currency=self.currency,
            reference=reference,
            unapplied_credit_account=self.gl_unapplied,
        )

    def _vendor_payment(self, *, amount=Decimal("500.00"), payment_date=DAY, reference=""):
        from purchases.services.payments import record_vendor_payment

        return record_vendor_payment(
            organization=self.org_a,
            vendor=self.vendor,
            payment_date=payment_date,
            amount=amount,
            source_account=self.gl_bank,
            allocations=[],
            currency=self.currency,
            reference=reference,
            vendor_advance_account=self.gl_vendor_advance,
        )

    def _posted_expense(self, *, amount=Decimal("250.00"), expense_date=DAY, paid_through=None):
        from purchases.services.expenses import create_expense, post_expense

        expense = create_expense(
            organization=self.org_a,
            expense_date=expense_date,
            amount=amount,
            expense_account=self.gl_office,
            paid_through_account=paid_through or self.gl_bank,
            currency=self.currency,
            vendor=self.vendor,
        )
        return post_expense(expense_id=expense.id, organization=self.org_a)

    def _import_csv(self, content, *, bank_account=None, mapping=SIGNED_MAPPING, file_name="stmt.csv"):
        from banking.services.imports import import_csv_statement

        return import_csv_statement(
            organization=self.org_a,
            bank_account=bank_account or self.bank,
            content=content,
            mapping=mapping,
            file_name=file_name,
        )


class BankingTestsBase(BankingFixtureMixin, TestCase):
    """Default base: wrapped in a transaction, rolled back per test."""


class BankingTransactionTestsBase(BankingFixtureMixin, TransactionTestCase):
    """For tests needing real transaction boundaries (concurrency races)."""
