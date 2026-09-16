"""AP and Purchase reports — mirror of reports/selectors/receivables.py with
the debit/credit sides swapped (PHASE 8 spec §10/§13). Derived from
`purchases.Bill`/`Payment`/`VendorCredit`/`Expense` via `purchases.selectors`.
No mutable vendor balance is ever stored (purchases/CLAUDE.md).

DATE FIELD USED FOR PURCHASE REPORTS: `Bill.bill_date` / `Expense.expense_date`
— the same fields `compliance.selectors` keys the input tax register on.

AGEING BUCKETS: identical boundaries to AR (reports.selectors.receivables) —
Current, 1-30, 31-60, 61-90, 90+ days past due_date.
"""

import datetime
from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, Sum

from purchases.selectors import get_bill_amount_due, get_outstanding_bills, get_overdue_bills
from reports.selectors.receivables import AGEING_BUCKETS, ageing_bucket

ZERO = Decimal("0")


def _bill_row(bill) -> dict:
    return {
        "bill_id": bill.id,
        "bill_number": bill.bill_number,
        "vendor_id": bill.vendor_id,
        "vendor_name": bill.vendor.display_name,
        "bill_date": bill.bill_date,
        "due_date": bill.due_date,
        "total": bill.total,
        "amount_due": get_bill_amount_due(bill=bill),
        "status": bill.status,
    }


def get_ap_ageing(*, organization, as_of=None) -> dict:
    as_of = as_of or datetime.date.today()
    buckets = {bucket: [] for bucket in AGEING_BUCKETS}
    for bill in get_outstanding_bills(organization=organization):
        amount_due = get_bill_amount_due(bill=bill)
        if amount_due <= ZERO:
            continue
        days_overdue = (as_of - bill.due_date).days
        row = {**_bill_row(bill), "days_overdue": max(days_overdue, 0)}
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


def get_vendor_balances(*, organization) -> list[dict]:
    balances = defaultdict(lambda: ZERO)
    names = {}
    for bill in get_outstanding_bills(organization=organization):
        amount_due = get_bill_amount_due(bill=bill)
        if amount_due <= ZERO:
            continue
        balances[bill.vendor_id] += amount_due
        names[bill.vendor_id] = bill.vendor.display_name
    rows = [
        {"vendor_id": vendor_id, "vendor_name": names[vendor_id], "balance": balance}
        for vendor_id, balance in balances.items()
    ]
    rows.sort(key=lambda r: r["vendor_name"])
    return rows


def get_outstanding_bills_report(*, organization) -> list[dict]:
    return [_bill_row(bill) for bill in get_outstanding_bills(organization=organization)]


def get_overdue_bills_report(*, organization, as_of=None) -> list[dict]:
    return [_bill_row(bill) for bill in get_overdue_bills(organization=organization, as_of=as_of)]


def _live_bill_qs(*, organization, from_date, to_date):
    from purchases.models.bill import Bill, BillStatus

    qs = Bill.objects.filter(organization=organization).exclude(status__in=[BillStatus.DRAFT, BillStatus.VOID])
    if from_date is not None:
        qs = qs.filter(bill_date__gte=from_date)
    if to_date is not None:
        qs = qs.filter(bill_date__lte=to_date)
    return qs


def get_purchases_by_vendor(*, organization, from_date=None, to_date=None) -> list[dict]:
    qs = _live_bill_qs(organization=organization, from_date=from_date, to_date=to_date)
    rows = (
        qs.values("vendor_id", "vendor__display_name")
        .annotate(bill_count=Count("id"), taxable_value=Sum("subtotal"), tax_total=Sum("tax_total"), total_purchases=Sum("total"))
        .order_by("-total_purchases")
    )
    return [
        {
            "vendor_id": row["vendor_id"],
            "vendor_name": row["vendor__display_name"],
            "bill_count": row["bill_count"],
            "taxable_value": row["taxable_value"] or ZERO,
            "tax_total": row["tax_total"] or ZERO,
            "total_purchases": row["total_purchases"] or ZERO,
        }
        for row in rows
    ]


def get_purchases_by_item(*, organization, from_date=None, to_date=None) -> list[dict]:
    from purchases.models.bill import BillLine, BillStatus

    qs = BillLine.objects.filter(organization=organization).exclude(bill__status__in=[BillStatus.DRAFT, BillStatus.VOID])
    if from_date is not None:
        qs = qs.filter(bill__bill_date__gte=from_date)
    if to_date is not None:
        qs = qs.filter(bill__bill_date__lte=to_date)
    rows = (
        qs.values("item_id", "item__name", "item__sku")
        .annotate(quantity=Sum("quantity"), taxable_value=Sum("taxable_amount"), tax_total=Sum("tax_amount"), total_purchases=Sum("line_total"))
        .order_by("-total_purchases")
    )
    return [
        {
            "item_id": row["item_id"],
            "item_name": row["item__name"],
            "item_sku": row["item__sku"],
            "quantity": row["quantity"] or Decimal("0"),
            "taxable_value": row["taxable_value"] or ZERO,
            "tax_total": row["tax_total"] or ZERO,
            "total_purchases": row["total_purchases"] or ZERO,
        }
        for row in rows
    ]


def get_expenses_by_category(*, organization, from_date=None, to_date=None) -> list[dict]:
    """'Category' is the expense's own GST/accounting classification —
    `Expense.expense_account` (must be an EXPENSE account, validated at
    record time — purchases/models/expense.py) — not a separate freeform
    category field, which does not exist on this model."""
    from purchases.models.expense import Expense, ExpenseStatus

    qs = Expense.objects.filter(organization=organization).exclude(status__in=[ExpenseStatus.DRAFT, ExpenseStatus.VOID])
    if from_date is not None:
        qs = qs.filter(expense_date__gte=from_date)
    if to_date is not None:
        qs = qs.filter(expense_date__lte=to_date)
    rows = (
        qs.values("expense_account_id", "expense_account__code", "expense_account__name")
        .annotate(expense_count=Count("id"), tax_total=Sum("tax_amount"), total_amount=Sum("total"))
        .order_by("-total_amount")
    )
    return [
        {
            "account_id": row["expense_account_id"],
            "account_code": row["expense_account__code"],
            "account_name": row["expense_account__name"],
            "expense_count": row["expense_count"],
            "tax_total": row["tax_total"] or ZERO,
            "total_amount": row["total_amount"] or ZERO,
        }
        for row in rows
    ]
