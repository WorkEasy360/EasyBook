
from django.db import IntegrityError
from django.test import TestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from core.exceptions import ApplicationError
from core.tenancy import clear_tenant_context, tenant_context
from core.tests.factories import make_org_with_owner
from items.models.item import Item, ItemType
from items.services.items import archive_item, create_item
from items.services.units import create_unit


class ItemTests(TestCase):
    def setUp(self):
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "item-owner-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "item-owner-b@example.com")
        with tenant_context(organization_id=self.org_a.id):
            self.unit = create_unit(organization=self.org_a, code="EA", name="Each")
            self.income = create_account(organization=self.org_a, code="4000", name="Sales", account_type=AccountType.INCOME)
            self.expense = create_account(organization=self.org_a, code="5000", name="COGS", account_type=AccountType.EXPENSE)
            self.asset = create_account(organization=self.org_a, code="1200", name="Inventory Asset", account_type=AccountType.ASSET)
        with tenant_context(organization_id=self.org_b.id):
            self.unit_b = create_unit(organization=self.org_b, code="EA", name="Each")
            self.income_b = create_account(organization=self.org_b, code="4000", name="Sales", account_type=AccountType.INCOME)

    def test_create_product(self):
        with tenant_context(organization_id=self.org_a.id):
            item = create_item(
                organization=self.org_a, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit,
                sku="WID-1", track_inventory=True, inventory_account=self.asset, cogs_account=self.expense,
                sales_account=self.income,
            )
        self.assertEqual(item.item_type, ItemType.PRODUCT)
        self.assertTrue(item.track_inventory)

    def test_create_service(self):
        with tenant_context(organization_id=self.org_a.id):
            item = create_item(
                organization=self.org_a, item_type=ItemType.SERVICE, name="Consulting", unit=self.unit,
                sales_account=self.income,
            )
        self.assertEqual(item.item_type, ItemType.SERVICE)
        self.assertFalse(item.track_inventory)

    def test_service_cannot_track_inventory(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError):
                create_item(
                    organization=self.org_a, item_type=ItemType.SERVICE, name="Consulting", unit=self.unit,
                    track_inventory=True,
                )

    def test_db_constraint_blocks_service_with_inventory_tracking(self):
        # belt-and-braces: even bypassing the service layer, the DB rejects it.
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(IntegrityError):
                Item.objects.create(
                    organization=self.org_a, item_type=ItemType.SERVICE, name="Bad", unit=self.unit,
                    track_inventory=True,
                )

    def test_sku_unique_per_organization(self):
        with tenant_context(organization_id=self.org_a.id):
            create_item(organization=self.org_a, item_type=ItemType.PRODUCT, name="A", unit=self.unit, sku="X-1")
            with self.assertRaises(IntegrityError):
                create_item(organization=self.org_a, item_type=ItemType.PRODUCT, name="B", unit=self.unit, sku="X-1")

    def test_blank_sku_allowed_multiple_times(self):
        with tenant_context(organization_id=self.org_a.id):
            create_item(organization=self.org_a, item_type=ItemType.PRODUCT, name="A", unit=self.unit)
            create_item(organization=self.org_a, item_type=ItemType.PRODUCT, name="B", unit=self.unit)

    def test_invalid_account_type_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError):
                create_item(
                    organization=self.org_a, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit,
                    sales_account=self.asset,
                )

    def test_cross_tenant_account_assignment_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError):
                create_item(
                    organization=self.org_a, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit,
                    sales_account=self.income_b,
                )

    def test_cross_tenant_unit_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError):
                create_item(organization=self.org_a, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit_b)

    def test_inactive_unit_rejected_for_new_item(self):
        with tenant_context(organization_id=self.org_a.id):
            from items.services.units import update_unit

            update_unit(unit=self.unit, is_active=False)
            with self.assertRaises(ApplicationError):
                create_item(organization=self.org_a, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit)

    def test_archive_item_deactivates(self):
        with tenant_context(organization_id=self.org_a.id):
            item = create_item(organization=self.org_a, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit)
            archived = archive_item(item=item)
        self.assertFalse(archived.is_active)

    def test_tenant_isolation(self):
        with tenant_context(organization_id=self.org_a.id):
            create_item(organization=self.org_a, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit)
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(list(Item.objects.all()), [])

    def test_no_tenant_context_fails_closed(self):
        with tenant_context(organization_id=self.org_a.id):
            create_item(organization=self.org_a, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit)
        clear_tenant_context()
        self.assertEqual(list(Item.objects.all()), [])
