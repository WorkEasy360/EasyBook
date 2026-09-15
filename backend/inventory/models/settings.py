from django.db import models

from core.models import TenantScopedModel


class InventorySettings(TenantScopedModel):
    """One row per organization. Lives here (not on accounts.Organization,
    a Phase 0 model this phase does not modify) purely to hold the
    negative-stock policy — see inventory/CLAUDE.md."""

    allow_negative_stock = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization"], name="uniq_inventory_settings_per_org"),
        ]

    def __str__(self):
        return f"InventorySettings({self.organization_id})"
