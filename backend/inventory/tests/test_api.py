import datetime
from decimal import Decimal

from rest_framework.test import APITestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from accounts.models import FiscalYear
from authz.roles import Role
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_membership, make_org_with_owner, make_user
from inventory.services.opening_stock import post_opening_stock
from inventory.services.warehouses import create_warehouse
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit


class InventoryAPITests(APITestCase):
    def setUp(self):
        self.org_a, self.owner_a, _ = make_org_with_owner("Org A", "api-inv-owner-a@example.com")
        self.org_b, self.owner_b, _ = make_org_with_owner("Org B", "api-inv-owner-b@example.com")
        self.viewer = make_user("api-inv-viewer@example.com")
        make_membership(self.org_a, self.viewer, role=Role.VIEWER)
        self.currency = make_currency("INR")

        with tenant_context(organization_id=self.org_a.id):
            self.unit = create_unit(organization=self.org_a, code="EA", name="Each")
            self.warehouse = create_warehouse(organization=self.org_a, code="MAIN", name="Main")
            self.inventory_account = create_account(
                organization=self.org_a, code="1200", name="Inventory Asset", account_type=AccountType.ASSET
            )
            self.equity = create_account(
                organization=self.org_a, code="3900", name="Opening Balance Equity", account_type=AccountType.EQUITY
            )
            self.product = create_item(
                organization=self.org_a, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit,
                track_inventory=True, inventory_account=self.inventory_account,
            )
            FiscalYear.objects.create(
                organization=self.org_a, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            post_opening_stock(
                organization=self.org_a, warehouse=self.warehouse, currency=self.currency,
                lines=[{"item": self.product, "quantity": Decimal("50"), "unit_cost": Decimal("10.00")}],
                opening_date=datetime.date(2026, 4, 1), contra_account=self.equity, actor=self.owner_a,
            )
        with tenant_context(organization_id=self.org_b.id):
            self.warehouse_b = create_warehouse(organization=self.org_b, code="MAIN", name="Main")

    def _headers(self, org):
        return {"HTTP_X_ORGANIZATION_ID": str(org.id)}

    def test_warehouse_list_shows_own_org_only(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/inventory/warehouses/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 200)
        ids = {w["id"] for w in response.data["results"]}
        self.assertIn(str(self.warehouse.id), ids)
        self.assertNotIn(str(self.warehouse_b.id), ids)

    def test_warehouse_detail_retrieve_and_update(self):
        # Regression test for the same class-level `queryset` bug fixed in
        # WarehouseDetailView (see items/api/views.py note).
        self.client.force_authenticate(user=self.owner_a)
        get_response = self.client.get(f"/api/v1/inventory/warehouses/{self.warehouse.id}/", **self._headers(self.org_a))
        self.assertEqual(get_response.status_code, 200, get_response.data)

        patch_response = self.client.patch(
            f"/api/v1/inventory/warehouses/{self.warehouse.id}/", {"name": "Main Warehouse"},
            format="json", **self._headers(self.org_a),
        )
        self.assertEqual(patch_response.status_code, 200, patch_response.data)
        self.assertEqual(patch_response.data["name"], "Main Warehouse")

    def test_warehouse_detail_not_visible_across_organizations(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get(f"/api/v1/inventory/warehouses/{self.warehouse_b.id}/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 404)

    def test_viewer_cannot_manage_warehouses(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.post(
            "/api/v1/inventory/warehouses/", {"code": "NEW", "name": "New"}, format="json", **self._headers(self.org_a)
        )
        self.assertEqual(response.status_code, 403)

    def test_stock_summary_endpoint(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get(
            "/api/v1/inventory/stock-summary/", {"item_id": str(self.product.id)}, **self._headers(self.org_a)
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["on_hand"], "50.0000")

    def test_create_and_post_adjustment_via_api(self):
        self.client.force_authenticate(user=self.owner_a)
        create_response = self.client.post(
            "/api/v1/inventory/adjustments/",
            {
                "warehouse_id": str(self.warehouse.id),
                "adjustment_date": "2026-04-15",
                "reason": "physical_count",
                "lines": [{"item_id": str(self.product.id), "direction": "adjustment_out", "quantity": "5"}],
            },
            format="json",
            **self._headers(self.org_a),
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        adjustment_id = create_response.data["id"]

        post_response = self.client.post(
            f"/api/v1/inventory/adjustments/{adjustment_id}/post/", **self._headers(self.org_a)
        )
        self.assertEqual(post_response.status_code, 200, post_response.data)
        self.assertEqual(post_response.data["status"], "posted")

    def test_viewer_cannot_create_adjustment(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.post(
            "/api/v1/inventory/adjustments/",
            {
                "warehouse_id": str(self.warehouse.id),
                "adjustment_date": "2026-04-15",
                "reason": "physical_count",
                "lines": [{"item_id": str(self.product.id), "direction": "adjustment_out", "quantity": "5"}],
            },
            format="json",
            **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 403)

    def test_transfer_endpoint(self):
        self.client.force_authenticate(user=self.owner_a)
        with tenant_context(organization_id=self.org_a.id):
            branch = create_warehouse(organization=self.org_a, code="BRANCH", name="Branch")
        response = self.client.post(
            "/api/v1/inventory/transfers/",
            {
                "item_id": str(self.product.id), "from_warehouse_id": str(self.warehouse.id),
                "to_warehouse_id": str(branch.id), "quantity": "10",
            },
            format="json",
            **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 201, response.data)

    def test_missing_organization_header_fails_closed(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/inventory/warehouses/")
        self.assertEqual(response.status_code, 400)
