"""Turning approved billable time into a customer invoice.

This module builds invoice LINES and hands them to
`sales.services.invoices.create_invoice`. It does NOT post accounting, does
not allocate an invoice number, and does not touch a JournalEntry — the
sales module owns all of that, and duplicating any of it here would give the
system two places that decide what an invoice means (root CLAUDE.md: use
Sales services, do not duplicate Sales invoice accounting).

The generated invoice is a DRAFT. A human reviews it and calls
`post_invoice`, exactly as for a recurring invoice. Time entries are marked
INVOICED as soon as the draft exists, not at posting: the entries are
already committed to that document, and leaving them unmarked would let a
second draft bill the same hours.

GROUPING: one invoice line per (task, rate). Grouping by task alone would
silently merge hours billed at different rates into one line at whichever
rate happened to come last; grouping per entry would produce a hundred-line
invoice for a busy month. Line descriptions name the task, and the entry
descriptions are preserved on the entries themselves for drill-down.
"""

from collections import defaultdict
from decimal import Decimal

from django.db import transaction

from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from projects.models.project import BillingMethod, Project
from projects.models.time_entry import TimeEntry, TimeEntryStatus
from projects.services.projects import resolve_service_item
from sales.services.invoices import create_invoice


def get_unbilled_time(*, project: Project, up_to_date=None):
    """Approved, billable, not-yet-invoiced entries — the exact set
    `invoice_project_time` will bill. Exposed separately so a caller can
    preview before committing."""
    qs = TimeEntry.objects.filter(
        project=project, status=TimeEntryStatus.APPROVED, is_billable=True, invoice_line__isnull=True
    )
    if up_to_date is not None:
        qs = qs.filter(entry_date__lte=up_to_date)
    return qs.select_related("task", "user").order_by("entry_date", "created_at")


@transaction.atomic
def invoice_project_time(
    *,
    organization,
    project: Project,
    invoice_date,
    due_date,
    receivable_account,
    up_to_date=None,
    tax_rate: Decimal = Decimal("0"),
    tax_payable_account=None,
    reference: str = "",
    notes: str = "",
    actor=None,
):
    """Creates ONE draft invoice covering every approved, unbilled, billable
    hour on the project (optionally up to `up_to_date`).

    Returns the created `sales.Invoice`.
    """
    if project.organization_id != organization.id:
        raise ApplicationError("Project must belong to the posting organization.", code="project_cross_org")

    if project.billing_method == BillingMethod.NON_BILLABLE:
        raise ApplicationError(
            "A non-billable project's time cannot be invoiced.", code="project_not_billable"
        )
    if project.billing_method == BillingMethod.FIXED_FEE:
        # The agreed fee is the revenue. Billing the hours as well would
        # charge the customer twice for the same work — invoice the fee
        # through sales directly instead.
        raise ApplicationError(
            "A fixed-fee project's time is not separately invoiceable — raise an invoice for the agreed fee "
            "through sales instead.",
            code="project_fixed_fee",
        )

    # Lock the entries being billed so a concurrent call cannot select the
    # same hours and bill them onto a second invoice. Same read-then-write
    # hazard as purchases' double-billing guard, and the same remedy.
    entries = list(get_unbilled_time(project=project, up_to_date=up_to_date).select_for_update())
    if not entries:
        raise ApplicationError(
            "There is no approved, unbilled billable time on this project.", code="no_billable_time"
        )

    # (task, rate) -> accumulated hours. See the module docstring for why the
    # rate is part of the key.
    grouped: dict[tuple, Decimal] = defaultdict(lambda: Decimal("0"))
    tasks_by_id = {}
    for entry in entries:
        if entry.billable_rate is None:
            raise ApplicationError(
                f"Time on task '{entry.task.name}' has no billable rate resolved and cannot be invoiced. "
                "Set a rate on the project, task or member and re-log or re-rate the time.",
                code="billable_rate_missing",
            )
        grouped[(entry.task_id, entry.billable_rate)] += entry.hours
        tasks_by_id[entry.task_id] = entry.task

    lines = []
    line_keys = []
    for (task_id, rate), hours in sorted(grouped.items(), key=lambda kv: tasks_by_id[kv[0][0]].name):
        task = tasks_by_id[task_id]
        service_item = resolve_service_item(project=project, task=task)
        if service_item is None:
            raise ApplicationError(
                f"Task '{task.name}' has no service item to invoice time as — set one on the task or "
                "the project.",
                code="service_item_required",
            )
        lines.append({
            "item": service_item,
            "description": f"{project.name} — {task.name}",
            "quantity": hours,
            "unit_price": rate,
            "tax_rate": tax_rate,
        })
        line_keys.append((task_id, rate))

    invoice = create_invoice(
        organization=organization,
        customer=project.customer,
        invoice_date=invoice_date,
        due_date=due_date,
        lines=lines,
        receivable_account=receivable_account,
        currency=project.currency,
        reference=reference or project.project_code,
        tax_payable_account=tax_payable_account,
        notes=notes,
        actor=actor,
    )

    # Attach each entry to the line that billed it. `create_invoice` numbers
    # lines 1..n in the order we supplied them, so the key order above maps
    # directly onto them.
    invoice_lines = list(invoice.lines.order_by("line_number"))
    line_by_key = dict(zip(line_keys, invoice_lines, strict=True))
    for entry in entries:
        entry.invoice_line = line_by_key[(entry.task_id, entry.billable_rate)]
        entry.status = TimeEntryStatus.INVOICED
        entry.save(update_fields=["invoice_line", "status", "updated_at"])

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="sales.Invoice",
        object_id=invoice.id,
        changes={
            "source": "projects.time_billing",
            "project_id": str(project.id),
            "entry_count": len(entries),
            "total": str(invoice.total),
        },
    )
    return invoice


@transaction.atomic
def release_invoiced_time(*, organization, invoice, actor=None) -> int:
    """Returns time entries to APPROVED when the invoice that billed them is
    voided, so the work can be re-billed rather than silently lost.

    Called by whoever voids the invoice; `sales` does not know about projects,
    so this is not wired into `void_invoice` automatically — see
    projects/CLAUDE.md for that boundary and its consequence.
    """
    entries = list(
        TimeEntry.objects.select_for_update().filter(
            organization=organization, invoice_line__invoice=invoice, status=TimeEntryStatus.INVOICED
        )
    )
    for entry in entries:
        entry.status = TimeEntryStatus.APPROVED
        entry.invoice_line = None
        entry.save(update_fields=["status", "invoice_line", "updated_at"])

    if entries:
        record_audit(
            organization_id=organization.id,
            actor=actor,
            action=AuditLog.Action.UPDATE,
            object_type="projects.TimeEntry",
            object_id=invoice.id,
            changes={"released_count": len(entries), "reason": "invoice_voided"},
        )
    return len(entries)
