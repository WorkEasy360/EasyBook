from decimal import Decimal

from django.db.utils import IntegrityError

from accounting.models.journal import JournalEntry, JournalStatus
from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from inventory.models.stock_movement import MovementType, StockMovement
from inventory.selectors import get_stock_on_hand
from purchases.models.bill import Bill, BillStatus
from purchases.selectors import get_bill_amount_due, get_billed_quantity_for_receipt_line
from purchases.services.bills import (
    create_bill,
    create_bill_from_goods_receipt,
    post_bill,
    replace_bill_lines,
    void_bill,
)
from purchases.tests.base import DUE_DATE, ORDER_DATE, PurchasesTestsBase


class BillCreationTests(PurchasesTestsBase):
    def test_create_draft_with_server_computed_totals(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, warehouse=self.warehouse,
                tax_recoverable_account=self.input_tax_account,
                lines=self._product_lines(tax_rate=Decimal("18")),
            )
        self.assertEqual(bill.status, BillStatus.DRAFT)
        self.assertEqual(bill.bill_number, "")  # numbered at POST time, like Invoice
        self.assertEqual(bill.subtotal, Decimal("500.00"))
        self.assertEqual(bill.tax_total, Decimal("90.00"))
        self.assertEqual(bill.total, Decimal("590.00"))

    def test_payable_account_must_be_a_liability(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_bill(
                    organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                    payable_account=self.bank_account, warehouse=self.warehouse, lines=self._product_lines(),
                )
        self.assertEqual(ctx.exception.detail.code, "invalid_account_type")

    def test_tax_recoverable_account_must_be_an_asset(self):
        """Input tax is recoverable from the authority — an ASSET, the mirror
        of sales' output tax being a LIABILITY."""
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_bill(
                    organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                    payable_account=self.ap_account, tax_recoverable_account=self.ap_account,
                    warehouse=self.warehouse, lines=self._product_lines(),
                )
        self.assertEqual(ctx.exception.detail.code, "invalid_account_type")

    def test_inventoried_line_without_warehouse_or_receipt_is_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_bill(
                    organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                    payable_account=self.ap_account, lines=self._product_lines(),
                )
        self.assertEqual(ctx.exception.detail.code, "warehouse_required")

    def test_item_without_purchase_account_rejected_for_expense_line(self):
        from items.models.item import ItemType
        from items.services.items import create_item

        with tenant_context(organization_id=self.org_a.id):
            bare = create_item(
                organization=self.org_a, item_type=ItemType.SERVICE, name="Bare", unit=self.unit
            )
            with self.assertRaises(ApplicationError) as ctx:
                create_bill(
                    organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                    payable_account=self.ap_account,
                    lines=[{"item": bare, "quantity": Decimal("1"), "unit_price": Decimal("10")}],
                )
        self.assertEqual(ctx.exception.detail.code, "item_missing_purchase_account")

    def test_line_expense_account_overrides_item_purchase_account(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account,
                lines=[{
                    "item": self.service, "quantity": Decimal("1"), "unit_price": Decimal("100"),
                    "expense_account": self.office_expense_account,
                }],
            )
            bill = post_bill(bill_id=bill.id, organization=self.org_a)
            journal = bill.accounting_journal
            self.assertEqual(self.account_movement(journal, self.office_expense_account), Decimal("100.00"))
            self.assertEqual(self.account_movement(journal, self.purchase_expense_account), Decimal("0"))

    def test_duplicate_vendor_bill_number_rejected_by_service(self):
        with tenant_context(organization_id=self.org_a.id):
            create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, vendor_bill_number="INV-9001",
                lines=self._service_lines(),
            )
            with self.assertRaises(ApplicationError) as ctx:
                create_bill(
                    organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                    payable_account=self.ap_account, vendor_bill_number="INV-9001",
                    lines=self._service_lines(),
                )
        self.assertEqual(ctx.exception.detail.code, "duplicate_vendor_bill_number")

    def test_duplicate_vendor_bill_number_also_blocked_at_the_database(self):
        """The service check gives a clean error; the constraint is the real
        backstop against a racing second insert."""
        with tenant_context(organization_id=self.org_a.id):
            create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, vendor_bill_number="INV-9002", lines=self._service_lines(),
            )
            with self.assertRaises(IntegrityError):
                Bill.objects.create(
                    organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                    currency=self.currency, payable_account=self.ap_account, vendor_bill_number="INV-9002",
                )

    def test_blank_vendor_bill_numbers_do_not_collide(self):
        with tenant_context(organization_id=self.org_a.id):
            create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, lines=self._service_lines(),
            )
            create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, lines=self._service_lines(),
            )
            self.assertEqual(Bill.objects.filter(vendor_bill_number="").count(), 2)


