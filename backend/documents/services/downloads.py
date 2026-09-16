from django.conf import settings

from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from documents.models.document import Document, UploadStatus
from documents.storage import get_storage


def get_download_url(*, document: Document, actor=None) -> dict:
    """Never for a quarantined document (phase section 7: "quarantined
    documents cannot be downloadable") — everything else, including an
    archived one, may still be retrieved since it can be the only surviving
    evidence behind a posted financial record (root CLAUDE.md rule 12)."""
    # Re-resolve through the tenant-scoped manager rather than trusting the
    # passed-in instance — defense in depth matching services/ocr.py and
    # services/review.py, so a caller holding a stale cross-org reference
    # fails closed here too, not only at the API layer's own lookup.
    document = Document.objects.get(pk=document.pk)

    if document.upload_status == UploadStatus.QUARANTINED:
        raise ApplicationError("This document is quarantined and cannot be downloaded.", code="document_quarantined")
    if document.upload_status in (UploadStatus.UPLOADING, UploadStatus.SCANNING, UploadStatus.FAILED):
        raise ApplicationError("This document is not yet available for download.", code="document_not_ready")

    ttl = settings.DOCUMENT_SIGNED_URL_TTL_SECONDS
    url = get_storage().get_signed_url(key=document.storage_key, expires_in=ttl)

    record_audit(
        organization_id=document.organization_id, actor=actor, action=AuditLog.Action.UPDATE,
        object_type="documents.Document", object_id=document.id, changes={"action": "download"},
    )
    return {"url": url, "expires_in": ttl}
