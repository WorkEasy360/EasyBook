"""AR and Sales reports — derived from `sales.Invoice`/`Payment`/`CreditNote`
via `sales.selectors` (PHASE 8 spec §9/§12). No mutable receivable balance is
ever stored here (root CLAUDE.md; sales/CLAUDE.md "no mutable customer
balance").

DATE FIELD USED FOR SALES REPORTS: `Invoice.invoice_date`, not posting date
or payment date — the same field `compliance.selectors` already keys the GST
registers on, so "sales in April" means the same thing everywhere in this
codebase. AR Ageing instead keys off `Invoice.due_date` relative to `as_of`,
per spec §9.

AGEING BUCKET BOUNDARIES (spec §9, tested in
reports/tests/test_receivables.py): Current (not yet due or due today),
1-30, 31-60, 61-90, 90+ days past due_date.
"""

import datetime
from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, Sum

from sales.selectors import get_invoice_amount_due, get_outstanding_invoices, get_overdue_invoices

ZERO = Decimal("0")
AGEING_BUCKETS = ("current", "1-30", "31-60", "61-90", "90+")


def ageing_bucket(days_overdue: int) -> str:
    if days_overdue <= 0:
        return "current"
    if days_overdue <= 30:
        return "1-30"
    if days_overdue <= 60:
        return "31-60"
    if days_overdue <= 90:
        return "61-90"
    return "90+"


def _invoice_row(invoice) -> dict:
    return {
        "invoice_id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "customer_id": invoice.customer_id,
        "customer_name": invoice.customer.display_name,
        "invoice_date": invoice.invoice_date,
        "due_date": invoice.due_date,
        "total": invoice.total,
        "amount_due": get_invoice_amount_due(invoice=invoice),
        "status": invoice.status,
    }


def get_ar_ageing(*, organization, as_of=None) -> dict:
    as_of = as_of or datetime.date.today()
    buckets = {bucket: [] for bucket in AGEING_BUCKETS}
    for invoice in get_outstanding_invoices(organization=organization):
        amount_due = get_invoice_amount_due(invoice=invoice)
        if amount_due <= ZERO:
            continue
        days_overdue = (as_of - invoice.due_date).days
        row = {**_invoice_row(invoice), "days_overdue": max(days_overdue, 0)}
        row["amount_due"] = amount_due
        buckets[ageing_bucket(days_overdue)].append(row)

    for rows in buckets.values():
        rows.sort(key=lambda r: r["due_date"])
    totals = {bucket: sum((row["amount_due"] for row in rows), ZERO) for bucket, rows in buckets.items()}
    return {
        "as_of": as_of,
        "buckets": buckets,
        "totals": totals,
        "grand_total": sum(totals.values(), ZERO),
    }


def get_customer_balances(*, organization) -> list[dict]:
    balances = defaultdict(lambda: ZERO)
    names = {}
    for invoice in get_outstanding_invoices(organization=organization):
        amount_due = get_invoice_amount_due(invoice=invoice)
        if amount_due <= ZERO:
            continue
        balances[invoice.customer_id] += amount_due
        names[invoice.customer_id] = invoice.customer.display_name
    rows = [
        {"customer_id": customer_id, "customer_name": names[customer_id], "balance": balance}
        for customer_id, balance in balances.items()
    ]
    rows.sort(key=lambda r: r["customer_name"])
    return rows


def get_outstanding_invoices_report(*, organization) -> list[dict]:
    return [_invoice_row(invoice) for invoice in get_outstanding_invoices(organization=organization)]


def get_overdue_invoices_report(*, organization, as_of=None) -> list[dict]:
    return [_invoice_row(invoice) for invoice in get_overdue_invoices(organization=organization, as_of=as_of)]


def _live_invoice_qs(*, organization, from_date, to_date):
    from sales.models.invoice import Invoice, InvoiceStatus

    qs = Invoice.objects.filter(organization=organization).exclude(
        status__in=[InvoiceStatus.DRAFT, InvoiceStatus.VOID]
    )
    if from_date is not None:
        qs = qs.filter(invoice_date__gte=from_date)
    if to_date is not None:
        qs = qs.filter(invoice_date__lte=to_date)
    return qs


def get_sales_by_customer(*, organization, from_date=None, to_date=None) -> list[dict]:
    """One grouped SQL aggregate, not a Python loop over invoices (PHASE 8
    spec §26)."""
    qs = _live_invoice_qs(organization=organization, from_date=from_date, to_date=to_date)
    rows = (
        qs.values("customer_id", "customer__display_name")
        .annotate(invoice_count=Count("id"), taxable_value=Sum("subtotal"), tax_total=Sum("tax_total"), total_sales=Sum("total"))
        .order_by("-total_sales")
    )
    return [
        {
            "customer_id": row["customer_id"],
            "customer_name": row["customer__display_name"],
            "invoice_count": row["invoice_count"],
            "taxable_value": row["taxable_value"] or ZERO,
            "tax_total": row["tax_total"] or ZERO,
            "total_sales": row["total_sales"] or ZERO,
        }
        for row in rows
    ]


def get_sales_by_item(*, organization, from_date=None, to_date=None) -> list[dict]:
    from sales.models.invoice import InvoiceLine, InvoiceStatus

    qs = InvoiceLine.objects.filter(organization=organization).exclude(
        invoice__status__in=[InvoiceStatus.DRAFT, InvoiceStatus.VOID]
    )
    if from_date is not None:
        qs = qs.filter(invoice__invoice_date__gte=from_date)
    if to_date is not None:
        qs = qs.filter(invoice__invoice_date__lte=to_date)
    rows = (
        qs.values("item_id", "item__name", "item__sku")
        .annotate(quantity=Sum("quantity"), taxable_value=Sum("taxable_amount"), tax_total=Sum("tax_amount"), total_sales=Sum("line_total"))
        .order_by("-total_sales")
    )
    return [
        {
            "item_id": row["item_id"],
            "item_name": row["item__name"],
            "item_sku": row["item__sku"],
            "quantity": row["quantity"] or Decimal("0"),
            "taxable_value": row["taxable_value"] or ZERO,
            "tax_total": row["tax_total"] or ZERO,
            "total_sales": row["total_sales"] or ZERO,
        }
        for row in rows
    ]
