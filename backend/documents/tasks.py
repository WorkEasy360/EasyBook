"""Celery tasks for documents. OCR extraction must never run inline in a web
request (root CLAUDE.md, phase section 10) — `services.ocr.request_ocr`
only enqueues this."""

from celery import shared_task

from core.tenancy import tenant_context
from documents.services.ocr import process_ocr


@shared_task
def run_ocr_task(document_id: str, organization_id: str):
    with tenant_context(organization_id=organization_id):
        process_ocr(document_id=document_id)
