from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from core.tenancy import tenant_context
from core.tests.factories import make_org_with_owner
from inventory.models.stock_movement import MovementType
from inventory.selectors import get_weighted_average_cost
from inventory.services.movements import record_stock_movement
from inventory.services.warehouses import create_warehouse
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit


class WeightedAverageValuationTests(TestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Acme", "valuation-owner@example.com")
        with tenant_context(organization_id=self.org.id):
            self.unit = create_unit(organization=self.org, code="EA", name="Each")
            self.warehouse = create_warehouse(organization=self.org, code="MAIN", name="Main")
            self.product = create_item(
                organization=self.org, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit,
                track_inventory=True,
            )

    def _receive(self, quantity, unit_cost):
        record_stock_movement(
            organization=self.org, item=self.product, warehouse=self.warehouse, movement_type=MovementType.RECEIPT,
            quantity=Decimal(quantity), unit_cost=Decimal(unit_cost), movement_date=timezone.now(),
        )

    def _issue(self, quantity):
        record_stock_movement(
            organization=self.org, item=self.product, warehouse=self.warehouse, movement_type=MovementType.ISSUE,
            quantity=Decimal(quantity), movement_date=timezone.now(),
        )

    def test_single_receipt_sets_average_cost(self):
        with tenant_context(organization_id=self.org.id):
            self._receive("10", "20.00")
            qty, cost = get_weighted_average_cost(item=self.product, warehouse=self.warehouse)
        self.assertEqual(qty, Decimal("10"))
        self.assertEqual(cost, Decimal("20.00"))

    def test_multiple_receipts_blend_average_cost(self):
        with tenant_context(organization_id=self.org.id):
            self._receive("10", "20.00")   # value = 200
            self._receive("10", "30.00")   # value = 300 -> total 500 / 20 = 25
            qty, cost = get_weighted_average_cost(item=self.product, warehouse=self.warehouse)
        self.assertEqual(qty, Decimal("20"))
        self.assertEqual(cost, Decimal("25"))

    def test_issue_does_not_alter_average_cost(self):
        with tenant_context(organization_id=self.org.id):
            self._receive("10", "20.00")
            self._receive("10", "30.00")
            self._issue("5")
            qty, cost = get_weighted_average_cost(item=self.product, warehouse=self.warehouse)
        self.assertEqual(qty, Decimal("15"))
        self.assertEqual(cost, Decimal("25"))

    def test_zero_stock_edge_case_defaults_to_zero_cost(self):
        with tenant_context(organization_id=self.org.id):
            qty, cost = get_weighted_average_cost(item=self.product, warehouse=self.warehouse)
        self.assertEqual(qty, Decimal("0"))
        self.assertEqual(cost, Decimal("0"))

    def test_receipt_after_full_issue_resets_cost_basis(self):
        with tenant_context(organization_id=self.org.id):
            self._receive("10", "20.00")
            self._issue("10")
            self._receive("5", "50.00")
            qty, cost = get_weighted_average_cost(item=self.product, warehouse=self.warehouse)
        self.assertEqual(qty, Decimal("5"))
        self.assertEqual(cost, Decimal("50.00"))
