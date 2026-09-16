from django.db import models

from core.models import TenantScopedModel


class DocumentTag(TenantScopedModel):
    name = models.CharField(max_length=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "name"], name="uniq_tag_name_per_org"),
        ]
        ordering = ["name"]

    def __str__(self):
        return self.name


class DocumentTagAssignment(TenantScopedModel):
    document = models.ForeignKey("documents.Document", on_delete=models.CASCADE, related_name="tag_assignments")
    tag = models.ForeignKey(DocumentTag, on_delete=models.CASCADE, related_name="assignments")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["document", "tag"], name="uniq_tag_per_document"),
        ]

    def __str__(self):
        return f"{self.document_id}:{self.tag.name}"
