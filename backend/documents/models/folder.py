from django.db import models

from core.models import TenantScopedModel


class DocumentFolder(TenantScopedModel):
    """Lightweight hierarchical grouping — deliberately not a full
    SharePoint-like permissions/sharing system (phase section 9)."""

    name = models.CharField(max_length=255)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="children"
    )

    class Meta:
        constraints = [
            # `nulls_distinct=False` (Postgres 15+) so two ROOT folders (parent
            # IS NULL) in the same org still collide on name — Postgres
            # otherwise treats NULL != NULL and would let "Receipts" exist
            # twice at the root.
            models.UniqueConstraint(
                fields=["organization", "parent", "name"], name="uniq_folder_name_per_parent",
                nulls_distinct=False,
            ),
        ]
        indexes = [models.Index(fields=["organization", "parent"])]
        ordering = ["name"]

    def __str__(self):
        return self.name
