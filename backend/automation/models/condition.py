from django.db import models

from core.models import TenantScopedModel


class AutomationCondition(TenantScopedModel):
    """One predicate in a rule's condition list. All conditions on a rule
    must pass (AND-only for Phase 11 — no OR/grouping) for its actions to
    run. `field`/`operator` are validated against a per-trigger allowlist at
    write time (automation/conditions/schemas.py) — never arbitrary model
    attribute access, never eval()'d (root CLAUDE.md)."""

    rule = models.ForeignKey("automation.AutomationRule", on_delete=models.CASCADE, related_name="conditions")
    order = models.PositiveIntegerField(default=0)
    field = models.CharField(max_length=100)
    operator = models.CharField(max_length=32)
    # Stored as text regardless of the field's declared type; the condition
    # evaluator (automation/conditions/evaluator.py, a later slice) parses it
    # as Decimal/date/str according to the allowlist before comparing.
    value = models.CharField(max_length=500, blank=True)

    class Meta:
        indexes = [models.Index(fields=["rule", "order"])]
        ordering = ["rule", "order"]

    def __str__(self):
        return f"{self.field} {self.operator} {self.value}"
