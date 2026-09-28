import datetime
from decimal import Decimal

from django.test import TestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from accounting.services.journals import create_draft_journal
from accounting.services.posting import post_journal
from accounts.models import FiscalYear
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from reports.selectors.balance_sheet import get_balance_sheet


class BalanceSheetTests(TestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Acme", "bs-owner@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            self.cash = create_account(organization=self.org, code="1000", name="Cash", account_type=AccountType.ASSET)
            self.equipment = create_account(
                organization=self.org,
                code="1500",
                name="Equipment",
                account_type=AccountType.ASSET,
                account_subtype="fixed_asset",
            )
            self.ap = create_account(
                organization=self.org, code="2000", name="Accounts Payable", account_type=AccountType.LIABILITY
            )
            self.loan = create_account(
                organization=self.org,
                code="2500",
                name="Long-Term Loan",
                account_type=AccountType.LIABILITY,
                account_subtype="long_term_liability",
            )
            self.capital = create_account(
                organization=self.org, code="3000", name="Owner's Capital", account_type=AccountType.EQUITY
            )
            self.revenue = create_account(
                organization=self.org, code="4000", name="Revenue", account_type=AccountType.INCOME
            )
            self.expense = create_account(
                organization=self.org, code="5000", name="Expense", account_type=AccountType.EXPENSE
            )
            FiscalYear.objects.create(
                organization=self.org, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )

            def post(date, lines):
                journal = create_draft_journal(
                    organization=self.org, posting_date=date, currency=self.currency, lines=lines, created_by=self.user
                )
                post_journal(journal_id=journal.id, organization=self.org, actor=self.user)

            # Owner invests 10,000 cash.
            post("2026-04-01", [
                {"account_id": self.cash.id, "debit": Decimal("10000")},
                {"account_id": self.capital.id, "credit": Decimal("10000")},
            ])
            # Buys equipment for 3,000, financed partly by a long-term loan.
            post("2026-04-02", [
                {"account_id": self.equipment.id, "debit": Decimal("3000")},
                {"account_id": self.cash.id, "credit": Decimal("1000")},
                {"account_id": self.loan.id, "credit": Decimal("2000")},
            ])
            # Buys supplies on credit (AP).
            post("2026-04-03", [
                {"account_id": self.expense.id, "debit": Decimal("500")},
                {"account_id": self.ap.id, "credit": Decimal("500")},
            ])
            # Earns revenue.
            post("2026-04-04", [
                {"account_id": self.cash.id, "debit": Decimal("1200")},
                {"account_id": self.revenue.id, "credit": Decimal("1200")},
            ])

    def test_balance_sheet_balances(self):
        with tenant_context(organization_id=self.org.id):
            result = get_balance_sheet(organization=self.org, as_of_date=datetime.date(2026, 4, 30))
        self.assertTrue(result["is_balanced"])
        self.assertEqual(result["totals"]["total_assets"], result["totals"]["total_liabilities_and_equity"])

    def test_assets_liabilities_equity_correct(self):
        with tenant_context(organization_id=self.org.id):
            result = get_balance_sheet(organization=self.org, as_of_date=datetime.date(2026, 4, 30))
        # Cash: 10000 - 1000 + 1200 = 10200. Equipment: 3000.
        self.assertEqual(result["totals"]["total_assets"], Decimal("13200"))
        # AP 500 + Loan 2000 = 2500.
        self.assertEqual(result["totals"]["total_liabilities"], Decimal("2500"))
        # Capital 10000 + current earnings (1200 revenue - 500 expense = 700).
        self.assertEqual(result["totals"]["total_equity"], Decimal("10700"))

    def test_fixed_asset_and_long_term_liability_classification(self):
        with tenant_context(organization_id=self.org.id):
            result = get_balance_sheet(organization=self.org, as_of_date=datetime.date(2026, 4, 30))
        fixed_codes = {row["account_code"] for row in result["assets"]["fixed_assets"]}
        self.assertEqual(fixed_codes, {"1500"})
        current_asset_codes = {row["account_code"] for row in result["assets"]["current_assets"]}
        self.assertEqual(current_asset_codes, {"1000"})
        long_term_codes = {row["account_code"] for row in result["liabilities"]["long_term_liabilities"]}
        self.assertEqual(long_term_codes, {"2500"})
        current_liability_codes = {row["account_code"] for row in result["liabilities"]["current_liabilities"]}
        self.assertEqual(current_liability_codes, {"2000"})

    def test_current_period_earnings_line_present(self):
        with tenant_context(organization_id=self.org.id):
            result = get_balance_sheet(organization=self.org, as_of_date=datetime.date(2026, 4, 30))
        earnings_rows = [row for row in result["equity"] if row["account_id"] is None]
        self.assertEqual(len(earnings_rows), 1)
        self.assertEqual(earnings_rows[0]["amount"], Decimal("700"))

    def test_as_of_date_excludes_later_activity(self):
        with tenant_context(organization_id=self.org.id):
            journal = create_draft_journal(
                organization=self.org,
                posting_date="2026-05-01",
                currency=self.currency,
                lines=[
                    {"account_id": self.cash.id, "debit": Decimal("5000")},
                    {"account_id": self.revenue.id, "credit": Decimal("5000")},
                ],
                created_by=self.user,
            )
            post_journal(journal_id=journal.id, organization=self.org, actor=self.user)

            result = get_balance_sheet(organization=self.org, as_of_date=datetime.date(2026, 4, 30))
        self.assertEqual(result["totals"]["total_assets"], Decimal("13200"))

    def test_reversed_journal_nets_out_of_the_balance_sheet(self):
        from accounting.services.posting import reverse_journal

        with tenant_context(organization_id=self.org.id):
            journal = create_draft_journal(
                organization=self.org,
                posting_date="2026-04-10",
                currency=self.currency,
                lines=[
                    {"account_id": self.cash.id, "debit": Decimal("5000")},
                    {"account_id": self.revenue.id, "credit": Decimal("5000")},
                ],
                created_by=self.user,
            )
            posted = post_journal(journal_id=journal.id, organization=self.org, actor=self.user)
            reverse_journal(
                journal_id=posted.id, organization=self.org, actor=self.user,
                posting_date=datetime.date(2026, 4, 11),
            )
            result = get_balance_sheet(organization=self.org, as_of_date=datetime.date(2026, 4, 30))
        self.assertTrue(result["is_balanced"])
        self.assertEqual(result["totals"]["total_assets"], Decimal("13200"))
        self.assertEqual(result["totals"]["total_equity"], Decimal("10700"))

    def test_balance_sheet_is_tenant_scoped(self):
        other_org, other_user, _ = make_org_with_owner("Other Org", "bs-other@example.com")
        with tenant_context(organization_id=other_org.id):
            FiscalYear.objects.create(
                organization=other_org, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            other_cash = create_account(
                organization=other_org, code="1000", name="Cash", account_type=AccountType.ASSET
            )
            other_capital = create_account(
                organization=other_org, code="3000", name="Capital", account_type=AccountType.EQUITY
            )
            journal = create_draft_journal(
                organization=other_org,
                posting_date="2026-04-01",
                currency=self.currency,
                lines=[
                    {"account_id": other_cash.id, "debit": Decimal("999")},
                    {"account_id": other_capital.id, "credit": Decimal("999")},
                ],
                created_by=other_user,
            )
            post_journal(journal_id=journal.id, organization=other_org, actor=other_user)

            other_result = get_balance_sheet(organization=other_org, as_of_date=datetime.date(2026, 4, 30))
        self.assertEqual(other_result["totals"]["total_assets"], Decimal("999"))

        with tenant_context(organization_id=self.org.id):
            own_result = get_balance_sheet(organization=self.org, as_of_date=datetime.date(2026, 4, 30))
        self.assertEqual(own_result["totals"]["total_assets"], Decimal("13200"))