class BillPostingAccountingTests(PurchasesTestsBase):
    def test_expense_bill_posts_dr_expense_cr_ap(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, lines=self._service_lines(),
            )
            bill = post_bill(bill_id=bill.id, organization=self.org_a)
            journal = bill.accounting_journal

            self.assertEqual(journal.status, JournalStatus.POSTED)
            self.assert_journal_balanced(journal)
            self.assertEqual(self.account_movement(journal, self.purchase_expense_account), Decimal("200.00"))
            self.assertEqual(self.account_movement(journal, self.ap_account), Decimal("-200.00"))

    def test_inventory_bill_posts_dr_inventory_cr_ap_and_receives_stock(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, warehouse=self.warehouse,
                lines=self._product_lines(quantity=Decimal("10"), unit_price=Decimal("50.00")),
            )
            bill = post_bill(bill_id=bill.id, organization=self.org_a)
            journal = bill.accounting_journal

            self.assert_journal_balanced(journal)
            self.assertEqual(self.account_movement(journal, self.inventory_account), Decimal("500.00"))
            self.assertEqual(self.account_movement(journal, self.ap_account), Decimal("-500.00"))
            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("10"))

    def test_tax_is_debited_to_recoverable_account_not_capitalised(self):
        """Recoverable input tax must never inflate the cost of the goods."""
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, warehouse=self.warehouse,
                tax_recoverable_account=self.input_tax_account,
                lines=self._product_lines(quantity=Decimal("10"), unit_price=Decimal("50.00"),
                                          tax_rate=Decimal("18")),
            )
            bill = post_bill(bill_id=bill.id, organization=self.org_a)
            journal = bill.accounting_journal

            self.assert_journal_balanced(journal)
            self.assertEqual(self.account_movement(journal, self.inventory_account), Decimal("500.00"))
            self.assertEqual(self.account_movement(journal, self.input_tax_account), Decimal("90.00"))
            self.assertEqual(self.account_movement(journal, self.ap_account), Decimal("-590.00"))

    def test_untracked_product_is_expensed_not_capitalised(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account,
                lines=[{"item": self.untracked_product, "quantity": Decimal("5"), "unit_price": Decimal("20")}],
            )
            bill = post_bill(bill_id=bill.id, organization=self.org_a)
            journal = bill.accounting_journal

            self.assertEqual(self.account_movement(journal, self.office_expense_account), Decimal("100.00"))
            self.assertEqual(self.account_movement(journal, self.inventory_account), Decimal("0"))
            self.assertEqual(StockMovement.objects.count(), 0)

    def test_posting_allocates_number_and_is_idempotent(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, lines=self._service_lines(),
            )
            posted = post_bill(bill_id=bill.id, organization=self.org_a)
            self.assertTrue(posted.bill_number.startswith("BILL-"))

            again = post_bill(bill_id=bill.id, organization=self.org_a)
            self.assertEqual(again.bill_number, posted.bill_number)
            self.assertEqual(JournalEntry.objects.count(), 1)

    def test_tax_without_recoverable_account_refused_at_post(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, lines=self._service_lines(tax_rate=Decimal("18")),
            )
            with self.assertRaises(ApplicationError) as ctx:
                post_bill(bill_id=bill.id, organization=self.org_a)
        self.assertEqual(ctx.exception.detail.code, "tax_account_required")

    def test_posted_bill_is_immutable(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, lines=self._service_lines(),
            )
            post_bill(bill_id=bill.id, organization=self.org_a)
            bill.refresh_from_db()
            bill.notes = "tampered"
            with self.assertRaises(ValueError):
                bill.save()
            with self.assertRaises(ApplicationError) as ctx:
                replace_bill_lines(bill=bill, lines=self._service_lines())
        self.assertEqual(ctx.exception.detail.code, "bill_not_draft")


