import uuid
from collections import defaultdict
from decimal import Decimal

from django.db import transaction

from accounting.models.account import AccountType
from accounts.services import allocate_sequence_number
from audit.models import AuditLog
from audit.services import record as record_audit
from core.enums import PaymentMethod
from core.exceptions import ApplicationError
from purchases.accounting_bridge import post_purchase_journal
from purchases.models.bill import Bill, BillStatus
from purchases.models.payment import VendorPayment, VendorPaymentAllocation
from purchases.models.vendor import Vendor
from purchases.selectors import get_bill_amount_due
from purchases.services.bills import refresh_bill_payment_status

VENDOR_PAYMENT_NUMBER_SEQUENCE_KEY = "vendor_payment"


def _validate_account(*, organization, account, expected_type, field_name):
    if account.organization_id != organization.id:
        raise ApplicationError(f"{field_name} must belong to the posting organization.", code="cross_org_reference")
    if account.account_type != expected_type:
        raise ApplicationError(
            f"{field_name} must reference an account of type '{expected_type}'.", code="invalid_account_type"
        )


@transaction.atomic
def record_vendor_payment(
    *,
    organization,
    vendor: Vendor,
    payment_date,
    amount: Decimal,
    source_account,
    allocations: list[dict],
    currency=None,
    payment_method: str = PaymentMethod.OTHER,
    reference: str = "",
    notes: str = "",
    vendor_advance_account=None,
    actor=None,
) -> VendorPayment:
    """Records one payment to a vendor and allocates it across one or more
    bills in a single atomic operation — the mirror of
    sales.services.payments.record_payment.

    Any amount not explicitly allocated becomes a vendor advance (a
    VendorPaymentAllocation with bill=None), never a negative bill balance
    and never a silent over-allocation. The advance sits in an ASSET account
    — money we have paid out and not yet consumed — which is the exact
    mirror of unapplied customer credit being a LIABILITY.

    Duplicate-request safety is the caller's responsibility via the
    Idempotency-Key header (core.idempotency): there is no DRAFT/POSTED
    status to exploit for idempotent-by-construction posting, unlike post_bill.
    """
    if vendor.organization_id != organization.id:
        raise ApplicationError("Vendor must belong to the posting organization.", code="vendor_cross_org")
    _validate_account(
        organization=organization, account=source_account, expected_type=AccountType.ASSET,
        field_name="source_account",
    )
    if amount <= 0:
        raise ApplicationError("Payment amount must be positive.", code="payment_amount_invalid")

    currency = currency or vendor.currency

    locked_bills = {}
    allocated_total = Decimal("0")
    validated_allocations = []
    for entry in allocations:
        bill_id = entry["bill"].id
        alloc_amount = entry["amount"]
        if alloc_amount <= 0:
            raise ApplicationError("Allocation amount must be positive.", code="allocation_amount_invalid")

        # Locked so two concurrent payments against the same bill can never
        # both succeed in over-allocating it — same pattern as
        # inventory.services.movements.record_stock_movement's stock guard.
        bill = locked_bills.get(bill_id)
        if bill is None:
            try:
                bill = Bill.objects.select_for_update().get(id=bill_id, organization=organization)
            except Bill.DoesNotExist:
                raise ApplicationError("Bill not found.", code="bill_not_found", status_code=404)
            locked_bills[bill_id] = bill

        if bill.vendor_id != vendor.id:
            raise ApplicationError("Bill does not belong to this vendor.", code="bill_vendor_mismatch")
        if bill.status not in (BillStatus.OPEN, BillStatus.PARTIALLY_PAID):
            raise ApplicationError(
                f"Cannot allocate a payment to a bill in status '{bill.status}'.", code="bill_not_payable"
            )

        already_allocated_here = sum(
            (a["amount"] for a in validated_allocations if a["bill"].id == bill_id), Decimal("0")
        )
        due = get_bill_amount_due(bill=bill) - already_allocated_here
        if alloc_amount > due:
            raise ApplicationError(
                f"Allocation ({alloc_amount}) exceeds the amount due on this bill ({due}).",
                code="over_allocation",
            )

        allocated_total += alloc_amount
        validated_allocations.append({"bill": bill, "amount": alloc_amount})

    if allocated_total > amount:
        raise ApplicationError(
            "Sum of allocations cannot exceed the payment amount.", code="allocation_exceeds_payment"
        )
    remainder = amount - allocated_total
    if remainder > 0:
        if vendor_advance_account is None:
            raise ApplicationError(
                "vendor_advance_account is required when the payment exceeds its allocated bills.",
                code="vendor_advance_account_required",
            )
        _validate_account(
            organization=organization, account=vendor_advance_account, expected_type=AccountType.ASSET,
            field_name="vendor_advance_account",
        )

    payment_number = allocate_sequence_number(
        organization_id=organization.id, key=VENDOR_PAYMENT_NUMBER_SEQUENCE_KEY, prefix="VPAY-"
    )

    # VendorPayment.save() refuses any update after the initial INSERT
    # (append-only, like StockMovement/AuditLog), so accounting_journal must
    # be known BEFORE that one create() call, not patched in with a second
    # .save(). Generate the id up front so the journal's source_id can
    # reference it — same construction as sales.services.payments.
    payment_id = uuid.uuid4()

    debit_by_account = defaultdict(lambda: Decimal("0"))
    for entry in validated_allocations:
        debit_by_account[entry["bill"].payable_account_id] += entry["amount"]
    if remainder > 0:
        debit_by_account[vendor_advance_account.id] += remainder

    journal_lines = [{"account_id": source_account.id, "credit": amount}]
    for account_id, amt in debit_by_account.items():
        if amt != 0:
            journal_lines.append({"account_id": account_id, "debit": amt})

    journal = post_purchase_journal(
        organization=organization,
        posting_date=payment_date,
        currency=currency,
        lines=journal_lines,
        memo=f"Vendor payment {payment_number}",
        source_type="purchases.VendorPayment",
        source_id=str(payment_id),
        actor=actor,
    )

    payment = VendorPayment.objects.create(
        id=payment_id,
        organization=organization,
        vendor=vendor,
        payment_number=payment_number,
        payment_date=payment_date,
        amount=amount,
        currency=currency,
        payment_method=payment_method,
        reference=reference,
        source_account=source_account,
        accounting_journal=journal,
        notes=notes,
        created_by=actor,
    )
    for entry in validated_allocations:
        VendorPaymentAllocation.objects.create(
            organization=organization, payment=payment, bill=entry["bill"], amount=entry["amount"]
        )
    if remainder > 0:
        VendorPaymentAllocation.objects.create(
            organization=organization, payment=payment, bill=None, amount=remainder
        )

    for entry in validated_allocations:
        refresh_bill_payment_status(bill=entry["bill"], actor=actor)

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="purchases.VendorPayment",
        object_id=payment.id,
        changes={
            "payment_number": payment_number, "amount": str(amount),
            "allocated": str(allocated_total), "advance": str(remainder),
        },
    )
    return payment
