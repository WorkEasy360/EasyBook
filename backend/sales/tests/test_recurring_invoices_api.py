import datetime
from decimal import Decimal

from rest_framework.test import APITestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from authz.roles import Role
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_membership, make_org_with_owner, make_user
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit
from sales.services.customers import create_customer
from sales.services.recurring_invoices import create_recurring_template


class RecurringInvoicesAPITests(APITestCase):
    def setUp(self):
        self.org_a, self.owner_a, _ = make_org_with_owner("Org A", "api-recur-owner-a@example.com")
        self.org_b, self.owner_b, _ = make_org_with_owner("Org B", "api-recur-owner-b@example.com")
        self.viewer = make_user("api-recur-viewer@example.com")
        make_membership(self.org_a, self.viewer, role=Role.VIEWER)
        self.currency = make_currency("INR")

        with tenant_context(organization_id=self.org_a.id):
            self.ar_account = create_account(
                organization=self.org_a, code="1100", name="AR", account_type=AccountType.ASSET
            )
            self.sales_account = create_account(
                organization=self.org_a, code="4000", name="Sales", account_type=AccountType.INCOME
            )
            self.unit = create_unit(organization=self.org_a, code="EA", name="Each")
            self.item = create_item(
                organization=self.org_a, item_type=ItemType.SERVICE, name="Retainer", unit=self.unit,
                sales_account=self.sales_account,
            )
            self.customer = create_customer(
                organization=self.org_a, customer_code="CUST-1", display_name="Acme", currency=self.currency
            )
            self.template = create_recurring_template(
                organization=self.org_a, customer=self.customer, frequency="monthly",
                start_date=datetime.date(2026, 4, 1), receivable_account=self.ar_account,
                lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("500.00")}],
            )
        with tenant_context(organization_id=self.org_b.id):
            self.customer_b = create_customer(
                organization=self.org_b, customer_code="CUST-1", display_name="Beta", currency=self.currency
            )

    def _headers(self, org):
        return {"HTTP_X_ORGANIZATION_ID": str(org.id)}

    def test_owner_can_create_template(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.post(
            "/api/v1/sales/recurring-invoices/",
            {
                "customer_id": str(self.customer.id), "frequency": "monthly", "start_date": "2026-04-01",
                "receivable_account_id": str(self.ar_account.id),
                "lines": [{"item_id": str(self.item.id), "quantity": "1", "unit_price": "100.00"}],
            },
            format="json", **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["next_run_at"], "2026-04-01")

    def test_viewer_cannot_create_template(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.post(
            "/api/v1/sales/recurring-invoices/",
            {
                "customer_id": str(self.customer.id), "frequency": "monthly", "start_date": "2026-04-01",
                "receivable_account_id": str(self.ar_account.id),
                "lines": [{"item_id": str(self.item.id), "quantity": "1", "unit_price": "100.00"}],
            },
            format="json", **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 403)

    def test_viewer_can_list_templates(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.get("/api/v1/sales/recurring-invoices/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 200)

    def test_deactivate_and_activate_via_api(self):
        self.client.force_authenticate(user=self.owner_a)
        deactivate_response = self.client.post(
            f"/api/v1/sales/recurring-invoices/{self.template.id}/deactivate/", **self._headers(self.org_a)
        )
        self.assertEqual(deactivate_response.status_code, 200, deactivate_response.data)
        self.assertFalse(deactivate_response.data["is_active"])

        activate_response = self.client.post(
            f"/api/v1/sales/recurring-invoices/{self.template.id}/activate/", **self._headers(self.org_a)
        )
        self.assertEqual(activate_response.status_code, 200, activate_response.data)
        self.assertTrue(activate_response.data["is_active"])

    def test_patch_template_lines(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.patch(
            f"/api/v1/sales/recurring-invoices/{self.template.id}/",
            {"lines": [{"item_id": str(self.item.id), "quantity": "3", "unit_price": "20.00"}]},
            format="json", **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["lines"]), 1)
        self.assertEqual(response.data["lines"][0]["quantity"], "3.0000")

    def test_non_member_cannot_access_other_org_templates(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/sales/recurring-invoices/", **self._headers(self.org_b))
        self.assertEqual(response.status_code, 403)