class BillGoodsReceiptTests(PurchasesTestsBase):
    """The core of Phase 4: goods received once must never be received twice."""

    def test_bill_linked_to_receipt_does_not_receive_stock_again(self):
        with tenant_context(organization_id=self.org_a.id):
            _receipt, receipt_line = self._make_received_gr(quantity=Decimal("10"), unit_cost=Decimal("50.0000"))
            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("10"))

            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account,
                lines=[{
                    "item": self.product, "quantity": Decimal("10"), "unit_price": Decimal("50.00"),
                    "source_goods_receipt_line": receipt_line,
                }],
            )
            bill = post_bill(bill_id=bill.id, organization=self.org_a)

            # Stock unchanged: the receipt moved it, the bill only values it.
            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("10"))
            self.assertEqual(StockMovement.objects.filter(source_type="purchases.Bill").count(), 0)
            # ...but the accounting still lands.
            journal = bill.accounting_journal
            self.assert_journal_balanced(journal)
            self.assertEqual(self.account_movement(journal, self.inventory_account), Decimal("500.00"))
            self.assertEqual(self.account_movement(journal, self.ap_account), Decimal("-500.00"))

    def test_price_variance_posts_explicitly_when_vendor_bills_more(self):
        """Inventory is capitalised at the RECEIPTED cost so the GL matches
        the movement valuation; the difference is a visible variance."""
        with tenant_context(organization_id=self.org_a.id):
            _receipt, receipt_line = self._make_received_gr(quantity=Decimal("10"), unit_cost=Decimal("50.0000"))
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, price_variance_account=self.price_variance_account,
                lines=[{
                    "item": self.product, "quantity": Decimal("10"), "unit_price": Decimal("55.00"),
                    "source_goods_receipt_line": receipt_line,
                }],
            )
            bill = post_bill(bill_id=bill.id, organization=self.org_a)
            journal = bill.accounting_journal

            self.assert_journal_balanced(journal)
            self.assertEqual(self.account_movement(journal, self.inventory_account), Decimal("500.00"))
            self.assertEqual(self.account_movement(journal, self.price_variance_account), Decimal("50.00"))
            self.assertEqual(self.account_movement(journal, self.ap_account), Decimal("-550.00"))

    def test_favourable_variance_credits_the_variance_account(self):
        with tenant_context(organization_id=self.org_a.id):
            _receipt, receipt_line = self._make_received_gr(quantity=Decimal("10"), unit_cost=Decimal("50.0000"))
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, price_variance_account=self.price_variance_account,
                lines=[{
                    "item": self.product, "quantity": Decimal("10"), "unit_price": Decimal("45.00"),
                    "source_goods_receipt_line": receipt_line,
                }],
            )
            bill = post_bill(bill_id=bill.id, organization=self.org_a)
            journal = bill.accounting_journal

            self.assert_journal_balanced(journal)
            self.assertEqual(self.account_movement(journal, self.inventory_account), Decimal("500.00"))
            self.assertEqual(self.account_movement(journal, self.price_variance_account), Decimal("-50.00"))
            self.assertEqual(self.account_movement(journal, self.ap_account), Decimal("-450.00"))

    def test_variance_without_a_variance_account_refused_at_post(self):
        with tenant_context(organization_id=self.org_a.id):
            _receipt, receipt_line = self._make_received_gr(quantity=Decimal("10"), unit_cost=Decimal("50.0000"))
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account,
                lines=[{
                    "item": self.product, "quantity": Decimal("10"), "unit_price": Decimal("55.00"),
                    "source_goods_receipt_line": receipt_line,
                }],
            )
            with self.assertRaises(ApplicationError) as ctx:
                post_bill(bill_id=bill.id, organization=self.org_a)
        self.assertEqual(ctx.exception.detail.code, "price_variance_account_required")

    def test_no_variance_account_needed_when_prices_agree(self):
        with tenant_context(organization_id=self.org_a.id):
            _receipt, receipt_line = self._make_received_gr(quantity=Decimal("10"), unit_cost=Decimal("50.0000"))
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account,
                lines=[{
                    "item": self.product, "quantity": Decimal("10"), "unit_price": Decimal("50.00"),
                    "source_goods_receipt_line": receipt_line,
                }],
            )
            self.assertEqual(post_bill(bill_id=bill.id, organization=self.org_a).status, BillStatus.OPEN)

    def test_over_billing_a_receipt_line_is_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            _receipt, receipt_line = self._make_received_gr(quantity=Decimal("10"), unit_cost=Decimal("50.0000"))
            with self.assertRaises(ApplicationError) as ctx:
                create_bill(
                    organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                    payable_account=self.ap_account,
                    lines=[{
                        "item": self.product, "quantity": Decimal("11"), "unit_price": Decimal("50.00"),
                        "source_goods_receipt_line": receipt_line,
                    }],
                )
        self.assertEqual(ctx.exception.detail.code, "over_billing")

    def test_one_receipt_line_may_be_billed_across_two_partial_bills(self):
        """A unique constraint on the receipt-line FK would have blocked this
        legitimate case — hence the derived quantity guard instead."""
        with tenant_context(organization_id=self.org_a.id):
            _receipt, receipt_line = self._make_received_gr(quantity=Decimal("10"), unit_cost=Decimal("50.0000"))
            for quantity in (Decimal("4"), Decimal("6")):
                bill = create_bill(
                    organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                    payable_account=self.ap_account,
                    lines=[{
                        "item": self.product, "quantity": quantity, "unit_price": Decimal("50.00"),
                        "source_goods_receipt_line": receipt_line,
                    }],
                )
                post_bill(bill_id=bill.id, organization=self.org_a)
            self.assertEqual(
                get_billed_quantity_for_receipt_line(goods_receipt_line=receipt_line), Decimal("10")
            )
            # A third bill for the same goods is refused.
            with self.assertRaises(ApplicationError) as ctx:
                create_bill(
                    organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                    payable_account=self.ap_account,
                    lines=[{
                        "item": self.product, "quantity": Decimal("1"), "unit_price": Decimal("50.00"),
                        "source_goods_receipt_line": receipt_line,
                    }],
                )
        self.assertEqual(ctx.exception.detail.code, "over_billing")

    def test_voiding_a_bill_frees_the_receipt_line_to_be_billed_again(self):
        """The other half of why this is not a unique constraint: a voided
        bill must not leave genuinely received goods permanently unbillable."""
        with tenant_context(organization_id=self.org_a.id):
            _receipt, receipt_line = self._make_received_gr(quantity=Decimal("10"), unit_cost=Decimal("50.0000"))
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account,
                lines=[{
                    "item": self.product, "quantity": Decimal("10"), "unit_price": Decimal("50.00"),
                    "source_goods_receipt_line": receipt_line,
                }],
            )
            post_bill(bill_id=bill.id, organization=self.org_a)
            void_bill(bill_id=bill.id, organization=self.org_a, reason="wrong vendor")

            self.assertEqual(
                get_billed_quantity_for_receipt_line(goods_receipt_line=receipt_line), Decimal("0")
            )
            replacement = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account,
                lines=[{
                    "item": self.product, "quantity": Decimal("10"), "unit_price": Decimal("50.00"),
                    "source_goods_receipt_line": receipt_line,
                }],
            )
            self.assertEqual(post_bill(bill_id=replacement.id, organization=self.org_a).status, BillStatus.OPEN)

    def test_cannot_bill_against_an_unreceived_goods_receipt(self):
        from purchases.services.goods_receipts import create_goods_receipt

        with tenant_context(organization_id=self.org_a.id):
            order = self._make_approved_po()
            receipt = create_goods_receipt(
                organization=self.org_a, vendor=self.vendor, warehouse=self.warehouse,
                receipt_date=ORDER_DATE, source_purchase_order=order,
                lines=[{"item": self.product, "quantity": Decimal("10"),
                        "source_order_line": order.lines.first()}],
            )
            with self.assertRaises(ApplicationError) as ctx:
                create_bill(
                    organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                    payable_account=self.ap_account,
                    lines=[{
                        "item": self.product, "quantity": Decimal("10"), "unit_price": Decimal("50.00"),
                        "source_goods_receipt_line": receipt.lines.first(),
                    }],
                )
        self.assertEqual(ctx.exception.detail.code, "goods_receipt_not_received")

    def test_convert_receipt_to_bill_links_every_line(self):
        with tenant_context(organization_id=self.org_a.id):
            receipt, receipt_line = self._make_received_gr(quantity=Decimal("10"), unit_cost=Decimal("50.0000"))
            bill = create_bill_from_goods_receipt(
                receipt=receipt, bill_date=ORDER_DATE, due_date=DUE_DATE, payable_account=self.ap_account,
            )
            line = bill.lines.get()
            self.assertEqual(line.source_goods_receipt_line_id, receipt_line.id)
            self.assertEqual(line.quantity, Decimal("10.0000"))
            self.assertEqual(line.unit_price, Decimal("50.00"))

            post_bill(bill_id=bill.id, organization=self.org_a)
            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("10"))

    def test_convert_receipt_to_bill_refuses_when_fully_billed(self):
        with tenant_context(organization_id=self.org_a.id):
            receipt, _ = self._make_received_gr(quantity=Decimal("10"), unit_cost=Decimal("50.0000"))
            first = create_bill_from_goods_receipt(
                receipt=receipt, bill_date=ORDER_DATE, due_date=DUE_DATE, payable_account=self.ap_account
            )
            post_bill(bill_id=first.id, organization=self.org_a)
            with self.assertRaises(ApplicationError) as ctx:
                create_bill_from_goods_receipt(
                    receipt=receipt, bill_date=ORDER_DATE, due_date=DUE_DATE, payable_account=self.ap_account
                )
        self.assertEqual(ctx.exception.detail.code, "goods_receipt_fully_billed")


