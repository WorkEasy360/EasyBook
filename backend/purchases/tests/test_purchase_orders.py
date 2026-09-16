import datetime
from decimal import Decimal

from accounting.models.journal import JournalEntry
from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from inventory.models.stock_movement import StockMovement
from purchases.models.purchase_order import PurchaseOrder, PurchaseOrderStatus
from purchases.services.purchase_orders import (
    approve_purchase_order,
    cancel_purchase_order,
    close_purchase_order,
    create_purchase_order,
    replace_order_lines,
)
from purchases.tests.base import ORDER_DATE, PurchasesTestsBase


class PurchaseOrderCreationTests(PurchasesTestsBase):
    def test_create_draft_with_server_computed_totals(self):
        with tenant_context(organization_id=self.org_a.id):
            order = create_purchase_order(
                organization=self.org_a, vendor=self.vendor, order_date=ORDER_DATE,
                lines=self._product_lines(tax_rate=Decimal("18")),
            )
            self.assertEqual(order.lines.count(), 1)
        self.assertEqual(order.status, PurchaseOrderStatus.DRAFT)
        self.assertEqual(order.subtotal, Decimal("500.00"))
        self.assertEqual(order.tax_total, Decimal("90.00"))
        self.assertEqual(order.total, Decimal("590.00"))

    def test_numbered_at_creation_unlike_bill(self):
        with tenant_context(organization_id=self.org_a.id):
            order = create_purchase_order(
                organization=self.org_a, vendor=self.vendor, order_date=ORDER_DATE, lines=self._product_lines()
            )
        self.assertTrue(order.order_number.startswith("PO-"))

    def test_creates_no_accounting_and_no_stock(self):
        with tenant_context(organization_id=self.org_a.id):
            create_purchase_order(
                organization=self.org_a, vendor=self.vendor, order_date=ORDER_DATE, lines=self._product_lines()
            )
            self.assertEqual(JournalEntry.objects.count(), 0)
            self.assertEqual(StockMovement.objects.count(), 0)

    def test_rejects_lines_from_another_organization(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_purchase_order(
                    organization=self.org_a, vendor=self.vendor, order_date=ORDER_DATE,
                    lines=[{"item": self.item_b, "quantity": Decimal("1"), "unit_price": Decimal("10")}],
                )
        self.assertEqual(ctx.exception.detail.code, "item_cross_org")

    def test_rejects_vendor_from_another_organization(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_purchase_order(
                    organization=self.org_a, vendor=self.vendor_b, order_date=ORDER_DATE,
                    lines=self._product_lines(),
                )
        self.assertEqual(ctx.exception.detail.code, "vendor_cross_org")

    def test_rejects_non_purchasable_item(self):
        from items.services.items import update_item

        with tenant_context(organization_id=self.org_a.id):
            update_item(item=self.product, is_purchasable=False)
            with self.assertRaises(ApplicationError) as ctx:
                create_purchase_order(
                    organization=self.org_a, vendor=self.vendor, order_date=ORDER_DATE,
                    lines=self._product_lines(),
                )
        self.assertEqual(ctx.exception.detail.code, "item_not_purchasable")

    def test_rejects_expected_date_before_order_date(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_purchase_order(
                    organization=self.org_a, vendor=self.vendor, order_date=ORDER_DATE,
                    expected_date=ORDER_DATE - datetime.timedelta(days=1), lines=self._product_lines(),
                )
        self.assertEqual(ctx.exception.detail.code, "purchase_order_expected_date_invalid")

    def test_rejects_empty_lines(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_purchase_order(
                    organization=self.org_a, vendor=self.vendor, order_date=ORDER_DATE, lines=[]
                )
        self.assertEqual(ctx.exception.detail.code, "purchase_order_no_lines")

    def test_inactive_vendor_rejected(self):
        from purchases.services.vendors import archive_vendor

        with tenant_context(organization_id=self.org_a.id):
            archive_vendor(vendor=self.vendor)
            with self.assertRaises(ApplicationError) as ctx:
                create_purchase_order(
                    organization=self.org_a, vendor=self.vendor, order_date=ORDER_DATE,
                    lines=self._product_lines(),
                )
        self.assertEqual(ctx.exception.detail.code, "vendor_inactive")


class PurchaseOrderTransitionTests(PurchasesTestsBase):
    def test_approve_from_draft(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po()
        self.assertEqual(order.status, PurchaseOrderStatus.APPROVED)

    def test_cannot_approve_twice(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po()
            with self.assertRaises(ApplicationError) as ctx:
                approve_purchase_order(order_id=order.id, organization=self.org_a)
        self.assertEqual(ctx.exception.detail.code, "purchase_order_invalid_status")

    def test_cancel_from_draft_and_from_approved(self):
        with tenant_context(organization_id=self.org_a.id):
            draft = create_purchase_order(
                organization=self.org_a, vendor=self.vendor, order_date=ORDER_DATE, lines=self._product_lines()
            )
            self.assertEqual(
                cancel_purchase_order(order_id=draft.id, organization=self.org_a).status,
                PurchaseOrderStatus.CANCELLED,
            )
            approved = self._make_approved_po()
            self.assertEqual(
                cancel_purchase_order(order_id=approved.id, organization=self.org_a).status,
                PurchaseOrderStatus.CANCELLED,
            )

    def test_close_short_closes_an_approved_order(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po()
            self.assertEqual(
                close_purchase_order(order_id=order.id, organization=self.org_a).status,
                PurchaseOrderStatus.CLOSED,
            )

    def test_replace_lines_only_while_draft(self):
        with tenant_context(organization_id=self.org_a.id):
            order = create_purchase_order(
                organization=self.org_a, vendor=self.vendor, order_date=ORDER_DATE, lines=self._product_lines()
            )
            replace_order_lines(order=order, lines=self._service_lines())
            order.refresh_from_db()
            self.assertEqual(order.total, Decimal("200.00"))

            approve_purchase_order(order_id=order.id, organization=self.org_a)
            order.refresh_from_db()
            with self.assertRaises(ApplicationError) as ctx:
                replace_order_lines(order=order, lines=self._product_lines())
        self.assertEqual(ctx.exception.detail.code, "purchase_order_not_draft")

    def test_approved_order_is_immutable_at_the_model_layer(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po()
            order.notes = "tampered"
            with self.assertRaises(ValueError):
                order.save()

    def test_only_draft_orders_can_be_deleted(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po()
            with self.assertRaises(ValueError):
                order.delete()

    def test_cannot_approve_order_with_no_lines(self):
        with tenant_context(organization_id=self.org_a.id):
            order = create_purchase_order(
                organization=self.org_a, vendor=self.vendor, order_date=ORDER_DATE, lines=self._product_lines()
            )
            order.lines.all().delete()
            with self.assertRaises(ApplicationError) as ctx:
                approve_purchase_order(order_id=order.id, organization=self.org_a)
        self.assertEqual(ctx.exception.detail.code, "purchase_order_no_lines")


class PurchaseOrderTenantIsolationTests(PurchasesTestsBase):
    def test_other_org_cannot_see_or_transition_order(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po()
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(PurchaseOrder.objects.count(), 0)
            with self.assertRaises(ApplicationError) as ctx:
                cancel_purchase_order(order_id=order.id, organization=self.org_b)
        self.assertEqual(ctx.exception.detail.code, "purchase_order_not_found")
