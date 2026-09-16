"""Phase 4 concurrency acceptance gate.

Real threads on real connections (`TransactionTestCase`), because
`TestCase`'s outer wrapping transaction serialises everything onto one
connection and would prove nothing. Mirrors sales/tests/test_concurrency.py
and covers the races unique to the buy side: double receipt, double billing
of the same received goods, and over-paying a vendor.

NumberSequence concurrency is NOT re-tested here —
accounts/tests/test_number_sequence.py already proves
`allocate_sequence_number` (shared by every purchase document's numbering)
is race-safe.
"""

import datetime
import threading
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import TestCase

from accounting.models.journal import JournalEntry
from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from inventory.models.stock_movement import StockMovement
from inventory.selectors import get_stock_on_hand
from purchases.models.bill import Bill, BillStatus
from purchases.models.goods_receipt import GoodsReceiptStatus
from purchases.selectors import get_bill_amount_due, get_billed_quantity_for_receipt_line
from purchases.services.bills import create_bill, post_bill
from purchases.services.goods_receipts import create_goods_receipt, receive_goods
from purchases.services.payments import record_vendor_payment
from purchases.tests.base import (
    DUE_DATE,
    ORDER_DATE,
    PurchasesFixtureMixin,
    PurchasesTransactionTestsBase,
)


def _run_concurrently(target, count=5):
    """Runs `target` in `count` threads, collecting (results, errors).
    Each thread closes its connection so it genuinely contends on its own."""
    results, errors = [], []
    lock = threading.Lock()

    def runner():
        try:
            value = target()
            with lock:
                results.append(value)
        except Exception as exc:
            with lock:
                errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=runner) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results, errors


class ConcurrentGoodsReceiptTests(PurchasesTransactionTestsBase):
    def test_concurrent_receive_of_same_receipt_moves_stock_exactly_once(self):
        """The no-double-receipt guarantee under contention: `receive_goods`
        is idempotent by construction (locked no-op once RECEIVED), so every
        caller succeeds but the warehouse is credited once."""
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po(lines=self._product_lines(quantity=Decimal("10")))
            receipt = create_goods_receipt(
                organization=self.org_a, vendor=self.vendor, warehouse=self.warehouse,
                receipt_date=ORDER_DATE, source_purchase_order=order,
                lines=[{"item": self.product, "quantity": Decimal("10"),
                        "source_order_line": order.lines.first()}],
            )
        receipt_id = receipt.id

        def receive():
            with tenant_context(organization_id=self.org_a.id):
                return receive_goods(receipt_id=receipt_id, organization=self.org_a).status

        results, errors = _run_concurrently(receive)

        self.assertEqual(errors, [])
        self.assertEqual(set(results), {GoodsReceiptStatus.RECEIVED})
        with tenant_context(organization_id=self.org_a.id):
            movements = StockMovement.objects.filter(
                source_type="purchases.GoodsReceipt", source_id=str(receipt_id)
            )
            self.assertEqual(movements.count(), 1)
            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("10"))


class ConcurrentBillPostTests(PurchasesTransactionTestsBase):
    def test_concurrent_post_of_same_bill_posts_exactly_one_journal(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, lines=self._service_lines(),
            )
        bill_id = bill.id

        def post():
            with tenant_context(organization_id=self.org_a.id):
                return post_bill(bill_id=bill_id, organization=self.org_a).bill_number

        results, errors = _run_concurrently(post)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 5)
        self.assertEqual(len(set(results)), 1, "all callers must observe the same bill_number")
        with tenant_context(organization_id=self.org_a.id):
            self.assertEqual(
                JournalEntry.objects.filter(source_type="purchases.Bill", source_id=str(bill_id)).count(), 1
            )

    def test_concurrent_posts_racing_the_same_stock_cannot_over_receive(self):
        """Two bills each receiving stock directly must both land — inbound
        movements never contend for a limited resource the way outbound ones
        do — but the resulting on-hand must be exactly the sum, never
        double-counted or lost."""
        with tenant_context(organization_id=self.org_a.id):
            bill_ids = []
            for _ in range(3):
                bill = create_bill(
                    organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                    payable_account=self.ap_account, warehouse=self.warehouse,
                    lines=self._product_lines(quantity=Decimal("5"), unit_price=Decimal("50.00")),
                )
                bill_ids.append(bill.id)

        pending = list(bill_ids)
        pop_lock = threading.Lock()

        def post_next():
            with pop_lock:
                bill_id = pending.pop()
            with tenant_context(organization_id=self.org_a.id):
                return post_bill(bill_id=bill_id, organization=self.org_a).bill_number

        results, errors = _run_concurrently(post_next, count=3)

        self.assertEqual(errors, [])
        self.assertEqual(len(set(results)), 3, "each bill must get its own number")
        with tenant_context(organization_id=self.org_a.id):
            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("15"))