class BillVoidTests(PurchasesTestsBase):
    def test_void_reverses_journal_and_directly_received_stock(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, warehouse=self.warehouse,
                lines=self._product_lines(quantity=Decimal("10"), unit_price=Decimal("50.00")),
            )
            post_bill(bill_id=bill.id, organization=self.org_a)
            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("10"))

            voided = void_bill(bill_id=bill.id, organization=self.org_a, reason="duplicate")
            self.assertEqual(voided.status, BillStatus.VOID)
            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("0"))
            reversal = StockMovement.objects.filter(source_type="purchases.Bill.void").get()
            self.assertEqual(reversal.movement_type, MovementType.ADJUSTMENT_OUT)
            # Reversal journal, not an edit of the original.
            self.assertEqual(JournalEntry.objects.count(), 2)

    def test_void_never_reverses_stock_that_arrived_on_a_goods_receipt(self):
        """The goods physically turned up — voiding the paperwork must not
        make them vanish from the warehouse."""
        with tenant_context(organization_id=self.org_a.id):
            _receipt, receipt_line = self._make_received_gr(quantity=Decimal("10"), unit_cost=Decimal("50.0000"))
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account,
                lines=[{
                    "item": self.product, "quantity": Decimal("10"), "unit_price": Decimal("50.00"),
                    "source_goods_receipt_line": receipt_line,
                }],
            )
            post_bill(bill_id=bill.id, organization=self.org_a)
            void_bill(bill_id=bill.id, organization=self.org_a, reason="wrong amount")

            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("10"))
            self.assertFalse(StockMovement.objects.filter(source_type="purchases.Bill.void").exists())

    def test_void_is_idempotent(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, lines=self._service_lines(),
            )
            post_bill(bill_id=bill.id, organization=self.org_a)
            void_bill(bill_id=bill.id, organization=self.org_a)
            void_bill(bill_id=bill.id, organization=self.org_a)
            self.assertEqual(JournalEntry.objects.count(), 2)

    def test_cannot_void_a_draft(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, lines=self._service_lines(),
            )
            with self.assertRaises(ApplicationError) as ctx:
                void_bill(bill_id=bill.id, organization=self.org_a)
        self.assertEqual(ctx.exception.detail.code, "bill_invalid_status")


class BillSelectorTests(PurchasesTestsBase):
    def test_amount_due_starts_at_the_full_total(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, lines=self._service_lines(),
            )
            bill = post_bill(bill_id=bill.id, organization=self.org_a)
            self.assertEqual(get_bill_amount_due(bill=bill), Decimal("200.00"))


class BillTenantIsolationTests(PurchasesTestsBase):
    def test_other_org_cannot_see_or_post_bill(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, lines=self._service_lines(),
            )
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(Bill.objects.count(), 0)
            with self.assertRaises(ApplicationError) as ctx:
                post_bill(bill_id=bill.id, organization=self.org_b)
        self.assertEqual(ctx.exception.detail.code, "bill_not_found")
