from django.db import models

from core.models import TenantScopedModel


class Warehouse(TenantScopedModel):
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=255)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], name="uniq_warehouse_code_per_org"),
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(is_default=True),
                name="uniq_default_warehouse_per_org",
            ),
        ]
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"
