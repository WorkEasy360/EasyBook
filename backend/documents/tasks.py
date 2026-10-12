"""Celery tasks for documents. OCR extraction must never run inline in a web
request (root CLAUDE.md, phase section 10) — `services.ocr.request_ocr`
only enqueues this."""

import datetime

from celery import shared_task
from celery.utils.log import get_task_logger
from django.utils import timezone

from core import access
from core.tenancy import tenant_context
from documents.services.ocr import has_stalled_ocr, process_ocr, recover_stalled_ocr

logger = get_task_logger("documents.tasks")

# QUEUED must wait out any healthy enqueue -> pickup delay; PROCESSING must
# outlast run_ocr_task's hard time limit (CELERY_TASK_ANNOTATIONS), so a live
# extraction is never failed underneath its worker.
OCR_RECOVERY_QUEUED_AFTER = datetime.timedelta(minutes=10)
OCR_RECOVERY_PROCESSING_AFTER = datetime.timedelta(minutes=60)
# How many times the sweeper re-enqueues one document before failing it
# visibly. Unbounded, a document that can never be processed is re-fetched and
# re-sent to the (paid) OCR provider every sweep, forever.
OCR_RECOVERY_MAX_ATTEMPTS = 5


@shared_task
def run_ocr_task(document_id: str, organization_id: str):
    # Extraction sends the document to the OCR provider and writes the result. An organization whose
    # Workeasy360 access ended is skipped: the document stays QUEUED, and the recovery sweeper re-enqueues
    # it after a renewal (docs/workeasy360-sso.md).
    if not access.background_allowed_for_id(organization_id, task="documents.run_ocr"):
        return
    with tenant_context(organization_id=organization_id):
        process_ocr(document_id=document_id)


@shared_task
def recover_stalled_ocr_task():
    """Documents whose OCR task message was lost stay QUEUED forever, and ones
    whose worker died mid-extraction stay PROCESSING forever — see
    services.ocr.recover_stalled_ocr."""
    from accounts.models import Organization

    now = timezone.now()
    totals = {"requeued": 0, "failed": 0}
    queued_before = now - OCR_RECOVERY_QUEUED_AFTER
    processing_before = now - OCR_RECOVERY_PROCESSING_AFTER
    for organization in Organization.objects.filter(is_active=True):
        # Asked only when something is stalled. Denied: re-enqueuing would be skipped again and would spend
        # the document's recovery budget.
        with tenant_context(organization_id=organization.id):
            stalled = has_stalled_ocr(queued_before=queued_before, processing_before=processing_before)
        if not stalled or not access.background_allowed(organization, task="documents.recover_stalled_ocr"):
            continue
        with tenant_context(organization_id=organization.id):
            result = recover_stalled_ocr(
                queued_before=queued_before,
                processing_before=processing_before,
                max_attempts=OCR_RECOVERY_MAX_ATTEMPTS,
            )
        totals["requeued"] += result["requeued"]
        totals["failed"] += result["failed"]
    logger.info("documents_ocr_recovery_swept", extra=totals)
    return totals
