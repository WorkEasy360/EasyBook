import datetime
from decimal import Decimal

from rest_framework.test import APITestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from accounting.services.journals import create_draft_journal
from accounting.services.posting import post_journal
from accounts.models import FiscalYear
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner


class GeneralLedgerAndJournalReportAPITests(APITestCase):
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("Acme", "gl-owner@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            self.cash = create_account(organization=self.org, code="1000", name="Cash", account_type=AccountType.ASSET)
            self.revenue = create_account(
                organization=self.org, code="4000", name="Revenue", account_type=AccountType.INCOME
            )
            FiscalYear.objects.create(
                organization=self.org, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            self.journals = []
            for amount, date in [("100.00", "2026-04-05"), ("50.00", "2026-04-15"), ("25.00", "2026-05-01")]:
                journal = create_draft_journal(
                    organization=self.org,
                    posting_date=date,
                    currency=self.currency,
                    lines=[
                        {"account_id": self.cash.id, "debit": Decimal(amount)},
                        {"account_id": self.revenue.id, "credit": Decimal(amount)},
                    ],
                    created_by=self.owner,
                )
                self.journals.append(post_journal(journal_id=journal.id, organization=self.org, actor=self.owner))

    def _headers(self):
        return {"HTTP_X_ORGANIZATION_ID": str(self.org.id)}

    def test_general_ledger_running_balance_and_pagination(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(
            "/api/v1/reports/general-ledger/", {"account": str(self.cash.id), "page_size": 2}, **self._headers()
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 3)
        self.assertEqual(len(response.data["results"]), 2)
        self.assertEqual(response.data["results"][0]["running_balance"], "100.00")
        self.assertEqual(response.data["results"][1]["running_balance"], "150.00")
        self.assertEqual(response.data["closing_balance"], "175.00")

        page2 = self.client.get(
            "/api/v1/reports/general-ledger/",
            {"account": str(self.cash.id), "page_size": 2, "page": 2},
            **self._headers(),
        )
        self.assertEqual(len(page2.data["results"]), 1)
        self.assertEqual(page2.data["results"][0]["running_balance"], "175.00")

    def test_general_ledger_requires_account(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get("/api/v1/reports/general-ledger/", **self._headers())
        self.assertEqual(response.status_code, 400)

    def test_journal_report_defaults_to_posted_only(self):
        with tenant_context(organization_id=self.org.id):
            create_draft_journal(
                organization=self.org,
                posting_date="2026-04-20",
                currency=self.currency,
                lines=[
                    {"account_id": self.cash.id, "debit": Decimal("10")},
                    {"account_id": self.revenue.id, "credit": Decimal("10")},
                ],
                created_by=self.owner,
            )
        self.client.force_authenticate(user=self.owner)
        response = self.client.get("/api/v1/reports/journals/", **self._headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 3)

    def test_journal_report_and_ledger_keep_a_reversed_original_beside_its_reversal(self):
        from accounting.services.posting import reverse_journal

        with tenant_context(organization_id=self.org.id):
            reverse_journal(
                journal_id=self.journals[0].id, organization=self.org, actor=self.owner,
                posting_date=datetime.date(2026, 4, 6),
            )
        self.client.force_authenticate(user=self.owner)
        journals = self.client.get("/api/v1/reports/journals/", **self._headers())
        self.assertEqual(journals.status_code, 200)
        self.assertEqual(journals.data["count"], 4)

        ledger = self.client.get(
            "/api/v1/reports/general-ledger/", {"account": str(self.cash.id)}, **self._headers()
        )
        self.assertEqual(ledger.status_code, 200)
        self.assertEqual(ledger.data["count"], 4)
        self.assertEqual(ledger.data["closing_balance"], "75.00")

    def test_journal_report_date_range_filter(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(
            "/api/v1/reports/journals/", {"from_date": "2026-04-01", "to_date": "2026-04-30"}, **self._headers()
        )
        self.assertEqual(response.data["count"], 2)

    def test_journal_report_account_filter(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(
            "/api/v1/reports/journals/", {"account": str(self.cash.id)}, **self._headers()
        )
        self.assertEqual(response.data["count"], 3)

    def test_trial_balance_endpoint_balances(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(
            "/api/v1/reports/trial-balance/", {"as_of_date": "2026-05-31"}, **self._headers()
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_balanced"])
        self.assertEqual(response.data["total_closing_debit"], response.data["total_closing_credit"])

    def test_general_ledger_cross_org_account_not_found(self):
        other_org, other_owner, _ = make_org_with_owner("Other Org", "gl-other@example.com")
        self.client.force_authenticate(user=other_owner)
        response = self.client.get(
            "/api/v1/reports/general-ledger/",
            {"account": str(self.cash.id)},
            HTTP_X_ORGANIZATION_ID=str(other_org.id),
        )
        self.assertEqual(response.status_code, 404)
