"""Read-only project queries: hours, revenue, cost, margin.

Everything is derived from the underlying records — time entries, invoice
lines, posted expenses — and nothing is stored on `Project`. Same
never-cache-a-cross-document-sum rule as the General Ledger, inventory
stock-on-hand and the sales/purchases selectors.

REVENUE is deliberately taken from the INVOICE, not from the time entries'
own `billable_rate * hours`. Those two can legitimately differ: an invoice
may be edited before posting, discounted, or partly credited. The invoice is
what the customer was actually asked to pay, so it is the authoritative
revenue figure; the entry-derived number is reported separately as
`unbilled_value` for work not yet on any invoice.
"""

from decimal import Decimal

from django.db.models import Sum


def _zero(value):
    return value or Decimal("0")


def get_project_hours(*, project) -> dict:
    """Hours split by how they can be treated. `billable` here means the
    entry was resolved as chargeable — not that it has been billed yet."""
    from projects.models.time_entry import TimeEntry, TimeEntryStatus

    entries = TimeEntry.objects.filter(project=project)
    total = _zero(entries.aggregate(t=Sum("hours"))["t"])
    billable = _zero(entries.filter(is_billable=True).aggregate(t=Sum("hours"))["t"])
    approved = _zero(
        entries.filter(status__in=[TimeEntryStatus.APPROVED, TimeEntryStatus.INVOICED])
        .aggregate(t=Sum("hours"))["t"]
    )
    invoiced = _zero(entries.filter(status=TimeEntryStatus.INVOICED).aggregate(t=Sum("hours"))["t"])
    return {
        "total_hours": total,
        "billable_hours": billable,
        "non_billable_hours": total - billable,
        "approved_hours": approved,
        "invoiced_hours": invoiced,
        "pending_approval_hours": _zero(
            entries.filter(status=TimeEntryStatus.SUBMITTED).aggregate(t=Sum("hours"))["t"]
        ),
    }


def get_project_revenue(*, project) -> Decimal:
    """What the customer has actually been invoiced for this project's time,
    from the invoice lines themselves.

    DRAFT invoices count. A generated draft has already consumed the time
    entries (they are INVOICED), so excluding it would show the work as
    neither billed nor billable — invisible in every direction. VOID invoices
    are excluded, and `release_invoiced_time` returns their hours to the
    unbilled pool.
    """
    from projects.models.time_entry import TimeEntry
    from sales.models.invoice import InvoiceStatus

    line_ids = (
        TimeEntry.objects.filter(project=project, invoice_line__isnull=False)
        .exclude(invoice_line__invoice__status=InvoiceStatus.VOID)
        .values_list("invoice_line_id", flat=True)
        .distinct()
    )
    from sales.models.invoice import InvoiceLine

    return _zero(
        InvoiceLine.objects.filter(id__in=list(line_ids)).aggregate(t=Sum("taxable_amount"))["t"]
    )


def get_project_unbilled_value(*, project) -> Decimal:
    """The value of billable work not yet on any invoice — approved or not.
    This is the pipeline figure; it is NOT revenue and is reported apart from
    it precisely so the two are never conflated."""
    from projects.models.time_entry import TimeEntry, TimeEntryStatus

    entries = TimeEntry.objects.filter(
        project=project, is_billable=True, invoice_line__isnull=True
    ).exclude(status=TimeEntryStatus.REJECTED)
    return sum((entry.billable_amount for entry in entries), Decimal("0"))


def get_project_labour_cost(*, project) -> Decimal:
    """What the hours cost us. Non-billable and unapproved time is included:
    it was still worked and still paid for. Rejected time is excluded — it is
    disputed, and counting it would overstate cost."""
    from projects.models.time_entry import TimeEntry, TimeEntryStatus

    entries = TimeEntry.objects.filter(project=project).exclude(status=TimeEntryStatus.REJECTED)
    return sum((entry.cost_amount for entry in entries), Decimal("0"))


def get_project_expense_cost(*, project) -> Decimal:
    """Posted expenses attributed to this project.

    Only POSTED ones: a draft expense is not yet a committed cost, and a void
    one never was. The net `amount` is used rather than `total`, because
    recoverable input tax is not a cost to the business — including it would
    overstate every project's cost by the tax rate.
    """
    from purchases.models.expense import Expense, ExpenseStatus

    return _zero(
        Expense.objects.filter(project=project, status=ExpenseStatus.POSTED)
        .aggregate(t=Sum("amount"))["t"]
    )


def get_project_profitability(*, project) -> dict:
    """The headline report: revenue, cost, margin, and the hours behind them.

    `margin_percent` is None when there is no revenue rather than zero — a
    project that has billed nothing has an undefined margin, and rendering
    that as 0% invites the reader to treat a not-yet-billed project as a
    break-even one.
    """
    hours = get_project_hours(project=project)
    revenue = get_project_revenue(project=project)
    labour_cost = get_project_labour_cost(project=project)
    expense_cost = get_project_expense_cost(project=project)
    total_cost = labour_cost + expense_cost
    margin = revenue - total_cost

    return {
        "project_id": project.id,
        "project_code": project.project_code,
        "name": project.name,
        "status": project.status,
        "billing_method": project.billing_method,
        "revenue": revenue,
        "unbilled_value": get_project_unbilled_value(project=project),
        "labour_cost": labour_cost,
        "expense_cost": expense_cost,
        "total_cost": total_cost,
        "margin": margin,
        "margin_percent": (
            (margin / revenue * Decimal("100")).quantize(Decimal("0.01")) if revenue else None
        ),
        "budget_amount": project.budget_amount,
        "budget_hours": project.budget_hours,
        "hours_over_budget": (
            hours["total_hours"] - project.budget_hours
            if project.budget_hours is not None and hours["total_hours"] > project.budget_hours
            else Decimal("0")
        ),
        **hours,
    }


def get_user_timesheet(*, organization, user, from_date, to_date):
    """One person's entries over a period — the timesheet view."""
    from projects.models.time_entry import TimeEntry

    return (
        TimeEntry.objects.filter(
            organization=organization, user=user, entry_date__gte=from_date, entry_date__lte=to_date
        )
        .select_related("project", "task")
        .order_by("entry_date", "created_at")
    )
