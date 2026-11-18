"""Cross-tenant rejection across every sensitive documents operation (phase
section 40) — each service re-resolves the row through the ambient tenant
context rather than trusting the passed-in instance, so calling it under the
WRONG organization's context fails closed with DoesNotExist."""

import time
from unittest import mock

from django.test import override_settings

from documents.models.document import Document
from documents.services.downloads import get_download_url, signed_url_ttl_seconds
from documents.services.ocr import request_ocr
from documents.services.review import approve_review
from documents.services.uploads import upload_document
from documents.tests.base import DocumentsTestsBase, tenant

PDF_BYTES = b"%PDF-1.4\n%mock\n"


class SignedUrlLifetimeTests(DocumentsTestsBase):
    """A document link is a bearer credential: its lifetime has a ceiling however it is configured."""

    def setUp(self):
        super().setUp()
        with tenant(self.org_a):
            self.document = upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES, original_filename="a.pdf"
            )

    def test_the_default_is_five_minutes(self):
        self.assertEqual(signed_url_ttl_seconds(), 300)

    @override_settings(DOCUMENT_SIGNED_URL_TTL_SECONDS=7 * 24 * 3600)
    def test_a_longer_setting_is_clamped_when_a_link_is_issued(self):
        self.assertEqual(signed_url_ttl_seconds(), 900)
        with tenant(self.org_a):
            self.assertEqual(get_download_url(document=self.document)["expires_in"], 900)

    @override_settings(DOCUMENT_SIGNED_URL_TTL_SECONDS=7 * 24 * 3600)
    def test_a_longer_setting_is_clamped_when_a_local_link_is_verified(self):
        with tenant(self.org_a):
            url = get_download_url(document=self.document)["url"]
        self.assertEqual(self.client.get(url).status_code, 200)
        with mock.patch("time.time", return_value=time.time() + 901):
            self.assertEqual(self.client.get(url).status_code, 404)  # older than the ceiling, whatever the setting says

    @override_settings(DOCUMENT_SIGNED_URL_TTL_SECONDS=120)
    def test_a_shorter_setting_is_kept(self):
        self.assertEqual(signed_url_ttl_seconds(), 120)

    @override_settings(DOCUMENT_SIGNED_URL_TTL_SECONDS=0)
    def test_a_nonsense_setting_still_gives_a_link_that_can_be_used(self):
        self.assertEqual(signed_url_ttl_seconds(), 1)


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
