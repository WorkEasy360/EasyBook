from decimal import Decimal

from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from purchases.models.bill import BillStatus
from purchases.models.payment import VendorPayment, VendorPaymentAllocation
from purchases.selectors import get_bill_amount_due, get_bill_amount_paid, get_payment_unapplied_amount
from purchases.services.bills import create_bill, post_bill
from purchases.services.payments import record_vendor_payment
from purchases.tests.base import DUE_DATE, ORDER_DATE, PurchasesTestsBase


class VendorPaymentTestsBase(PurchasesTestsBase):
    def _open_bill(self, unit_price=Decimal("100.00"), quantity=Decimal("2")):
        bill = create_bill(
            organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
            payable_account=self.ap_account,
            lines=self._service_lines(quantity=quantity, unit_price=unit_price),
        )
        return post_bill(bill_id=bill.id, organization=self.org_a)


class VendorPaymentTests(VendorPaymentTestsBase):
    def test_full_payment_settles_bill_and_posts_dr_ap_cr_bank(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_bill()
            payment = record_vendor_payment(
                organization=self.org_a, vendor=self.vendor, payment_date=ORDER_DATE,
                amount=Decimal("200.00"), source_account=self.bank_account,
                allocations=[{"bill": bill, "amount": Decimal("200.00")}],
            )
            journal = payment.accounting_journal
            self.assert_journal_balanced(journal)
            self.assertEqual(self.account_movement(journal, self.ap_account), Decimal("200.00"))
            self.assertEqual(self.account_movement(journal, self.bank_account), Decimal("-200.00"))

            bill.refresh_from_db()
            self.assertEqual(bill.status, BillStatus.PAID)
            self.assertEqual(get_bill_amount_due(bill=bill), Decimal("0.00"))

    def test_partial_payment_moves_bill_to_partially_paid(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_bill()
            record_vendor_payment(
                organization=self.org_a, vendor=self.vendor, payment_date=ORDER_DATE,
                amount=Decimal("75.00"), source_account=self.bank_account,
                allocations=[{"bill": bill, "amount": Decimal("75.00")}],
            )
            bill.refresh_from_db()
            self.assertEqual(bill.status, BillStatus.PARTIALLY_PAID)
            self.assertEqual(get_bill_amount_paid(bill=bill), Decimal("75.00"))
            self.assertEqual(get_bill_amount_due(bill=bill), Decimal("125.00"))

    def test_overpayment_becomes_a_vendor_advance_not_a_negative_balance(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_bill()
            payment = record_vendor_payment(
                organization=self.org_a, vendor=self.vendor, payment_date=ORDER_DATE,
                amount=Decimal("250.00"), source_account=self.bank_account,
                allocations=[{"bill": bill, "amount": Decimal("200.00")}],
                vendor_advance_account=self.vendor_advance_account,
            )
            self.assertEqual(get_payment_unapplied_amount(payment=payment), Decimal("50.00"))
            self.assertEqual(get_bill_amount_due(bill=bill), Decimal("0.00"))

            journal = payment.accounting_journal
            self.assert_journal_balanced(journal)
            # An advance is an ASSET — the mirror of unapplied customer credit
            # being a liability.
            self.assertEqual(self.account_movement(journal, self.vendor_advance_account), Decimal("50.00"))

    def test_advance_account_required_when_payment_exceeds_allocations(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_bill()
            with self.assertRaises(ApplicationError) as ctx:
                record_vendor_payment(
                    organization=self.org_a, vendor=self.vendor, payment_date=ORDER_DATE,
                    amount=Decimal("250.00"), source_account=self.bank_account,
                    allocations=[{"bill": bill, "amount": Decimal("200.00")}],
                )
        self.assertEqual(ctx.exception.detail.code, "vendor_advance_account_required")

    def test_advance_account_must_be_an_asset(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                record_vendor_payment(
                    organization=self.org_a, vendor=self.vendor, payment_date=ORDER_DATE,
                    amount=Decimal("50.00"), source_account=self.bank_account, allocations=[],
                    vendor_advance_account=self.ap_account,
                )
        self.assertEqual(ctx.exception.detail.code, "invalid_account_type")

    def test_over_allocation_to_one_bill_rejected(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_bill()
            with self.assertRaises(ApplicationError) as ctx:
                record_vendor_payment(
                    organization=self.org_a, vendor=self.vendor, payment_date=ORDER_DATE,
                    amount=Decimal("300.00"), source_account=self.bank_account,
                    allocations=[{"bill": bill, "amount": Decimal("300.00")}],
                )
        self.assertEqual(ctx.exception.detail.code, "over_allocation")

    def test_two_allocations_to_the_same_bill_cannot_jointly_overpay(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_bill()
            with self.assertRaises(ApplicationError) as ctx:
                record_vendor_payment(
                    organization=self.org_a, vendor=self.vendor, payment_date=ORDER_DATE,
                    amount=Decimal("250.00"), source_account=self.bank_account,
                    allocations=[
                        {"bill": bill, "amount": Decimal("150.00")},
                        {"bill": bill, "amount": Decimal("100.00")},
                    ],
                )
        self.assertEqual(ctx.exception.detail.code, "over_allocation")

    def test_allocations_cannot_exceed_the_payment_amount(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_bill()
            with self.assertRaises(ApplicationError) as ctx:
                record_vendor_payment(
                    organization=self.org_a, vendor=self.vendor, payment_date=ORDER_DATE,
                    amount=Decimal("50.00"), source_account=self.bank_account,
                    allocations=[{"bill": bill, "amount": Decimal("150.00")}],
                )
        self.assertEqual(ctx.exception.detail.code, "allocation_exceeds_payment")

    def test_cannot_pay_a_draft_bill(self):
        with tenant_context(organization_id=self.org_a.id):
            draft = create_bill(
                organization=self.org_a, vendor=self.vendor, bill_date=ORDER_DATE, due_date=DUE_DATE,
                payable_account=self.ap_account, lines=self._service_lines(),
            )
            with self.assertRaises(ApplicationError) as ctx:
                record_vendor_payment(
                    organization=self.org_a, vendor=self.vendor, payment_date=ORDER_DATE,
                    amount=Decimal("10.00"), source_account=self.bank_account,
                    allocations=[{"bill": draft, "amount": Decimal("10.00")}],
                )
        self.assertEqual(ctx.exception.detail.code, "bill_not_payable")

    def test_cannot_allocate_to_another_vendors_bill(self):
        from purchases.services.vendors import create_vendor

        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_bill()
            other = create_vendor(
                organization=self.org_a, vendor_code="VEN-9", display_name="Other", currency=self.currency
            )
            with self.assertRaises(ApplicationError) as ctx:
                record_vendor_payment(
                    organization=self.org_a, vendor=other, payment_date=ORDER_DATE,
                    amount=Decimal("50.00"), source_account=self.bank_account,
                    allocations=[{"bill": bill, "amount": Decimal("50.00")}],
                )
        self.assertEqual(ctx.exception.detail.code, "bill_vendor_mismatch")

    def test_source_account_must_be_an_asset(self):
        with tenant_context(organization_id=self.org_a.id):
            with self.assertRaises(ApplicationError) as ctx:
                record_vendor_payment(
                    organization=self.org_a, vendor=self.vendor, payment_date=ORDER_DATE,
                    amount=Decimal("10.00"), source_account=self.ap_account, allocations=[],
                    vendor_advance_account=self.vendor_advance_account,
                )
        self.assertEqual(ctx.exception.detail.code, "invalid_account_type")

    def test_payment_is_append_only(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_bill()
            payment = record_vendor_payment(
                organization=self.org_a, vendor=self.vendor, payment_date=ORDER_DATE,
                amount=Decimal("200.00"), source_account=self.bank_account,
                allocations=[{"bill": bill, "amount": Decimal("200.00")}],
            )
            payment.reference = "tampered"
            with self.assertRaises(ValueError):
                payment.save()
            with self.assertRaises(ValueError):
                payment.delete()

            allocation = VendorPaymentAllocation.objects.get(payment=payment, bill=bill)
            with self.assertRaises(ValueError):
                allocation.delete()

    def test_paid_bill_cannot_be_voided(self):
        from purchases.services.bills import void_bill

        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_bill()
            record_vendor_payment(
                organization=self.org_a, vendor=self.vendor, payment_date=ORDER_DATE,
                amount=Decimal("100.00"), source_account=self.bank_account,
                allocations=[{"bill": bill, "amount": Decimal("100.00")}],
            )
            with self.assertRaises(ApplicationError) as ctx:
                void_bill(bill_id=bill.id, organization=self.org_a)
        self.assertEqual(ctx.exception.detail.code, "bill_has_payments")


class VendorPaymentTenantIsolationTests(VendorPaymentTestsBase):
    def test_other_org_cannot_see_payment(self):
        with tenant_context(organization_id=self.org_a.id):
            bill = self._open_bill()
            record_vendor_payment(
                organization=self.org_a, vendor=self.vendor, payment_date=ORDER_DATE,
                amount=Decimal("200.00"), source_account=self.bank_account,
                allocations=[{"bill": bill, "amount": Decimal("200.00")}],
            )
        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(VendorPayment.objects.count(), 0)
            self.assertEqual(VendorPaymentAllocation.objects.count(), 0)
