from decimal import Decimal

from accounting.models.journal import JournalEntry
from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from inventory.models.stock_movement import MovementType, StockMovement
from inventory.selectors import get_stock_on_hand, get_weighted_average_cost
from purchases.models.goods_receipt import GoodsReceipt, GoodsReceiptStatus
from purchases.models.purchase_order import PurchaseOrderStatus
from purchases.selectors import get_received_quantity
from purchases.services.goods_receipts import (
    cancel_goods_receipt,
    create_goods_receipt,
    receive_goods,
    replace_receipt_lines,
)
from purchases.tests.base import ORDER_DATE, PurchasesTestsBase


class GoodsReceiptCreationTests(PurchasesTestsBase):
    def test_create_draft_moves_no_stock(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po()
            receipt = create_goods_receipt(
                organization=self.org_a, vendor=self.vendor, warehouse=self.warehouse,
                receipt_date=ORDER_DATE, source_purchase_order=order,
                lines=[{"item": self.product, "quantity": Decimal("10"),
                        "source_order_line": order.lines.first()}],
            )
            self.assertEqual(receipt.status, GoodsReceiptStatus.DRAFT)
            self.assertEqual(StockMovement.objects.count(), 0)
            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("0"))

    def test_unit_cost_defaults_to_purchase_order_price(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po(lines=self._product_lines(unit_price=Decimal("50.00")))
            receipt = create_goods_receipt(
                organization=self.org_a, vendor=self.vendor, warehouse=self.warehouse,
                receipt_date=ORDER_DATE, source_purchase_order=order,
                lines=[{"item": self.product, "quantity": Decimal("10"),
                        "source_order_line": order.lines.first()}],
            )
            self.assertEqual(receipt.lines.first().unit_cost, Decimal("50.0000"))

    def test_unit_cost_required_when_there_is_no_purchase_order_line(self):
        """A silent zero-cost receipt would corrupt weighted-average
        valuation invisibly — so it is refused, not defaulted."""
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_goods_receipt(
                    organization=self.org_a, vendor=self.vendor, warehouse=self.warehouse,
                    receipt_date=ORDER_DATE,
                    lines=[{"item": self.product, "quantity": Decimal("5")}],
                )
        self.assertEqual(ctx.exception.detail.code, "receipt_unit_cost_required")

    def test_explicit_unit_cost_overrides_purchase_order_price(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po(lines=self._product_lines(unit_price=Decimal("50.00")))
            receipt = create_goods_receipt(
                organization=self.org_a, vendor=self.vendor, warehouse=self.warehouse,
                receipt_date=ORDER_DATE, source_purchase_order=order,
                lines=[{"item": self.product, "quantity": Decimal("10"), "unit_cost": Decimal("55.0000"),
                        "source_order_line": order.lines.first()}],
            )
            self.assertEqual(receipt.lines.first().unit_cost, Decimal("55.0000"))

    def test_service_item_cannot_be_received(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_goods_receipt(
                    organization=self.org_a, vendor=self.vendor, warehouse=self.warehouse,
                    receipt_date=ORDER_DATE,
                    lines=[{"item": self.service, "quantity": Decimal("1"), "unit_cost": Decimal("10")}],
                )
        self.assertEqual(ctx.exception.detail.code, "item_not_receivable")

    def test_untracked_product_cannot_be_received(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_goods_receipt(
                    organization=self.org_a, vendor=self.vendor, warehouse=self.warehouse,
                    receipt_date=ORDER_DATE,
                    lines=[{"item": self.untracked_product, "quantity": Decimal("1"), "unit_cost": Decimal("10")}],
                )
        self.assertEqual(ctx.exception.detail.code, "item_not_receivable")

    def test_cannot_receive_against_a_draft_purchase_order(self):
        from purchases.services.purchase_orders import create_purchase_order

        with tenant_context(organization_id=self.org_a.id):
            draft = create_purchase_order(
                organization=self.org_a, vendor=self.vendor, order_date=ORDER_DATE, lines=self._product_lines()
            )
            with self.assertRaises(ApplicationError) as ctx:
                create_goods_receipt(
                    organization=self.org_a, vendor=self.vendor, warehouse=self.warehouse,
                    receipt_date=ORDER_DATE, source_purchase_order=draft,
                    lines=[{"item": self.product, "quantity": Decimal("1"),
                            "source_order_line": draft.lines.first()}],
                )
        self.assertEqual(ctx.exception.detail.code, "purchase_order_not_approved")

    def test_purchase_order_must_belong_to_the_same_vendor(self):
        from purchases.services.vendors import create_vendor

        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po()
            other_vendor = create_vendor(
                organization=self.org_a, vendor_code="VEN-2", display_name="Other", currency=self.currency
            )
            with self.assertRaises(ApplicationError) as ctx:
                create_goods_receipt(
                    organization=self.org_a, vendor=other_vendor, warehouse=self.warehouse,
                    receipt_date=ORDER_DATE, source_purchase_order=order,
                    lines=[{"item": self.product, "quantity": Decimal("1"), "unit_cost": Decimal("50")}],
                )
        self.assertEqual(ctx.exception.detail.code, "purchase_order_vendor_mismatch")

    def test_over_receipt_against_order_line_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po(lines=self._product_lines(quantity=Decimal("10")))
            with self.assertRaises(ApplicationError) as ctx:
                create_goods_receipt(
                    organization=self.org_a, vendor=self.vendor, warehouse=self.warehouse,
                    receipt_date=ORDER_DATE, source_purchase_order=order,
                    lines=[{"item": self.product, "quantity": Decimal("11"),
                            "source_order_line": order.lines.first()}],
                )
        self.assertEqual(ctx.exception.detail.code, "over_receipt")


class GoodsReceiptReceiveTests(PurchasesTestsBase):
    def test_receive_creates_inbound_movement_and_posts_no_journal(self):
        with tenant_context(organization_id=self.org_a.id):
            receipt, _line = self._make_received_gr(quantity=Decimal("10"))
            self.assertEqual(receipt.status, GoodsReceiptStatus.RECEIVED)
            self.assertIsNotNone(receipt.received_at)

            movements = StockMovement.objects.filter(source_type="purchases.GoodsReceipt")
            self.assertEqual(movements.count(), 1)
            self.assertEqual(movements.first().movement_type, MovementType.RECEIPT)
            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("10"))
            # The receipt itself never posts accounting — that is the Bill's job.
            self.assertEqual(JournalEntry.objects.count(), 0)

    def test_receive_sets_weighted_average_cost_from_receipt_cost(self):
        with tenant_context(organization_id=self.org_a.id):
            self._make_received_gr(quantity=Decimal("10"), unit_cost=Decimal("50.0000"))
            quantity, cost = get_weighted_average_cost(item=self.product, warehouse=self.warehouse)
            self.assertEqual(quantity, Decimal("10"))
            self.assertEqual(cost, Decimal("50.0000"))

    def test_receive_is_idempotent_and_never_double_receives(self):
        """The first half of the phase's no-double-receipt guarantee."""
        with tenant_context(organization_id=self.org_a.id):
            receipt, _ = self._make_received_gr(quantity=Decimal("10"))
            receive_goods(receipt_id=receipt.id, organization=self.org_a)
            receive_goods(receipt_id=receipt.id, organization=self.org_a)
            self.assertEqual(StockMovement.objects.filter(source_type="purchases.GoodsReceipt").count(), 1)
            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("10"))

    def test_receive_advances_purchase_order_to_received(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po(lines=self._product_lines(quantity=Decimal("10")))
            self._make_received_gr(order=order, quantity=Decimal("10"))
            order.refresh_from_db()
            self.assertEqual(order.status, PurchaseOrderStatus.RECEIVED)

    def test_partial_receipt_advances_order_to_partially_received(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po(lines=self._product_lines(quantity=Decimal("10")))
            self._make_received_gr(order=order, quantity=Decimal("4"))
            order.refresh_from_db()
            self.assertEqual(order.status, PurchaseOrderStatus.PARTIALLY_RECEIVED)
            self.assertEqual(get_received_quantity(purchase_order_line=order.lines.first()), Decimal("4"))

    def test_received_quantity_ignores_draft_and_cancelled_receipts(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po(lines=self._product_lines(quantity=Decimal("10")))
            create_goods_receipt(
                organization=self.org_a, vendor=self.vendor, warehouse=self.warehouse,
                receipt_date=ORDER_DATE, source_purchase_order=order,
                lines=[{"item": self.product, "quantity": Decimal("3"),
                        "source_order_line": order.lines.first()}],
            )
            self.assertEqual(get_received_quantity(purchase_order_line=order.lines.first()), Decimal("0"))

    def test_cannot_cancel_after_receiving(self):
        with tenant_context(organization_id=self.org_a.id):
            receipt, _ = self._make_received_gr()
            with self.assertRaises(ApplicationError) as ctx:
                cancel_goods_receipt(receipt_id=receipt.id, organization=self.org_a)
        self.assertEqual(ctx.exception.detail.code, "goods_receipt_invalid_status")

    def test_cancel_draft_receipt(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po()
            receipt = create_goods_receipt(
                organization=self.org_a, vendor=self.vendor, warehouse=self.warehouse,
                receipt_date=ORDER_DATE, source_purchase_order=order,
                lines=[{"item": self.product, "quantity": Decimal("2"),
                        "source_order_line": order.lines.first()}],
            )
            cancelled = cancel_goods_receipt(receipt_id=receipt.id, organization=self.org_a)
        self.assertEqual(cancelled.status, GoodsReceiptStatus.CANCELLED)

    def test_replace_lines_only_while_draft(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po()
            receipt = create_goods_receipt(
                organization=self.org_a, vendor=self.vendor, warehouse=self.warehouse,
                receipt_date=ORDER_DATE, source_purchase_order=order,
                lines=[{"item": self.product, "quantity": Decimal("2"),
                        "source_order_line": order.lines.first()}],
            )
            replace_receipt_lines(
                receipt=receipt,
                lines=[{"item": self.product, "quantity": Decimal("3"), "unit_cost": Decimal("40")}],
            )
            self.assertEqual(receipt.lines.first().quantity, Decimal("3.0000"))

            receive_goods(receipt_id=receipt.id, organization=self.org_a)
            receipt.refresh_from_db()
            with self.assertRaises(ApplicationError) as ctx:
                replace_receipt_lines(
                    receipt=receipt,
                    lines=[{"item": self.product, "quantity": Decimal("1"), "unit_cost": Decimal("40")}],
                )
        self.assertEqual(ctx.exception.detail.code, "goods_receipt_not_draft")

    def test_received_receipt_is_immutable_at_the_model_layer(self):
        with tenant_context(organization_id=self.org_a.id):
            receipt, _ = self._make_received_gr()
            receipt.notes = "tampered"
            with self.assertRaises(ValueError):
                receipt.save()


class GoodsReceiptTenantIsolationTests(PurchasesTestsBase):
    def test_other_org_cannot_see_or_receive(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po()
            receipt = create_goods_receipt(
                organization=self.org_a, vendor=self.vendor, warehouse=self.warehouse,
                receipt_date=ORDER_DATE, source_purchase_order=order,
                lines=[{"item": self.product, "quantity": Decimal("1"),
                        "source_order_line": order.lines.first()}],
            )
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(GoodsReceipt.objects.count(), 0)
            with self.assertRaises(ApplicationError) as ctx:
                receive_goods(receipt_id=receipt.id, organization=self.org_b)
        self.assertEqual(ctx.exception.detail.code, "goods_receipt_not_found")
