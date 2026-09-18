from django.conf import settings
from django.db import models

from core.models import TenantScopedModel


class DocumentType(models.TextChoices):
    RECEIPT = "receipt", "Receipt"
    INVOICE = "invoice", "Invoice"
    BILL = "bill", "Bill"
    BANK_STATEMENT = "bank_statement", "Bank Statement"
    TAX_DOCUMENT = "tax_document", "Tax Document"
    CONTRACT = "contract", "Contract"
    ATTACHMENT = "attachment", "Attachment"
    GENERAL = "general", "General"


class UploadStatus(models.TextChoices):
    UPLOADING = "uploading", "Uploading"
    SCANNING = "scanning", "Scanning"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"
    QUARANTINED = "quarantined", "Quarantined"
    ARCHIVED = "archived", "Archived"


class OCRStatus(models.TextChoices):
    NOT_REQUESTED = "not_requested", "Not Requested"
    QUEUED = "queued", "Queued"
    PROCESSING = "processing", "Processing"
    NEEDS_REVIEW = "needs_review", "Needs Review"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class StorageBackend(models.TextChoices):
    LOCAL = "local", "Local"
    S3 = "s3", "S3"


# Legal transitions for `Document.upload_status`. Enforced in
# `services/uploads.py::transition_upload_status` — see SLICE 3 / phase
# section 31 ("Use explicit state transitions... Do not allow arbitrary
# status edits").
UPLOAD_STATUS_TRANSITIONS = {
    UploadStatus.UPLOADING: {UploadStatus.SCANNING, UploadStatus.FAILED},
    UploadStatus.SCANNING: {UploadStatus.READY, UploadStatus.QUARANTINED, UploadStatus.FAILED},
    UploadStatus.READY: {UploadStatus.ARCHIVED},
    UploadStatus.FAILED: set(),
    UploadStatus.QUARANTINED: set(),
    UploadStatus.ARCHIVED: set(),
}

# Legal transitions for `Document.ocr_status`. Enforced in
# `services/ocr.py::transition_ocr_status`.
OCR_STATUS_TRANSITIONS = {
    OCRStatus.NOT_REQUESTED: {OCRStatus.QUEUED},
    OCRStatus.QUEUED: {OCRStatus.PROCESSING, OCRStatus.FAILED},
    OCRStatus.PROCESSING: {OCRStatus.NEEDS_REVIEW, OCRStatus.COMPLETED, OCRStatus.FAILED},
    OCRStatus.NEEDS_REVIEW: {OCRStatus.COMPLETED},
    OCRStatus.COMPLETED: set(),
    OCRStatus.FAILED: {OCRStatus.QUEUED},
}


class Document(TenantScopedModel):
    """A single uploaded file plus its lifecycle state. Never the source of
    truth for accounting — see root CLAUDE.md pipeline rule and
    documents/CLAUDE.md. The file bytes themselves live in whichever
    `DocumentStorage` backend `storage_backend` names; this row only ever
    holds a `storage_key` reference, never the bytes.
    """

    title = models.CharField(max_length=255)
    original_filename = models.CharField(max_length=255)
    # Opaque, server-generated (see services/uploads.py::generate_storage_key)
    # — never derived from client-supplied input, so it carries no path-
    # traversal or collision risk regardless of what the client named the file.
    storage_key = models.CharField(max_length=512, unique=True)
    storage_backend = models.CharField(max_length=10, choices=StorageBackend.choices)
    mime_type = models.CharField(max_length=127)
    file_size = models.PositiveBigIntegerField()
    checksum_sha256 = models.CharField(max_length=64)

    document_type = models.CharField(max_length=20, choices=DocumentType.choices, default=DocumentType.GENERAL)
    upload_status = models.CharField(max_length=20, choices=UploadStatus.choices, default=UploadStatus.UPLOADING)
    ocr_status = models.CharField(max_length=20, choices=OCRStatus.choices, default=OCRStatus.NOT_REQUESTED)
    # Spent by the recovery sweeper (documents/tasks.py) when it re-enqueues a
    # document stuck QUEUED, and reset whenever OCR is requested afresh. Without
    # a budget the sweeper re-enqueues forever, re-fetching the file and
    # re-invoking the (paid) OCR provider every cycle.
    ocr_recovery_attempts = models.PositiveIntegerField(default=0)

    folder = models.ForeignKey(
        "documents.DocumentFolder", null=True, blank=True, on_delete=models.SET_NULL, related_name="documents"
    )

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="uploaded_documents"
    )

    # Retention/legal-hold foundation (phase section 33) — no policy engine
    # yet, just the columns a future retention job can key off.
    retention_until = models.DateField(null=True, blank=True)
    legal_hold = models.BooleanField(default=False)

    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["organization", "created_at"]),
            models.Index(fields=["organization", "document_type"]),
            models.Index(fields=["organization", "upload_status"]),
            models.Index(fields=["organization", "ocr_status"]),
            models.Index(fields=["organization", "checksum_sha256"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return self.title
