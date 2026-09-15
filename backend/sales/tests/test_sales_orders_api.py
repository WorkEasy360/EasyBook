from decimal import Decimal

from rest_framework.test import APITestCase

from authz.roles import Role
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_membership, make_org_with_owner, make_user
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit
from sales.services.customers import create_customer
from sales.services.quotes import accept_quote, create_quote, send_quote
from sales.services.sales_orders import create_sales_order


class SalesOrdersAPITests(APITestCase):
    def setUp(self):
        self.org_a, self.owner_a, _ = make_org_with_owner("Org A", "api-so-owner-a@example.com")
        self.org_b, self.owner_b, _ = make_org_with_owner("Org B", "api-so-owner-b@example.com")
        self.viewer = make_user("api-so-viewer@example.com")
        make_membership(self.org_a, self.viewer, role=Role.VIEWER)
        self.currency = make_currency("INR")

        with tenant_context(organization_id=self.org_a.id):
            self.unit = create_unit(organization=self.org_a, code="EA", name="Each")
            self.item = create_item(organization=self.org_a, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit)
            self.customer = create_customer(
                organization=self.org_a, customer_code="CUST-1", display_name="Acme", currency=self.currency
            )
            self.order = create_sales_order(
                organization=self.org_a, customer=self.customer, order_date="2026-04-01",
                lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("100.00")}],
            )
            self.quote = create_quote(
                organization=self.org_a, customer=self.customer, issue_date="2026-04-01",
                lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("50.00")}],
            )
            send_quote(quote_id=self.quote.id, organization=self.org_a)
            accept_quote(quote_id=self.quote.id, organization=self.org_a)
        with tenant_context(organization_id=self.org_b.id):
            self.unit_b = create_unit(organization=self.org_b, code="EA", name="Each")
            self.item_b = create_item(organization=self.org_b, item_type=ItemType.PRODUCT, name="Gadget", unit=self.unit_b)
            self.customer_b = create_customer(
                organization=self.org_b, customer_code="CUST-1", display_name="Beta", currency=self.currency
            )
            self.order_b = create_sales_order(
                organization=self.org_b, customer=self.customer_b, order_date="2026-04-01",
                lines=[{"item": self.item_b, "quantity": Decimal("1"), "unit_price": Decimal("10.00")}],
            )

    def _headers(self, org):
        return {"HTTP_X_ORGANIZATION_ID": str(org.id)}

    def test_owner_can_create_order(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.post(
            "/api/v1/sales/orders/",
            {
                "customer_id": str(self.customer.id), "order_date": "2026-04-01",
                "lines": [{"item_id": str(self.item.id), "quantity": "2", "unit_price": "50.00"}],
            },
            format="json", **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["total"], "100.00")

    def test_viewer_cannot_create_order(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.post(
            "/api/v1/sales/orders/",
            {
                "customer_id": str(self.customer.id), "order_date": "2026-04-01",
                "lines": [{"item_id": str(self.item.id), "quantity": "1", "unit_price": "10.00"}],
            },
            format="json", **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 403)

    def test_order_list_is_tenant_scoped(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/sales/orders/", **self._headers(self.org_a))
        ids = {o["id"] for o in response.data["results"]}
        self.assertIn(str(self.order.id), ids)
        self.assertNotIn(str(self.order_b.id), ids)

    def test_confirm_order_via_api(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.post(f"/api/v1/sales/orders/{self.order.id}/confirm/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "confirmed")

    def test_invalid_transition_rejected_via_api(self):
        self.client.force_authenticate(user=self.owner_a)
        self.client.post(f"/api/v1/sales/orders/{self.order.id}/cancel/", **self._headers(self.org_a))
        response = self.client.post(f"/api/v1/sales/orders/{self.order.id}/confirm/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 400)

    def test_patch_draft_order_lines(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.patch(
            f"/api/v1/sales/orders/{self.order.id}/",
            {"lines": [{"item_id": str(self.item.id), "quantity": "5", "unit_price": "20.00"}]},
            format="json", **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["total"], "100.00")

    def test_cannot_patch_lines_on_confirmed_order(self):
        self.client.force_authenticate(user=self.owner_a)
        self.client.post(f"/api/v1/sales/orders/{self.order.id}/confirm/", **self._headers(self.org_a))
        response = self.client.patch(
            f"/api/v1/sales/orders/{self.order.id}/",
            {"lines": [{"item_id": str(self.item.id), "quantity": "5", "unit_price": "20.00"}]},
            format="json", **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 400)

    def test_convert_quote_to_order_via_api(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.post(
            f"/api/v1/sales/quotes/{self.quote.id}/convert/", {}, format="json", **self._headers(self.org_a)
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["total"], "50.00")
        self.assertEqual(str(response.data["source_quote"]), str(self.quote.id))

    def test_convert_quote_twice_rejected_via_api(self):
        self.client.force_authenticate(user=self.owner_a)
        self.client.post(f"/api/v1/sales/quotes/{self.quote.id}/convert/", {}, format="json", **self._headers(self.org_a))
        response = self.client.post(
            f"/api/v1/sales/quotes/{self.quote.id}/convert/", {}, format="json", **self._headers(self.org_a)
        )
        self.assertEqual(response.status_code, 400)

    def test_non_member_cannot_access_other_org_orders(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/sales/orders/", **self._headers(self.org_b))
        self.assertEqual(response.status_code, 403)
