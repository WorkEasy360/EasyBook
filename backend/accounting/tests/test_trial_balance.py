import datetime
from decimal import Decimal

from django.test import TestCase

from accounting.models.account import AccountType
from accounting.selectors import get_trial_balance
from accounting.services.accounts import create_account
from accounting.services.journals import create_draft_journal
from accounting.services.opening_balances import post_opening_balances
from accounting.services.posting import post_journal
from accounts.models import FiscalYear
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner


class TrialBalanceTests(TestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Acme", "tb-owner@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            self.cash = create_account(organization=self.org, code="1000", name="Cash", account_type=AccountType.ASSET)
            self.equity = create_account(
                organization=self.org, code="3000", name="Opening Balance Equity",
                account_type=AccountType.EQUITY, is_system=True,
            )
            self.revenue = create_account(
                organization=self.org, code="4000", name="Revenue", account_type=AccountType.INCOME
            )
            self.fiscal_year = FiscalYear.objects.create(
                organization=self.org, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )

    def test_opening_balances_post_as_a_balanced_journal(self):
        with tenant_context(organization_id=self.org.id):
            journal = post_opening_balances(
                organization=self.org,
                fiscal_year=self.fiscal_year,
                currency=self.currency,
                balances=[
                    {"account_id": self.cash.id, "debit": Decimal("5000.00")},
                    {"account_id": self.equity.id, "credit": Decimal("5000.00")},
                ],
                actor=self.user,
            )
        self.assertTrue(journal.journal_number)
        self.assertEqual(journal.source_type, "opening_balance")

    def test_trial_balance_totals_match(self):
        with tenant_context(organization_id=self.org.id):
            post_opening_balances(
                organization=self.org,
                fiscal_year=self.fiscal_year,
                currency=self.currency,
                balances=[
                    {"account_id": self.cash.id, "debit": Decimal("5000.00")},
                    {"account_id": self.equity.id, "credit": Decimal("5000.00")},
                ],
                actor=self.user,
            )
            journal = create_draft_journal(
                organization=self.org,
                posting_date="2026-04-15",
                currency=self.currency,
                lines=[
                    {"account_id": self.cash.id, "debit": Decimal("1200.00")},
                    {"account_id": self.revenue.id, "credit": Decimal("1200.00")},
                ],
                created_by=self.user,
            )
            post_journal(journal_id=journal.id, organization=self.org, actor=self.user)

            trial_balance = get_trial_balance(organization=self.org, as_of_date=datetime.date(2026, 12, 31))

        self.assertTrue(trial_balance["is_balanced"])
        self.assertEqual(trial_balance["total_closing_debit"], trial_balance["total_closing_credit"])

        cash_row = next(r for r in trial_balance["rows"] if r["account"] == self.cash)
        self.assertEqual(cash_row["closing_debit"], Decimal("6200.00"))

        equity_row = next(r for r in trial_balance["rows"] if r["account"] == self.equity)
        self.assertEqual(equity_row["closing_credit"], Decimal("5000.00"))

        revenue_row = next(r for r in trial_balance["rows"] if r["account"] == self.revenue)
        self.assertEqual(revenue_row["closing_credit"], Decimal("1200.00"))

    def test_trial_balance_excludes_draft_journals(self):
        with tenant_context(organization_id=self.org.id):
            create_draft_journal(
                organization=self.org,
                posting_date="2026-04-15",
                currency=self.currency,
                lines=[
                    {"account_id": self.cash.id, "debit": Decimal("999.00")},
                    {"account_id": self.revenue.id, "credit": Decimal("999.00")},
                ],
            )
            trial_balance = get_trial_balance(organization=self.org, as_of_date=datetime.date(2026, 12, 31))
        self.assertEqual(trial_balance["total_closing_debit"], Decimal("0"))
        self.assertTrue(trial_balance["is_balanced"])
