from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models

from ai.fields import VectorField
from core.models import TenantScopedModel


class IndexStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    INDEXED = "indexed", "Indexed"
    NOT_INDEXABLE = "not_indexable", "Not indexable"
    FAILED = "failed", "Failed"


class TextSource(models.TextChoices):
    OCR = "ocr", "OCR (completed)"
    NATIVE_TEXT = "native_text", "Native text file"


class DocumentIndex(TenantScopedModel):
    """Current RAG index state for one Document (one row per document).

    `content_hash` fingerprints (normalized text + chunking version +
    embedding config). An index request whose freshly computed hash equals
    the stored hash of an INDEXED row is a no-op — that is what makes
    indexing idempotent (ai/rag/indexing.py).
    """

    document = models.OneToOneField("documents.Document", on_delete=models.CASCADE, related_name="ai_index")
    status = models.CharField(max_length=20, choices=IndexStatus.choices, default=IndexStatus.PENDING)
    # Machine-readable reason for NOT_INDEXABLE/FAILED (never a raw provider
    # message, which can carry request fragments).
    reason_code = models.CharField(max_length=50, blank=True)
    text_source = models.CharField(max_length=20, choices=TextSource.choices, blank=True)
    content_hash = models.CharField(max_length=64, blank=True)
    embedding_config_key = models.CharField(max_length=200, blank=True)
    chunk_count = models.PositiveIntegerField(default=0)
    indexed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["organization", "status"])]

    def __str__(self):
        return f"DocumentIndex({self.document_id}, {self.status})"


class DocumentChunk(TenantScopedModel):
    """One retrievable passage of an authorized document's text.

    Tenant-scoped + RLS like every other org-owned table — there is no
    global vector table. `document_type` is denormalized for filtering only;
    retrieval still JOINs `documents.Document` and requires it to be READY at
    query time, so archiving/quarantining hides chunks immediately, before
    the physical purge (ai/rag/signals.py) even runs.
    """

    document = models.ForeignKey("documents.Document", on_delete=models.CASCADE, related_name="ai_chunks")
    chunk_index = models.PositiveIntegerField()
    text = models.TextField()
    page_number = models.PositiveIntegerField(null=True, blank=True)
    section = models.CharField(max_length=255, blank=True)
    char_start = models.PositiveIntegerField()
    char_end = models.PositiveIntegerField()
    text_source = models.CharField(max_length=20, choices=TextSource.choices)
    document_type = models.CharField(max_length=20)

    content_hash = models.CharField(max_length=64)
    index_content_hash = models.CharField(max_length=64)

    embedding = VectorField(null=True, blank=True)
    embedding_provider = models.CharField(max_length=50)
    embedding_model = models.CharField(max_length=100)
    embedding_dimensions = models.PositiveIntegerField()
    embedding_version = models.CharField(max_length=50)
    embedding_config_key = models.CharField(max_length=200)

    # Stored tsvector for PostgreSQL full-text search. 'english' adds
    # stemming ("terminate"/"termination"); identifiers such as INV-1024 or
    # a GSTIN survive the parser as tokens, and ai/retrieval/lexical.py adds
    # an exact substring pass for them on top.
    search_vector = models.GeneratedField(
        expression=SearchVector("text", config="english"),
        output_field=SearchVectorField(),
        db_persist=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["document", "chunk_index"], name="uniq_ai_chunk_per_document_index"),
        ]
        indexes = [
            models.Index(fields=["organization", "embedding_config_key"]),
            models.Index(fields=["organization", "document"]),
            GinIndex(fields=["search_vector"], name="ai_chunk_search_vector_gin"),
        ]
        ordering = ["document_id", "chunk_index"]

    def __str__(self):
        return f"DocumentChunk({self.document_id}#{self.chunk_index})"
