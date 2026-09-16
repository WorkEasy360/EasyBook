from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from core.models import TenantScopedModel


class ReviewStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class DocumentReview(TenantScopedModel):
    """One authoritative review outcome per document's OCR extraction.

    A single row (not an append-only log) so "two reviewers approve
    concurrently" resolves to exactly one authoritative approval by
    construction: `services/review.py::approve_review` takes a
    `select_for_update()` lock and treats a second APPROVE on an already
    APPROVED row as an idempotent no-op rather than a race. `audit.AuditLog`
    (append-only, see audit/CLAUDE.md) is the actual history of who did what
    when — this row is current-state only.
    """

    document = models.OneToOneField("documents.Document", on_delete=models.CASCADE, related_name="review")

    status = models.CharField(max_length=10, choices=ReviewStatus.choices, default=ReviewStatus.PENDING)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    # Reviewer-corrected values, keyed by field name — the values a draft
    # business record would be created from (services/drafts.py), never
    # written back into OCRResult.structured_payload, which stays exactly
    # what the provider produced.
    corrected_fields = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["organization", "status"])]

    def __str__(self):
        return f"DocumentReview({self.document_id}, {self.status})"
