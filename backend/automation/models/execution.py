import uuid

from django.conf import settings
from django.db import models

from core.models import TenantScopedModel


class ExecutionStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    PARTIAL = "partial", "Partial"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class TriggerSource(models.TextChoices):
    """Explicit trigger origin (phase section 45) — prevents the same
    business occurrence being double-counted via both an event and a
    schedule scan for a rule whose semantics would duplicate the action."""

    EVENT = "event", "Event"
    SCHEDULE = "schedule", "Schedule"
    MANUAL = "manual", "Manual"


class AutomationExecution(TenantScopedModel):
    """One run of one rule against one trigger occurrence.

    `rule_version`/`conditions_snapshot`/`actions_snapshot` freeze exactly
    what this run evaluated against — a later edit to the rule can never
    rewrite how a past execution is interpreted (phase sections 27, 67).

    The UniqueConstraint on (rule, trigger_event) is the authoritative,
    DB-enforced guard against the same event executing the same rule twice
    (phase section 11) — two dispatcher workers racing the same event can
    both attempt the INSERT, but only one succeeds.
    """

    rule = models.ForeignKey("automation.AutomationRule", on_delete=models.PROTECT, related_name="executions")
    rule_version = models.PositiveIntegerField()
    trigger_source = models.CharField(max_length=16, choices=TriggerSource.choices)
    trigger_event = models.ForeignKey(
        "automation.AutomationEvent", null=True, blank=True, on_delete=models.PROTECT, related_name="executions"
    )
    entity_id = models.CharField(max_length=64, blank=True)
    conditions_snapshot = models.JSONField(default=list, blank=True)
    actions_snapshot = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=16, choices=ExecutionStatus.choices, default=ExecutionStatus.PENDING)

    # Loop-prevention lineage — mirrors AutomationEvent (automation/models/event.py).
    causation_id = models.UUIDField(null=True, blank=True)
    correlation_id = models.UUIDField(default=uuid.uuid4)
    depth = models.PositiveIntegerField(default=0)

    # Run-as policy (phase section 23): initiated_by is who/what triggered
    # this run (null for an event/schedule occurrence, set for a manual
    # run); executed_as is whose permissions gate every action step,
    # re-verified live at each step rather than cached from rule creation
    # (phase section 23: "do not silently inherit removed permissions").
    initiated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    executed_as = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    # Incremented by the recovery sweeper (automation/tasks.py) in its OWN
    # transaction. attempt_count cannot serve as the sweeper's budget: it is
    # written inside the run's transaction, so a run that fails by rolling back
    # (statement timeout, lost connection, a bug) undoes it, and the sweeper
    # would re-enqueue the same execution forever.
    recovery_attempts = models.PositiveIntegerField(default=0)
    error_summary = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["rule", "trigger_event"],
                condition=models.Q(trigger_event__isnull=False),
                name="uniq_automation_execution_per_rule_event",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "rule", "status"]),
            models.Index(fields=["organization", "created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.rule_id}@{self.trigger_source}:{self.status}"
