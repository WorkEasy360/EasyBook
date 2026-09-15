"""Read-only sales queries. Only what Phase 3 actually needs (root
CLAUDE.md: don't build a full Reports module yet) — see sales/CLAUDE.md.
"""

from decimal import Decimal

from django.db.models import Sum


def get_fulfilled_quantity(*, sales_order_line) -> Decimal:
    """Sums DeliveryChallanLine quantities linked to this SalesOrderLine
    across non-draft, non-cancelled challans. Never a stored field on
    SalesOrderLine — always derived, same principle as inventory
    stock-on-hand/GL (see sales/CLAUDE.md)."""
    from sales.models.delivery import DeliveryChallanLine, DeliveryChallanStatus

    total = DeliveryChallanLine.objects.filter(
        source_order_line=sales_order_line,
        challan__status__in=[DeliveryChallanStatus.DISPATCHED, DeliveryChallanStatus.DELIVERED],
    ).aggregate(total=Sum("quantity"))["total"]
    return total or Decimal("0")


def get_invoice_amount_paid(*, invoice) -> Decimal:
    """Sums PaymentAllocation amounts against this invoice — never a stored
    field on Invoice (see sales/CLAUDE.md and root CLAUDE.md: never
    trust/store a manually editable amount_paid). Unapplied-credit
    allocations (invoice=None) are excluded by the FK filter itself."""
    from sales.models.payment import PaymentAllocation

    total = PaymentAllocation.objects.filter(invoice=invoice).aggregate(total=Sum("amount"))["total"]
    return total or Decimal("0")


def get_invoice_amount_credited(*, invoice) -> Decimal:
    """Sums CreditNote.amount_applied_to_invoice for ISSUED credit notes
    against this invoice — see models/credit_note.py for why that field is
    a frozen historical fact rather than something recomputed here."""
    from sales.models.credit_note import CreditNote, CreditNoteStatus

    total = CreditNote.objects.filter(
        source_invoice=invoice, status=CreditNoteStatus.ISSUED
    ).aggregate(total=Sum("amount_applied_to_invoice"))["total"]
    return total or Decimal("0")


def get_invoice_amount_due(*, invoice) -> Decimal:
    return invoice.total - get_invoice_amount_paid(invoice=invoice) - get_invoice_amount_credited(invoice=invoice)


def get_payment_unapplied_amount(*, payment) -> Decimal:
    """The portion of a CustomerPayment held as unapplied customer credit
    (a PaymentAllocation row with invoice=None) — see root CLAUDE.md §27
    overpayment policy."""
    from sales.models.payment import PaymentAllocation

    total = PaymentAllocation.objects.filter(payment=payment, invoice__isnull=True).aggregate(total=Sum("amount"))[
        "total"
    ]
    return total or Decimal("0")


def get_outstanding_invoices(*, organization):
    """Invoices with a positive amount still due — SENT/PARTIALLY_PAID only
    (DRAFT has no financial effect yet, PAID/VOID have none left)."""
    from sales.models.invoice import Invoice, InvoiceStatus

    invoices = Invoice.objects.filter(organization=organization, status__in=[InvoiceStatus.SENT, InvoiceStatus.PARTIALLY_PAID])
    return [invoice for invoice in invoices if get_invoice_amount_due(invoice=invoice) > 0]


def get_overdue_invoices(*, organization, as_of=None):
    """Outstanding invoices past due_date — computed on demand, never a
    stored 'overdue' status transition (see InvoiceStatus.OVERDUE)."""
    import datetime as _datetime

    as_of = as_of or _datetime.date.today()
    return [invoice for invoice in get_outstanding_invoices(organization=organization) if invoice.due_date < as_of]
