from django.db import models

from core.models import TenantScopedModel


class UnitOfMeasure(TenantScopedModel):
    """Org-scoped unit catalog (Each, Kg, Box, ...).

    Org-scoped rather than a global table like accounts.Currency because the
    existing RLS helper (core.rls.enable_rls_org_scoped) hides NULL-organization
    rows from everyone — a "global + per-org custom" shape would need a new
    RLS policy variant, which this phase doesn't introduce. `is_system` marks
    the standard set seeded by items.services.units.seed_default_units();
    orgs may still add their own on top. See items/CLAUDE.md.
    """

    code = models.CharField(max_length=16)
    name = models.CharField(max_length=64)
    symbol = models.CharField(max_length=8, blank=True)
    is_active = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], name="uniq_unit_code_per_org"),
        ]
        ordering = ["code"]

    def __str__(self):
        return self.code
