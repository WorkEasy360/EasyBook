import datetime
from decimal import Decimal

from django.test import TestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from accounting.services.journals import create_draft_journal
from accounting.services.posting import post_journal
from accounts.models import FiscalYear
from banking.services.bank_accounts import create_bank_account
from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from reports.selectors.cash_flow import get_cash_flow_statement


class CashFlowTests(TestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Acme", "cf-owner@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            self.cash = create_account(organization=self.org, code="1000", name="Cash", account_type=AccountType.ASSET)
            self.ar = create_account(
                organization=self.org, code="1200", name="Accounts Receivable", account_type=AccountType.ASSET
            )
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
            self.bank_account = create_bank_account(
                organization=self.org, name="Main Bank", account=self.cash, currency=self.currency
            )

            def post(date, lines):
                journal = create_draft_journal(
                    organization=self.org, posting_date=date, currency=self.currency, lines=lines, created_by=self.user
                )
                post_journal(journal_id=journal.id, organization=self.org, actor=self.user)

            # Owner invests 10,000 cash (financing).
            post("2026-04-01", [
                {"account_id": self.cash.id, "debit": Decimal("10000")},
                {"account_id": self.capital.id, "credit": Decimal("10000")},
            ])
            # Takes a long-term loan of 5,000 cash (financing).
            post("2026-04-02", [
                {"account_id": self.cash.id, "debit": Decimal("5000")},
                {"account_id": self.loan.id, "credit": Decimal("5000")},
            ])
            # Buys equipment for cash, 4,000 (investing).
            post("2026-04-03", [
                {"account_id": self.equipment.id, "debit": Decimal("4000")},
                {"account_id": self.cash.id, "credit": Decimal("4000")},
            ])
            # Sells on credit — revenue recognized, cash NOT received yet (operating, AR up).
            post("2026-04-04", [
                {"account_id": self.ar.id, "debit": Decimal("2000")},
                {"account_id": self.revenue.id, "credit": Decimal("2000")},
            ])
            # Incurs an expense on credit (AP up) — operating.
            post("2026-04-05", [
                {"account_id": self.expense.id, "debit": Decimal("800")},
                {"account_id": self.ap.id, "credit": Decimal("800")},
            ])
            # Collects cash sale directly (operating, cash in).
            post("2026-04-06", [
                {"account_id": self.cash.id, "debit": Decimal("1500")},
                {"account_id": self.revenue.id, "credit": Decimal("1500")},
            ])

    def test_cash_flow_reconciles_to_closing_cash(self):
        with tenant_context(organization_id=self.org.id):
            result = get_cash_flow_statement(
                organization=self.org, from_date=datetime.date(2026, 4, 1), to_date=datetime.date(2026, 4, 30)
            )
        self.assertTrue(result["reconciles"])
        total = (
            result["operating_activities"]["total"]
            + result["investing_activities"]["total"]
            + result["financing_activities"]["total"]
        )
        self.assertEqual(total, result["net_change_in_cash"])
        self.assertEqual(result["closing_cash"] - result["opening_cash"], result["net_change_in_cash"])

    def test_opening_and_closing_cash_correct(self):
        with tenant_context(organization_id=self.org.id):
            result = get_cash_flow_statement(
                organization=self.org, from_date=datetime.date(2026, 4, 1), to_date=datetime.date(2026, 4, 30)
            )
        self.assertEqual(result["opening_cash"], Decimal("0"))
        # 10000 + 5000 - 4000 + 1500 = 12500
        self.assertEqual(result["closing_cash"], Decimal("12500"))

    def test_investing_and_financing_classified_correctly(self):
        with tenant_context(organization_id=self.org.id):
            result = get_cash_flow_statement(
                organization=self.org, from_date=datetime.date(2026, 4, 1), to_date=datetime.date(2026, 4, 30)
            )
        self.assertEqual(result["investing_activities"]["total"], Decimal("-4000"))
        self.assertEqual(result["financing_activities"]["total"], Decimal("15000"))

    def test_operating_includes_net_profit_and_working_capital(self):
        with tenant_context(organization_id=self.org.id):
            result = get_cash_flow_statement(
                organization=self.org, from_date=datetime.date(2026, 4, 1), to_date=datetime.date(2026, 4, 30)
            )
        # Net profit = revenue(2000+1500) - expense(800) = 2700.
        self.assertEqual(result["operating_activities"]["net_profit"], Decimal("2700"))
        # Working capital: AR +2000 (uses cash: -2000), AP +800 (releases cash: +800).
        # Operating total = 2700 - 2000 + 800 = 1500.
        self.assertEqual(result["operating_activities"]["total"], Decimal("1500"))

    def test_missing_from_date_rejected(self):
        with tenant_context(organization_id=self.org.id):
            with self.assertRaises(ApplicationError):
                get_cash_flow_statement(organization=self.org, from_date=None, to_date=datetime.date(2026, 4, 30))

    def test_no_bank_account_configured_rejected(self):
        other_org, _other_user, _ = make_org_with_owner("No Bank Org", "cf-nobank@example.com")
        with tenant_context(organization_id=other_org.id):
            FiscalYear.objects.create(
                organization=other_org, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            with self.assertRaises(ApplicationError):
                get_cash_flow_statement(
                    organization=other_org, from_date=datetime.date(2026, 4, 1), to_date=datetime.date(2026, 4, 30)
                )

    def test_cash_flow_is_tenant_scoped(self):
        other_org, other_user, _ = make_org_with_owner("Other Org", "cf-other@example.com")
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
            create_bank_account(organization=other_org, name="Other Bank", account=other_cash, currency=self.currency)
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

            other_result = get_cash_flow_statement(
                organization=other_org, from_date=datetime.date(2026, 4, 1), to_date=datetime.date(2026, 4, 30)
            )
        self.assertEqual(other_result["closing_cash"], Decimal("999"))

        with tenant_context(organization_id=self.org.id):
            own_result = get_cash_flow_statement(
                organization=self.org, from_date=datetime.date(2026, 4, 1), to_date=datetime.date(2026, 4, 30)
            )
        self.assertEqual(own_result["closing_cash"], Decimal("12500"))
