from django.db import models

from core.models import TenantScopedModel


class AutomationActionConfig(TenantScopedModel):
    """One action a rule runs, in `order`, when all of its conditions pass.
    `config` is validated against the action's own JSON schema at write time
    (automation/actions/registry.py::validate_action_config) — unknown keys
    are rejected, never stored unvalidated."""

    rule = models.ForeignKey("automation.AutomationRule", on_delete=models.CASCADE, related_name="actions")
    order = models.PositiveIntegerField(default=0)
    action_id = models.CharField(max_length=64)
    config = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["rule", "order"])]
        ordering = ["rule", "order"]

    def __str__(self):
        return f"{self.action_id}#{self.order}"
