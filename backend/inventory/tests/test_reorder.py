from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from core.tenancy import tenant_context
from core.tests.factories import make_org_with_owner
from inventory.models.stock_movement import MovementType
from inventory.selectors import get_low_stock_items
from inventory.services.movements import record_stock_movement
from inventory.services.warehouses import create_warehouse
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit


class ReorderTests(TestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Acme", "reorder-owner@example.com")
        with tenant_context(organization_id=self.org.id):
            self.unit = create_unit(organization=self.org, code="EA", name="Each")
            self.warehouse = create_warehouse(organization=self.org, code="MAIN", name="Main")
            self.low_item = create_item(
                organization=self.org, item_type=ItemType.PRODUCT, name="Low Stock Widget", unit=self.unit,
                track_inventory=True, reorder_level=Decimal("10"),
            )
            self.healthy_item = create_item(
                organization=self.org, item_type=ItemType.PRODUCT, name="Healthy Widget", unit=self.unit,
                track_inventory=True, reorder_level=Decimal("10"),
            )
            self.no_reorder_item = create_item(
                organization=self.org, item_type=ItemType.PRODUCT, name="Untracked Reorder", unit=self.unit,
                track_inventory=True, reorder_level=Decimal("0"),
            )
            record_stock_movement(
                organization=self.org, item=self.low_item, warehouse=self.warehouse,
                movement_type=MovementType.RECEIPT, quantity=Decimal("5"), unit_cost=Decimal("1"),
                movement_date=timezone.now(),
            )
            record_stock_movement(
                organization=self.org, item=self.healthy_item, warehouse=self.warehouse,
                movement_type=MovementType.RECEIPT, quantity=Decimal("50"), unit_cost=Decimal("1"),
                movement_date=timezone.now(),
            )

    def test_low_stock_detection_is_correct(self):
        with tenant_context(organization_id=self.org.id):
            low_stock = get_low_stock_items(organization=self.org)
        low_stock_item_ids = {entry["item"].id for entry in low_stock}
        self.assertIn(self.low_item.id, low_stock_item_ids)
        self.assertNotIn(self.healthy_item.id, low_stock_item_ids)
        self.assertNotIn(self.no_reorder_item.id, low_stock_item_ids)

    def test_low_stock_reports_correct_quantities(self):
        with tenant_context(organization_id=self.org.id):
            low_stock = get_low_stock_items(organization=self.org)
        entry = next(e for e in low_stock if e["item"].id == self.low_item.id)
        self.assertEqual(entry["on_hand"], Decimal("5"))
        self.assertEqual(entry["reorder_level"], Decimal("10"))
