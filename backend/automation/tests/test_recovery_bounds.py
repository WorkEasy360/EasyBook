"""The recovery sweepers must give up, even when the work rolls itself back.

Regression (found auditing the P0 recovery-sweeper work itself): the budget
that stops a sweeper re-enqueuing forever was `AutomationExecution.attempt_count`,
which `run_execution` increments INSIDE the task's own transaction. Any
exception escaping the task — a statement timeout, a lost connection, a
KeyError from a rule edited while its execution was pending — rolls that
increment back with everything else. The execution returns to PENDING with the
budget untouched, so the sweeper re-enqueued it every 10 minutes forever, with
nothing visible to the user. The OCR sweeper had no budget at all: a document
that could not be processed was re-enqueued indefinitely, re-fetching from
storage and re-invoking the (paid) OCR provider every time.

The budget now lives in a counter the SWEEPER increments in its own committed
transaction, so it advances no matter what the run does.
"""

import datetime
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from automation.models.execution import AutomationExecution, ExecutionStatus
from automation.services.execution import create_manual_execution
from automation.services.rules import activate_rule, create_rule
from automation.tasks import RECOVERY_MAX_ATTEMPTS, recover_stalled_executions_task
from core.tenancy import tenant_context
from core.tests.factories import make_org_with_owner
from documents.models.document import Document, OCRStatus
from documents.services.transitions import transition_ocr_status
from documents.services.uploads import upload_document
from documents.tasks import OCR_RECOVERY_MAX_ATTEMPTS, recover_stalled_ocr_task
from documents.tests.base import DocumentsTestsBase, tenant

PDF_BYTES = b"%PDF-1.4\n%mock\n"


class ExecutionRecoveryBoundTests(TestCase):
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("Org", "automation-recovery-bound@example.com")
        with tenant_context(organization_id=self.org.id):
            rule = create_rule(
                organization=self.org, name="Manual notify", trigger_type="manual", conditions=[],
                actions=[{"action_id": "send_notification", "config": {"message": "hello"}}],
                actor=self.owner,
            )
            self.rule = activate_rule(rule=rule, actor=self.owner)
            self.execution = create_manual_execution(rule=self.rule, actor=self.owner)

    def _age(self, minutes=30):
        # Inside the tenant: the sweeper leaves the database session scoped to
        # the last organization it visited (core/tenancy.py), so an unscoped
        # UPDATE here silently matches no rows under RLS.
        with tenant_context(organization_id=self.org.id):
            AutomationExecution.objects.filter(pk=self.execution.pk).update(
                updated_at=timezone.now() - datetime.timedelta(minutes=minutes)
            )

    def _sweep(self):
        with self.captureOnCommitCallbacks(execute=True):
            return recover_stalled_executions_task()

    def test_a_run_that_rolls_itself_back_still_exhausts_the_budget(self):
        """Every attempt fails in a way that undoes its own bookkeeping."""
        sweeps = []
        with patch(
            "automation.services.execution.build_facts", side_effect=RuntimeError("worker died mid-run")
        ):
            for _ in range(RECOVERY_MAX_ATTEMPTS + 1):
                self._age()
                try:
                    sweeps.append(self._sweep())
                except RuntimeError:
                    # Eager Celery surfaces the task's own failure; a real
                    # worker would log it and move on.
                    sweeps.append(None)

        with tenant_context(organization_id=self.org.id):
            self.execution.refresh_from_db()
        self.assertEqual(self.execution.status, ExecutionStatus.FAILED)
        self.assertIn("recovery", self.execution.error_summary.lower())

    def test_the_sweeper_budget_does_not_depend_on_the_run_touching_the_database(self):
        with patch("automation.tasks.run_execution_task.delay"):
            for _ in range(RECOVERY_MAX_ATTEMPTS):
                self._age()
                self._sweep()
            self._age()
            result = self._sweep()

        self.assertEqual(result["abandoned"], 1)
        with tenant_context(organization_id=self.org.id):
            self.execution.refresh_from_db()
        self.assertEqual(self.execution.status, ExecutionStatus.FAILED)


class OcrRecoveryBoundTests(DocumentsTestsBase):
    def setUp(self):
        super().setUp()
        with tenant(self.org_a):
            self.document = upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                original_filename="receipt.pdf",
            )
            transition_ocr_status(Document.objects.get(pk=self.document.pk), OCRStatus.QUEUED)

    def _age(self, minutes=30):
        # Inside the tenant: the sweeper leaves the database session scoped to
        # the last organization it visited (core/tenancy.py), so an unscoped
        # UPDATE here silently matches no rows under RLS.
        with tenant(self.org_a):
            Document.objects.filter(pk=self.document.pk).update(
                updated_at=timezone.now() - datetime.timedelta(minutes=minutes)
            )

    def _sweep(self):
        with self.captureOnCommitCallbacks(execute=False):
            # execute=False: the point is what the sweeper does, not what the
            # re-enqueued task would do.
            return recover_stalled_ocr_task()

    def test_a_document_that_never_gets_processed_is_failed_visibly_instead_of_looping(self):
        for _ in range(OCR_RECOVERY_MAX_ATTEMPTS):
            self._age()
            self.assertEqual(self._sweep()["requeued"], 1)

        self._age()
        result = self._sweep()

        self.assertEqual(result["requeued"], 0)
        self.assertEqual(result["failed"], 1)
        with tenant(self.org_a):
            self.document.refresh_from_db()
            self.assertEqual(self.document.ocr_status, OCRStatus.FAILED)
            self.assertEqual(self.document.ocr_result.error_code, "ocr_recovery_exhausted")

    def test_requesting_ocr_again_gives_a_document_a_fresh_budget(self):
        from documents.services.ocr import request_ocr

        for _ in range(OCR_RECOVERY_MAX_ATTEMPTS + 1):
            self._age()
            self._sweep()

        with tenant(self.org_a):
            self.document.refresh_from_db()
            self.assertEqual(self.document.ocr_status, OCRStatus.FAILED)
            with self.captureOnCommitCallbacks(execute=False):
                request_ocr(document=self.document, actor=self.user_a)
            self.document.refresh_from_db()
            self.assertEqual(self.document.ocr_status, OCRStatus.QUEUED)
            self.assertEqual(self.document.ocr_recovery_attempts, 0)
