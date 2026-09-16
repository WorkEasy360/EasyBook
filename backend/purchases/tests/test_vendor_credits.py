from decimal import Decimal

from accounting.models.journal import JournalEntry
from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from inventory.models.stock_movement import MovementType, StockMovement
from inventory.selectors import get_stock_on_hand
from purchases.models.bill import BillStatus
from purchases.models.vendor_credit import VendorCredit, VendorCreditStatus
from purchases.selectors import get_bill_amount_credited, get_bill_amount_due
from purchases.services.bills import create_bill, post_bill
from purchases.services.vendor_credits import (
    create_vendor_credit,
    issue_vendor_credit,
    replace_vendor_credit_lines,
    void_vendor_credit,
)
from purchases.tests.base import DUE_DATE, ORDER_DATE, PurchasesTestsBase


class VendorCreditTestsBase(PurchasesTestsBase):
    def _open_service_bill(self, unit_price=Decimal("100.00"), quantity=Decimal("2")):
        bill = create_bill(
            organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
            payable_account=self.ap_account,
            lines=self._service_lines(quantity=quantity, unit_price=unit_price),
        )
        return post_bill(bill_id=bill.id, organization=self.org_a)

    def _open_product_bill(self, quantity=Decimal("10"), unit_price=Decimal("50.00")):
        bill = create_bill(
            organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
            payable_account=self.ap_account, warehouse=self.warehouse,
            lines=self._product_lines(quantity=quantity, unit_price=unit_price),
        )
        return post_bill(bill_id=bill.id, organization=self.org_a)


