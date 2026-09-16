"""Upload orchestration (phase slices 1-3).

Deliberately NOT wrapped in one outer `@transaction.atomic`: if `storage.put`
fails after the `Document` row is created, the row must survive as an
explicit `FAILED` record (phase section 29/30 — "avoid orphaned state where
practical... implement recoverable state transitions"), not be rolled back
into a silent no-op by an enclosing savepoint. See documents/CLAUDE.md.
"""

import uuid

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from documents.models.document import Document, DocumentType, StorageBackend, UploadStatus
from documents.services.malware_scan import get_scanner
from documents.services.transitions import transition_upload_status
from documents.services.validation import compute_checksum, validate_upload
from documents.storage import get_storage


def generate_storage_key(*, organization_id, extension: str) -> str:
    """Opaque and server-generated — never derived from the client filename,
    so it carries no path-traversal or predictability risk (phase section 2)."""
    return f"{organization_id}/{uuid.uuid4().hex}{extension}"


@transaction.atomic
def _create_document_row(
    *, organization, uploaded_by, title, safe_filename, storage_key, mime_type, file_size, checksum,
    document_type, folder, actor,
) -> Document:
    backend = getattr(settings, "DOCUMENT_STORAGE_BACKEND", "local")
    document = Document.objects.create(
        organization=organization,
        title=title or safe_filename,
        original_filename=safe_filename,
        storage_key=storage_key,
        storage_backend=StorageBackend.S3 if backend == "s3" else StorageBackend.LOCAL,
        mime_type=mime_type,
        file_size=file_size,
        checksum_sha256=checksum,
        document_type=document_type,
        folder=folder,
        uploaded_by=uploaded_by,
    )
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="documents.Document",
        object_id=document.id,
        changes={"original_filename": safe_filename, "file_size": file_size, "document_type": document_type},
    )
    return document


def upload_document(
    *,
    organization,
    uploaded_by,
    content: bytes,
    original_filename: str,
    title: str = "",
    declared_content_type: str = "",
    document_type: str = DocumentType.GENERAL,
    folder=None,
    actor=None,
) -> Document:
    extension, mime_type, safe_filename = validate_upload(
        filename=original_filename, content=content, declared_content_type=declared_content_type
    )
    checksum = compute_checksum(content)
    storage_key = generate_storage_key(organization_id=organization.id, extension=extension)

    document = _create_document_row(
        organization=organization, uploaded_by=uploaded_by, title=title, safe_filename=safe_filename,
        storage_key=storage_key, mime_type=mime_type, file_size=len(content), checksum=checksum,
        document_type=document_type, folder=folder, actor=actor,
    )

    storage = get_storage()
    try:
        storage.put(key=storage_key, content=content, content_type=mime_type)
    except Exception as exc:
        transition_upload_status(document, UploadStatus.FAILED)
        record_audit(
            organization_id=organization.id, actor=actor, action=AuditLog.Action.UPDATE,
            object_type="documents.Document", object_id=document.id,
            changes={"upload_status": UploadStatus.FAILED, "reason": "storage_write_failed"},
        )
        raise ApplicationError("Failed to store the uploaded file.", code="storage_write_failed") from exc

    transition_upload_status(document, UploadStatus.SCANNING)

    scanner = get_scanner()
    result = scanner.scan(content)
    if result.clean:
        transition_upload_status(document, UploadStatus.READY)
    else:
        transition_upload_status(document, UploadStatus.QUARANTINED)
        record_audit(
            organization_id=organization.id, actor=actor, action=AuditLog.Action.UPDATE,
            object_type="documents.Document", object_id=document.id,
            changes={"upload_status": UploadStatus.QUARANTINED, "scanner": result.scanner_name,
                     "signature": result.signature},
        )

    return document


def update_document(*, document: Document, actor=None, **fields) -> Document:
    """Metadata-only update (title/document_type/folder) — no upload/OCR
    status transition happens here, those go through their own guarded
    transitions."""
    document = Document.objects.get(pk=document.pk)
    if "folder" in fields:
        folder = fields["folder"]
        if folder is not None and folder.organization_id != document.organization_id:
            raise ApplicationError("folder must belong to the same organization.", code="cross_org_reference")

    changes = {}
    for field, value in fields.items():
        if getattr(document, field) == value:
            continue
        changes[field] = str(value.pk) if hasattr(value, "pk") else value
        setattr(document, field, value)
    if not changes:
        return document

    document.save(update_fields=[*changes.keys(), "updated_at"])
    record_audit(
        organization_id=document.organization_id, actor=actor, action=AuditLog.Action.UPDATE,
        object_type="documents.Document", object_id=document.id, changes=changes,
    )
    return document


def archive_document(*, document: Document, actor=None) -> Document:
    document = Document.objects.get(pk=document.pk)
    transition_upload_status(document, UploadStatus.ARCHIVED)
    document.archived_at = timezone.now()
    document.save(update_fields=["archived_at", "updated_at"])
    record_audit(
        organization_id=document.organization_id, actor=actor, action=AuditLog.Action.UPDATE,
        object_type="documents.Document", object_id=document.id, changes={"upload_status": UploadStatus.ARCHIVED},
    )
    return document
