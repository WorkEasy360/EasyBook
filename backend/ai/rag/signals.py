"""Propagate document state changes into the RAG index.

`documents` never imports `ai` (layering: ai sits above documents), so the
propagation is a receiver here, not a call from documents' services.

Enforcement does NOT depend on these receivers: retrieval requires the
document to be READY (and OCR-sourced chunks to have COMPLETED OCR) at query
time. These receivers make the physical purge immediate and in the SAME
transaction as the state change, so no stale row outlives it on commit.
"""

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from ai.models.chunk import DocumentChunk, DocumentIndex, IndexStatus, TextSource
from documents.models.document import Document, OCRStatus, UploadStatus
from documents.models.ocr_result import OCRResult

_NON_RETRIEVABLE_TERMINAL = frozenset({UploadStatus.ARCHIVED, UploadStatus.QUARANTINED, UploadStatus.FAILED})


def _auto_index_enabled() -> bool:
    return bool(getattr(settings, "AI_AUTO_INDEX_DOCUMENTS", False))


def _enqueue_index(document: Document) -> None:
    from ai.rag.tasks import index_document_task

    document_id, organization_id = str(document.pk), str(document.organization_id)
    transaction.on_commit(lambda: index_document_task.delay(document_id, organization_id))


@receiver(post_save, sender=Document, dispatch_uid="ai_document_state_changed")
def document_state_changed(sender, instance: Document, created, update_fields=None, **kwargs):
    if created:
        return
    if instance.upload_status in _NON_RETRIEVABLE_TERMINAL:
        # UPLOADING/SCANNING documents never had chunks, so only the terminal
        # non-READY states need a purge.
        # all_objects: RLS still scopes the DELETE to the current tenant; the
        # app-level manager would silently match nothing without a contextvar.
        DocumentChunk.all_objects.filter(document_id=instance.pk).delete()
        DocumentIndex.all_objects.filter(document_id=instance.pk).exclude(
            status=IndexStatus.NOT_INDEXABLE, reason_code=f"document_{instance.upload_status}",
        ).update(
            status=IndexStatus.NOT_INDEXABLE, reason_code=f"document_{instance.upload_status}",
            chunk_count=0, content_hash="", indexed_at=None,
        )
        return

    relevant = update_fields is None or {"upload_status", "ocr_status"} & set(update_fields)
    if relevant and _auto_index_enabled():
        from ai.rag.text_source import NATIVE_TEXT_MIME_TYPES

        if instance.ocr_status == OCRStatus.COMPLETED or instance.mime_type in NATIVE_TEXT_MIME_TYPES:
            _enqueue_index(instance)


@receiver(post_save, sender=OCRResult, dispatch_uid="ai_ocr_result_replaced")
def ocr_result_replaced(sender, instance: OCRResult, **kwargs):
    """OCR text was written or replaced: chunks built from the previous OCR
    text are stale and must stop existing now, not after a reindex."""
    deleted, _ = DocumentChunk.all_objects.filter(document_id=instance.document_id, text_source=TextSource.OCR).delete()
    if deleted:
        DocumentIndex.all_objects.filter(document_id=instance.document_id).update(
            status=IndexStatus.PENDING, reason_code="ocr_text_replaced", chunk_count=0, content_hash="", indexed_at=None,
        )
