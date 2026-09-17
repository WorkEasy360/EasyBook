"""Documents API surface: auth, tenant enforcement, RBAC, upload/download/
OCR/review/link endpoints."""

from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APIClient

from accounts.models import Membership
from authz.roles import Role
from core.tenancy import tenant_context
from core.tests.factories import make_user
from documents.models.document import UploadStatus
from documents.ocr.providers.base import ExtractionResult
from documents.services.uploads import upload_document
from documents.tests.base import DocumentsTestsBase, tenant

PDF_BYTES = b"%PDF-1.4\n%mock\n"


class DocumentsApiTestsBase(DocumentsTestsBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user_a)

    def _headers(self, organization=None):
        return {"HTTP_X_ORGANIZATION_ID": str((organization or self.org_a).id)}

    def _as_role(self, role, email):
        user = make_user(email)
        with tenant_context(user_id=user.id):
            Membership.objects.create(organization=self.org_a, user=user, role=role)
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def _upload_pdf(self, filename="receipt.pdf"):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return self.client.post(
            "/api/v1/documents/upload/",
            {"file": SimpleUploadedFile(filename, PDF_BYTES, content_type="application/pdf")},
            format="multipart", **self._headers(),
        )


class UploadEndpointTests(DocumentsApiTestsBase):
    def test_requires_authentication(self):
        anonymous = APIClient()
        response = anonymous.get("/api/v1/documents/", **self._headers())
        self.assertEqual(response.status_code, 401)

    def test_organization_header_required(self):
        response = self.client.get("/api/v1/documents/")
        self.assertEqual(response.status_code, 400)

    def test_cannot_use_organization_you_do_not_belong_to(self):
        response = self.client.get("/api/v1/documents/", **self._headers(self.org_b))
        self.assertEqual(response.status_code, 403)

    def test_upload_and_list(self):
        response = self._upload_pdf()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["upload_status"], UploadStatus.READY)
        self.assertIn("possible_duplicate_ids", response.data)
        self.assertNotIn("storage_key", response.data)

        listing = self.client.get("/api/v1/documents/", **self._headers())
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.data["count"], 1)

    def test_upload_rejects_disallowed_extension(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        response = self.client.post(
            "/api/v1/documents/upload/",
            {"file": SimpleUploadedFile("evil.exe", b"MZ" + b"0" * 10, content_type="application/octet-stream")},
            format="multipart", **self._headers(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "unsupported_file_type")

    @override_settings(DOCUMENT_MAX_UPLOAD_SIZES={"default": 10, "image": 10, "pdf": 10})
    def test_upload_rejects_oversized_file(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        response = self.client.post(
            "/api/v1/documents/upload/",
            {"file": SimpleUploadedFile("receipt.pdf", PDF_BYTES, content_type="application/pdf")},
            format="multipart", **self._headers(),
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["error"]["code"], "file_too_large")

    @override_settings(DOCUMENT_MAX_UPLOAD_SIZES={"default": 10, "image": 10, "pdf": 10})
    def test_oversized_upload_never_reaches_the_read_call(self):
        """DocumentUploadView.post checks UploadedFile.size against
        max_upload_size() BEFORE the `content=uploaded.read()` call that
        passes to upload_document() — an oversized upload must be rejected
        without ever being pulled fully into memory. Proven, not just
        asserted: upload_document is the only place .read() is invoked in
        this view, and only as an inline argument to this exact call, so if
        it's never reached, .read() provably never ran either. Patching
        UploadedFile.read directly doesn't work here — the test client's own
        multipart encoding must read the file first to build the outgoing
        request, which isn't the code path this test cares about."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        with patch("documents.api.views.upload_document") as mock_upload:
            response = self.client.post(
                "/api/v1/documents/upload/",
                {"file": SimpleUploadedFile("receipt.pdf", PDF_BYTES, content_type="application/pdf")},
                format="multipart", **self._headers(),
            )
        mock_upload.assert_not_called()
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["error"]["code"], "file_too_large")

    def test_staff_can_upload_but_viewer_cannot(self):
        staff_client = self._as_role(Role.STAFF, "staff@example.com")
        viewer_client = self._as_role(Role.VIEWER, "viewer@example.com")
        from django.core.files.uploadedfile import SimpleUploadedFile

        body = {"file": SimpleUploadedFile("r.pdf", PDF_BYTES, content_type="application/pdf")}
        self.assertEqual(
            staff_client.post("/api/v1/documents/upload/", body, format="multipart", **self._headers()).status_code,
            201,
        )
        body2 = {"file": SimpleUploadedFile("r2.pdf", PDF_BYTES, content_type="application/pdf")}
        self.assertEqual(
            viewer_client.post("/api/v1/documents/upload/", body2, format="multipart", **self._headers()).status_code,
            403,
        )

    def test_tenant_isolation_on_list(self):
        self._upload_pdf()
        response = self.client.get("/api/v1/documents/", **self._headers(self.org_b))
        self.assertEqual(response.status_code, 403)  # user_a has no membership in org_b


class DocumentDetailAndArchiveTests(DocumentsApiTestsBase):
    def test_retrieve_update_and_archive(self):
        upload = self._upload_pdf()
        doc_id = upload.data["id"]

        detail = self.client.get(f"/api/v1/documents/{doc_id}/", **self._headers())
        self.assertEqual(detail.status_code, 200)

        patched = self.client.patch(
            f"/api/v1/documents/{doc_id}/", {"title": "Renamed"}, format="json", **self._headers()
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.data["title"], "Renamed")

        archived = self.client.post(f"/api/v1/documents/{doc_id}/archive/", **self._headers())
        self.assertEqual(archived.status_code, 200)
        self.assertEqual(archived.data["upload_status"], UploadStatus.ARCHIVED)

    def test_viewer_can_retrieve_but_not_patch(self):
        upload = self._upload_pdf()
        doc_id = upload.data["id"]
        viewer_client = self._as_role(Role.VIEWER, "viewer2@example.com")
        self.assertEqual(viewer_client.get(f"/api/v1/documents/{doc_id}/", **self._headers()).status_code, 200)
        self.assertEqual(
            viewer_client.patch(
                f"/api/v1/documents/{doc_id}/", {"title": "x"}, format="json", **self._headers()
            ).status_code,
            403,
        )


class DownloadEndpointTests(DocumentsApiTestsBase):
    def test_download_returns_signed_url(self):
        upload = self._upload_pdf()
        doc_id = upload.data["id"]
        response = self.client.post(f"/api/v1/documents/{doc_id}/download/", **self._headers())
        self.assertEqual(response.status_code, 200)
        self.assertIn("url", response.data)

        follow = self.client.get(response.data["url"])
        self.assertEqual(follow.status_code, 200)

    def test_quarantined_document_cannot_be_downloaded(self):
        from documents.services.malware_scan import EICAR_SIGNATURE

        with tenant(self.org_a):
            document = upload_document(
                organization=self.org_a, uploaded_by=self.user_a,
                content=b"x " + EICAR_SIGNATURE, original_filename="bad.txt",
            )
        response = self.client.post(f"/api/v1/documents/{document.id}/download/", **self._headers())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "document_quarantined")


class OcrAndReviewEndpointTests(DocumentsApiTestsBase):
    def test_ocr_request_and_result_and_review(self):
        upload = self._upload_pdf()
        doc_id = upload.data["id"]

        fake_result = ExtractionResult(provider="fake", provider_version="1", raw_text="hi", confidence=0.95)
        with patch("documents.services.ocr.get_provider") as mock_provider:
            mock_provider.return_value.extract.return_value = fake_result
            queued = self.client.post(f"/api/v1/documents/{doc_id}/ocr/", **self._headers())
        self.assertEqual(queued.status_code, 202)

        result = self.client.get(f"/api/v1/documents/{doc_id}/ocr/", **self._headers())
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data["provider"], "fake")

        review = self.client.post(
            f"/api/v1/documents/{doc_id}/review/", {"status": "approved"}, format="json", **self._headers()
        )
        self.assertEqual(review.status_code, 200)
        self.assertEqual(review.data["status"], "approved")

    def test_staff_cannot_review(self):
        upload = self._upload_pdf()
        doc_id = upload.data["id"]
        with patch("documents.services.ocr.get_provider") as mock_provider:
            mock_provider.return_value.extract.return_value = ExtractionResult(
                provider="fake", provider_version="1", raw_text="hi", confidence=0.2
            )
            self.client.post(f"/api/v1/documents/{doc_id}/ocr/", **self._headers())

        staff_client = self._as_role(Role.STAFF, "staff-review@example.com")
        response = staff_client.post(
            f"/api/v1/documents/{doc_id}/review/", {"status": "approved"}, format="json", **self._headers()
        )
        self.assertEqual(response.status_code, 403)


class LinkEndpointTests(DocumentsApiTestsBase):
    def test_create_and_delete_link(self):
        from purchases.services.vendors import create_vendor

        upload = self._upload_pdf()
        doc_id = upload.data["id"]
        with tenant(self.org_a):
            from core.tests.factories import make_currency

            vendor = create_vendor(
                organization=self.org_a, vendor_code="VEN-API", display_name="API Vendor",
                currency=make_currency("INR"),
            )

        create_response = self.client.post(
            f"/api/v1/documents/{doc_id}/links/",
            {"entity_type": "vendor", "entity_id": str(vendor.id)}, format="json", **self._headers(),
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        link_id = create_response.data["id"]

        listing = self.client.get(f"/api/v1/documents/{doc_id}/links/", **self._headers())
        self.assertEqual(len(listing.data), 1)

        delete_response = self.client.delete(f"/api/v1/documents/{doc_id}/links/{link_id}/", **self._headers())
        self.assertEqual(delete_response.status_code, 204)


class SearchEndpointTests(DocumentsApiTestsBase):
    def test_search_by_query_param(self):
        self._upload_pdf(filename="special-report.pdf")
        response = self.client.get("/api/v1/documents/search/?q=special-report", **self._headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