class ConcurrentBillingOfOneReceiptTests(PurchasesTransactionTestsBase):
    def test_concurrent_bills_cannot_jointly_over_bill_one_receipt_line(self):
        """The double-BILLING race: several bills each claiming the whole of
        one received line. The derived quantity guard must let at most the
        received quantity through in total."""
        with tenant_context(organization_id=self.org_a.id):
            _receipt, receipt_line = self._make_received_gr(quantity=Decimal("10"), unit_cost=Decimal("50.0000"))
        receipt_line_id = receipt_line.id

        def bill_it():
            with tenant_context(organization_id=self.org_a.id):
                from purchases.models.goods_receipt import GoodsReceiptLine

                line = GoodsReceiptLine.objects.get(pk=receipt_line_id)
                bill = create_bill(
                    organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                    payable_account=self.ap_account,
                    lines=[{
                        "item": self.product, "quantity": Decimal("10"), "unit_price": Decimal("50.00"),
                        "source_goods_receipt_line": line,
                    }],
                )
                return post_bill(bill_id=bill.id, organization=self.org_a).bill_number

        results, errors = _run_concurrently(bill_it)

        with tenant_context(organization_id=self.org_a.id):
            from purchases.models.goods_receipt import GoodsReceiptLine

            line = GoodsReceiptLine.objects.get(pk=receipt_line_id)
            billed = get_billed_quantity_for_receipt_line(goods_receipt_line=line)
            # The invariant that matters: the vendor is never paid for more
            # than physically arrived, whatever interleaving occurred.
            self.assertLessEqual(billed, Decimal("10"))
            self.assertGreaterEqual(len(results), 1, "at least one bill must succeed")
            self.assertTrue(
                all(isinstance(exc, ApplicationError) for exc in errors),
                f"losers must fail cleanly, got: {errors}",
            )
            self.assertTrue(
                all(exc.detail.code == "over_billing" for exc in errors),
                f"losers must be rejected as over_billing, got: {[e.detail.code for e in errors]}",
            )


class ConcurrentVendorPaymentTests(PurchasesTransactionTestsBase):
    def test_concurrent_full_payments_cannot_overpay_a_bill(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account,
                lines=self._service_lines(quantity=Decimal("1"), unit_price=Decimal("100.00")),
            )
            bill = post_bill(bill_id=bill.id, organization=self.org_a)
        bill_id = bill.id

        def pay():
            with tenant_context(organization_id=self.org_a.id):
                bill_ref = Bill.objects.get(pk=bill_id)
                payment = record_vendor_payment(
                    organization=self.org_a, vendor=self.vendor, payment_date=ORDER_DATE,
                    amount=Decimal("100.00"), source_account=self.bank_account,
                    allocations=[{"bill": bill_ref, "amount": Decimal("100.00")}],
                )
                return payment.id

        results, errors = _run_concurrently(pay)

        self.assertEqual(len(results), 1, "exactly one payment may settle the bill")
        self.assertEqual(len(errors), 4)
        # Which error depends on lock-acquisition order: a loser that grabs
        # the lock before the winner commits sees over_allocation, one that
        # arrives after sees an already-PAID bill. Never a silent double pay.
        self.assertTrue(
            all(
                isinstance(exc, ApplicationError)
                and exc.detail.code in ("over_allocation", "bill_not_payable")
                for exc in errors
            ),
            f"unexpected errors: {errors}",
        )
        with tenant_context(organization_id=self.org_a.id):
            bill.refresh_from_db()
            self.assertEqual(bill.status, BillStatus.PAID)
            self.assertEqual(get_bill_amount_due(bill=bill), Decimal("0.00"))


