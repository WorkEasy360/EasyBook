"""Document search (phase section 18).

Plain `ILIKE` (`icontains`) matching over title/filename/OCR text — real
PostgreSQL capability, not a stub, and adequate at this phase's scale. A
GIN-indexed `SearchVector` (true Postgres full-text search) or an external
engine (Elasticsearch/OpenSearch) is a follow-up once a measured need for
ranking/stemming appears; the phase spec explicitly says not to introduce
either without one.

OCR text is untrusted (phase section 20) — it is only ever matched against
here, never rendered as HTML, and this module returns a `Document` queryset,
never raw OCR text by itself.
"""

from django.db.models import Q, QuerySet

from documents.models.document import Document, UploadStatus
from documents.models.ocr_result import OCRResult
from documents.models.tag import DocumentTagAssignment


def search_documents(
    *,
    query: str = "",
    document_type: str | None = None,
    folder_id=None,
    tag_names: list[str] | None = None,
    include_ocr_text: bool = True,
) -> QuerySet[Document]:
    # Quarantined files are never surfaced by search (phase section 7/21 —
    # unavailable for download, so there is no reason to even list them).
    qs = Document.objects.exclude(upload_status=UploadStatus.QUARANTINED)

    if document_type:
        qs = qs.filter(document_type=document_type)
    if folder_id is not None:
        qs = qs.filter(folder_id=folder_id)
    if tag_names:
        tagged_ids = DocumentTagAssignment.objects.filter(tag__name__in=tag_names).values_list(
            "document_id", flat=True
        )
        qs = qs.filter(id__in=set(tagged_ids))

    if query:
        matches = Q(title__icontains=query) | Q(original_filename__icontains=query)
        if include_ocr_text:
            text_matches = OCRResult.objects.filter(raw_text__icontains=query).values_list(
                "document_id", flat=True
            )
            matches |= Q(id__in=set(text_matches))
        qs = qs.filter(matches)

    return qs.distinct()
