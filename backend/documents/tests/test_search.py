from unittest.mock import patch

from documents.models.document import DocumentType
from documents.ocr.providers.base import ExtractionResult
from documents.search import search_documents
from documents.services.folders import create_folder
from documents.services.malware_scan import EICAR_SIGNATURE
from documents.services.ocr import request_ocr
from documents.services.tags import assign_tag, get_or_create_tag
from documents.services.uploads import upload_document
from documents.tests.base import DocumentsTestsBase, tenant

PDF_BYTES = b"%PDF-1.4\n%mock\n"


class SearchDocumentsTests(DocumentsTestsBase):
    def test_search_by_title(self):
        with tenant(self.org_a):
            upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                original_filename="a.pdf", title="Office Rent Receipt",
            )
            upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                original_filename="b.pdf", title="Electricity Bill",
            )
            results = list(search_documents(query="rent"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Office Rent Receipt")

    def test_search_by_filename(self):
        with tenant(self.org_a):
            upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                original_filename="vendor-invoice-2026.pdf",
            )
            results = list(search_documents(query="vendor-invoice"))
        self.assertEqual(len(results), 1)

    def test_search_by_ocr_text(self):
        fake_result = ExtractionResult(
            provider="fake", provider_version="1", raw_text="GSTIN 27AAAAA0000A1Z5", confidence=0.95,
        )
        with tenant(self.org_a):
            document = upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES, original_filename="a.pdf",
            )
            with patch("documents.services.ocr.get_provider") as mock_provider:
                mock_provider.return_value.extract.return_value = fake_result
                request_ocr(document=document)
            results = list(search_documents(query="GSTIN"))
        self.assertEqual(len(results), 1)

    def test_quarantined_documents_excluded(self):
        content = b"header " + EICAR_SIGNATURE
        with tenant(self.org_a):
            upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=content,
                original_filename="bad.txt", title="Quarantine Me",
            )
            results = list(search_documents(query="Quarantine"))
        self.assertEqual(results, [])

    def test_filter_by_document_type(self):
        with tenant(self.org_a):
            upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                original_filename="a.pdf", document_type=DocumentType.RECEIPT,
            )
            upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                original_filename="b.pdf", document_type=DocumentType.CONTRACT,
            )
            results = list(search_documents(document_type=DocumentType.RECEIPT))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].document_type, DocumentType.RECEIPT)

    def test_filter_by_folder(self):
        with tenant(self.org_a):
            folder = create_folder(organization=self.org_a, name="Receipts")
            in_folder = upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                original_filename="a.pdf", folder=folder,
            )
            upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES, original_filename="b.pdf",
            )
            results = list(search_documents(folder_id=folder.id))
        self.assertEqual([r.id for r in results], [in_folder.id])

    def test_filter_by_tag(self):
        with tenant(self.org_a):
            tagged = upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES, original_filename="a.pdf",
            )
            upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES, original_filename="b.pdf",
            )
            tag = get_or_create_tag(organization=self.org_a, name="urgent")
            assign_tag(document=tagged, tag=tag)
            results = list(search_documents(tag_names=["urgent"]))
        self.assertEqual([r.id for r in results], [tagged.id])

    def test_tenant_isolation(self):
        with tenant(self.org_a):
            upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                original_filename="a.pdf", title="Shared Title",
            )
        with tenant(self.org_b):
            results = list(search_documents(query="Shared"))
        self.assertEqual(results, [])
