from django.db import models

from core.models import TenantScopedModel


class AutomationSchedule(TenantScopedModel):
    """One row per rule using a pure schedule trigger (schedule.daily/
    weekly/monthly). `next_run_at` advances only after a successful
    occurrence claim (services/scheduling.py), same one-row-per-schedule
    shape as sales.RecurringInvoiceTemplate."""

    rule = models.OneToOneField("automation.AutomationRule", on_delete=models.CASCADE, related_name="schedule")
    next_run_at = models.DateField()

    class Meta:
        indexes = [models.Index(fields=["organization", "next_run_at"])]

    def __str__(self):
        return f"{self.rule_id}@{self.next_run_at}"


class AutomationScheduleOccurrence(TenantScopedModel):
    """Append-only idempotency ledger for pure schedule triggers (phase
    section 43) — the UniqueConstraint on (rule, scheduled_for) is the
    authoritative guard against two scheduler processes claiming the same
    occurrence, exactly like sales.RecurringInvoiceRun."""

    rule = models.ForeignKey(
        "automation.AutomationRule", on_delete=models.PROTECT, related_name="schedule_occurrences"
    )
    scheduled_for = models.DateField()
    execution = models.ForeignKey("automation.AutomationExecution", on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["rule", "scheduled_for"], name="uniq_automation_schedule_occurrence"),
        ]

    def __str__(self):
        return f"{self.rule_id}@{self.scheduled_for}"


class AutomationScanOccurrence(TenantScopedModel):
    """Append-only idempotency ledger for scan-based (stateful, non-event)
    triggers like invoice.overdue/stock.low (phase sections 46-48). The
    UniqueConstraint on (rule, entity_id, occurrence_date) is the
    authoritative guard against two concurrent scan runs firing the same
    rule for the same entity on the same day — and, combined with
    AutomationRule.cooldown_days, against re-firing every single scan while
    an entity remains in the matched state."""

    rule = models.ForeignKey("automation.AutomationRule", on_delete=models.PROTECT, related_name="scan_occurrences")
    entity_id = models.CharField(max_length=64)
    occurrence_date = models.DateField()
    execution = models.ForeignKey("automation.AutomationExecution", on_delete=models.PROTECT, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["rule", "entity_id", "occurrence_date"], name="uniq_automation_scan_occurrence"
            ),
        ]
        indexes = [models.Index(fields=["organization", "rule", "entity_id"])]

    def __str__(self):
        return f"{self.rule_id}:{self.entity_id}@{self.occurrence_date}"
