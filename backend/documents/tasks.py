"""Celery tasks for documents. OCR extraction must never run inline in a web
request (root CLAUDE.md, phase section 10) — `services.ocr.request_ocr`
only enqueues this."""

import datetime

from celery import shared_task
from django.utils import timezone

from core.tenancy import tenant_context
from documents.services.ocr import process_ocr, recover_stalled_ocr

# QUEUED must wait out any healthy enqueue -> pickup delay; PROCESSING must
# outlast run_ocr_task's hard time limit (CELERY_TASK_ANNOTATIONS), so a live
# extraction is never failed underneath its worker.
OCR_RECOVERY_QUEUED_AFTER = datetime.timedelta(minutes=10)
OCR_RECOVERY_PROCESSING_AFTER = datetime.timedelta(minutes=60)


@shared_task
def run_ocr_task(document_id: str, organization_id: str):
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
    for organization in Organization.objects.filter(is_active=True):
        with tenant_context(organization_id=organization.id):
            result = recover_stalled_ocr(
                queued_before=now - OCR_RECOVERY_QUEUED_AFTER,
                processing_before=now - OCR_RECOVERY_PROCESSING_AFTER,
            )
        totals["requeued"] += result["requeued"]
        totals["failed"] += result["failed"]
    return totals
