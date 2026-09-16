import datetime

from rest_framework.test import APITestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from accounts.models import FiscalYear
from authz.roles import Role
from core.tenancy import tenant_context
from core.tests.factories import make_membership, make_org_with_owner, make_user


class AccountingAPITests(APITestCase):
    def setUp(self):
        self.org_a, self.owner_a, _ = make_org_with_owner("Org A", "api-acct-owner-a@example.com")
        self.org_b, self.owner_b, _ = make_org_with_owner("Org B", "api-acct-owner-b@example.com")

        self.viewer = make_user("api-viewer@example.com")
        make_membership(self.org_a, self.viewer, role=Role.VIEWER)

        with tenant_context(organization_id=self.org_a.id):
            self.cash = create_account(organization=self.org_a, code="1000", name="Cash", account_type=AccountType.ASSET)
            self.revenue = create_account(
                organization=self.org_a, code="4000", name="Revenue", account_type=AccountType.INCOME
            )
            self.fiscal_year = FiscalYear.objects.create(
                organization=self.org_a, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
        with tenant_context(organization_id=self.org_b.id):
            self.cash_b = create_account(
                organization=self.org_b, code="1000", name="Cash", account_type=AccountType.ASSET
            )

    def _headers(self, org):
        return {"HTTP_X_ORGANIZATION_ID": str(org.id)}

    def test_owner_can_list_accounts(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/accounting/accounts/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 200)
        codes = {a["code"] for a in response.data["results"]}
        self.assertEqual(codes, {"1000", "4000"})

    def test_account_list_is_tenant_scoped(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/accounting/accounts/", **self._headers(self.org_a))
        codes = {a["code"] for a in response.data["results"]}
        # Org A's own accounts, and only those. The set assertion is the
        # point of the test — it was computed but never checked.
        self.assertEqual(codes, {"1000", "4000"})
        self.assertNotIn(self.cash_b.id, [a["id"] for a in response.data["results"]])
        self.assertEqual(len(response.data["results"]), 2)

    def test_non_member_cannot_access_other_org_accounts(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/accounting/accounts/", **self._headers(self.org_b))
        self.assertEqual(response.status_code, 403)

    def test_missing_organization_header_fails_closed(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/accounting/accounts/")
        self.assertEqual(response.status_code, 400)

    def test_unauthenticated_request_rejected(self):
        response = self.client.get("/api/v1/accounting/accounts/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 401)

    def test_owner_can_create_account(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.post(
            "/api/v1/accounting/accounts/",
            {"code": "1010", "name": "Bank", "account_type": AccountType.ASSET},
            **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 201)

    def test_viewer_cannot_create_account(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.post(
            "/api/v1/accounting/accounts/",
            {"code": "1010", "name": "Bank", "account_type": AccountType.ASSET},
            **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 403)

    def test_viewer_can_list_accounts(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.get("/api/v1/accounting/accounts/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 200)

    def test_create_and_post_journal_via_api(self):
        self.client.force_authenticate(user=self.owner_a)
        create_response = self.client.post(
            "/api/v1/accounting/journals/",
            {
                "posting_date": "2026-04-10",
                "currency": "INR",
                "lines": [
                    {"account_id": str(self.cash.id), "debit": "100.00"},
                    {"account_id": str(self.revenue.id), "credit": "100.00"},
                ],
            },
            format="json",
            **self._headers(self.org_a),
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        journal_id = create_response.data["id"]

        post_response = self.client.post(
            f"/api/v1/accounting/journals/{journal_id}/post/", **self._headers(self.org_a)
        )
        self.assertEqual(post_response.status_code, 200, post_response.data)
        self.assertEqual(post_response.data["status"], "posted")
        self.assertTrue(post_response.data["journal_number"])

    def test_viewer_cannot_post_journal(self):
        self.client.force_authenticate(user=self.owner_a)
        create_response = self.client.post(
            "/api/v1/accounting/journals/",
            {
                "posting_date": "2026-04-10",
                "currency": "INR",
                "lines": [
                    {"account_id": str(self.cash.id), "debit": "100.00"},
                    {"account_id": str(self.revenue.id), "credit": "100.00"},
                ],
            },
            format="json",
            **self._headers(self.org_a),
        )
        journal_id = create_response.data["id"]

        self.client.force_authenticate(user=self.viewer)
        post_response = self.client.post(
            f"/api/v1/accounting/journals/{journal_id}/post/", **self._headers(self.org_a)
        )
        self.assertEqual(post_response.status_code, 403)

    def test_account_detail_retrieve_and_update(self):
        # Regression test: AccountDetailView previously set `queryset =
        # Account.objects.all()` as a class attribute, which evaluates once
        # at import time (before any tenant context exists) and permanently
        # bakes in TenantManager's `.none()` — every retrieve/update 404'd.
        self.client.force_authenticate(user=self.owner_a)
        get_response = self.client.get(f"/api/v1/accounting/accounts/{self.cash.id}/", **self._headers(self.org_a))
        self.assertEqual(get_response.status_code, 200, get_response.data)
        self.assertEqual(get_response.data["code"], "1000")

        patch_response = self.client.patch(
            f"/api/v1/accounting/accounts/{self.cash.id}/", {"description": "Petty cash"},
            format="json", **self._headers(self.org_a),
        )
        self.assertEqual(patch_response.status_code, 200, patch_response.data)
        self.assertEqual(patch_response.data["description"], "Petty cash")

    def test_account_detail_not_visible_across_organizations(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get(f"/api/v1/accounting/accounts/{self.cash_b.id}/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 404)

    def test_journal_detail_retrieve(self):
        self.client.force_authenticate(user=self.owner_a)
        create_response = self.client.post(
            "/api/v1/accounting/journals/",
            {
                "posting_date": "2026-04-10",
                "currency": "INR",
                "lines": [
                    {"account_id": str(self.cash.id), "debit": "100.00"},
                    {"account_id": str(self.revenue.id), "credit": "100.00"},
                ],
            },
            format="json",
            **self._headers(self.org_a),
        )
        journal_id = create_response.data["id"]
        get_response = self.client.get(f"/api/v1/accounting/journals/{journal_id}/", **self._headers(self.org_a))
        self.assertEqual(get_response.status_code, 200, get_response.data)
        self.assertEqual(len(get_response.data["lines"]), 2)

    def test_trial_balance_endpoint(self):
        self.client.force_authenticate(user=self.owner_a)
        create_response = self.client.post(
            "/api/v1/accounting/journals/",
            {
                "posting_date": "2026-04-10",
                "currency": "INR",
                "lines": [
                    {"account_id": str(self.cash.id), "debit": "100.00"},
                    {"account_id": str(self.revenue.id), "credit": "100.00"},
                ],
            },
            format="json",
            **self._headers(self.org_a),
        )
        journal_id = create_response.data["id"]
        self.client.post(f"/api/v1/accounting/journals/{journal_id}/post/", **self._headers(self.org_a))

        response = self.client.get(
            "/api/v1/accounting/reports/trial-balance/",
            {"as_of_date": "2026-12-31"},
            **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_balanced"])
        self.assertEqual(response.data["total_closing_debit"], "100.00")