class VendorCreditIssueTests(VendorCreditTestsBase):
    def test_issue_against_a_bill_debits_ap_and_credits_the_expense(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_service_bill()
            credit = create_vendor_credit(
                organization=self.org_a, vendor=self.vendor, credit_date=ORDER_DATE, source_bill=bill,
                lines=self._service_lines(quantity=Decimal("1"), unit_price=Decimal("100.00")),
            )
            credit = issue_vendor_credit(vendor_credit_id=credit.id, organization=self.org_a)
            journal = credit.accounting_journal

            self.assertEqual(credit.status, VendorCreditStatus.ISSUED)
            self.assertTrue(credit.credit_number.startswith("VC-"))
            self.assert_journal_balanced(journal)
            # AP reduced (debit), the original expense reversed (credit).
            self.assertEqual(self.account_movement(journal, self.ap_account), Decimal("100.00"))
            self.assertEqual(self.account_movement(journal, self.purchase_expense_account), Decimal("-100.00"))

    def test_credit_reduces_the_bill_balance_without_mutating_the_bill(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_service_bill()
            original_total = bill.total
            credit = create_vendor_credit(
                organization=self.org_a, vendor=self.vendor, credit_date=ORDER_DATE, source_bill=bill,
                lines=self._service_lines(quantity=Decimal("1"), unit_price=Decimal("100.00")),
            )
            issue_vendor_credit(vendor_credit_id=credit.id, organization=self.org_a)

            bill.refresh_from_db()
            self.assertEqual(bill.total, original_total)  # never rewritten
            self.assertEqual(bill.status, BillStatus.PARTIALLY_PAID)
            self.assertEqual(get_bill_amount_credited(bill=bill), Decimal("100.00"))
            self.assertEqual(get_bill_amount_due(bill=bill), Decimal("100.00"))

    def test_credit_covering_the_whole_bill_settles_it(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_service_bill()
            credit = create_vendor_credit(
                organization=self.org_a, vendor=self.vendor, credit_date=ORDER_DATE, source_bill=bill,
                lines=self._service_lines(quantity=Decimal("2"), unit_price=Decimal("100.00")),
            )
            issue_vendor_credit(vendor_credit_id=credit.id, organization=self.org_a)
            bill.refresh_from_db()
            self.assertEqual(bill.status, BillStatus.PAID)
            self.assertEqual(get_bill_amount_due(bill=bill), Decimal("0.00"))

    def test_excess_credit_becomes_a_vendor_credit_asset(self):
        """The mirror of sales' unapplied CUSTOMER credit being a liability:
        credit we hold WITH a supplier is value owed to us — an asset."""
        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_service_bill(unit_price=Decimal("50.00"), quantity=Decimal("2"))  # total 100
            credit = create_vendor_credit(
                organization=self.org_a, vendor=self.vendor, credit_date=ORDER_DATE, source_bill=bill,
                unapplied_credit_account=self.vendor_advance_account,
                lines=self._service_lines(quantity=Decimal("1"), unit_price=Decimal("150.00")),
            )
            credit = issue_vendor_credit(vendor_credit_id=credit.id, organization=self.org_a)
            journal = credit.accounting_journal

            self.assert_journal_balanced(journal)
            self.assertEqual(credit.amount_applied_to_bill, Decimal("100.00"))
            self.assertEqual(self.account_movement(journal, self.ap_account), Decimal("100.00"))
            self.assertEqual(self.account_movement(journal, self.vendor_advance_account), Decimal("50.00"))

    def test_unapplied_account_required_when_credit_exceeds_the_bill(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_service_bill(unit_price=Decimal("50.00"), quantity=Decimal("2"))
            credit = create_vendor_credit(
                organization=self.org_a, vendor=self.vendor, credit_date=ORDER_DATE, source_bill=bill,
                lines=self._service_lines(quantity=Decimal("1"), unit_price=Decimal("150.00")),
            )
            with self.assertRaises(ApplicationError) as ctx:
                issue_vendor_credit(vendor_credit_id=credit.id, organization=self.org_a)
        self.assertEqual(ctx.exception.detail.code, "unapplied_credit_account_required")

    def test_standalone_credit_goes_entirely_to_the_unapplied_account(self):
        with tenant_context(organization_id=self.org_a.id):
            credit = create_vendor_credit(
                organization=self.org_a, vendor=self.vendor, credit_date=ORDER_DATE,
                unapplied_credit_account=self.vendor_advance_account,
                lines=self._service_lines(quantity=Decimal("1"), unit_price=Decimal("80.00")),
            )
            credit = issue_vendor_credit(vendor_credit_id=credit.id, organization=self.org_a)
            journal = credit.accounting_journal

            self.assert_journal_balanced(journal)
            self.assertEqual(credit.amount_applied_to_bill, Decimal("0.00"))
            self.assertEqual(self.account_movement(journal, self.vendor_advance_account), Decimal("80.00"))

    def test_tax_is_reversed_out_of_the_recoverable_account(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, tax_recoverable_account=self.input_tax_account,
                lines=self._service_lines(quantity=Decimal("2"), unit_price=Decimal("100.00"),
                                          tax_rate=Decimal("18")),
            )
            bill = post_bill(bill_id=bill.id, organization=self.org_a)
            credit = create_vendor_credit(
                organization=self.org_a, vendor=self.vendor, credit_date=ORDER_DATE, source_bill=bill,
                lines=self._service_lines(quantity=Decimal("1"), unit_price=Decimal("100.00"),
                                          tax_rate=Decimal("18")),
            )
            credit = issue_vendor_credit(vendor_credit_id=credit.id, organization=self.org_a)
            journal = credit.accounting_journal

            self.assert_journal_balanced(journal)
            # Input tax previously claimed is given back: a credit on the
            # recoverable ASSET account.
            self.assertEqual(self.account_movement(journal, self.input_tax_account), Decimal("-18.00"))

    def test_issue_is_idempotent(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_service_bill()
            credit = create_vendor_credit(
                organization=self.org_a, vendor=self.vendor, credit_date=ORDER_DATE, source_bill=bill,
                lines=self._service_lines(quantity=Decimal("1"), unit_price=Decimal("100.00")),
            )
            first = issue_vendor_credit(vendor_credit_id=credit.id, organization=self.org_a)
            second = issue_vendor_credit(vendor_credit_id=credit.id, organization=self.org_a)
            self.assertEqual(first.credit_number, second.credit_number)
            # The bill's journal, plus exactly one for this credit.
            self.assertEqual(JournalEntry.objects.count(), 2)

    def test_credit_cannot_exceed_the_linked_bill_line_quantity(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_service_bill(quantity=Decimal("2"))
            with self.assertRaises(ApplicationError) as ctx:
                create_vendor_credit(
                    organization=self.org_a, vendor=self.vendor, credit_date=ORDER_DATE, source_bill=bill,
                    lines=[{
                        "item": self.service, "quantity": Decimal("3"), "unit_price": Decimal("100.00"),
                        "source_bill_line": bill.lines.first(),
                    }],
                )
        self.assertEqual(ctx.exception.detail.code, "credit_quantity_exceeds_bill")

    def test_bill_must_belong_to_the_same_vendor(self):
        from purchases.services.vendors import create_vendor

        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_service_bill()
            other = create_vendor(
                organization=self.org_a, vendor_code="VEN-9", display_name="Other", currency=self.currency
            )
            with self.assertRaises(ApplicationError) as ctx:
                create_vendor_credit(
                    organization=self.org_a, vendor=other, credit_date=ORDER_DATE, source_bill=bill,
                    lines=self._service_lines(),
                )
        self.assertEqual(ctx.exception.detail.code, "bill_vendor_mismatch")


class VendorCreditStockReturnTests(VendorCreditTestsBase):
    def test_return_stock_issues_an_outbound_movement_and_credits_inventory(self):
        """Goods go BACK to the vendor — the exact inverse of the sales-side
        restock, which brings goods back IN."""
        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_product_bill(quantity=Decimal("10"), unit_price=Decimal("50.00"))
            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("10"))

            credit = create_vendor_credit(
                organization=self.org_a, vendor=self.vendor, credit_date=ORDER_DATE, source_bill=bill,
                warehouse=self.warehouse,
                lines=[{
                    "item": self.product, "quantity": Decimal("4"), "unit_price": Decimal("50.00"),
                    "return_stock": True, "unit_cost": Decimal("50.0000"),
                    "source_bill_line": bill.lines.first(),
                }],
            )
            credit = issue_vendor_credit(vendor_credit_id=credit.id, organization=self.org_a)

            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("6"))
            movement = StockMovement.objects.filter(source_type="purchases.VendorCredit").get()
            self.assertEqual(movement.movement_type, MovementType.ADJUSTMENT_OUT)

            journal = credit.accounting_journal
            self.assert_journal_balanced(journal)
            self.assertEqual(self.account_movement(journal, self.inventory_account), Decimal("-200.00"))
            self.assertEqual(self.account_movement(journal, self.ap_account), Decimal("200.00"))

    def test_credit_without_return_stock_moves_no_stock(self):
        """A pricing-error credit must not move goods that never left."""
        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_product_bill(quantity=Decimal("10"), unit_price=Decimal("50.00"))
            credit = create_vendor_credit(
                organization=self.org_a, vendor=self.vendor, credit_date=ORDER_DATE, source_bill=bill,
                reason="pricing_error",
                lines=[{
                    "item": self.product, "quantity": Decimal("10"), "unit_price": Decimal("5.00"),
                    "source_bill_line": bill.lines.first(),
                }],
            )
            issue_vendor_credit(vendor_credit_id=credit.id, organization=self.org_a)
            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("10"))
            self.assertFalse(StockMovement.objects.filter(source_type="purchases.VendorCredit").exists())

    def test_service_item_cannot_be_returned_to_stock(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_vendor_credit(
                    organization=self.org_a, vendor=self.vendor, credit_date=ORDER_DATE,
                    warehouse=self.warehouse, unapplied_credit_account=self.vendor_advance_account,
                    lines=[{
                        "item": self.service, "quantity": Decimal("1"), "unit_price": Decimal("10.00"),
                        "return_stock": True, "unit_cost": Decimal("10.0000"),
                    }],
                )
        self.assertEqual(ctx.exception.detail.code, "service_cannot_return_stock")

    def test_return_requires_a_unit_cost_and_a_warehouse(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                create_vendor_credit(
                    organization=self.org_a, vendor=self.vendor, credit_date=ORDER_DATE,
                    warehouse=self.warehouse, unapplied_credit_account=self.vendor_advance_account,
                    lines=[{
                        "item": self.product, "quantity": Decimal("1"), "unit_price": Decimal("50.00"),
                        "return_stock": True,
                    }],
                )
            self.assertEqual(ctx.exception.detail.code, "return_cost_required")

            with self.assertRaises(ApplicationError) as ctx:
                create_vendor_credit(
                    organization=self.org_a, vendor=self.vendor, credit_date=ORDER_DATE,
                    unapplied_credit_account=self.vendor_advance_account,
                    lines=[{
                        "item": self.product, "quantity": Decimal("1"), "unit_price": Decimal("50.00"),
                        "return_stock": True, "unit_cost": Decimal("50.0000"),
                    }],
                )
        self.assertEqual(ctx.exception.detail.code, "warehouse_required")


class VendorCreditVoidTests(VendorCreditTestsBase):
    def test_void_reverses_journal_and_the_stock_return(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_product_bill(quantity=Decimal("10"), unit_price=Decimal("50.00"))
            credit = create_vendor_credit(
                organization=self.org_a, vendor=self.vendor, credit_date=ORDER_DATE, source_bill=bill,
                warehouse=self.warehouse,
                lines=[{
                    "item": self.product, "quantity": Decimal("4"), "unit_price": Decimal("50.00"),
                    "return_stock": True, "unit_cost": Decimal("50.0000"),
                    "source_bill_line": bill.lines.first(),
                }],
            )
            issue_vendor_credit(vendor_credit_id=credit.id, organization=self.org_a)
            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("6"))

            voided = void_vendor_credit(vendor_credit_id=credit.id, organization=self.org_a)
            self.assertEqual(voided.status, VendorCreditStatus.VOID)
            self.assertEqual(get_stock_on_hand(item=self.product, warehouse=self.warehouse), Decimal("10"))
            reversal = StockMovement.objects.filter(source_type="purchases.VendorCredit.void").get()
            self.assertEqual(reversal.movement_type, MovementType.ADJUSTMENT_IN)

    def test_void_restores_the_bill_balance(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_service_bill()
            credit = create_vendor_credit(
                organization=self.org_a, vendor=self.vendor, credit_date=ORDER_DATE, source_bill=bill,
                lines=self._service_lines(quantity=Decimal("1"), unit_price=Decimal("100.00")),
            )
            issue_vendor_credit(vendor_credit_id=credit.id, organization=self.org_a)
            self.assertEqual(get_bill_amount_due(bill=bill), Decimal("100.00"))

            void_vendor_credit(vendor_credit_id=credit.id, organization=self.org_a)
            bill.refresh_from_db()
            self.assertEqual(get_bill_amount_due(bill=bill), Decimal("200.00"))

    def test_void_is_idempotent(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_service_bill()
            credit = create_vendor_credit(
                organization=self.org_a, vendor=self.vendor, credit_date=ORDER_DATE, source_bill=bill,
                lines=self._service_lines(quantity=Decimal("1"), unit_price=Decimal("100.00")),
            )
            issue_vendor_credit(vendor_credit_id=credit.id, organization=self.org_a)
            void_vendor_credit(vendor_credit_id=credit.id, organization=self.org_a)
            void_vendor_credit(vendor_credit_id=credit.id, organization=self.org_a)
            # Bill, credit, and exactly one reversal - not two.
            self.assertEqual(JournalEntry.objects.count(), 3)

    def test_issued_credit_is_immutable(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_service_bill()
            credit = create_vendor_credit(
                organization=self.org_a, vendor=self.vendor, credit_date=ORDER_DATE, source_bill=bill,
                lines=self._service_lines(quantity=Decimal("1"), unit_price=Decimal("100.00")),
            )
            issue_vendor_credit(vendor_credit_id=credit.id, organization=self.org_a)
            credit.refresh_from_db()
            credit.notes = "tampered"
            with self.assertRaises(ValueError):
                credit.save()
            with self.assertRaises(ApplicationError) as ctx:
                replace_vendor_credit_lines(vendor_credit=credit, lines=self._service_lines())
        self.assertEqual(ctx.exception.detail.code, "vendor_credit_not_draft")

    def test_bill_with_credits_applied_cannot_be_voided(self):
        from purchases.services.bills import void_bill

        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_service_bill()
            credit = create_vendor_credit(
                organization=self.org_a, vendor=self.vendor, credit_date=ORDER_DATE, source_bill=bill,
                lines=self._service_lines(quantity=Decimal("1"), unit_price=Decimal("100.00")),
            )
            issue_vendor_credit(vendor_credit_id=credit.id, organization=self.org_a)
            with self.assertRaises(ApplicationError) as ctx:
                void_bill(bill_id=bill.id, organization=self.org_a)
        self.assertEqual(ctx.exception.detail.code, "bill_has_credits")


class VendorCreditTenantIsolationTests(VendorCreditTestsBase):
    def test_other_org_cannot_see_or_issue(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_service_bill()
            credit = create_vendor_credit(
                organization=self.org_a, vendor=self.vendor, credit_date=ORDER_DATE, source_bill=bill,
                lines=self._service_lines(quantity=Decimal("1"), unit_price=Decimal("100.00")),
            )
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(VendorCredit.objects.count(), 0)
            with self.assertRaises(ApplicationError) as ctx:
                issue_vendor_credit(vendor_credit_id=credit.id, organization=self.org_b)
        self.assertEqual(ctx.exception.detail.code, "vendor_credit_not_found")
