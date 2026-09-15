from django.db import connection
from django.test import TransactionTestCase

from core.tenancy import clear_tenant_context, tenant_context
from core.tests.factories import make_org_with_owner
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit


class RawSQLRLSTests(TransactionTestCase):
    def setUp(self):
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "items-rls-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "items-rls-b@example.com")
        with tenant_context(organization_id=self.org_a.id):
            self.unit_a = create_unit(organization=self.org_a, code="EA", name="Each")
            self.item_a = create_item(organization=self.org_a, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit_a)
        with tenant_context(organization_id=self.org_b.id):
            self.unit_b = create_unit(organization=self.org_b, code="EA", name="Each")
            create_item(organization=self.org_b, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit_b)

    def test_rls_blocks_direct_sql_without_tenant_context(self):
        clear_tenant_context()
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM items_item")
            rows = cursor.fetchall()
        self.assertEqual(rows, [])

    def test_rls_direct_sql_scoped_to_org_a_cannot_see_org_b(self):
        with tenant_context(organization_id=self.org_a.id):
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM items_item")
                ids = {row[0] for row in cursor.fetchall()}
        self.assertEqual(ids, {self.item_a.id})
