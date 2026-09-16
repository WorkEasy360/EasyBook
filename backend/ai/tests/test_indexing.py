from unittest.mock import patch

from django.test import override_settings

from ai.embeddings import active_embedding_spec, override_embedding_provider
from ai.embeddings.fake import FakeEmbeddingProvider
from ai.models import DocumentChunk, DocumentIndex, IndexStatus, TextSource
from ai.providers.errors import ProviderUnavailable
from ai.retrieval.search import hybrid_search
from ai.tests.base import AITestsBase, index, tenant, upload_text
from audit.models import AuditLog
from documents.models.document import DocumentType, OCRStatus, UploadStatus
from documents.models.ocr_result import OCRResult
from documents.ocr.providers.base import ExtractionResult
from documents.services.malware_scan import EICAR_SIGNATURE
from documents.services.ocr import request_ocr
from documents.services.review import approve_review
from documents.services.uploads import archive_document, upload_document

PDF_BYTES = b"%PDF-1.4\n%mock\n"
CONTRACT_TEXT = (
    "VENDOR AGREEMENT\n\nThe vendor shall deliver consulting services monthly.\n\n"
    "Termination: either party may terminate with thirty days written notice."
)


def upload_pdf_with_ocr(test, text, confidence=0.95, title="Scanned contract"):
    with tenant(test.org_a, test.user_a):
        document = upload_document(
            organization=test.org_a, uploaded_by=test.user_a, content=PDF_BYTES, original_filename="scan.pdf",
            title=title, document_type=DocumentType.CONTRACT,
        )
        with patch("documents.services.ocr.get_provider") as provider:
            provider.return_value.extract.return_value = ExtractionResult(
                provider="test", provider_version="1", raw_text=text, confidence=confidence,
            )
            provider.return_value.name, provider.return_value.version = "test", "1"
            request_ocr(document=document)
        document.refresh_from_db()
    return document


