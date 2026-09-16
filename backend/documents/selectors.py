"""Read-only queries derived on demand — never stored (mirrors
purchases/selectors.py, sales/selectors.py)."""

from documents.models.document import Document, UploadStatus


def find_possible_duplicates(*, checksum: str, exclude_id=None):
    """Same-checksum documents already on file for the current organization
    (ambient tenant context, like every other TenantManager query). A
    "possible duplicate" hint only — the caller decides whether to proceed
    (phase section 6: never auto-reject a legitimate re-upload)."""
    qs = Document.objects.filter(checksum_sha256=checksum).exclude(upload_status=UploadStatus.ARCHIVED)
    if exclude_id is not None:
        qs = qs.exclude(id=exclude_id)
    return qs
