"""The Phase 4 acceptance-gate workflow, end to end through the services:

    Vendor -> Purchase Order -> Goods Receipt -> Bill -> Payment
           -> Accounting -> Trial Balance

Asserted at every hop: stock moves exactly once, accounting balances, AP
rises and falls correctly, and the authoritative reports agree with the
documents. This is the purchase-side counterpart of the sales golden path.
"""

import datetime
from decimal import Decimal

from accounting.selectors import get_trial_balance
from core.tenancy import tenant_context
from inventory.models.stock_movement import StockMovement
from inventory.selectors import get_stock_on_hand, get_weighted_average_cost
from purchases.models.bill import BillStatus
from purchases.models.purchase_order import PurchaseOrderStatus
from purchases.selectors import get_bill_amount_due, get_outstanding_bills, get_overdue_bills
from purchases.services.bills import create_bill_from_goods_receipt, post_bill
from purchases.services.goods_receipts import create_goods_receipt, receive_goods
from purchases.services.payments import record_vendor_payment
from purchases.services.purchase_orders import approve_purchase_order, create_purchase_order
from purchases.services.three_way_match import match_bill, match_purchase_order
from purchases.tests.base import DUE_DATE, ORDER_DATE, PurchasesTestsBase


class PurchaseWorkflowE2ETests(PurchasesTestsBase):
    def test_full_procure_to_pay_cycle(self):
        with tenant_context(organization_id=self.org_a.id):
            # 1. Purchase Order — a commitment, nothing financial yet.
            order = create_purchase_order(
                organization=self.org_a, vendor=self.vendor, order_date=ORDER_DATE,
                expected_date=ORDER_DATE + datetime.timedelta(days=7), warehouse=self.warehouse,
                lines=self._product_lines(quantity=Decimal("100"), unit_price=Decimal("25.00"),
                                          tax_rate=Decimal("18")),
            )
            self.assertEqual(order.total, Decimal("2950.00"))
            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("0"))

            order = approve_purchase_order(order_id=order.id, organization=self.org_a)
            order_line = order.lines.get()

            # 2. Goods Receipt — stock arrives; still no journal.
            receipt = create_goods_receipt(
                organization=self.org_a, vendor=self.vendor, warehouse=self.warehouse,
                receipt_date=ORDER_DATE + datetime.timedelta(days=7), source_purchase_order=order,
                vendor_document_number="DN-5512",
                lines=[{"item": self.product, "quantity": Decimal("100"),
                        "source_order_line": order_line}],
            )
            receipt = receive_goods(receipt_id=receipt.id, organization=self.org_a)

            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("100"))
            _quantity, cost = get_weighted_average_cost(item=self.product, warehouse=self.warehouse)
            self.assertEqual(cost, Decimal("25.0000"))
            order.refresh_from_db()
            self.assertEqual(order.status, PurchaseOrderStatus.RECEIVED)

            # 3. Bill — built from the receipt, so it must NOT re-receive.
            bill = create_bill_from_goods_receipt(
                receipt=receipt, bill_date=ORDER_DATE + datetime.timedelta(days=8), due_date=DUE_DATE,
                payable_account=self.ap_account, tax_recoverable_account=self.input_tax_account,
                vendor_bill_number="ACME-8891",
                tax_rate_by_line={receipt.lines.get().id: Decimal("18")},
            )
            bill = post_bill(bill_id=bill.id, organization=self.org_a)

            self.assertEqual(bill.status, BillStatus.OPEN)
            self.assertEqual(bill.total, Decimal("2950.00"))
            # Stock unchanged: received once, by the receipt.
            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("100"))
            self.assertEqual(StockMovement.objects.filter(source_type="purchases.Bill").count(), 0)

            journal = bill.accounting_journal
            self.assert_journal_balanced(journal)
            self.assertEqual(self.account_movement(journal, self.inventory_account), Decimal("2500.00"))
            self.assertEqual(self.account_movement(journal, self.input_tax_account), Decimal("450.00"))
            self.assertEqual(self.account_movement(journal, self.ap_account), Decimal("-2950.00"))

            # 4. Three-way match: everything agrees.
            self.assertTrue(match_purchase_order(organization=self.org_a, purchase_order=order)["matched"])
            self.assertTrue(match_bill(organization=self.org_a, bill=bill)["matched"])

            # 5. Payment — part now, the rest later.
            self.assertEqual(len(get_outstanding_bills(organization=self.org_a)), 1)
            record_vendor_payment(
                organization=self.org_a, vendor=self.vendor,
                payment_date=ORDER_DATE + datetime.timedelta(days=10),
                amount=Decimal("1000.00"), source_account=self.bank_account,
                allocations=[{"bill": bill, "amount": Decimal("1000.00")}],
            )
            bill.refresh_from_db()
            self.assertEqual(bill.status, BillStatus.PARTIALLY_PAID)
            self.assertEqual(get_bill_amount_due(bill=bill), Decimal("1950.00"))

            record_vendor_payment(
                organization=self.org_a, vendor=self.vendor,
                payment_date=ORDER_DATE + datetime.timedelta(days=20),
                amount=Decimal("1950.00"), source_account=self.bank_account,
                allocations=[{"bill": bill, "amount": Decimal("1950.00")}],
            )
            bill.refresh_from_db()
            self.assertEqual(bill.status, BillStatus.PAID)
            self.assertEqual(get_bill_amount_due(bill=bill), Decimal("0.00"))
            self.assertEqual(len(get_outstanding_bills(organization=self.org_a)), 0)

            # 6. The authoritative reports agree with the documents.
            trial_balance = get_trial_balance(
                organization=self.org_a, as_of_date=ORDER_DATE + datetime.timedelta(days=30)
            )
            self.assertTrue(trial_balance["is_balanced"])
            by_account = {row["account"].id: row for row in trial_balance["rows"]}

            # Inventory capitalised, AP fully cleared, bank down by the total.
            self.assertEqual(by_account[self.inventory_account.id]["closing_debit"], Decimal("2500.00"))
            ap_row = by_account[self.ap_account.id]
            self.assertEqual(ap_row["closing_debit"], Decimal("0.00"))
            self.assertEqual(ap_row["closing_credit"], Decimal("0.00"))
            self.assertEqual(by_account[self.bank_account.id]["closing_credit"], Decimal("2950.00"))

    def test_partial_receipt_then_partial_bill_keeps_every_balance_honest(self):
        """The messy real-world path: 100 ordered, 60 arrive, 60 billed and
        paid, 40 still outstanding on the order."""
        with tenant_context(organization_id=self.org_a.id):
            order = create_purchase_order(
                organization=self.org_a, vendor=self.vendor, order_date=ORDER_DATE,
                warehouse=self.warehouse,
                lines=self._product_lines(quantity=Decimal("100"), unit_price=Decimal("25.00")),
            )
            order = approve_purchase_order(order_id=order.id, organization=self.org_a)
            order_line = order.lines.get()

            receipt = create_goods_receipt(
                organization=self.org_a, vendor=self.vendor, warehouse=self.warehouse,
                receipt_date=ORDER_DATE, source_purchase_order=order,
                lines=[{"item": self.product, "quantity": Decimal("60"), "source_order_line": order_line}],
            )
            receipt = receive_goods(receipt_id=receipt.id, organization=self.org_a)

            order.refresh_from_db()
            self.assertEqual(order.status, PurchaseOrderStatus.PARTIALLY_RECEIVED)
            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("60"))

            bill = create_bill_from_goods_receipt(
                receipt=receipt, bill_date=ORDER_DATE, due_date=DUE_DATE, payable_account=self.ap_account,
            )
            bill = post_bill(bill_id=bill.id, organization=self.org_a)
            self.assertEqual(bill.total, Decimal("1500.00"))
            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("60"))

            # The three documents AGREE — 60 arrived, 60 billed, at the
            # agreed price — so the match is clean even though the order is
            # only part-delivered. The matcher's job is to catch
            # discrepancies BETWEEN the documents, not to flag an open order
            # still awaiting its balance; that is what the PO's own
            # PARTIALLY_RECEIVED status says, asserted above. Flagging every
            # in-progress order would bury the real exceptions.
            match = match_purchase_order(organization=self.org_a, purchase_order=order)
            self.assertTrue(match["matched"])
            line = match["lines"][0]
            self.assertEqual(line["ordered_quantity"], Decimal("100.0000"))
            self.assertEqual(line["received_quantity"], Decimal("60.0000"))
            self.assertEqual(line["billed_quantity"], Decimal("60.0000"))

            record_vendor_payment(
                organization=self.org_a, vendor=self.vendor, payment_date=ORDER_DATE,
                amount=Decimal("1500.00"), source_account=self.bank_account,
                allocations=[{"bill": bill, "amount": Decimal("1500.00")}],
            )
            bill.refresh_from_db()
            self.assertEqual(bill.status, BillStatus.PAID)

            trial_balance = get_trial_balance(organization=self.org_a, as_of_date=DUE_DATE)
            self.assertTrue(trial_balance["is_balanced"])
            by_account = {row["account"].id: row for row in trial_balance["rows"]}
            # Only the 60 that actually arrived are capitalised.
            self.assertEqual(by_account[self.inventory_account.id]["closing_debit"], Decimal("1500.00"))

    def test_overdue_bills_are_derived_not_stored(self):
        with tenant_context(organization_id=self.org_a.id):
            receipt, _receipt_line = self._make_received_gr(quantity=Decimal("10"), unit_cost=Decimal("50.0000"))
            bill = create_bill_from_goods_receipt(
                receipt=receipt, bill_date=ORDER_DATE, due_date=DUE_DATE, payable_account=self.ap_account
            )
            bill = post_bill(bill_id=bill.id, organization=self.org_a)

            self.assertEqual(len(get_overdue_bills(organization=self.org_a, as_of=DUE_DATE)), 0)
            later = DUE_DATE + datetime.timedelta(days=1)
            self.assertEqual(len(get_overdue_bills(organization=self.org_a, as_of=later)), 1)
            # Nothing was written to reach that conclusion.
            bill.refresh_from_db()
            self.assertEqual(bill.status, BillStatus.OPEN)
