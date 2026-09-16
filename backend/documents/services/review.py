"""Human review workflow (phase section 15).

`_finalize_review` is idempotent-by-construction, the same pattern
`purchases`/`sales` use for post/void: a second APPROVE (or REJECT) racing
against the first on the SAME document is a locked no-op, not an error —
see `documents.models.review.DocumentReview` docstring and phase section 41
("two reviewers approve same extraction -> one authoritative approval").
Approving one extraction with the OPPOSITE outcome of an already-finalized
review IS a genuine conflict and raises, rather than silently overwriting a
completed review.
"""

from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from documents.models.document import Document, OCRStatus
from documents.models.review import DocumentReview, ReviewStatus
from documents.services.transitions import transition_ocr_status


@transaction.atomic
def _finalize_review(
    *, document: Document, reviewer, target_status: str, corrected_fields: dict | None, notes: str,
) -> DocumentReview:
    document = Document.objects.select_for_update().get(pk=document.pk)
    if document.ocr_status not in (OCRStatus.NEEDS_REVIEW, OCRStatus.COMPLETED):
        raise ApplicationError(
            "Document has no OCR extraction to review yet.", code="nothing_to_review"
        )

    review = DocumentReview.objects.select_for_update().filter(document=document).first()
    if review is None:
        review = DocumentReview.objects.create(document=document, organization=document.organization)

    if review.status == target_status:
        return review  # idempotent no-op: same-outcome race or literal retry
    if review.status != ReviewStatus.PENDING:
        raise ApplicationError(
            f"Review is already finalized as {review.status}.", code="review_already_finalized"
        )

    review.status = target_status
    review.reviewer = reviewer
    review.reviewed_at = timezone.now()
    review.corrected_fields = corrected_fields or {}
    review.notes = notes
    review.save(update_fields=["status", "reviewer", "reviewed_at", "corrected_fields", "notes", "updated_at"])

    if target_status == ReviewStatus.APPROVED and document.ocr_status != OCRStatus.COMPLETED:
        transition_ocr_status(document, OCRStatus.COMPLETED)

    record_audit(
        organization_id=document.organization_id, actor=reviewer, action=AuditLog.Action.UPDATE,
        object_type="documents.DocumentReview", object_id=review.id,
        changes={"status": target_status, "document_id": str(document.id)},
    )
    return review


def approve_review(*, document: Document, reviewer, corrected_fields: dict | None = None, notes: str = "") -> DocumentReview:
    return _finalize_review(
        document=document, reviewer=reviewer, target_status=ReviewStatus.APPROVED,
        corrected_fields=corrected_fields, notes=notes,
    )


def reject_review(*, document: Document, reviewer, notes: str = "") -> DocumentReview:
    return _finalize_review(
        document=document, reviewer=reviewer, target_status=ReviewStatus.REJECTED,
        corrected_fields=None, notes=notes,
    )
