"""Where indexable text comes from — and when there is none.

Never invents content (phase section 12). A document is indexable only if
it is READY and real text exists:
  * OCR text, once the document's OCR is COMPLETED (human-approved review or
    high-confidence extraction) and the OCRResult carries no error; or
  * a native text file (text/plain, text/csv — already magic-byte/MIME
    validated at upload by documents/services/validation.py), decoded as
    strict UTF-8.
Anything else — PDFs/images awaiting OCR, NEEDS_REVIEW extractions, the
manual OCR provider's empty output — is NOT_INDEXABLE with a reason code.
"""

from dataclasses import dataclass

from ai.models.chunk import TextSource
from documents.models.document import Document, OCRStatus, UploadStatus
from documents.models.ocr_result import MAX_RAW_TEXT_CHARS, OCRResult

NATIVE_TEXT_MIME_TYPES = frozenset({"text/plain", "text/csv"})


@dataclass(frozen=True)
class IndexableText:
    text: str
    source: str


@dataclass(frozen=True)
class NotIndexable:
    reason_code: str


def resolve_document_text(document: Document) -> IndexableText | NotIndexable:
    if document.upload_status != UploadStatus.READY:
        return NotIndexable(reason_code=f"document_{document.upload_status}")

    if document.ocr_status == OCRStatus.COMPLETED:
        ocr = OCRResult.objects.filter(document_id=document.pk).first()
        if ocr is not None and not ocr.error_code and ocr.raw_text.strip():
            return IndexableText(text=ocr.raw_text[:MAX_RAW_TEXT_CHARS], source=TextSource.OCR)

    if document.mime_type in NATIVE_TEXT_MIME_TYPES:
        from documents.storage import get_storage

        content = get_storage().get_bytes(key=document.storage_key)
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            return NotIndexable(reason_code="text_decode_failed")
        if not text.strip():
            return NotIndexable(reason_code="no_text_available")
        return IndexableText(text=text[:MAX_RAW_TEXT_CHARS], source=TextSource.NATIVE_TEXT)

    return NotIndexable(reason_code="no_text_available")
