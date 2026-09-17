import threading

from django.db import connections

from audit.models import AuditLog
from core.exceptions import ApplicationError
from core.tests.factories import make_user
from documents.models.document import Document, OCRStatus
from documents.models.review import DocumentReview, ReviewStatus
from documents.services.ocr import request_ocr
from documents.services.review import approve_review, reject_review
from documents.services.uploads import upload_document
from documents.tests.base import DocumentsTestsBase, DocumentsTransactionTestsBase, tenant

PDF_BYTES = b"%PDF-1.4\n%mock\n"


class ReviewWorkflowTests(DocumentsTestsBase):
    def setUp(self):
        super().setUp()
        with tenant(self.org_a):
            self.document = upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                original_filename="receipt.pdf",
            )
            with self.captureOnCommitCallbacks(execute=True):
                request_ocr(document=self.document)
            self.document.refresh_from_db()

    def test_cannot_review_before_ocr_requested(self):
        with tenant(self.org_a):
            fresh = upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                original_filename="other.pdf",
            )
            with self.assertRaises(ApplicationError) as ctx:
                approve_review(document=fresh, reviewer=self.user_a)
        self.assertEqual(ctx.exception.get_codes(), "nothing_to_review")

    def test_approve_sets_reviewer_and_completes_ocr(self):
        with tenant(self.org_a):
            review = approve_review(
                document=self.document, reviewer=self.user_a, corrected_fields={"total": "100.00"},
            )
            self.document.refresh_from_db()
        self.assertEqual(review.status, ReviewStatus.APPROVED)
        self.assertEqual(review.reviewer_id, self.user_a.id)
        self.assertIsNotNone(review.reviewed_at)
        self.assertEqual(review.corrected_fields, {"total": "100.00"})
        self.assertEqual(self.document.ocr_status, OCRStatus.COMPLETED)

    def test_reject_records_notes_without_completing_ocr(self):
        with tenant(self.org_a):
            review = reject_review(document=self.document, reviewer=self.user_a, notes="blurry scan")
            self.document.refresh_from_db()
        self.assertEqual(review.status, ReviewStatus.REJECTED)
        self.assertEqual(self.document.ocr_status, OCRStatus.NEEDS_REVIEW)

    def test_approve_is_idempotent(self):
        with tenant(self.org_a):
            approve_review(document=self.document, reviewer=self.user_a)
            second = approve_review(document=self.document, reviewer=self.user_a)
            count = DocumentReview.objects.filter(document=self.document).count()
        self.assertEqual(second.status, ReviewStatus.APPROVED)
        self.assertEqual(count, 1)

    def test_reject_after_approve_raises(self):
        with tenant(self.org_a):
            approve_review(document=self.document, reviewer=self.user_a)
            with self.assertRaises(ApplicationError) as ctx:
                reject_review(document=self.document, reviewer=self.user_a)
        self.assertEqual(ctx.exception.get_codes(), "review_already_finalized")

    def test_review_is_audited(self):
        with tenant(self.org_a):
            approve_review(document=self.document, reviewer=self.user_a)
            logs = AuditLog.objects.filter(object_type="documents.DocumentReview")
        self.assertTrue(logs.exists())


class ReviewConcurrencyTests(DocumentsTransactionTestsBase):
    def test_two_reviewers_approving_concurrently_yields_one_authoritative_approval(self):
        with tenant(self.org_a):
            document = upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                original_filename="race.pdf",
            )
            request_ocr(document=document)
            reviewer_a = self.user_a
            reviewer_b = make_user(email="reviewer-b@example.com")
        document_id = document.id

        errors = []

        def approve_as(user):
            try:
                with tenant(self.org_a):
                    doc = Document.objects.get(pk=document_id)
                    approve_review(document=doc, reviewer=user)
            except Exception as exc:
                errors.append(exc)
            finally:
                connections.close_all()

        threads = [
            threading.Thread(target=approve_as, args=(reviewer_a,)),
            threading.Thread(target=approve_as, args=(reviewer_b,)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        with tenant(self.org_a):
            reviews = DocumentReview.objects.filter(document_id=document_id)
            self.assertEqual(reviews.count(), 1)
            self.assertEqual(reviews.first().status, ReviewStatus.APPROVED)
