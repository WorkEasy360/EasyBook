from django.db import models

from core.models import TenantScopedModel


class HsnSacClassification(models.TextChoices):
    HSN = "hsn", "HSN (goods)"
    SAC = "sac", "SAC (services)"


class HsnSacCode(TenantScopedModel):
    """Manual-entry HSN/SAC metadata only — no catalog is seeded here.

    Root CLAUDE.md forbids inventing compliance data, and no verified
    official HSN/SAC master dataset was available at implementation time
    (see items/CLAUDE.md). Organizations enter the codes relevant to them.
    """

    code = models.CharField(max_length=16)
    description = models.CharField(max_length=255, blank=True)
    classification = models.CharField(max_length=8, choices=HsnSacClassification.choices)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], name="uniq_hsn_sac_code_per_org"),
        ]
        verbose_name = "HSN/SAC code"
        verbose_name_plural = "HSN/SAC codes"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} ({self.classification})"
