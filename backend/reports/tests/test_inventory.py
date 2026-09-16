import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from accounts.models import FiscalYear
from core.tenancy import tenant_context
from core.tests.factories import make_org_with_owner
from inventory.models.stock_movement import MovementType
from inventory.services.adjustments import create_draft_adjustment, post_stock_adjustment
from inventory.services.movements import record_stock_movement
from inventory.services.warehouses import create_warehouse
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit
from reports.selectors.inventory import (
    get_inventory_movement_queryset,
    get_inventory_valuation,
    get_low_stock_report,
    get_stock_adjustment_queryset,
    get_stock_summary,
)


class InventoryReportsTests(TestCase):
    def setUp(self):
        self.org, self.user, _ = make_org_with_owner("Acme", "inv-report-owner@example.com")
        with tenant_context(organization_id=self.org.id):
            self.unit = create_unit(organization=self.org, code="EA", name="Each")
            self.warehouse_main = create_warehouse(organization=self.org, code="MAIN", name="Main")
            self.warehouse_secondary = create_warehouse(organization=self.org, code="SEC", name="Secondary")
            self.widget = create_item(
                organization=self.org, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit,
                sku="WID-1", track_inventory=True, reorder_level=Decimal("5"),
            )
            self.gadget = create_item(
                organization=self.org, item_type=ItemType.PRODUCT, name="Gadget", unit=self.unit,
                sku="GAD-1", track_inventory=True,
            )
            record_stock_movement(
                organization=self.org, item=self.widget, warehouse=self.warehouse_main,
                movement_type=MovementType.RECEIPT, quantity=Decimal("10"), unit_cost=Decimal("20.00"),
                movement_date=timezone.now(),
            )
            record_stock_movement(
                organization=self.org, item=self.widget, warehouse=self.warehouse_main,
                movement_type=MovementType.ISSUE, quantity=Decimal("7"), movement_date=timezone.now(),
            )
            record_stock_movement(
                organization=self.org, item=self.gadget, warehouse=self.warehouse_secondary,
                movement_type=MovementType.RECEIPT, quantity=Decimal("50"), unit_cost=Decimal("5.00"),
                movement_date=timezone.now(),
            )

    def test_stock_summary_reflects_weighted_average_valuation(self):
        with tenant_context(organization_id=self.org.id):
            rows = get_stock_summary(organization=self.org)
        by_item = {row["item_id"]: row for row in rows}
        self.assertEqual(by_item[self.widget.id]["quantity_on_hand"], Decimal("3"))
        self.assertEqual(by_item[self.widget.id]["average_cost"], Decimal("20.00"))
        self.assertEqual(by_item[self.widget.id]["inventory_value"], Decimal("60.00"))
        self.assertEqual(by_item[self.gadget.id]["quantity_on_hand"], Decimal("50"))

    def test_stock_summary_filters_by_warehouse(self):
        with tenant_context(organization_id=self.org.id):
            rows = get_stock_summary(organization=self.org, warehouse=self.warehouse_secondary)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["item_id"], self.gadget.id)

    def test_inventory_valuation_totals(self):
        with tenant_context(organization_id=self.org.id):
            result = get_inventory_valuation(organization=self.org)
        # Widget: 3 * 20 = 60. Gadget: 50 * 5 = 250. Total 310.
        self.assertEqual(result["total_value"], Decimal("310.00"))

    def test_inventory_movement_report_filters_and_orders(self):
        with tenant_context(organization_id=self.org.id):
            qs = get_inventory_movement_queryset(organization=self.org, item=self.widget)
        self.assertEqual(qs.count(), 2)
        types = [m.movement_type for m in qs]
        self.assertEqual(types, [MovementType.RECEIPT, MovementType.ISSUE])

    def test_low_stock_report(self):
        with tenant_context(organization_id=self.org.id):
            rows = get_low_stock_report(organization=self.org)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["item_id"], self.widget.id)
        self.assertEqual(rows[0]["on_hand"], Decimal("3"))

    def test_stock_adjustment_report(self):
        with tenant_context(organization_id=self.org.id):
            FiscalYear.objects.create(
                organization=self.org, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            contra = create_account(
                organization=self.org, code="5900", name="Inventory Shrinkage", account_type=AccountType.EXPENSE
            )
            adjustment = create_draft_adjustment(
                organization=self.org, warehouse=self.warehouse_main, adjustment_date="2026-04-01",
                reason="shrinkage", contra_account=contra,
                lines=[{"item": self.widget, "direction": "adjustment_out", "quantity": Decimal("1")}],
            )
            post_stock_adjustment(adjustment_id=adjustment.id, organization=self.org)
            qs = get_stock_adjustment_queryset(organization=self.org)
        self.assertEqual(qs.count(), 1)

    def test_inventory_reports_are_tenant_scoped(self):
        other_org, _other_user, _ = make_org_with_owner("Other Org", "inv-report-other@example.com")
        with tenant_context(organization_id=other_org.id):
            other_rows = get_stock_summary(organization=other_org)
        self.assertEqual(other_rows, [])

        with tenant_context(organization_id=self.org.id):
            own_rows = get_stock_summary(organization=self.org)
        self.assertEqual(len(own_rows), 2)
