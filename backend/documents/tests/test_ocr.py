import threading
from unittest.mock import patch

from django.db import connections

from core.exceptions import ApplicationError
from documents.models.document import Document, OCRStatus, UploadStatus
from documents.models.ocr_result import OCRResult
from documents.ocr.providers.base import ExtractionResult
from documents.services.ocr import process_ocr, request_ocr
from documents.services.uploads import upload_document
from documents.tests.base import DocumentsTestsBase, DocumentsTransactionTestsBase, tenant

PDF_BYTES = b"%PDF-1.4\n%mock\n"


class RequestOcrTests(DocumentsTestsBase):
    def setUp(self):
        super().setUp()
        with tenant(self.org_a):
            self.document = upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                original_filename="receipt.pdf",
            )

    def test_manual_provider_result_needs_review(self):
        with tenant(self.org_a):
            document = request_ocr(document=self.document, actor=self.user_a)
            document.refresh_from_db()
            result = OCRResult.objects.get(document=document)
        self.assertEqual(document.ocr_status, OCRStatus.NEEDS_REVIEW)
        self.assertEqual(result.provider, "manual")
        self.assertEqual(result.confidence, 0)

    def test_ocr_requires_ready_document(self):
        with tenant(self.org_a):
            self.document.upload_status = UploadStatus.QUARANTINED
            self.document.save(update_fields=["upload_status"])
            with self.assertRaises(ApplicationError) as ctx:
                request_ocr(document=self.document)
        self.assertEqual(ctx.exception.get_codes(), "document_not_ready")

    def test_duplicate_ocr_request_is_idempotent(self):
        with tenant(self.org_a):
            request_ocr(document=self.document)
            request_ocr(document=self.document)
            count = OCRResult.objects.filter(document=self.document).count()
        self.assertEqual(count, 1)

    def test_high_confidence_routes_to_completed(self):
        fake_result = ExtractionResult(
            provider="fake", provider_version="1", raw_text="Total: 100.00", confidence=0.95,
        )
        with tenant(self.org_a):
            with patch("documents.services.ocr.get_provider") as mock_provider:
                mock_provider.return_value.extract.return_value = fake_result
                mock_provider.return_value.name = "fake"
                mock_provider.return_value.version = "1"
                document = request_ocr(document=self.document)
                document.refresh_from_db()
        self.assertEqual(document.ocr_status, OCRStatus.COMPLETED)

    def test_low_confidence_routes_to_needs_review(self):
        fake_result = ExtractionResult(
            provider="fake", provider_version="1", raw_text="blurry", confidence=0.2,
        )
        with tenant(self.org_a):
            with patch("documents.services.ocr.get_provider") as mock_provider:
                mock_provider.return_value.extract.return_value = fake_result
                document = request_ocr(document=self.document)
                document.refresh_from_db()
        self.assertEqual(document.ocr_status, OCRStatus.NEEDS_REVIEW)

    def test_extraction_failure_stores_error_and_does_not_raise(self):
        with tenant(self.org_a):
            with patch("documents.services.ocr.get_storage") as mock_storage:
                mock_storage.return_value.get_bytes.side_effect = OSError("read failed")
                document = request_ocr(document=self.document)
                document.refresh_from_db()
                result = OCRResult.objects.get(document=document)
        self.assertEqual(document.ocr_status, OCRStatus.FAILED)
        self.assertEqual(result.error_code, "ocr_extraction_failed")

    def test_failed_ocr_can_be_retried(self):
        with tenant(self.org_a):
            with patch("documents.services.ocr.get_storage") as mock_storage:
                mock_storage.return_value.get_bytes.side_effect = OSError("read failed")
                request_ocr(document=self.document)
            document = request_ocr(document=self.document)
            document.refresh_from_db()
        self.assertEqual(document.ocr_status, OCRStatus.NEEDS_REVIEW)


class OcrConcurrencyTests(DocumentsTransactionTestsBase):
    def test_duplicate_queued_processing_produces_one_result(self):
        with tenant(self.org_a):
            document = upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                original_filename="race.pdf",
            )
            document.ocr_status = OCRStatus.QUEUED
            document.save(update_fields=["ocr_status"])
        document_id = document.id

        errors = []

        def worker():
            try:
                with tenant(self.org_a):
                    process_ocr(document_id=document_id)
            except Exception as exc:
                errors.append(exc)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        with tenant(self.org_a):
            self.assertEqual(OCRResult.objects.filter(document_id=document_id).count(), 1)
            final = Document.objects.get(pk=document_id)
        self.assertEqual(final.ocr_status, OCRStatus.NEEDS_REVIEW)