class BillPostRollbackTests(PurchasesFixtureMixin, TestCase):
    """Single-threaded, so a plain TestCase is right here: the point is that
    one atomic block either lands entirely or not at all."""

    def test_failure_after_stock_receipt_rolls_the_whole_post_back(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, warehouse=self.warehouse,
                lines=self._product_lines(quantity=Decimal("5"), unit_price=Decimal("50.00")),
            )

            # Fail AFTER stock has been received but BEFORE the journal posts.
            with mock.patch(
                "purchases.services.bills.post_purchase_journal",
                side_effect=RuntimeError("accounting engine unavailable"),
            ):
                with self.assertRaises(RuntimeError):
                    post_bill(bill_id=bill.id, organization=self.org_a)

            bill.refresh_from_db()
            self.assertEqual(bill.status, BillStatus.DRAFT)
            self.assertEqual(bill.bill_number, "", "no number may be left allocated to a failed post")
            self.assertIsNone(bill.accounting_journal)
            # No orphan movement, no orphan journal.
            self.assertFalse(StockMovement.objects.filter(source_type="purchases.Bill").exists())
            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("0"))
            self.assertEqual(JournalEntry.objects.count(), 0)

    def test_failure_during_goods_receipt_leaves_no_partial_stock(self):
        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po(
                lines=[
                    {"item": self.product, "quantity": Decimal("5"), "unit_price": Decimal("50.00")},
                    {"item": self.product, "quantity": Decimal("3"), "unit_price": Decimal("50.00")},
                ]
            )
            receipt = create_goods_receipt(
                organization=self.org_a, vendor=self.vendor, warehouse=self.warehouse,
                receipt_date=ORDER_DATE, source_purchase_order=order,
                lines=[
                    {"item": self.product, "quantity": Decimal("5"),
                     "source_order_line": order.lines.all()[0]},
                    {"item": self.product, "quantity": Decimal("3"),
                     "source_order_line": order.lines.all()[1]},
                ],
            )

            real_record = StockMovement.objects.create
            calls = {"n": 0}

            def fail_on_second(*args, **kwargs):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise RuntimeError("warehouse system unavailable")
                return real_record(*args, **kwargs)

            with mock.patch.object(StockMovement.objects, "create", side_effect=fail_on_second):
                with self.assertRaises(RuntimeError):
                    receive_goods(receipt_id=receipt.id, organization=self.org_a)

            receipt.refresh_from_db()
            self.assertEqual(receipt.status, GoodsReceiptStatus.DRAFT)
            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("0"))
            self.assertFalse(StockMovement.objects.filter(source_type="purchases.GoodsReceipt").exists())
            order.refresh_from_db()
            self.assertNotIn(
                order.status,
                ("partially_received", "received"),
                "a rolled-back receipt must not advance the purchase order",
            )

    def test_fiscal_year_guard_blocks_posting_outside_an_open_period(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor,
                bill_date=datetime.date(2030, 1, 1), due_date=datetime.date(2030, 2, 1),
                payable_account=self.ap_account, lines=self._service_lines(),
            )
            with self.assertRaises(ApplicationError):
                post_bill(bill_id=bill.id, organization=self.org_a)
            bill.refresh_from_db()
            self.assertEqual(bill.status, BillStatus.DRAFT)
            self.assertEqual(JournalEntry.objects.count(), 0)
