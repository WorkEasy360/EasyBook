from django.conf import settings
from django.db import models

from core.models import TenantScopedModel


class RuleStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    ARCHIVED = "archived", "Archived"


class AutomationRule(TenantScopedModel):
    """A tenant-owned automation definition: trigger -> conditions -> actions.

    Only ACTIVE rules are ever evaluated by the engine (automation/engine/,
    later slices) — DRAFT/PAUSED/ARCHIVED never run. `version` increments
    whenever the trigger/conditions/actions change (services/rules.py) so a
    past AutomationExecution can record which definition it actually ran
    under (root CLAUDE.md phase section 27) once the execution engine lands.
    """

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    trigger_type = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=RuleStatus.choices, default=RuleStatus.DRAFT)
    version = models.PositiveIntegerField(default=1)
    priority = models.PositiveIntegerField(default=0)
    stop_on_failure = models.BooleanField(default=False)
    max_runs_per_period = models.PositiveIntegerField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        indexes = [
            models.Index(fields=["organization", "trigger_type", "status"]),
            models.Index(fields=["organization", "created_at"]),
        ]
        ordering = ["-priority", "name"]

    def __str__(self):
        return f"{self.name} ({self.trigger_type})"
