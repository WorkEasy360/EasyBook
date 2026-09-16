"""Read-only purchase queries.

Every function here derives its answer from the underlying documents rather
than reading a stored total — the same never-cache-a-cross-document-sum
principle as inventory stock-on-hand, the General Ledger, and
`sales/selectors.py`. Only what Phase 4 actually needs (root CLAUDE.md:
the Reports module is Phase 8, not this one).
"""

from decimal import Decimal

from django.db.models import Sum


def get_received_quantity(*, purchase_order_line) -> Decimal:
    """Sums GoodsReceiptLine quantities linked to this PurchaseOrderLine
    across RECEIVED receipts. Draft and cancelled receipts contribute
    nothing — nothing physically arrived. Never a stored field on
    PurchaseOrderLine."""
    from purchases.models.goods_receipt import GoodsReceiptLine, GoodsReceiptStatus

    total = GoodsReceiptLine.objects.filter(
        source_order_line=purchase_order_line,
        receipt__status=GoodsReceiptStatus.RECEIVED,
    ).aggregate(total=Sum("quantity"))["total"]
    return total or Decimal("0")


def get_billed_quantity(*, purchase_order_line) -> Decimal:
    """Sums BillLine quantities linked to this PurchaseOrderLine across
    non-DRAFT, non-VOID bills. Used by the three-way match to detect
    overbilling against the order."""
    from purchases.models.bill import BillLine, BillStatus

    total = BillLine.objects.filter(
        source_order_line=purchase_order_line,
    ).exclude(
        bill__status__in=[BillStatus.DRAFT, BillStatus.VOID]
    ).aggregate(total=Sum("quantity"))["total"]
    return total or Decimal("0")


def get_billed_quantity_for_receipt_line(*, goods_receipt_line, exclude_bill=None) -> Decimal:
    """Sums BillLine quantities already billed against this GoodsReceiptLine.

    This is the double-billing guard (see purchases/models/bill.py for why it
    is a derived sum rather than a unique constraint). VOID bills are
    excluded, so voiding a bill genuinely frees the receipt line to be billed
    again. DRAFT bills ARE counted: two drafts each claiming the same
    received goods is a mistake worth catching at the point the second draft
    is built, not at posting time when the user has moved on.

    `exclude_bill` lets a bill being edited ignore its own existing lines.
    """
    from purchases.models.bill import BillLine, BillStatus

    qs = BillLine.objects.filter(source_goods_receipt_line=goods_receipt_line).exclude(
        bill__status=BillStatus.VOID
    )
    if exclude_bill is not None:
        qs = qs.exclude(bill=exclude_bill)
    total = qs.aggregate(total=Sum("quantity"))["total"]
    return total or Decimal("0")


def get_bill_amount_paid(*, bill) -> Decimal:
    """Sums VendorPaymentAllocation amounts against this bill — never a
    stored field on Bill. Vendor-advance allocations (bill=None) are excluded
    by the FK filter itself."""
    from purchases.models.payment import VendorPaymentAllocation

    total = VendorPaymentAllocation.objects.filter(bill=bill).aggregate(total=Sum("amount"))["total"]
    return total or Decimal("0")


def get_bill_amount_credited(*, bill) -> Decimal:
    """Sums VendorCredit.amount_applied_to_bill for ISSUED credits against
    this bill — see models/vendor_credit.py for why that field is a frozen
    historical fact rather than something recomputed here."""
    from purchases.models.vendor_credit import VendorCredit, VendorCreditStatus

    total = VendorCredit.objects.filter(
        source_bill=bill, status=VendorCreditStatus.ISSUED
    ).aggregate(total=Sum("amount_applied_to_bill"))["total"]
    return total or Decimal("0")


def get_bill_amount_due(*, bill) -> Decimal:
    return bill.total - get_bill_amount_paid(bill=bill) - get_bill_amount_credited(bill=bill)


def get_payment_unapplied_amount(*, payment) -> Decimal:
    """The portion of a VendorPayment held as an unmatched advance with the
    vendor (a VendorPaymentAllocation row with bill=None)."""
    from purchases.models.payment import VendorPaymentAllocation

    total = VendorPaymentAllocation.objects.filter(payment=payment, bill__isnull=True).aggregate(
        total=Sum("amount")
    )["total"]
    return total or Decimal("0")


def get_outstanding_bills(*, organization):
    """Bills with a positive amount still owed — OPEN/PARTIALLY_PAID only
    (DRAFT has no financial effect yet, PAID/VOID have none left)."""
    from purchases.models.bill import Bill, BillStatus

    bills = Bill.objects.filter(organization=organization, status__in=[BillStatus.OPEN, BillStatus.PARTIALLY_PAID])
    return [bill for bill in bills if get_bill_amount_due(bill=bill) > 0]


def get_overdue_bills(*, organization, as_of=None):
    """Outstanding bills past due_date — computed on demand, never a stored
    'overdue' status transition (see BillStatus.OVERDUE)."""
    import datetime as _datetime

    as_of = as_of or _datetime.date.today()
    return [bill for bill in get_outstanding_bills(organization=organization) if bill.due_date < as_of]
