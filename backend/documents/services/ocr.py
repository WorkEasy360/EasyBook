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

    # On commit: called from the OCR endpoint this is still inside the
    # request's transaction, and a worker that claimed the message first would
    # see the document not yet QUEUED, skip it as a duplicate, and leave it
    # QUEUED forever.
    document_id, organization_id = str(document.id), str(document.organization_id)
    transaction.on_commit(lambda: run_ocr_task.delay(document_id, organization_id))
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


def recover_stalled_ocr(*, queued_before, processing_before, batch_size: int = 200) -> dict:
    """Recovery sweeper body (documents/tasks.py::recover_stalled_ocr_task),
    run inside the caller's tenant_context.

    QUEUED past `queued_before`: the task message was lost — re-enqueue it
    (process_ocr only ever claims a QUEUED document, so a duplicate is a
    no-op). PROCESSING past `processing_before` (longer than run_ocr_task's
    hard time limit): the worker died mid-extraction and a redelivered message
    will rightly refuse to reprocess it — fail it visibly instead, which is
    also the state `request_ocr` accepts a fresh request from.
    """
    from documents.tasks import run_ocr_task

    requeued = failed = 0
    stalled_queued = (
        Document.objects.select_for_update(skip_locked=True)
        .filter(ocr_status=OCRStatus.QUEUED, updated_at__lt=queued_before)
        .order_by("updated_at")[:batch_size]
    )
    for document in stalled_queued:
        # Touching updated_at keeps the next sweep from re-enqueuing it again
        # while this message is still on its way to a worker.
        document.save(update_fields=["updated_at"])
        transaction.on_commit(
            lambda document_id=str(document.id), organization_id=str(document.organization_id): run_ocr_task.delay(
                document_id, organization_id
            )
        )
        requeued += 1

    stalled_processing = (
        Document.objects.select_for_update(skip_locked=True)
        .filter(ocr_status=OCRStatus.PROCESSING, updated_at__lt=processing_before)
        .order_by("updated_at")[:batch_size]
    )
    for document in stalled_processing:
        OCRResult.objects.update_or_create(
            document=document,
            defaults={
                "organization": document.organization,
                "provider": "none",
                "provider_version": "",
                "raw_text": "",
                "structured_payload": {},
                "confidence": None,
                "error_code": "ocr_worker_lost",
                "error_message": "Text extraction did not finish. Request OCR again.",
                "processed_at": timezone.now(),
            },
        )
        transition_ocr_status(document, OCRStatus.FAILED)
        record_audit(
            organization_id=document.organization_id, action=AuditLog.Action.UPDATE,
            object_type="documents.Document", object_id=document.id,
            changes={"ocr_status": OCRStatus.FAILED, "reason": "ocr_worker_lost"},
        )
        failed += 1
    return {"requeued": requeued, "failed": failed}