class IndexingTests(AITestsBase):
    def test_native_text_document_is_chunked_and_embedded(self):
        document = upload_text(self.org_a, self.user_a, CONTRACT_TEXT, title="Vendor agreement")
        result = index(self.org_a, document)
        self.assertEqual(result.status, IndexStatus.INDEXED)
        with tenant(self.org_a):
            chunks = list(DocumentChunk.objects.filter(document=document))
        self.assertEqual(len(chunks), result.chunk_count)
        self.assertGreater(len(chunks), 0)
        spec = active_embedding_spec()
        for chunk in chunks:
            self.assertEqual(chunk.organization_id, self.org_a.id)
            self.assertEqual(len(chunk.embedding), spec.dimensions)
            self.assertEqual(chunk.embedding_config_key, spec.config_key)
            self.assertEqual(chunk.embedding_model, spec.model)
            self.assertEqual(chunk.text_source, TextSource.NATIVE_TEXT)
            self.assertEqual(chunk.index_content_hash, result.content_hash)

    def test_unchanged_document_indexing_is_idempotent(self):
        document = upload_text(self.org_a, self.user_a, CONTRACT_TEXT)
        provider = FakeEmbeddingProvider(spec=active_embedding_spec())
        with override_embedding_provider(provider):
            first = index(self.org_a, document)
            with tenant(self.org_a):
                ids_before = set(DocumentChunk.objects.filter(document=document).values_list("id", flat=True))
            second = index(self.org_a, document)
            with tenant(self.org_a):
                ids_after = set(DocumentChunk.objects.filter(document=document).values_list("id", flat=True))
        self.assertEqual(provider.calls, 1, "an unchanged document must not be re-embedded")
        self.assertEqual(ids_before, ids_after)
        self.assertEqual(first.content_hash, second.content_hash)

    def test_force_reindex_rebuilds_one_chunk_set(self):
        document = upload_text(self.org_a, self.user_a, CONTRACT_TEXT)
        first = index(self.org_a, document)
        index(self.org_a, document, force=True)
        with tenant(self.org_a):
            self.assertEqual(DocumentChunk.objects.filter(document=document).count(), first.chunk_count)

    def test_indexing_is_audited(self):
        document = upload_text(self.org_a, self.user_a, CONTRACT_TEXT)
        index(self.org_a, document, actor=self.user_a)
        with tenant(self.org_a):
            entry = AuditLog.objects.filter(object_type="ai.DocumentIndex", object_id=str(document.pk)).get()
        self.assertEqual(entry.changes["event"], "indexed")

    def test_quarantined_document_is_never_indexed(self):
        document = upload_text(self.org_a, self.user_a, "invoice text " + EICAR_SIGNATURE.decode("latin-1"))
        self.assertEqual(document.upload_status, UploadStatus.QUARANTINED)
        result = index(self.org_a, document)
        self.assertEqual(result.status, IndexStatus.NOT_INDEXABLE)
        self.assertEqual(result.reason_code, "document_quarantined")
        with tenant(self.org_a):
            self.assertFalse(DocumentChunk.objects.filter(document=document).exists())

    def test_pdf_without_completed_ocr_is_not_indexable_and_nothing_is_invented(self):
        low_confidence = upload_pdf_with_ocr(self, "Some extracted text", confidence=0.4)
        self.assertEqual(low_confidence.ocr_status, OCRStatus.NEEDS_REVIEW)
        result = index(self.org_a, low_confidence)
        self.assertEqual(result.status, IndexStatus.NOT_INDEXABLE)
        self.assertEqual(result.reason_code, "no_text_available")
        with tenant(self.org_a):
            self.assertFalse(DocumentChunk.objects.exists())

    def test_reviewed_ocr_text_becomes_indexable(self):
        document = upload_pdf_with_ocr(self, CONTRACT_TEXT, confidence=0.4)
        with tenant(self.org_a, self.user_a):
            approve_review(document=document, reviewer=self.user_a)
        result = index(self.org_a, document)
        self.assertEqual(result.status, IndexStatus.INDEXED)
        self.assertEqual(result.text_source, TextSource.OCR)

    def test_changed_ocr_text_replaces_old_chunks(self):
        document = upload_pdf_with_ocr(self, "The warranty period is twelve months.")
        index(self.org_a, document)
        with tenant(self.org_a):
            self.assertTrue(hybrid_search(organization=self.org_a, query="warranty period").chunks)
            ocr = OCRResult.objects.get(document=document)
            ocr.raw_text = "The indemnity cap equals annual fees."
            ocr.save()
            # Stale chunks must stop being retrievable immediately, before any reindex.
            self.assertFalse(DocumentChunk.objects.filter(document=document).exists())
            self.assertFalse(hybrid_search(organization=self.org_a, query="warranty period").chunks)
        result = index(self.org_a, document)
        self.assertEqual(result.status, IndexStatus.INDEXED)
        with tenant(self.org_a):
            texts = list(DocumentChunk.objects.filter(document=document).values_list("text", flat=True))
            self.assertTrue(hybrid_search(organization=self.org_a, query="indemnity cap").chunks)
        self.assertTrue(all("warranty" not in text for text in texts))

    def test_archived_document_chunks_are_purged_and_unretrievable(self):
        document = upload_text(self.org_a, self.user_a, CONTRACT_TEXT, title="Vendor agreement")
        index(self.org_a, document)
        with tenant(self.org_a, self.user_a):
            self.assertTrue(hybrid_search(organization=self.org_a, query="termination notice").chunks)
            archive_document(document=document, actor=self.user_a)
            self.assertFalse(DocumentChunk.objects.filter(document=document).exists())
            self.assertEqual(DocumentIndex.objects.get(document=document).status, IndexStatus.NOT_INDEXABLE)
            self.assertFalse(hybrid_search(organization=self.org_a, query="termination notice").chunks)
        self.assertEqual(index(self.org_a, document).reason_code, "document_archived")

    def test_embedding_failure_marks_failed_without_partial_chunks(self):
        document = upload_text(self.org_a, self.user_a, CONTRACT_TEXT)
        failing = FakeEmbeddingProvider(spec=active_embedding_spec(), fail_with=ProviderUnavailable("down"))
        with override_embedding_provider(failing):
            result = index(self.org_a, document)
        self.assertEqual(result.status, IndexStatus.FAILED)
        self.assertEqual(result.reason_code, "ai_unavailable")
        with tenant(self.org_a):
            self.assertFalse(DocumentChunk.objects.filter(document=document).exists())

    def test_embedding_failure_on_refresh_keeps_previous_complete_set(self):
        document = upload_text(self.org_a, self.user_a, CONTRACT_TEXT)
        good = index(self.org_a, document)
        failing = FakeEmbeddingProvider(spec=active_embedding_spec(), fail_with=ProviderUnavailable("down"))
        with override_embedding_provider(failing):
            result = index(self.org_a, document, force=True)
        self.assertEqual(result.status, IndexStatus.INDEXED)
        with tenant(self.org_a):
            self.assertEqual(DocumentChunk.objects.filter(document=document).count(), good.chunk_count)

    def test_cross_tenant_document_id_resolves_to_nothing(self):
        document = upload_text(self.org_b, self.user_b, CONTRACT_TEXT)
        self.assertIsNone(index(self.org_a, document))
        with tenant(self.org_b):
            self.assertFalse(DocumentChunk.objects.filter(document=document).exists())

    @override_settings(AI_AUTO_INDEX_DOCUMENTS=True)
    def test_ready_text_document_is_indexed_automatically_on_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            document = upload_text(self.org_a, self.user_a, CONTRACT_TEXT)
        with tenant(self.org_a):
            self.assertEqual(DocumentIndex.objects.get(document=document).status, IndexStatus.INDEXED)
