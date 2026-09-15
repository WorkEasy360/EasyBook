from django.db import IntegrityError
from django.test import TestCase

from core.tenancy import clear_tenant_context, tenant_context
from core.tests.factories import make_org_with_owner
from inventory.models.warehouse import Warehouse
from inventory.services.warehouses import archive_warehouse, create_warehouse, update_warehouse


class WarehouseTests(TestCase):
    def setUp(self):
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "wh-owner-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "wh-owner-b@example.com")

    def test_create_warehouse(self):
        with tenant_context(organization_id=self.org_a.id):
            warehouse = create_warehouse(organization=self.org_a, code="MAIN", name="Main Warehouse")
        self.assertEqual(warehouse.code, "MAIN")

    def test_code_unique_per_organization(self):
        with tenant_context(organization_id=self.org_a.id):
            create_warehouse(organization=self.org_a, code="MAIN", name="Main")
            with self.assertRaises(IntegrityError):
                create_warehouse(organization=self.org_a, code="MAIN", name="Main Again")

    def test_same_code_allowed_across_organizations(self):
        with tenant_context(organization_id=self.org_a.id):
            create_warehouse(organization=self.org_a, code="MAIN", name="Main")
        with tenant_context(organization_id=self.org_b.id):
            warehouse_b = create_warehouse(organization=self.org_b, code="MAIN", name="Main")
        self.assertEqual(warehouse_b.code, "MAIN")

    def test_only_one_default_warehouse_per_organization(self):
        with tenant_context(organization_id=self.org_a.id):
            first = create_warehouse(organization=self.org_a, code="A", name="A", is_default=True)
            second = create_warehouse(organization=self.org_a, code="B", name="B", is_default=True)
            first.refresh_from_db()
        self.assertFalse(first.is_default)
        self.assertTrue(second.is_default)

    def test_setting_default_via_update_unsets_previous(self):
        with tenant_context(organization_id=self.org_a.id):
            first = create_warehouse(organization=self.org_a, code="A", name="A", is_default=True)
            second = create_warehouse(organization=self.org_a, code="B", name="B")
            update_warehouse(warehouse=second, is_default=True)
            first.refresh_from_db()
        self.assertFalse(first.is_default)

    def test_default_warehouses_independent_across_organizations(self):
        with tenant_context(organization_id=self.org_a.id):
            create_warehouse(organization=self.org_a, code="MAIN", name="Main", is_default=True)
        with tenant_context(organization_id=self.org_b.id):
            warehouse_b = create_warehouse(organization=self.org_b, code="MAIN", name="Main", is_default=True)
        self.assertTrue(warehouse_b.is_default)

    def test_archive_warehouse_deactivates_and_clears_default(self):
        with tenant_context(organization_id=self.org_a.id):
            warehouse = create_warehouse(organization=self.org_a, code="MAIN", name="Main", is_default=True)
            archived = archive_warehouse(warehouse=warehouse)
        self.assertFalse(archived.is_active)
        self.assertFalse(archived.is_default)

    def test_tenant_isolation(self):
        with tenant_context(organization_id=self.org_a.id):
            create_warehouse(organization=self.org_a, code="MAIN", name="Main")
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(list(Warehouse.objects.all()), [])

    def test_no_tenant_context_fails_closed(self):
        with tenant_context(organization_id=self.org_a.id):
            create_warehouse(organization=self.org_a, code="MAIN", name="Main")
        clear_tenant_context()
        self.assertEqual(list(Warehouse.objects.all()), [])
