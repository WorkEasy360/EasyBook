"""Recovery sweeper for OCR requests no worker will ever finish.

Regression (phase 12 P0 remediation): `process_ocr` only claims a QUEUED
document, so a document whose `run_ocr_task` message was lost stayed QUEUED
forever, and one whose worker died mid-extraction stayed PROCESSING forever —
a redelivered message sees PROCESSING and correctly refuses to double-process
it, and nothing else ever looked again. `request_ocr` treats both states as
"already requested", so the user could not even ask again.
"""

import datetime

from django.utils import timezone

from documents.models.document import Document, OCRStatus
from documents.models.ocr_result import OCRResult
from documents.services.transitions import transition_ocr_status
from documents.services.uploads import upload_document
from documents.tasks import recover_stalled_ocr_task
from documents.tests.base import DocumentsTestsBase, tenant

PDF_BYTES = b"%PDF-1.4\n%mock\n"


class OcrRecoveryTests(DocumentsTestsBase):
    def setUp(self):
        super().setUp()
        with tenant(self.org_a):
            self.document = upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                original_filename="receipt.pdf",
            )

    def _set_state(self, status, *, minutes_ago):
        with tenant(self.org_a):
            document = Document.objects.get(pk=self.document.pk)
            if status in (OCRStatus.QUEUED, OCRStatus.PROCESSING):
                transition_ocr_status(document, OCRStatus.QUEUED)
            if status == OCRStatus.PROCESSING:
                transition_ocr_status(document, OCRStatus.PROCESSING)
        Document.all_objects.filter(pk=self.document.pk).update(
            updated_at=timezone.now() - datetime.timedelta(minutes=minutes_ago)
        )

    def _sweep(self):
        with self.captureOnCommitCallbacks(execute=True):
            return recover_stalled_ocr_task()

    def test_stale_queued_document_is_re_enqueued_and_processed(self):
        self._set_state(OCRStatus.QUEUED, minutes_ago=30)

        result = self._sweep()

        with tenant(self.org_a):
            self.document.refresh_from_db()
            self.assertEqual(self.document.ocr_status, OCRStatus.NEEDS_REVIEW)
        self.assertEqual(result["requeued"], 1)

    def test_recently_queued_document_is_left_for_its_own_task(self):
        self._set_state(OCRStatus.QUEUED, minutes_ago=1)

        result = self._sweep()

        with tenant(self.org_a):
            self.document.refresh_from_db()
            self.assertEqual(self.document.ocr_status, OCRStatus.QUEUED)
        self.assertEqual(result["requeued"], 0)

    def test_document_stuck_processing_fails_visibly_and_can_be_requested_again(self):
        self._set_state(OCRStatus.PROCESSING, minutes_ago=120)

        result = self._sweep()

        with tenant(self.org_a):
            self.document.refresh_from_db()
            self.assertEqual(self.document.ocr_status, OCRStatus.FAILED)
            ocr_result = OCRResult.objects.get(document=self.document)
            self.assertEqual(ocr_result.error_code, "ocr_worker_lost")
        self.assertEqual(result["failed"], 1)
