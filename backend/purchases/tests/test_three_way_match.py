from decimal import Decimal

from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from purchases.services.bills import create_bill, post_bill, void_bill
from purchases.services.three_way_match import MatchException, match_bill, match_purchase_order
from purchases.tests.base import DUE_DATE, ORDER_DATE, PurchasesTestsBase


class ThreeWayMatchTests(PurchasesTestsBase):
    def _bill_for(self, receipt_line, order_line, quantity, unit_price, order=None):
        bill = create_bill(
            organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
            payable_account=self.ap_account, price_variance_account=self.price_variance_account,
            source_purchase_order=order,
            lines=[{
                "item": self.product, "quantity": quantity, "unit_price": unit_price,
                "source_goods_receipt_line": receipt_line, "source_order_line": order_line,
            }],
        )
        return post_bill(bill_id=bill.id, organization=self.org_a)

    def test_perfect_match_reports_no_exceptions(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po(lines=self._product_lines(quantity=Decimal("10"),
                                                                     unit_price=Decimal("50.00")))
            _receipt, receipt_line = self._make_received_gr(order=order, quantity=Decimal("10"))
            self._bill_for(receipt_line, order.lines.first(), Decimal("10"), Decimal("50.00"), order=order)

            result = match_purchase_order(organization=self.org_a, purchase_order=order)
            self.assertTrue(result["matched"])
            self.assertEqual(result["exceptions"], [])
            line = result["lines"][0]
            self.assertEqual(line["ordered_quantity"], Decimal("10.0000"))
            self.assertEqual(line["received_quantity"], Decimal("10.0000"))
            self.assertEqual(line["billed_quantity"], Decimal("10.0000"))

    def test_price_mismatch_is_reported(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po(lines=self._product_lines(quantity=Decimal("10"),
                                                                     unit_price=Decimal("50.00")))
            _receipt, receipt_line = self._make_received_gr(order=order, quantity=Decimal("10"))
            self._bill_for(receipt_line, order.lines.first(), Decimal("10"), Decimal("55.00"), order=order)

            result = match_purchase_order(organization=self.org_a, purchase_order=order)
            self.assertFalse(result["matched"])
            self.assertIn(MatchException.PRICE_MISMATCH, result["exceptions"])
            self.assertEqual(result["lines"][0]["max_price_variance"], Decimal("5.00"))

    def test_price_tolerance_suppresses_a_small_variance(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po(lines=self._product_lines(quantity=Decimal("10"),
                                                                     unit_price=Decimal("50.00")))
            _receipt, receipt_line = self._make_received_gr(order=order, quantity=Decimal("10"))
            self._bill_for(receipt_line, order.lines.first(), Decimal("10"), Decimal("50.50"), order=order)

            result = match_purchase_order(
                organization=self.org_a, purchase_order=order, price_tolerance=Decimal("1.00")
            )
            self.assertNotIn(MatchException.PRICE_MISMATCH, result["exceptions"])

    def test_billed_more_than_received_is_the_headline_exception(self):
        """The vendor invoiced for goods that never turned up."""
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po(lines=self._product_lines(quantity=Decimal("10"),
                                                                     unit_price=Decimal("50.00")))
            self._make_received_gr(order=order, quantity=Decimal("4"))
            # Billed 10 against the order, but only 4 arrived. The bill line
            # links to the ORDER line, not a receipt line, so nothing stops
            # it being raised — which is exactly what the match must catch.
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, warehouse=self.warehouse, source_purchase_order=order,
                lines=[{
                    "item": self.product, "quantity": Decimal("10"), "unit_price": Decimal("50.00"),
                    "source_order_line": order.lines.first(),
                }],
            )
            post_bill(bill_id=bill.id, organization=self.org_a)

            result = match_purchase_order(organization=self.org_a, purchase_order=order)
            self.assertFalse(result["matched"])
            self.assertIn(MatchException.BILLED_NOT_RECEIVED, result["exceptions"])

    def test_received_not_yet_billed_is_reported(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po(lines=self._product_lines(quantity=Decimal("10")))
            self._make_received_gr(order=order, quantity=Decimal("10"))

            result = match_purchase_order(organization=self.org_a, purchase_order=order)
            self.assertIn(MatchException.RECEIVED_NOT_BILLED, result["exceptions"])

    def test_bill_line_not_on_the_order_is_reported(self):
        """A purely line-driven walk would miss this entirely — there is no
        PO line to hang it off."""
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po(lines=self._product_lines(quantity=Decimal("10")))
            self._make_received_gr(order=order, quantity=Decimal("10"))
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, source_purchase_order=order,
                lines=[{"item": self.service, "quantity": Decimal("1"), "unit_price": Decimal("999.00")}],
            )
            post_bill(bill_id=bill.id, organization=self.org_a)

            result = match_purchase_order(organization=self.org_a, purchase_order=order)
            self.assertIn(MatchException.NOT_ON_ORDER, result["exceptions"])
            self.assertEqual(len(result["unordered_bill_lines"]), 1)
            self.assertEqual(result["unordered_bill_lines"][0]["line_total"], Decimal("999.00"))

    def test_over_billing_the_order_quantity_is_reported(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po(lines=self._product_lines(quantity=Decimal("10"),
                                                                     unit_price=Decimal("50.00")))
            self._make_received_gr(order=order, quantity=Decimal("10"))
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, warehouse=self.warehouse, source_purchase_order=order,
                lines=[{
                    "item": self.product, "quantity": Decimal("12"), "unit_price": Decimal("50.00"),
                    "source_order_line": order.lines.first(),
                }],
            )
            post_bill(bill_id=bill.id, organization=self.org_a)

            result = match_purchase_order(organization=self.org_a, purchase_order=order)
            self.assertIn(MatchException.QUANTITY_OVER_BILLED, result["exceptions"])

    def test_draft_and_void_bills_are_excluded_from_the_match(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po(lines=self._product_lines(quantity=Decimal("10"),
                                                                     unit_price=Decimal("50.00")))
            _receipt, receipt_line = self._make_received_gr(order=order, quantity=Decimal("10"))
            bill = self._bill_for(receipt_line, order.lines.first(), Decimal("10"), Decimal("55.00"), order=order)
            self.assertIn(
                MatchException.PRICE_MISMATCH,
                match_purchase_order(organization=self.org_a, purchase_order=order)["exceptions"],
            )

            void_bill(bill_id=bill.id, organization=self.org_a, reason="wrong price")
            result = match_purchase_order(organization=self.org_a, purchase_order=order)
            self.assertNotIn(MatchException.PRICE_MISMATCH, result["exceptions"])
            self.assertEqual(result["lines"][0]["billed_quantity"], Decimal("0"))

    def test_match_is_recomputed_not_cached(self):
        """No stored verdict to go stale — a correction is reflected at once."""
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po(lines=self._product_lines(quantity=Decimal("10"),
                                                                     unit_price=Decimal("50.00")))
            # Nothing ordered has arrived and nothing has been billed, so
            # nothing is yet out of step — an approved PO awaiting delivery
            # is not an exception.
            self.assertTrue(match_purchase_order(organization=self.org_a, purchase_order=order)["matched"])

            _receipt, receipt_line = self._make_received_gr(order=order, quantity=Decimal("10"))
            self.assertIn(
                MatchException.RECEIVED_NOT_BILLED,
                match_purchase_order(organization=self.org_a, purchase_order=order)["exceptions"],
            )

            self._bill_for(receipt_line, order.lines.first(), Decimal("10"), Decimal("50.00"), order=order)
            self.assertTrue(match_purchase_order(organization=self.org_a, purchase_order=order)["matched"])

    def test_negative_tolerance_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po()
            with self.assertRaises(ApplicationError) as ctx:
                match_purchase_order(
                    organization=self.org_a, purchase_order=order, price_tolerance=Decimal("-1")
                )
        self.assertEqual(ctx.exception.detail.code, "match_tolerance_invalid")

    def test_cross_org_purchase_order_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po()
        with tenant_context(organization_id=self.org_b.id):
            with self.assertRaises(ApplicationError) as ctx:
                match_purchase_order(organization=self.org_b, purchase_order=order)
        self.assertEqual(ctx.exception.detail.code, "purchase_order_cross_org")


class BillMatchTests(PurchasesTestsBase):
    def test_bill_with_no_purchase_order_matches_vacuously(self):
        """Nothing was agreed in advance, so there is nothing to compare —
        flagging every ad-hoc purchase would make the report useless."""
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, lines=self._service_lines(),
            )
            bill = post_bill(bill_id=bill.id, organization=self.org_a)
            result = match_bill(organization=self.org_a, bill=bill)
            self.assertTrue(result["matched"])
            self.assertEqual(result["exceptions"], [])

    def test_bill_match_reports_price_variance_per_line(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po(lines=self._product_lines(quantity=Decimal("10"),
                                                                     unit_price=Decimal("50.00")))
            _receipt, receipt_line = self._make_received_gr(order=order, quantity=Decimal("10"))
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, price_variance_account=self.price_variance_account,
                source_purchase_order=order,
                lines=[{
                    "item": self.product, "quantity": Decimal("10"), "unit_price": Decimal("60.00"),
                    "source_goods_receipt_line": receipt_line, "source_order_line": order.lines.first(),
                }],
            )
            bill = post_bill(bill_id=bill.id, organization=self.org_a)

            result = match_bill(organization=self.org_a, bill=bill)
            self.assertFalse(result["matched"])
            line = result["lines"][0]
            self.assertEqual(line["price_variance"], Decimal("10.00"))
            self.assertEqual(line["ordered_unit_price"], Decimal("50.00"))
            self.assertEqual(line["received_quantity"], Decimal("10.0000"))
            self.assertIsNotNone(line["goods_receipt_line_id"])

    def test_bill_line_without_order_line_on_a_po_bill_is_flagged(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po()
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, source_purchase_order=order, lines=self._service_lines(),
            )
            bill = post_bill(bill_id=bill.id, organization=self.org_a)
            result = match_bill(organization=self.org_a, bill=bill)
            self.assertIn(MatchException.NOT_ON_ORDER, result["exceptions"])

    def test_match_never_blocks_posting(self):
        """The matcher reports; it does not gate. A genuine, accepted price
        increase must still be postable."""
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po(lines=self._product_lines(quantity=Decimal("10"),
                                                                     unit_price=Decimal("50.00")))
            _receipt, receipt_line = self._make_received_gr(order=order, quantity=Decimal("10"))
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, price_variance_account=self.price_variance_account,
                source_purchase_order=order,
                lines=[{
                    "item": self.product, "quantity": Decimal("10"), "unit_price": Decimal("99.00"),
                    "source_goods_receipt_line": receipt_line, "source_order_line": order.lines.first(),
                }],
            )
            posted = post_bill(bill_id=bill.id, organization=self.org_a)
            self.assertIsNotNone(posted.bill_number)
            self.assertFalse(match_bill(organization=self.org_a, bill=posted)["matched"])
