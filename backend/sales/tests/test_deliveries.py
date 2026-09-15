import datetime
from decimal import Decimal

from django.test import TestCase

from core.exceptions import ApplicationError
from core.tenancy import clear_tenant_context, tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from inventory.models.stock_movement import MovementType, StockMovement
from inventory.selectors import get_stock_on_hand
from inventory.services.opening_stock import post_opening_stock
from inventory.services.warehouses import create_warehouse
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit
from sales.models.delivery import DeliveryChallan, DeliveryChallanStatus
from sales.models.sales_order import SalesOrderStatus
from sales.services.customers import create_customer
from sales.services.deliveries import cancel_delivery, create_delivery_challan, dispatch_delivery, mark_delivered
from sales.services.sales_orders import confirm_order, create_sales_order


class DeliveryChallanTests(TestCase):
    def setUp(self):
        self.org_a, self.user_a, _ = make_org_with_owner("Org A", "delivery-owner-a@example.com")
        self.org_b, self.user_b, _ = make_org_with_owner("Org B", "delivery-owner-b@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org_a.id):
            self.warehouse = create_warehouse(organization=self.org_a, code="MAIN", name="Main Warehouse")
            self.unit = create_unit(organization=self.org_a, code="EA", name="Each")
            self.item = create_item(
                organization=self.org_a, item_type=ItemType.PRODUCT, name="Widget", unit=self.unit,
                sku="WID-1", track_inventory=True,
            )
            self.service_item = create_item(
                organization=self.org_a, item_type=ItemType.SERVICE, name="Consulting", unit=self.unit,
            )
            post_opening_stock(
                organization=self.org_a, warehouse=self.warehouse, currency=self.currency,
                lines=[{"item": self.item, "quantity": Decimal("100"), "unit_cost": Decimal("10.00")}],
                opening_date=datetime.date(2026, 4, 1),
            )
            self.customer = create_customer(
                organization=self.org_a, customer_code="CUST-1", display_name="Acme", currency=self.currency
            )
        with tenant_context(organization_id=self.org_b.id):
            self.warehouse_b = create_warehouse(organization=self.org_b, code="MAIN", name="Main")
            self.unit_b = create_unit(organization=self.org_b, code="EA", name="Each")
            self.customer_b = create_customer(
                organization=self.org_b, customer_code="CUST-1", display_name="Beta", currency=self.currency
            )

    def _lines(self, item=None, quantity=Decimal("10"), **kwargs):
        return [{"item": item or self.item, "quantity": quantity, **kwargs}]

    def test_create_draft_challan(self):
        with tenant_context(organization_id=self.org_a.id):
            challan = create_delivery_challan(
                organization=self.org_a, customer=self.customer, warehouse=self.warehouse,
                challan_date="2026-04-05", lines=self._lines(),
            )
            self.assertEqual(challan.lines.count(), 1)
        self.assertEqual(challan.status, DeliveryChallanStatus.DRAFT)
        self.assertTrue(challan.challan_number.startswith("DC-"))

    def test_service_item_rejected_on_challan(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError):
                create_delivery_challan(
                    organization=self.org_a, customer=self.customer, warehouse=self.warehouse,
                    challan_date="2026-04-05", lines=self._lines(item=self.service_item),
                )

    def test_dispatch_creates_stock_issue_movement(self):
        with tenant_context(organization_id=self.org_a.id):
            challan = create_delivery_challan(
                organization=self.org_a, customer=self.customer, warehouse=self.warehouse,
                challan_date="2026-04-05", lines=self._lines(quantity=Decimal("10")),
            )
            dispatch_delivery(challan_id=challan.id, organization=self.org_a)
            on_hand = get_stock_on_hand(item=self.item, warehouse=self.warehouse)
            self.assertEqual(on_hand, Decimal("90"))
            issue_movements = StockMovement.objects.filter(
                movement_type=MovementType.ISSUE, source_type="sales.DeliveryChallan", source_id=str(challan.id)
            )
            self.assertEqual(issue_movements.count(), 1)
            self.assertEqual(issue_movements.first().unit_cost, Decimal("10.0000"))

    def test_dispatch_sets_status_and_timestamp(self):
        with tenant_context(organization_id=self.org_a.id):
            challan = create_delivery_challan(
                organization=self.org_a, customer=self.customer, warehouse=self.warehouse,
                challan_date="2026-04-05", lines=self._lines(),
            )
            dispatched = dispatch_delivery(challan_id=challan.id, organization=self.org_a)
        self.assertEqual(dispatched.status, DeliveryChallanStatus.DISPATCHED)
        self.assertIsNotNone(dispatched.dispatched_at)

    def test_duplicate_dispatch_prevented(self):
        with tenant_context(organization_id=self.org_a.id):
            challan = create_delivery_challan(
                organization=self.org_a, customer=self.customer, warehouse=self.warehouse,
                challan_date="2026-04-05", lines=self._lines(quantity=Decimal("10")),
            )
            dispatch_delivery(challan_id=challan.id, organization=self.org_a)
            # Idempotent no-op, like accounting.post_journal — not a second issue.
            dispatch_delivery(challan_id=challan.id, organization=self.org_a)
            issue_movements = StockMovement.objects.filter(
                movement_type=MovementType.ISSUE, source_type="sales.DeliveryChallan", source_id=str(challan.id)
            )
            self.assertEqual(issue_movements.count(), 1)
            on_hand = get_stock_on_hand(item=self.item, warehouse=self.warehouse)
            self.assertEqual(on_hand, Decimal("90"))

    def test_insufficient_stock_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            challan = create_delivery_challan(
                organization=self.org_a, customer=self.customer, warehouse=self.warehouse,
                challan_date="2026-04-05", lines=self._lines(quantity=Decimal("1000")),
            )
            with self.assertRaises(ApplicationError):
                dispatch_delivery(challan_id=challan.id, organization=self.org_a)
            self.assertEqual(get_stock_on_hand(item=self.item, warehouse=self.warehouse), Decimal("100"))

    def test_rollback_atomic_on_partial_failure(self):
        # Two lines: first has enough stock, second doesn't — the whole
        # dispatch must roll back, leaving zero movements for this challan.
        with tenant_context(organization_id=self.org_a.id):
            item2 = create_item(
                organization=self.org_a, item_type=ItemType.PRODUCT, name="Gadget", unit=self.unit,
                sku="GAD-1", track_inventory=True,
            )
            challan = create_delivery_challan(
                organization=self.org_a, customer=self.customer, warehouse=self.warehouse,
                challan_date="2026-04-05",
                lines=[
                    {"item": self.item, "quantity": Decimal("10")},
                    {"item": item2, "quantity": Decimal("5")},
                ],
            )
            with self.assertRaises(ApplicationError):
                dispatch_delivery(challan_id=challan.id, organization=self.org_a)
            self.assertEqual(get_stock_on_hand(item=self.item, warehouse=self.warehouse), Decimal("100"))
            challan.refresh_from_db()
            self.assertEqual(challan.status, DeliveryChallanStatus.DRAFT)
            self.assertEqual(
                StockMovement.objects.filter(source_type="sales.DeliveryChallan", source_id=str(challan.id)).count(), 0
            )

    def test_mark_delivered_after_dispatch(self):
        with tenant_context(organization_id=self.org_a.id):
            challan = create_delivery_challan(
                organization=self.org_a, customer=self.customer, warehouse=self.warehouse,
                challan_date="2026-04-05", lines=self._lines(),
            )
            dispatch_delivery(challan_id=challan.id, organization=self.org_a)
            delivered = mark_delivered(challan_id=challan.id, organization=self.org_a)
        self.assertEqual(delivered.status, DeliveryChallanStatus.DELIVERED)

    def test_cannot_mark_delivered_before_dispatch(self):
        with tenant_context(organization_id=self.org_a.id):
            challan = create_delivery_challan(
                organization=self.org_a, customer=self.customer, warehouse=self.warehouse,
                challan_date="2026-04-05", lines=self._lines(),
            )
            with self.assertRaises(ApplicationError):
                mark_delivered(challan_id=challan.id, organization=self.org_a)

    def test_cancel_draft_challan(self):
        with tenant_context(organization_id=self.org_a.id):
            challan = create_delivery_challan(
                organization=self.org_a, customer=self.customer, warehouse=self.warehouse,
                challan_date="2026-04-05", lines=self._lines(),
            )
            cancelled = cancel_delivery(challan_id=challan.id, organization=self.org_a)
        self.assertEqual(cancelled.status, DeliveryChallanStatus.CANCELLED)

    def test_cannot_cancel_dispatched_challan(self):
        with tenant_context(organization_id=self.org_a.id):
            challan = create_delivery_challan(
                organization=self.org_a, customer=self.customer, warehouse=self.warehouse,
                challan_date="2026-04-05", lines=self._lines(),
            )
            dispatch_delivery(challan_id=challan.id, organization=self.org_a)
            with self.assertRaises(ApplicationError):
                cancel_delivery(challan_id=challan.id, organization=self.org_a)

    def test_fulfillment_against_sales_order_marks_fulfilled(self):
        with tenant_context(organization_id=self.org_a.id):
            order = create_sales_order(
                organization=self.org_a, customer=self.customer, order_date="2026-04-01",
                lines=[{"item": self.item, "quantity": Decimal("10"), "unit_price": Decimal("50.00")}],
            )
            confirm_order(order_id=order.id, organization=self.org_a)
            order.refresh_from_db()
            order_line = order.lines.first()
            challan = create_delivery_challan(
                organization=self.org_a, customer=self.customer, warehouse=self.warehouse,
                challan_date="2026-04-05", source_sales_order=order,
                lines=[{"item": self.item, "quantity": Decimal("10"), "source_order_line": order_line}],
            )
            dispatch_delivery(challan_id=challan.id, organization=self.org_a)
            order.refresh_from_db()
        self.assertEqual(order.status, SalesOrderStatus.FULFILLED)

    def test_partial_fulfillment_marks_partially_fulfilled(self):
        with tenant_context(organization_id=self.org_a.id):
            order = create_sales_order(
                organization=self.org_a, customer=self.customer, order_date="2026-04-01",
                lines=[{"item": self.item, "quantity": Decimal("10"), "unit_price": Decimal("50.00")}],
            )
            confirm_order(order_id=order.id, organization=self.org_a)
            order.refresh_from_db()
            order_line = order.lines.first()
            challan = create_delivery_challan(
                organization=self.org_a, customer=self.customer, warehouse=self.warehouse,
                challan_date="2026-04-05", source_sales_order=order,
                lines=[{"item": self.item, "quantity": Decimal("4"), "source_order_line": order_line}],
            )
            dispatch_delivery(challan_id=challan.id, organization=self.org_a)
            order.refresh_from_db()
        self.assertEqual(order.status, SalesOrderStatus.PARTIALLY_FULFILLED)

    def test_over_fulfillment_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            order = create_sales_order(
                organization=self.org_a, customer=self.customer, order_date="2026-04-01",
                lines=[{"item": self.item, "quantity": Decimal("10"), "unit_price": Decimal("50.00")}],
            )
            confirm_order(order_id=order.id, organization=self.org_a)
            order.refresh_from_db()
            order_line = order.lines.first()
            with self.assertRaises(ApplicationError) as ctx:
                create_delivery_challan(
                    organization=self.org_a, customer=self.customer, warehouse=self.warehouse,
                    challan_date="2026-04-05", source_sales_order=order,
                    lines=[{"item": self.item, "quantity": Decimal("11"), "source_order_line": order_line}],
                )
            self.assertEqual(ctx.exception.get_codes(), "over_fulfillment")

    def test_cannot_deliver_against_unconfirmed_order(self):
        with tenant_context(organization_id=self.org_a.id):
            order = create_sales_order(
                organization=self.org_a, customer=self.customer, order_date="2026-04-01",
                lines=[{"item": self.item, "quantity": Decimal("10"), "unit_price": Decimal("50.00")}],
            )
            with self.assertRaises(ApplicationError):
                create_delivery_challan(
                    organization=self.org_a, customer=self.customer, warehouse=self.warehouse,
                    challan_date="2026-04-05", source_sales_order=order, lines=self._lines(),
                )

    def test_cross_org_warehouse_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError):
                create_delivery_challan(
                    organization=self.org_a, customer=self.customer, warehouse=self.warehouse_b,
                    challan_date="2026-04-05", lines=self._lines(),
                )

    def test_tenant_isolation(self):
        with tenant_context(organization_id=self.org_a.id):
            create_delivery_challan(
                organization=self.org_a, customer=self.customer, warehouse=self.warehouse,
                challan_date="2026-04-05", lines=self._lines(),
            )
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(list(DeliveryChallan.objects.all()), [])

    def test_no_tenant_context_fails_closed(self):
        with tenant_context(organization_id=self.org_a.id):
            create_delivery_challan(
                organization=self.org_a, customer=self.customer, warehouse=self.warehouse,
                challan_date="2026-04-05", lines=self._lines(),
            )
        clear_tenant_context()
        self.assertEqual(list(DeliveryChallan.objects.all()), [])
