"""`request_ocr` must enqueue only after its transaction commits.

Regression (phase 12 P0 remediation): `request_ocr` marked the document
QUEUED and then called `run_ocr_task.delay()` immediately. Called from the
OCR endpoint, that is still inside the request's transaction
(ATOMIC_REQUESTS): a worker could claim the message first, see the document
not yet QUEUED, treat it as a duplicate (`process_ocr` only claims QUEUED
documents), and leave it QUEUED forever.
"""

from unittest.mock import patch

from documents.services.ocr import request_ocr
from documents.services.uploads import upload_document
from documents.tasks import run_ocr_task
from documents.tests.base import DocumentsTestsBase, tenant

PDF_BYTES = b"%PDF-1.4\n%mock\n"


class OcrDispatchAfterCommitTests(DocumentsTestsBase):
    def test_ocr_task_is_enqueued_only_after_commit(self):
        with tenant(self.org_a):
            document = upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                original_filename="receipt.pdf",
            )
            with patch.object(run_ocr_task, "delay") as delay:
                with self.captureOnCommitCallbacks(execute=False) as callbacks:
                    request_ocr(document=document, actor=self.user_a)
                    delay.assert_not_called()
                for callback in callbacks:
                    callback()
        delay.assert_called_once_with(str(document.id), str(document.organization_id))
