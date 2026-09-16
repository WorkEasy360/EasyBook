from unittest.mock import patch

from audit.models import AuditLog
from core.exceptions import ApplicationError
from documents.models.document import Document, UploadStatus
from documents.services.malware_scan import EICAR_SIGNATURE
from documents.services.uploads import archive_document, upload_document
from documents.tests.base import DocumentsTestsBase, tenant

PDF_BYTES = b"%PDF-1.4\n%mock pdf content\n"


class UploadDocumentTests(DocumentsTestsBase):
    def test_valid_upload_becomes_ready(self):
        with tenant(self.org_a):
            document = upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                original_filename="receipt.pdf", title="Office rent",
            )
        self.assertEqual(document.upload_status, UploadStatus.READY)
        self.assertEqual(document.checksum_sha256, __import__("hashlib").sha256(PDF_BYTES).hexdigest())
        self.assertTrue(document.storage_key)

    def test_upload_creates_audit_entry(self):
        with tenant(self.org_a):
            document = upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                original_filename="receipt.pdf",
            )
            logs = AuditLog.objects.filter(object_type="documents.Document", object_id=str(document.id))
        self.assertTrue(logs.exists())

    def test_infected_upload_is_quarantined(self):
        content = b"header " + EICAR_SIGNATURE + b" footer"
        with tenant(self.org_a):
            document = upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=content,
                original_filename="bad.txt",
            )
        self.assertEqual(document.upload_status, UploadStatus.QUARANTINED)

    def test_invalid_upload_raises_and_creates_no_document(self):
        with tenant(self.org_a):
            before = Document.objects.count()
            with self.assertRaises(ApplicationError):
                upload_document(
                    organization=self.org_a, uploaded_by=self.user_a, content=b"MZ" + b"0" * 10,
                    original_filename="evil.exe",
                )
            self.assertEqual(Document.objects.count(), before)

    def test_storage_failure_leaves_document_in_failed_state(self):
        with tenant(self.org_a):
            with patch("documents.services.uploads.get_storage") as mock_get_storage:
                mock_get_storage.return_value.put.side_effect = OSError("disk full")
                with self.assertRaises(ApplicationError) as ctx:
                    upload_document(
                        organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                        original_filename="receipt.pdf",
                    )
            self.assertEqual(ctx.exception.get_codes(), "storage_write_failed")
            document = Document.objects.get(original_filename="receipt.pdf")
            self.assertEqual(document.upload_status, UploadStatus.FAILED)

    def test_archive_ready_document(self):
        with tenant(self.org_a):
            document = upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                original_filename="receipt.pdf",
            )
            archived = archive_document(document=document, actor=self.user_a)
        self.assertEqual(archived.upload_status, UploadStatus.ARCHIVED)
        self.assertIsNotNone(archived.archived_at)

    def test_cannot_archive_twice(self):
        with tenant(self.org_a):
            document = upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                original_filename="receipt.pdf",
            )
            archive_document(document=document, actor=self.user_a)
            with self.assertRaises(ApplicationError):
                archive_document(document=document, actor=self.user_a)

    def test_tenant_isolation_document_created_in_org_a_not_visible_in_org_b(self):
        with tenant(self.org_a):
            upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                original_filename="receipt.pdf",
            )
        with tenant(self.org_b):
            self.assertEqual(Document.objects.count(), 0)
