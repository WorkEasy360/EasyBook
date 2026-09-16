from django.conf import settings
from django.db import models

from core.models import TenantScopedModel


class AdjustmentReason(models.TextChoices):
    PHYSICAL_COUNT = "physical_count", "Physical Count"
    DAMAGED = "damaged", "Damaged"
    SHRINKAGE = "shrinkage", "Shrinkage"
    FOUND = "found", "Found Stock"
    CORRECTION = "correction", "Data Correction"
    OTHER = "other", "Other"


class AdjustmentStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    POSTED = "posted", "Posted"
    REVERSED = "reversed", "Reversed"


class AdjustmentLineDirection(models.TextChoices):
    IN = "adjustment_in", "Adjustment In"
    OUT = "adjustment_out", "Adjustment Out"


class StockAdjustment(TenantScopedModel):
    """Header for a physical-count correction, damage/shrinkage write-off,
    or found-stock entry. Mirrors accounting.JournalEntry's DRAFT/POSTED
    lifecycle and reversal-by-counter-entry pattern deliberately — see
    inventory/CLAUDE.md."""

    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.PROTECT, related_name="+")
    adjustment_date = models.DateField()
    reason = models.CharField(max_length=32, choices=AdjustmentReason.choices)
    memo = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=AdjustmentStatus.choices, default=AdjustmentStatus.DRAFT)

    # Explicit, not defaulted — see items/CLAUDE.md: no implicit "shrinkage
    # expense" account is invented. Omitting it posts a quantity-only
    # adjustment with no accounting journal.
    contra_account = models.ForeignKey(
        "accounting.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    accounting_journal = models.ForeignKey(
        "accounting.JournalEntry", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    reverses = models.OneToOneField(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversal"
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["organization", "status", "adjustment_date"]),
        ]
        ordering = ["-adjustment_date", "-created_at"]

    def __str__(self):
        return f"Adjustment {self.id} ({self.status})"

    _MUTABLE_AFTER_DRAFT_FIELDS = {
        "status", "posted_by", "posted_by_id", "posted_at", "accounting_journal",
        "accounting_journal_id", "updated_at",
    }

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous_status = StockAdjustment.all_objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if previous_status and previous_status != AdjustmentStatus.DRAFT:
                update_fields = kwargs.get("update_fields")
                if not update_fields or not set(update_fields) <= self._MUTABLE_AFTER_DRAFT_FIELDS:
                    raise ValueError(
                        "Posted stock adjustments are immutable except through the posting/reversal services."
                    )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != AdjustmentStatus.DRAFT:
            raise ValueError("Only draft stock adjustments can be deleted.")
        super().delete(*args, **kwargs)


class StockAdjustmentLine(TenantScopedModel):
    adjustment = models.ForeignKey(StockAdjustment, on_delete=models.CASCADE, related_name="lines")
    item = models.ForeignKey("items.Item", on_delete=models.PROTECT, related_name="+")
    line_number = models.PositiveIntegerField()
    direction = models.CharField(max_length=16, choices=AdjustmentLineDirection.choices)
    quantity = models.DecimalField(max_digits=18, decimal_places=4)
    # Only meaningful (and required) for direction=IN — see
    # inventory/services/adjustments.py: an OUT line is valued at the
    # current weighted-average cost computed at posting time, never
    # user-supplied.
    unit_cost = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="adjustment_line_quantity_positive"),
        ]
        indexes = [
            models.Index(fields=["adjustment", "line_number"]),
        ]
        ordering = ["adjustment", "line_number"]

    def __str__(self):
        return f"{self.adjustment_id}#{self.line_number}"

    def _parent_status(self):
        return StockAdjustment.all_objects.filter(pk=self.adjustment_id).values_list("status", flat=True).first()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            parent_status = self._parent_status()
            if parent_status and parent_status != AdjustmentStatus.DRAFT:
                raise ValueError("Cannot modify a line belonging to a posted stock adjustment.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        parent_status = self._parent_status()
        if parent_status and parent_status != AdjustmentStatus.DRAFT:
            raise ValueError("Cannot delete a line belonging to a posted stock adjustment.")
        super().delete(*args, **kwargs)
