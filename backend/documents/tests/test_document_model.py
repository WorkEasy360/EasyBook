from django.db import IntegrityError
from django.test import override_settings

from documents.models import Document, DocumentType, StorageBackend, UploadStatus
from documents.storage import get_storage
from documents.tests.base import DocumentsTestsBase, tenant


class DocumentModelTests(DocumentsTestsBase):
    def test_create_document_defaults(self):
        with tenant(self.org_a):
            doc = Document.objects.create(
                organization=self.org_a,
                title="Office rent receipt",
                original_filename="receipt.pdf",
                storage_key="org/receipt-1.pdf",
                storage_backend=StorageBackend.LOCAL,
                mime_type="application/pdf",
                file_size=1024,
                checksum_sha256="a" * 64,
            )
        self.assertEqual(doc.document_type, DocumentType.GENERAL)
        self.assertEqual(doc.upload_status, UploadStatus.UPLOADING)

    def test_storage_key_unique(self):
        with tenant(self.org_a):
            Document.objects.create(
                organization=self.org_a, title="A", original_filename="a.pdf",
                storage_key="dup-key", storage_backend=StorageBackend.LOCAL,
                mime_type="application/pdf", file_size=1, checksum_sha256="a" * 64,
            )
            with self.assertRaises(IntegrityError):
                Document.objects.create(
                    organization=self.org_a, title="B", original_filename="b.pdf",
                    storage_key="dup-key", storage_backend=StorageBackend.LOCAL,
                    mime_type="application/pdf", file_size=1, checksum_sha256="b" * 64,
                )


class LocalStorageTests(DocumentsTestsBase):
    @override_settings(DOCUMENT_STORAGE_BACKEND="local")
    def test_put_get_signed_url_delete_roundtrip(self):
        storage = get_storage()
        storage.put(key="tests/hello.txt", content=b"hello world", content_type="text/plain")
        self.assertTrue(storage.exists(key="tests/hello.txt"))

        url = storage.get_signed_url(key="tests/hello.txt", expires_in=60)
        self.assertIn("/api/v1/documents/local-storage/", url)
        self.assertEqual(storage.get_bytes(key="tests/hello.txt"), b"hello world")

        storage.delete(key="tests/hello.txt")
        self.assertFalse(storage.exists(key="tests/hello.txt"))

    @override_settings(DOCUMENT_STORAGE_BACKEND="local")
    def test_signed_url_raises_for_missing_key(self):
        storage = get_storage()
        with self.assertRaises(FileNotFoundError):
            storage.get_signed_url(key="tests/does-not-exist.txt", expires_in=60)

    def test_unknown_backend_raises(self):
        with override_settings(DOCUMENT_STORAGE_BACKEND="carrier-pigeon"):
            with self.assertRaises(ValueError):
                get_storage()

    @override_settings(DOCUMENT_STORAGE_BACKEND="s3", DOCUMENT_STORAGE_S3_BUCKET="bucket")
    def test_s3_backend_selected_by_setting(self):
        from unittest.mock import patch

        from documents.storage.s3 import S3DocumentStorage

        with patch("boto3.client"):
            storage = get_storage()
        self.assertIsInstance(storage, S3DocumentStorage)
