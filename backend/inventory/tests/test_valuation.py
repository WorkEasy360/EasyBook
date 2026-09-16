import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from inventory.models.stock_movement import MovementType
from inventory.selectors import get_stock_on_hand, get_weighted_average_cost
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


class AsOfCutoffTests(TestCase):
    """Regression: `as_of` is routinely a `date` (challan_date, invoice_date,
    bill_date are all DateFields) compared against `movement_date`, an aware
    DateTimeField. Django coerced the date to midnight, which EXCLUDED every
    movement made during that same day — so "the average cost as of the
    dispatch date" silently skipped that date's own receipts. Fixed in
    inventory/selectors.py::_as_of_cutoff by widening a date to end-of-day."""

    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Org", "asof@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            self.warehouse = create_warehouse(organization=self.org, code="MAIN", name="Main")
            self.unit = create_unit(organization=self.org, code="EA", name="Each")
            self.item = create_item(
                organization=self.org, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit,
                sku="W-1", track_inventory=True,
            )

    def test_same_day_movements_are_included_by_a_date_as_of(self):
        day = datetime.date(2026, 4, 5)
        with tenant_context(organization_id=self.org.id):
            record_stock_movement(
                organization=self.org, item=self.item, warehouse=self.warehouse,
                movement_type=MovementType.RECEIPT, quantity=Decimal("10"),
                unit_cost=Decimal("50.0000"), movement_date=day,
            )
            # Recorded at 09:30 on the same day, not midnight.
            record_stock_movement(
                organization=self.org, item=self.item, warehouse=self.warehouse,
                movement_type=MovementType.RECEIPT, quantity=Decimal("10"),
                unit_cost=Decimal("70.0000"),
                movement_date=datetime.datetime(2026, 4, 5, 9, 30),
            )

            on_hand = get_stock_on_hand(item=self.item, warehouse=self.warehouse, as_of=day)
            quantity, cost = get_weighted_average_cost(
                item=self.item, warehouse=self.warehouse, as_of=day
            )

        # Both receipts fall on `day`; a midnight cutoff would have seen only
        # the first (or neither) and reported a cost of 50, not 60.
        self.assertEqual(on_hand, Decimal("20"))
        self.assertEqual(quantity, Decimal("20"))
        self.assertEqual(cost, Decimal("60.0000"))

    def test_a_later_day_is_still_excluded(self):
        with tenant_context(organization_id=self.org.id):
            record_stock_movement(
                organization=self.org, item=self.item, warehouse=self.warehouse,
                movement_type=MovementType.RECEIPT, quantity=Decimal("10"),
                unit_cost=Decimal("50.0000"), movement_date=datetime.date(2026, 4, 5),
            )
            record_stock_movement(
                organization=self.org, item=self.item, warehouse=self.warehouse,
                movement_type=MovementType.RECEIPT, quantity=Decimal("10"),
                unit_cost=Decimal("90.0000"), movement_date=datetime.date(2026, 4, 6),
            )
            on_hand = get_stock_on_hand(
                item=self.item, warehouse=self.warehouse, as_of=datetime.date(2026, 4, 5)
            )
        self.assertEqual(on_hand, Decimal("10"))
