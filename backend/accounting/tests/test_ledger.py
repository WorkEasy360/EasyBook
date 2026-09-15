import datetime
from decimal import Decimal

from django.test import TestCase

from accounting.models.account import AccountType
from accounting.selectors import get_account_activity, get_account_running_ledger
from accounting.services.accounts import create_account
from accounting.services.journals import create_draft_journal
from accounting.services.posting import post_journal
from accounts.models import FiscalYear
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner


class GeneralLedgerTests(TestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Acme", "ledger-owner@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            self.cash = create_account(organization=self.org, code="1000", name="Cash", account_type=AccountType.ASSET)
            self.revenue = create_account(
                organization=self.org, code="4000", name="Revenue", account_type=AccountType.INCOME
            )
            FiscalYear.objects.create(
                organization=self.org, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            for amount, date in [("100.00", "2026-04-05"), ("50.00", "2026-04-15"), ("25.00", "2026-05-01")]:
                journal = create_draft_journal(
                    organization=self.org,
                    posting_date=date,
                    currency=self.currency,
                    lines=[
                        {"account_id": self.cash.id, "debit": Decimal(amount)},
                        {"account_id": self.revenue.id, "credit": Decimal(amount)},
                    ],
                    created_by=self.user,
                )
                post_journal(journal_id=journal.id, organization=self.org, actor=self.user)

    def test_account_activity_full_history(self):
        with tenant_context(organization_id=self.org.id):
            activity = get_account_activity(account=self.cash)
        self.assertEqual(activity["opening_balance"], Decimal("0"))
        self.assertEqual(activity["period_debit"], Decimal("175.00"))
        self.assertEqual(activity["closing_balance"], Decimal("175.00"))

    def test_account_activity_with_date_range_computes_opening_balance(self):
        with tenant_context(organization_id=self.org.id):
            activity = get_account_activity(
                account=self.cash, from_date=datetime.date(2026, 4, 20), to_date=datetime.date(2026, 5, 31)
            )
        self.assertEqual(activity["opening_balance"], Decimal("150.00"))
        self.assertEqual(activity["period_debit"], Decimal("25.00"))
        self.assertEqual(activity["closing_balance"], Decimal("175.00"))

    def test_running_ledger_balances_incrementally(self):
        with tenant_context(organization_id=self.org.id):
            ledger = get_account_running_ledger(account=self.cash)
        running_balances = [entry["running_balance"] for entry in ledger["entries"]]
        self.assertEqual(running_balances, [Decimal("100.00"), Decimal("150.00"), Decimal("175.00")])

    def test_ledger_is_tenant_scoped(self):
        other_org, other_user, _ = None, None, None
        from core.tests.factories import make_org_with_owner as _make

        other_org, other_user, _ = _make("Other Org", "ledger-other@example.com")
        with tenant_context(organization_id=other_org.id):
            other_cash = create_account(
                organization=other_org, code="1000", name="Cash", account_type=AccountType.ASSET
            )
            activity = get_account_activity(account=other_cash)
        self.assertEqual(activity["closing_balance"], Decimal("0"))
