"""RAG ingestion: AUTHORIZED DOCUMENT -> TEXT -> NORMALIZE -> CHUNK -> EMBED -> STORE.

Idempotency and concurrency (phase sections 14/47/82):
  * `content_hash` = sha256(chunking config + embedding config key + normalized
    text). An INDEXED row with the same hash means there is nothing to do —
    no embedding call, no writes.
  * Embedding runs OUTSIDE any lock (it is a slow external call).
  * The write phase locks the `documents.Document` row (SELECT ... FOR
    UPDATE — every indexer for a document, and every archive/quarantine
    UPDATE, serializes on it), then re-checks: still READY, text unchanged,
    and not already indexed at this hash by a racing worker. Only then are
    the old chunks deleted and the new set inserted, in one transaction —
    readers see either the old complete set or the new complete set.
  * Archive/quarantine during indexing: either the archive commits first
    (the re-check under the lock sees non-READY and purges), or indexing
    commits first (the archive's UPDATE then fires ai/rag/signals.py, which
    purges). Retrieval ALSO requires READY at query time, so there is no
    window in which a non-READY document's chunk is retrievable.

Callers must already be inside the document's tenant context (the Celery
task wraps this in core.tenancy.tenant_context).
"""

import hashlib
import logging

from django.db import transaction
from django.utils import timezone

from ai.config import get_ai_config
from ai.embeddings import active_embedding_spec, embed_documents
from ai.models.chunk import DocumentChunk, DocumentIndex, IndexStatus, TextSource
from ai.providers.errors import AIProviderError
from ai.rag.chunking import chunk_text, normalize_text
from ai.rag.text_source import IndexableText, resolve_document_text
from audit.models import AuditLog
from audit.services import record as record_audit
from documents.models.document import Document, UploadStatus

logger = logging.getLogger("ai.rag")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def compute_index_hash(*, normalized_text: str, chunking, spec) -> str:
    header = f"{chunking.version}|{chunking.target_chars}|{chunking.overlap_chars}|{chunking.max_chars}|{spec.config_key}"
    return _sha256(f"{header}\n{normalized_text}")


def _set_index_state(document: Document, **fields) -> DocumentIndex:
    index, _ = DocumentIndex.objects.get_or_create(document=document, defaults={"organization_id": document.organization_id})
    for name, value in fields.items():
        setattr(index, name, value)
    index.save()
    return index


def _purge_and_mark(document_id, *, status: str, reason_code: str) -> DocumentIndex | None:
    with transaction.atomic():
        document = Document.objects.select_for_update().filter(pk=document_id).first()
        if document is None:
            return None
        DocumentChunk.objects.filter(document=document).delete()
        return _set_index_state(
            document, status=status, reason_code=reason_code, chunk_count=0, content_hash="", indexed_at=None,
        )


def index_document(*, document_id, force: bool = False, actor=None) -> DocumentIndex | None:
    """Returns the resulting DocumentIndex, or None if the document does not
    exist in the CURRENT tenant (a cross-tenant id resolves to nothing)."""
    config = get_ai_config()
    spec = active_embedding_spec()

    document = Document.objects.filter(pk=document_id).first()
    if document is None:
        return None

    resolved = resolve_document_text(document)
    if not isinstance(resolved, IndexableText):
        return _purge_and_mark(document.pk, status=IndexStatus.NOT_INDEXABLE, reason_code=resolved.reason_code)
    normalized = normalize_text(resolved.text)
    if not normalized:
        return _purge_and_mark(document.pk, status=IndexStatus.NOT_INDEXABLE, reason_code="no_text_available")

    content_hash = compute_index_hash(normalized_text=normalized, chunking=config.chunking, spec=spec)
    existing = DocumentIndex.objects.filter(document=document).first()
    if not force and existing and existing.status == IndexStatus.INDEXED and existing.content_hash == content_hash:
        return existing

    chunks = chunk_text(normalized, config.chunking)
    try:
        embedded = embed_documents([chunk.text for chunk in chunks])
    except AIProviderError as exc:
        logger.warning("ai_index_embedding_failed", extra={"document_id": str(document.pk), "error_type": type(exc).__name__})
        with transaction.atomic():
            locked = Document.objects.select_for_update().get(pk=document.pk)
            index = DocumentIndex.objects.filter(document=locked).first()
            if index is not None and index.status == IndexStatus.INDEXED:
                # Keep the previous COMPLETE chunk set serving; only record
                # nothing — a failed refresh must not delete good data.
                return index
            return _set_index_state(locked, status=IndexStatus.FAILED, reason_code=exc.code, chunk_count=0)

    with transaction.atomic():
        locked = Document.objects.select_for_update().filter(pk=document.pk).first()
        if locked is None or locked.upload_status != UploadStatus.READY:
            DocumentChunk.objects.filter(document_id=document.pk).delete()
            if locked is None:
                return None
            return _set_index_state(
                locked, status=IndexStatus.NOT_INDEXABLE, reason_code=f"document_{locked.upload_status}",
                chunk_count=0, content_hash="", indexed_at=None,
            )

        if resolved.source == TextSource.OCR:
            # OCR text can be replaced while we were embedding (a re-run
            # overwrites OCRResult). Never store chunks for superseded text.
            current = resolve_document_text(locked)
            if not isinstance(current, IndexableText) or compute_index_hash(
                normalized_text=normalize_text(current.text), chunking=config.chunking, spec=spec
            ) != content_hash:
                return _set_index_state(locked, status=IndexStatus.PENDING, reason_code="content_changed_during_indexing")

        index = DocumentIndex.objects.filter(document=locked).first()
        if not force and index and index.status == IndexStatus.INDEXED and index.content_hash == content_hash:
            return index  # a concurrent worker finished the same work first

        DocumentChunk.objects.filter(document=locked).delete()
        DocumentChunk.objects.bulk_create(
            [
                DocumentChunk(
                    organization_id=locked.organization_id,
                    document=locked,
                    chunk_index=chunk.index,
                    text=chunk.text,
                    page_number=chunk.page_number,
                    section=chunk.section,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    text_source=resolved.source,
                    document_type=locked.document_type,
                    content_hash=_sha256(chunk.text),
                    index_content_hash=content_hash,
                    embedding=vector,
                    embedding_provider=embedded.spec.provider,
                    embedding_model=embedded.spec.model,
                    embedding_dimensions=embedded.spec.dimensions,
                    embedding_version=embedded.spec.version,
                    embedding_config_key=embedded.spec.config_key,
                )
                for chunk, vector in zip(chunks, embedded.vectors, strict=True)
            ]
        )
        previous_hash = index.content_hash if index else ""
        index = _set_index_state(
            locked, status=IndexStatus.INDEXED, reason_code="", text_source=resolved.source,
            content_hash=content_hash, embedding_config_key=embedded.spec.config_key,
            chunk_count=len(chunks), indexed_at=timezone.now(),
        )

    record_audit(
        organization_id=locked.organization_id, actor=actor, action=AuditLog.Action.UPDATE,
        object_type="ai.DocumentIndex", object_id=locked.pk,
        changes={
            "event": "reindexed" if previous_hash else "indexed",
            "chunk_count": len(chunks),
            "embedding_config_key": embedded.spec.config_key,
            "text_source": resolved.source,
        },
    )
    return index
