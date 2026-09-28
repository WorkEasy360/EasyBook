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
from reports.selectors.pnl import get_profit_and_loss


class ProfitAndLossTests(TestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Acme", "pnl-owner@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            self.cash = create_account(organization=self.org, code="1000", name="Cash", account_type=AccountType.ASSET)
            self.revenue = create_account(
                organization=self.org, code="4000", name="Sales Revenue", account_type=AccountType.INCOME
            )
            self.other_income = create_account(
                organization=self.org,
                code="4900",
                name="Interest Income",
                account_type=AccountType.INCOME,
                account_subtype="other_income",
            )
            self.cogs = create_account(
                organization=self.org,
                code="5000",
                name="Cost of Goods Sold",
                account_type=AccountType.EXPENSE,
                account_subtype="cogs",
            )
            self.opex = create_account(
                organization=self.org, code="6000", name="Rent Expense", account_type=AccountType.EXPENSE
            )
            FiscalYear.objects.create(
                organization=self.org, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            FiscalYear.objects.create(
                organization=self.org, start_date=datetime.date(2025, 4, 1), end_date=datetime.date(2026, 3, 31)
            )

            def post(date, lines):
                journal = create_draft_journal(
                    organization=self.org, posting_date=date, currency=self.currency, lines=lines, created_by=self.user
                )
                post_journal(journal_id=journal.id, organization=self.org, actor=self.user)

            # Revenue 1000, COGS 400, Rent 100, other income 50 — all in April.
            post("2026-04-05", [
                {"account_id": self.cash.id, "debit": Decimal("1000")},
                {"account_id": self.revenue.id, "credit": Decimal("1000")},
            ])
            post("2026-04-06", [
                {"account_id": self.cogs.id, "debit": Decimal("400")},
                {"account_id": self.cash.id, "credit": Decimal("400")},
            ])
            post("2026-04-07", [
                {"account_id": self.opex.id, "debit": Decimal("100")},
                {"account_id": self.cash.id, "credit": Decimal("100")},
            ])
            post("2026-04-08", [
                {"account_id": self.cash.id, "debit": Decimal("50")},
                {"account_id": self.other_income.id, "credit": Decimal("50")},
            ])
            # Outside the reporting window — must not leak in.
            post("2026-05-01", [
                {"account_id": self.cash.id, "debit": Decimal("999")},
                {"account_id": self.revenue.id, "credit": Decimal("999")},
            ])

    def test_income_cogs_and_expenses_correct(self):
        with tenant_context(organization_id=self.org.id):
            result = get_profit_and_loss(
                organization=self.org,
                from_date=datetime.date(2026, 4, 1),
                to_date=datetime.date(2026, 4, 30),
            )
        totals = result["totals"]
        self.assertEqual(totals["revenue"], Decimal("1000"))
        self.assertEqual(totals["cogs"], Decimal("400"))
        self.assertEqual(totals["gross_profit"], Decimal("600"))
        self.assertEqual(totals["operating_expenses"], Decimal("100"))
        self.assertEqual(totals["operating_profit"], Decimal("500"))
        self.assertEqual(totals["other_income"], Decimal("50"))
        self.assertEqual(totals["net_profit"], Decimal("550"))

    def test_date_filtering_excludes_out_of_range_journals(self):
        with tenant_context(organization_id=self.org.id):
            result = get_profit_and_loss(
                organization=self.org,
                from_date=datetime.date(2026, 4, 1),
                to_date=datetime.date(2026, 4, 30),
            )
        self.assertEqual(result["totals"]["revenue"], Decimal("1000"))

    def test_reversed_journal_nets_to_zero_and_is_dropped(self):
        from accounting.services.posting import reverse_journal

        with tenant_context(organization_id=self.org.id):
            journal = create_draft_journal(
                organization=self.org,
                posting_date="2026-04-10",
                currency=self.currency,
                lines=[
                    {"account_id": self.cash.id, "debit": Decimal("200")},
                    {"account_id": self.revenue.id, "credit": Decimal("200")},
                ],
                created_by=self.user,
            )
            posted = post_journal(journal_id=journal.id, organization=self.org, actor=self.user)
            # Explicit date: defaulting to "today" put the reversal outside
            # this window, which is how this test used to pass while the
            # selector dropped the reversed original instead of netting it.
            reverse_journal(
                journal_id=posted.id, organization=self.org, actor=self.user,
                posting_date=datetime.date(2026, 4, 12),
            )

            result = get_profit_and_loss(
                organization=self.org,
                from_date=datetime.date(2026, 4, 1),
                to_date=datetime.date(2026, 4, 30),
            )
        # The 200 posted + the 200 reversal cancel out; original 1000 stands.
        self.assertEqual(result["totals"]["revenue"], Decimal("1000"))

    def test_later_period_reversal_keeps_original_period_and_nets_over_both(self):
        from accounting.services.posting import reverse_journal

        with tenant_context(organization_id=self.org.id):
            journal = create_draft_journal(
                organization=self.org,
                posting_date="2026-04-10",
                currency=self.currency,
                lines=[
                    {"account_id": self.cash.id, "debit": Decimal("200")},
                    {"account_id": self.revenue.id, "credit": Decimal("200")},
                ],
                created_by=self.user,
            )
            posted = post_journal(journal_id=journal.id, organization=self.org, actor=self.user)
            reverse_journal(
                journal_id=posted.id, organization=self.org, actor=self.user,
                posting_date=datetime.date(2026, 5, 10),
            )
            april = get_profit_and_loss(
                organization=self.org, from_date=datetime.date(2026, 4, 1), to_date=datetime.date(2026, 4, 30)
            )
            april_and_may = get_profit_and_loss(
                organization=self.org, from_date=datetime.date(2026, 4, 1), to_date=datetime.date(2026, 5, 31)
            )
        # April already reported the 200; a May reversal must not rewrite it.
        self.assertEqual(april["totals"]["revenue"], Decimal("1200"))
        # 1000 + 200 - 200 + 999 (the May journal from setUp).
        self.assertEqual(april_and_may["totals"]["revenue"], Decimal("1999"))

    def test_comparison_period_and_variance(self):
        with tenant_context(organization_id=self.org.id):
            journal = create_draft_journal(
                organization=self.org,
                posting_date="2026-03-01",
                currency=self.currency,
                lines=[
                    {"account_id": self.cash.id, "debit": Decimal("500")},
                    {"account_id": self.revenue.id, "credit": Decimal("500")},
                ],
                created_by=self.user,
            )
            post_journal(journal_id=journal.id, organization=self.org, actor=self.user)

            result = get_profit_and_loss(
                organization=self.org,
                from_date=datetime.date(2026, 4, 1),
                to_date=datetime.date(2026, 4, 30),
                comparison_from=datetime.date(2026, 3, 1),
                comparison_to=datetime.date(2026, 3, 31),
            )
        self.assertEqual(result["comparison_totals"]["revenue"], Decimal("500"))
        variance = result["variance"]["revenue"]
        self.assertEqual(Decimal(variance["current"]), Decimal("1000"))
        self.assertEqual(Decimal(variance["previous"]), Decimal("500"))
        self.assertEqual(Decimal(variance["variance"]), Decimal("500"))
        self.assertEqual(Decimal(variance["variance_percent"]), Decimal("100"))

    def test_zero_previous_variance_never_infinite(self):
        from reports.selectors.params import compute_variance

        result = compute_variance(current=Decimal("100"), previous=Decimal("0"))
        self.assertIsNone(result["variance_percent"])
        zero_case = compute_variance(current=Decimal("0"), previous=Decimal("0"))
        self.assertEqual(zero_case["variance_percent"], "0")

    def test_pnl_is_tenant_scoped(self):
        other_org, other_user, _ = make_org_with_owner("Other Org", "pnl-other@example.com")
        with tenant_context(organization_id=other_org.id):
            FiscalYear.objects.create(
                organization=other_org, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            other_cash = create_account(
                organization=other_org, code="1000", name="Cash", account_type=AccountType.ASSET
            )
            other_revenue = create_account(
                organization=other_org, code="4000", name="Revenue", account_type=AccountType.INCOME
            )
            journal = create_draft_journal(
                organization=other_org,
                posting_date="2026-04-05",
                currency=self.currency,
                lines=[
                    {"account_id": other_cash.id, "debit": Decimal("777")},
                    {"account_id": other_revenue.id, "credit": Decimal("777")},
                ],
                created_by=other_user,
            )
            post_journal(journal_id=journal.id, organization=other_org, actor=other_user)

            result = get_profit_and_loss(
                organization=other_org,
                from_date=datetime.date(2026, 4, 1),
                to_date=datetime.date(2026, 4, 30),
            )
        self.assertEqual(result["totals"]["revenue"], Decimal("777"))

        with tenant_context(organization_id=self.org.id):
            own_result = get_profit_and_loss(
                organization=self.org,
                from_date=datetime.date(2026, 4, 1),
                to_date=datetime.date(2026, 4, 30),
            )
        self.assertEqual(own_result["totals"]["revenue"], Decimal("1000"))
