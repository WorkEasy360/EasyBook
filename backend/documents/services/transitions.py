from core.exceptions import ApplicationError
from documents.models.document import OCR_STATUS_TRANSITIONS, UPLOAD_STATUS_TRANSITIONS


def transition_upload_status(document, new_status) -> None:
    """Enforces `UPLOAD_STATUS_TRANSITIONS` (phase section 31 — "do not allow
    arbitrary status edits"). Callers still hold the row inside the caller's
    transaction; this only validates and writes the field."""
    current = document.upload_status
    if new_status not in UPLOAD_STATUS_TRANSITIONS.get(current, set()):
        raise ApplicationError(
            f"Cannot transition Document upload_status from {current} to {new_status}.",
            code="invalid_upload_status_transition",
        )
    document.upload_status = new_status
    document.save(update_fields=["upload_status", "updated_at"])


def transition_ocr_status(document, new_status) -> None:
    current = document.ocr_status
    if new_status not in OCR_STATUS_TRANSITIONS.get(current, set()):
        raise ApplicationError(
            f"Cannot transition Document ocr_status from {current} to {new_status}.",
            code="invalid_ocr_status_transition",
        )
    document.ocr_status = new_status
    document.save(update_fields=["ocr_status", "updated_at"])
