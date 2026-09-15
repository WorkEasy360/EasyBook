from rest_framework.test import APITestCase

from authz.roles import Role
from core.tenancy import tenant_context
from core.tests.factories import make_membership, make_org_with_owner, make_user
from items.models.item import ItemType
from items.services.units import create_unit


class ItemsAPITests(APITestCase):
    def setUp(self):
        self.org_a, self.owner_a, _ = make_org_with_owner("Org A", "api-items-owner-a@example.com")
        self.org_b, self.owner_b, _ = make_org_with_owner("Org B", "api-items-owner-b@example.com")
        self.viewer = make_user("api-items-viewer@example.com")
        make_membership(self.org_a, self.viewer, role=Role.VIEWER)

        with tenant_context(organization_id=self.org_a.id):
            self.unit = create_unit(organization=self.org_a, code="EA", name="Each")
        with tenant_context(organization_id=self.org_b.id):
            self.unit_b = create_unit(organization=self.org_b, code="EA", name="Each")

    def _headers(self, org):
        return {"HTTP_X_ORGANIZATION_ID": str(org.id)}

    def test_owner_can_create_item(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.post(
            "/api/v1/items/",
            {"item_type": ItemType.PRODUCT, "name": "Widget", "unit": str(self.unit.id)},
            **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 201, response.data)

    def test_viewer_cannot_create_item(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.post(
            "/api/v1/items/",
            {"item_type": ItemType.PRODUCT, "name": "Widget", "unit": str(self.unit.id)},
            **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 403)

    def test_viewer_can_list_items(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.get("/api/v1/items/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 200)

    def test_cross_org_unit_rejected_via_api(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.post(
            "/api/v1/items/",
            {"item_type": ItemType.PRODUCT, "name": "Widget", "unit": str(self.unit_b.id)},
            **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 400)

    def test_unit_list_is_tenant_scoped(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/items/units/", **self._headers(self.org_a))
        self.assertEqual(response.status_code, 200)
        ids = {u["id"] for u in response.data["results"]}
        self.assertIn(str(self.unit.id), ids)
        self.assertNotIn(str(self.unit_b.id), ids)

    def test_non_member_cannot_access_other_org_items(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/items/", **self._headers(self.org_b))
        self.assertEqual(response.status_code, 403)

    def test_missing_organization_header_fails_closed(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/items/")
        self.assertEqual(response.status_code, 400)

    def test_archive_item_via_patch(self):
        self.client.force_authenticate(user=self.owner_a)
        create_response = self.client.post(
            "/api/v1/items/",
            {"item_type": ItemType.PRODUCT, "name": "Widget", "unit": str(self.unit.id)},
            **self._headers(self.org_a),
        )
        item_id = create_response.data["id"]
        patch_response = self.client.patch(
            f"/api/v1/items/{item_id}/", {"is_active": False}, format="json", **self._headers(self.org_a)
        )
        self.assertEqual(patch_response.status_code, 200)
        self.assertFalse(patch_response.data["is_active"])
