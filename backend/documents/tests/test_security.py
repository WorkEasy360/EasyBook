"""Cross-tenant rejection across every sensitive documents operation (phase
section 40) — each service re-resolves the row through the ambient tenant
context rather than trusting the passed-in instance, so calling it under the
WRONG organization's context fails closed with DoesNotExist."""

from documents.models.document import Document
from documents.services.downloads import get_download_url
from documents.services.ocr import request_ocr
from documents.services.review import approve_review
from documents.services.uploads import upload_document
from documents.tests.base import DocumentsTestsBase, tenant

PDF_BYTES = b"%PDF-1.4\n%mock\n"


class CrossTenantRejectionTests(DocumentsTestsBase):
    def setUp(self):
        super().setUp()
        with tenant(self.org_a):
            self.document = upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                original_filename="secret.pdf",
            )

    def test_org_b_cannot_download_org_a_document(self):
        with tenant(self.org_b):
            with self.assertRaises(Document.DoesNotExist):
                get_download_url(document=self.document)

    def test_org_b_cannot_request_ocr_on_org_a_document(self):
        with tenant(self.org_b):
            with self.assertRaises(Document.DoesNotExist):
                request_ocr(document=self.document)

    def test_org_b_cannot_review_org_a_document(self):
        with tenant(self.org_b):
            with self.assertRaises(Document.DoesNotExist):
                approve_review(document=self.document, reviewer=self.user_b)

    def test_org_b_cannot_list_org_a_document(self):
        with tenant(self.org_b):
            self.assertEqual(Document.objects.filter(pk=self.document.pk).count(), 0)

    def test_missing_tenant_context_fails_closed(self):
        # No tenant() context manager at all.
        self.assertEqual(Document.objects.filter(pk=self.document.pk).count(), 0)
