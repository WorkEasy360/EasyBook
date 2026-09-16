import datetime
from decimal import Decimal

from rest_framework.test import APITestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from accounting.services.journals import create_draft_journal
from accounting.services.posting import post_journal
from accounts.models import FiscalYear
from authz.roles import Role
from banking.services.bank_accounts import create_bank_account
from core.tenancy import tenant_context
from core.tests.factories import make_membership, make_org_with_owner, make_user


class ReportsFinancialStatementsAPITests(APITestCase):
    def setUp(self):
        self.org_a, self.owner_a, _ = make_org_with_owner("Org A", "reports-owner-a@example.com")
        self.org_b, self.owner_b, _ = make_org_with_owner("Org B", "reports-owner-b@example.com")
        self.viewer = make_user("reports-viewer@example.com")
        make_membership(self.org_a, self.viewer, role=Role.VIEWER)
        self.currency_code = "INR"

        with tenant_context(organization_id=self.org_a.id):
            from core.tests.factories import make_currency

            currency = make_currency(self.currency_code)
            self.cash = create_account(organization=self.org_a, code="1000", name="Cash", account_type=AccountType.ASSET)
            self.capital = create_account(
                organization=self.org_a, code="3000", name="Capital", account_type=AccountType.EQUITY
            )
            self.revenue = create_account(
                organization=self.org_a, code="4000", name="Revenue", account_type=AccountType.INCOME
            )
            FiscalYear.objects.create(
                organization=self.org_a, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            create_bank_account(organization=self.org_a, name="Main Bank", account=self.cash, currency=currency)

            journal = create_draft_journal(
                organization=self.org_a,
                posting_date="2026-04-05",
                currency=currency,
                lines=[
                    {"account_id": self.cash.id, "debit": Decimal("1000")},
                    {"account_id": self.capital.id, "credit": Decimal("500")},
                    {"account_id": self.revenue.id, "credit": Decimal("500")},
                ],
                created_by=self.owner_a,
            )
            post_journal(journal_id=journal.id, organization=self.org_a, actor=self.owner_a)

        with tenant_context(organization_id=self.org_b.id):
            self.cash_b = create_account(
                organization=self.org_b, code="1000", name="Cash", account_type=AccountType.ASSET
            )

    def _headers(self, org):
        return {"HTTP_X_ORGANIZATION_ID": str(org.id)}

    # --- Profit & Loss -------------------------------------------------

    def test_owner_can_view_profit_loss(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get(
            "/api/v1/reports/profit-loss/",
            {"from_date": "2026-04-01", "to_date": "2026-04-30"},
            **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["totals"]["revenue"], "500.00")

    def test_viewer_can_view_profit_loss(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.get(
            "/api/v1/reports/profit-loss/",
            {"from_date": "2026-04-01", "to_date": "2026-04-30"},
            **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 200)

    def test_non_member_cannot_view_profit_loss(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/reports/profit-loss/", **self._headers(self.org_b))
        self.assertEqual(response.status_code, 403)

    def test_missing_organization_header_fails_closed(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/reports/profit-loss/")
        self.assertEqual(response.status_code, 400)

    def test_unauthenticated_rejected(self):
        response = self.client.get("/api/v1/reports/profit-loss/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 401)

    def test_invalid_date_rejected(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get(
            "/api/v1/reports/profit-loss/", {"from_date": "not-a-date"}, **self._headers(self.org_a)
        )
        self.assertEqual(response.status_code, 400)

    def test_profit_loss_cannot_see_other_org_data(self):
        self.client.force_authenticate(user=self.owner_b)
        response = self.client.get(
            "/api/v1/reports/profit-loss/",
            {"from_date": "2026-04-01", "to_date": "2026-04-30"},
            **self._headers(self.org_b),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["totals"]["revenue"], "0")

    # --- Balance Sheet ---------------------------------------------------

    def test_balance_sheet_endpoint(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get(
            "/api/v1/reports/balance-sheet/", {"as_of_date": "2026-04-30"}, **self._headers(self.org_a)
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_balanced"])
        self.assertEqual(response.data["totals"]["total_assets"], "1000.00")

    def test_balance_sheet_cross_org_isolated(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get(
            "/api/v1/reports/balance-sheet/", {"as_of_date": "2026-04-30"}, **self._headers(self.org_b)
        )
        self.assertEqual(response.status_code, 403)

    # --- Cash Flow ---------------------------------------------------------

    def test_cash_flow_endpoint(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get(
            "/api/v1/reports/cash-flow/",
            {"from_date": "2026-04-01", "to_date": "2026-04-30"},
            **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["reconciles"])
        self.assertEqual(response.data["closing_cash"], "1000.00")

    def test_cash_flow_missing_from_date_rejected(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get(
            "/api/v1/reports/cash-flow/", {"to_date": "2026-04-30"}, **self._headers(self.org_a)
        )
        self.assertEqual(response.status_code, 400)

    def test_cash_flow_no_bank_account_configured(self):
        self.client.force_authenticate(user=self.owner_b)
        with tenant_context(organization_id=self.org_b.id):
            FiscalYear.objects.create(
                organization=self.org_b, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
        response = self.client.get(
            "/api/v1/reports/cash-flow/",
            {"from_date": "2026-04-01", "to_date": "2026-04-30"},
            **self._headers(self.org_b),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "cash_accounts_not_configured")
