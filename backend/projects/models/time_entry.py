from decimal import Decimal

from django.conf import settings
from django.db import models

from core.models import TenantScopedModel


class TimeEntryStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    # Terminal: the entry has been billed onto a customer invoice and is no
    # longer editable by anyone, exactly like a posted journal line.
    INVOICED = "invoiced", "Invoiced"


class TimeEntry(TenantScopedModel):
    """One person's hours on one task on one day.

    Not a financial record in itself — logging time posts NO journal. It
    becomes money only when approved billable time is invoiced, and at that
    point `sales` does the posting (see projects/CLAUDE.md). That is why this
    model has an approval lifecycle rather than a DRAFT/POSTED one.

    RATE SNAPSHOTS: `billable_rate` and `cost_rate` are resolved and frozen
    when the entry is created (see services/rates.py), not read live from the
    project or member. A rate change next quarter must not silently restate
    the value of work already done — the same historical-snapshot principle
    as sales/purchases document lines.
    """

    project = models.ForeignKey("projects.Project", on_delete=models.PROTECT, related_name="time_entries")
    task = models.ForeignKey("projects.Task", on_delete=models.PROTECT, related_name="time_entries")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="time_entries")

    entry_date = models.DateField()
    # 2dp: time is logged in hours (1.5 = ninety minutes), not as a duration.
    # Quantities elsewhere use 4dp, but a sixth of a second of billable time
    # is precision nobody has and nobody wants on an invoice.
    hours = models.DecimalField(max_digits=8, decimal_places=2)
    description = models.CharField(max_length=500, blank=True)

    status = models.CharField(max_length=16, choices=TimeEntryStatus.choices, default=TimeEntryStatus.DRAFT)
    # Whether this entry is chargeable. Resolved at creation from the task
    # AND the project's billing method — never trusted from the client alone.
    is_billable = models.BooleanField(default=False)

    billable_rate = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    cost_rate = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    # Set when the entry is billed. The FK is to the LINE, not the invoice,
    # so profitability can attribute the exact amount charged for this hour
    # rather than apportioning an invoice total.
    invoice_line = models.ForeignKey(
        "sales.InvoiceLine", null=True, blank=True, on_delete=models.PROTECT, related_name="time_entries"
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(hours__gt=0), name="time_entry_hours_positive"),
            # 24h in a day is the only defensible hard ceiling; anything
            # tighter is a policy this phase has no configuration surface for.
            models.CheckConstraint(condition=models.Q(hours__lte=Decimal("24")), name="time_entry_hours_max_24"),
            models.CheckConstraint(
                condition=models.Q(billable_rate__isnull=True) | models.Q(billable_rate__gte=Decimal("0")),
                name="time_entry_billable_rate_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(cost_rate__isnull=True) | models.Q(cost_rate__gte=Decimal("0")),
                name="time_entry_cost_rate_nonnegative",
            ),
            # An invoiced entry must point at the line that billed it, and an
            # entry that points at a line must be INVOICED. Enforced in the
            # database because "billed but not marked billed" is precisely the
            # state that double-bills a customer.
            models.CheckConstraint(
                condition=(
                    models.Q(status="invoiced", invoice_line__isnull=False)
                    | ~models.Q(status="invoiced") & models.Q(invoice_line__isnull=True)
                ),
                name="time_entry_invoiced_iff_linked",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "project", "status"]),
            models.Index(fields=["organization", "user", "entry_date"]),
            models.Index(fields=["organization", "status", "is_billable"]),
            models.Index(fields=["task"]),
        ]
        ordering = ["-entry_date", "-created_at"]

    def __str__(self):
        return f"{self.user_id}@{self.task_id}:{self.hours}h"

    @property
    def billable_amount(self) -> Decimal:
        """What this entry is worth to bill. Zero for non-billable time or
        time with no resolved rate — never None, so callers can sum without
        special-casing."""
        if not self.is_billable or self.billable_rate is None:
            return Decimal("0")
        return (self.hours * self.billable_rate).quantize(Decimal("0.01"))

    @property
    def cost_amount(self) -> Decimal:
        """What this entry cost us. Non-billable time still has a cost — that
        is the whole point of tracking it."""
        if self.cost_rate is None:
            return Decimal("0")
        return (self.hours * self.cost_rate).quantize(Decimal("0.01"))

    # Once an entry has been invoiced it is frozen: only the fields the
    # invoicing service itself writes stay mutable. Mirrors the
    # posted-document guards in sales/purchases.
    _MUTABLE_AFTER_INVOICING_FIELDS = {"status", "invoice_line", "invoice_line_id", "updated_at"}

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous_status = TimeEntry.all_objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if previous_status == TimeEntryStatus.INVOICED:
                update_fields = kwargs.get("update_fields")
                if not update_fields or not set(update_fields) <= self._MUTABLE_AFTER_INVOICING_FIELDS:
                    raise ValueError("Invoiced time entries are immutable except through time entry services.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status == TimeEntryStatus.INVOICED:
            raise ValueError("Invoiced time entries cannot be deleted.")
        super().delete(*args, **kwargs)
