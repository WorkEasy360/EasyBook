from rest_framework.test import APITestCase

from authz.roles import Role
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_membership, make_org_with_owner, make_user
from sales.services.customers import create_customer


class CustomersAPITests(APITestCase):
    def setUp(self):
        self.org_a, self.owner_a, _ = make_org_with_owner("Org A", "api-cust-owner-a@example.com")
        self.org_b, self.owner_b, _ = make_org_with_owner("Org B", "api-cust-owner-b@example.com")
        self.viewer = make_user("api-cust-viewer@example.com")
        make_membership(self.org_a, self.viewer, role=Role.VIEWER)
        self.currency = make_currency("INR")

        with tenant_context(organization_id=self.org_a.id):
            self.customer_a = create_customer(
                organization=self.org_a, customer_code="CUST-A1", display_name="Customer A1", currency=self.currency
            )
        with tenant_context(organization_id=self.org_b.id):
            self.customer_b = create_customer(
                organization=self.org_b, customer_code="CUST-B1", display_name="Customer B1", currency=self.currency
            )

    def _headers(self, org):
        return {"HTTP_X_ORGANIZATION_ID": str(org.id)}

    def test_owner_can_create_customer(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.post(
            "/api/v1/sales/customers/",
            {"customer_code": "CUST-A2", "display_name": "New Customer", "currency": self.currency.code},
            **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 201, response.data)

    def test_viewer_cannot_create_customer(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.post(
            "/api/v1/sales/customers/",
            {"customer_code": "CUST-A2", "display_name": "New Customer", "currency": self.currency.code},
            **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 403)

    def test_viewer_can_list_customers(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.get("/api/v1/sales/customers/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 200)

    def test_customer_list_is_tenant_scoped(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/sales/customers/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 200)
        ids = {c["id"] for c in response.data["results"]}
        self.assertIn(str(self.customer_a.id), ids)
        self.assertNotIn(str(self.customer_b.id), ids)

    def test_non_member_cannot_access_other_org_customers(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/sales/customers/", **self._headers(self.org_b))
        self.assertEqual(response.status_code, 403)

    def test_missing_organization_header_fails_closed(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/sales/customers/")
        self.assertEqual(response.status_code, 400)

    def test_archive_customer_via_patch(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.patch(
            f"/api/v1/sales/customers/{self.customer_a.id}/",
            {"is_active": False}, format="json", **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_active"])

    def test_cannot_fetch_other_org_customer_by_id(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get(f"/api/v1/sales/customers/{self.customer_b.id}/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 404)
