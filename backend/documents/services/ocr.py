"""OCR orchestration (phase slices 5-6).

`request_ocr` only ever enqueues work — the actual extraction
(`process_ocr`) runs on a Celery worker (documents/tasks.py) and must never
run inline in a web request (root CLAUDE.md / phase section 10).
"""

from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from documents.models.document import Document, OCRStatus, UploadStatus
from documents.models.ocr_result import MAX_RAW_TEXT_CHARS, OCRResult
from documents.ocr.providers import get_provider
from documents.ocr.providers.base import ExtractionResult
from documents.services.transitions import transition_ocr_status
from documents.storage import get_storage

# OCR states that mean "already requested, in flight, or done" — a second
# `request_ocr` call on any of these is a no-op, not an error (phase section
# 26/37: "duplicate OCR request idempotent").
_ALREADY_REQUESTED = {OCRStatus.QUEUED, OCRStatus.PROCESSING, OCRStatus.NEEDS_REVIEW, OCRStatus.COMPLETED}


def request_ocr(*, document: Document, actor=None) -> Document:
    with transaction.atomic():
        document = Document.objects.select_for_update().get(pk=document.pk)
        if document.upload_status != UploadStatus.READY:
            raise ApplicationError(
                "OCR can only be requested for a READY document.", code="document_not_ready"
            )
        if document.ocr_status in _ALREADY_REQUESTED:
            return document
        transition_ocr_status(document, OCRStatus.QUEUED)

    record_audit(
        organization_id=document.organization_id, actor=actor, action=AuditLog.Action.UPDATE,
        object_type="documents.Document", object_id=document.id, changes={"ocr_status": OCRStatus.QUEUED},
    )

    from documents.tasks import run_ocr_task

    run_ocr_task.delay(str(document.id), str(document.organization_id))
    return document


def process_ocr(*, document_id) -> None:
    """The Celery task body (documents/tasks.py::run_ocr_task calls this
    inside a `tenant_context`). Never raises for an extraction failure — that
    is stored on OCRResult (phase section 12/37: "OCR failure stores error
    safely"), not propagated to crash the worker."""
    with transaction.atomic():
        try:
            document = Document.objects.select_for_update().get(pk=document_id)
        except Document.DoesNotExist:
            return
        if document.ocr_status != OCRStatus.QUEUED:
            # Already claimed by another worker, or moved on since — a
            # duplicate/racing task is a no-op (phase section 27/41).
            return
        transition_ocr_status(document, OCRStatus.PROCESSING)

    provider = get_provider()
    try:
        content = get_storage().get_bytes(key=document.storage_key)
        result = provider.extract(content=content, mime_type=document.mime_type)
    except Exception as exc:
        result = ExtractionResult(
            provider=provider.name, provider_version=provider.version, raw_text="",
            error_code="ocr_extraction_failed", error_message=str(exc)[:500],
        )

    with transaction.atomic():
        document = Document.objects.select_for_update().get(pk=document_id)
        confidence = (
            Decimal(str(round(result.confidence, 4))) if result.confidence is not None else None
        )
        OCRResult.objects.update_or_create(
            document=document,
            defaults={
                "organization": document.organization,
                "provider": result.provider,
                "provider_version": result.provider_version,
                "raw_text": result.raw_text[:MAX_RAW_TEXT_CHARS],
                "structured_payload": result.fields,
                "confidence": confidence,
                "error_code": result.error_code,
                "error_message": result.error_message,
                "processed_at": timezone.now(),
            },
        )

        if result.failed:
            new_status = OCRStatus.FAILED
        elif confidence is not None and confidence >= Decimal(str(settings.DOCUMENT_OCR_CONFIDENCE_HIGH)):
            new_status = OCRStatus.COMPLETED
        else:
            new_status = OCRStatus.NEEDS_REVIEW
        transition_ocr_status(document, new_status)

    record_audit(
        organization_id=document.organization_id, action=AuditLog.Action.UPDATE,
        object_type="documents.Document", object_id=document.id,
        changes={"ocr_status": new_status, "provider": result.provider},
    )
