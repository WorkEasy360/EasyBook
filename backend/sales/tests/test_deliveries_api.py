import datetime
from decimal import Decimal

from rest_framework.test import APITestCase

from authz.roles import Role
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_membership, make_org_with_owner, make_user
from inventory.services.opening_stock import post_opening_stock
from inventory.services.warehouses import create_warehouse
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit
from sales.services.customers import create_customer
from sales.services.deliveries import create_delivery_challan


class DeliveriesAPITests(APITestCase):
    def setUp(self):
        self.org_a, self.owner_a, _ = make_org_with_owner("Org A", "api-delivery-owner-a@example.com")
        self.org_b, self.owner_b, _ = make_org_with_owner("Org B", "api-delivery-owner-b@example.com")
        self.viewer = make_user("api-delivery-viewer@example.com")
        make_membership(self.org_a, self.viewer, role=Role.VIEWER)
        self.currency = make_currency("INR")

        with tenant_context(organization_id=self.org_a.id):
            self.warehouse = create_warehouse(organization=self.org_a, code="MAIN", name="Main")
            self.unit = create_unit(organization=self.org_a, code="EA", name="Each")
            self.item = create_item(
                organization=self.org_a, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit,
                track_inventory=True,
            )
            post_opening_stock(
                organization=self.org_a, warehouse=self.warehouse, currency=self.currency,
                lines=[{"item": self.item, "quantity": Decimal("50"), "unit_cost": Decimal("5.00")}],
                opening_date=datetime.date(2026, 4, 1),
            )
            self.customer = create_customer(
                organization=self.org_a, customer_code="CUST-1", display_name="Acme", currency=self.currency
            )
            self.challan = create_delivery_challan(
                organization=self.org_a, customer=self.customer, warehouse=self.warehouse,
                challan_date="2026-04-05", lines=[{"item": self.item, "quantity": Decimal("5")}],
            )
        with tenant_context(organization_id=self.org_b.id):
            self.warehouse_b = create_warehouse(organization=self.org_b, code="MAIN", name="Main")
            self.unit_b = create_unit(organization=self.org_b, code="EA", name="Each")
            self.item_b = create_item(
                organization=self.org_b, item_type=ItemType.PRODUCT, name="Gadget", unit=self.unit_b,
                track_inventory=True,
            )
            self.customer_b = create_customer(
                organization=self.org_b, customer_code="CUST-1", display_name="Beta", currency=self.currency
            )
            self.challan_b = create_delivery_challan(
                organization=self.org_b, customer=self.customer_b, warehouse=self.warehouse_b,
                challan_date="2026-04-05", lines=[{"item": self.item_b, "quantity": Decimal("1")}],
            )

    def _headers(self, org):
        return {"HTTP_X_ORGANIZATION_ID": str(org.id)}

    def test_owner_can_create_challan(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.post(
            "/api/v1/sales/deliveries/",
            {
                "customer_id": str(self.customer.id), "warehouse_id": str(self.warehouse.id),
                "challan_date": "2026-04-05",
                "lines": [{"item_id": str(self.item.id), "quantity": "3"}],
            },
            format="json", **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 201, response.data)

    def test_viewer_cannot_create_challan(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.post(
            "/api/v1/sales/deliveries/",
            {
                "customer_id": str(self.customer.id), "warehouse_id": str(self.warehouse.id),
                "challan_date": "2026-04-05",
                "lines": [{"item_id": str(self.item.id), "quantity": "3"}],
            },
            format="json", **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 403)

    def test_delivery_list_is_tenant_scoped(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/sales/deliveries/", **self._headers(self.org_a))
        ids = {c["id"] for c in response.data["results"]}
        self.assertIn(str(self.challan.id), ids)
        self.assertNotIn(str(self.challan_b.id), ids)

    def test_dispatch_via_api_issues_stock(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.post(f"/api/v1/sales/deliveries/{self.challan.id}/dispatch/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "dispatched")

    def test_deliver_after_dispatch_via_api(self):
        self.client.force_authenticate(user=self.owner_a)
        self.client.post(f"/api/v1/sales/deliveries/{self.challan.id}/dispatch/", **self._headers(self.org_a))
        response = self.client.post(f"/api/v1/sales/deliveries/{self.challan.id}/deliver/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "delivered")

    def test_invalid_transition_rejected_via_api(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.post(f"/api/v1/sales/deliveries/{self.challan.id}/deliver/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 400)

    def test_non_member_cannot_access_other_org_deliveries(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/sales/deliveries/", **self._headers(self.org_b))
        self.assertEqual(response.status_code, 403)
