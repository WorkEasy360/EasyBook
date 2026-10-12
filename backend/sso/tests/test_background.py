"""Background work honours the organization's Workeasy360 entitlement (design section 5, class A): before a
job produces a customer business effect it asks ``core.access.background_allowed``. Denied: the effect is
skipped, ``workeasy360.background_skipped`` is logged, nothing is raised, and the schedule is left as it was
so the work resumes - once - after a renewal. Outage: the last answer stands, none means run. Organizations
not linked to Workeasy360 are never asked.

Each gated task is proven against the real task with a linked organization and the fake provider."""

import datetime
import os
import subprocess
import sys
from contextlib import nullcontext
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.utils import timezone

from ai.models import DocumentIndex, IndexStatus
from ai.rag.indexing import SKIPPED_FOR_ACCESS
from ai.rag.tasks import index_document_task, resume_skipped_indexing_task
from ai.tests.base import AITestsBase, upload_text
from ai.tests.base import tenant as ai_tenant
from automation.models.execution import AutomationExecution, ExecutionStatus
from automation.models.notification import AutomationNotification
from automation.models.schedule import AutomationSchedule
from automation.services.execution import create_manual_execution
from automation.services.rules import activate_rule, create_rule
from automation.tasks import (
    dispatch_automation_events_task,
    recover_stalled_executions_task,
    run_due_scans_task,
    run_due_schedules_task,
    run_execution_task,
)
from automation.tests.test_tasks import AutomationTasksTestsBase
from core import access
from core.tenancy import tenant_context
from core.tests.factories import make_org_with_owner
from documents.models.document import Document, OCRStatus
from documents.services.transitions import transition_ocr_status
from documents.services.uploads import upload_document
from documents.tasks import recover_stalled_ocr_task, run_ocr_task
from documents.tests.base import DocumentsTestsBase
from documents.tests.base import tenant as documents_tenant
from purchases.models.bill import Bill
from purchases.models.expense import Expense
from purchases.models.recurring import RecurringBillTemplate, RecurringExpenseTemplate
from purchases.services.recurring import (
    create_recurring_bill_template,
    create_recurring_expense_template,
    generate_due_bills,
    generate_due_expenses,
)
from purchases.tests.base import ORDER_DATE, PurchasesTransactionTestsBase
from sales.models.invoice import Invoice
from sales.models.recurring_invoice import RecurringFrequency, RecurringInvoiceTemplate
from sales.services.recurring_invoices import generate_due_invoices
from sales.tests.test_recurring_invoices import RecurringInvoiceTestsBase
from sso import entitlement
from sso.tests.linked import LinkedToWorkeasy360
from sso.tests.test_entitlement import DeadCache
from sso.tests.test_sign_in import W360_ORG

LOGGER = "sso.entitlement"
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
PDF_BYTES = b"%PDF-1.4\n%mock\n"


class BackgroundGateTests(LinkedToWorkeasy360, TestCase):
    def setUp(self):
        super().setUp()
        self.organization, _user, _membership = make_org_with_owner("Gated", "gated@example.com")
        self.link(self.organization)

    def allowed(self, task="test.task"):
        return access.background_allowed(self.organization, task=task)

    def test_an_entitled_organization_runs(self):
        self.assertTrue(self.allowed())

    def test_an_unlinked_organization_is_never_asked(self):
        organization, _user, _membership = make_org_with_owner("Plain", "plain@example.com")
        self.deny()
        self.assertTrue(access.background_allowed(organization, task="test.task"))
        self.assertEqual(self.asked(), 0)

    def test_a_denial_skips_logs_and_does_not_raise(self):
        self.deny("expired")
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            self.assertFalse(self.allowed("sales.generate_recurring_invoices"))
        skipped = [record for record in logs.records if record.getMessage() == "workeasy360.background_skipped"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0].organization_id, str(self.organization.pk))
        self.assertEqual(skipped[0].task, "sales.generate_recurring_invoices")
        self.assertEqual(skipped[0].reason, "expired")

    def test_a_read_only_subscription_does_not_run_jobs(self):
        self.provider.entitlement = {"decision": "allow", "reason": "entitled", "write_allowed": False}
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            self.assertFalse(self.allowed())
        self.assertEqual(logs.records[0].reason, "read_only")

    def test_a_denial_is_trusted_for_fifteen_minutes_not_one(self):
        self.deny()
        self.assertFalse(self.allowed())
        self.assertEqual(self.asked(), 1)
        self.age_organization_answer(entitlement.DENIAL_RECHECK_SECONDS + 60)  # past a person's window
        self.allow()
        self.assertFalse(self.allowed())
        self.assertEqual(self.asked(), 1)  # not asked again: a background denial lasts 900 s
        # ...while a person's request, with the same answer, asks again after a minute.
        self.assertIsNotNone(entitlement.access_for(self.organization))
        self.assertEqual(self.asked(), 2)

    def test_a_renewal_is_seen_after_the_background_window(self):
        self.deny()
        self.assertFalse(self.allowed())
        self.allow()
        self.age_organization_answer(entitlement.BACKGROUND_DENIAL_SECONDS + 1)
        self.assertTrue(self.allowed())

    def test_an_allow_is_trusted_for_its_own_window(self):
        self.assertTrue(self.allowed())
        self.assertTrue(self.allowed())
        self.assertEqual(self.asked(), 1)

    def test_an_outage_keeps_the_last_answer(self):
        self.deny()
        self.assertFalse(self.allowed())
        self.age_organization_answer(entitlement.BACKGROUND_DENIAL_SECONDS + 1)
        self.provider.entitlement_status = 503
        self.assertFalse(self.allowed())  # an outage never turns a denial into permission

    def test_an_outage_never_stops_an_allowed_organization(self):
        self.assertTrue(self.allowed())
        self.age_organization_answer(901)
        self.provider.entitlement_status = 503
        self.assertTrue(self.allowed())

    def test_an_outage_with_no_answer_yet_runs(self):
        self.provider.entitlement_status = 503
        self.assertTrue(self.allowed())

    def test_a_refusal_of_the_client_skips_the_work(self):
        self.provider.entitlement_status = 401
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            self.assertFalse(self.allowed())
        skipped = [record for record in logs.records if record.getMessage() == "workeasy360.background_skipped"]
        self.assertEqual(skipped[0].reason, entitlement.REFUSED)

    def test_an_unexpected_failure_is_an_outage_not_an_error(self):
        with mock.patch.object(entitlement.get_client(), "verify_entitlement", side_effect=ValueError("bad key")):
            self.assertTrue(self.allowed())

    def test_a_refusal_of_the_client_is_re_asked_every_minute_even_for_background_work(self):
        self.provider.entitlement_status = 401
        with self.assertLogs(LOGGER, level="WARNING"):
            self.assertFalse(self.allowed())
        self.provider.entitlement_status = 200
        self.age_organization_answer(entitlement.DENIAL_RECHECK_SECONDS + 1)
        self.age_client_refusal(entitlement.DENIAL_RECHECK_SECONDS + 1)
        self.assertTrue(self.allowed())  # fixed, and seen within a minute - not after 900 s

    def test_a_refusal_is_not_trusted_for_the_background_window(self):
        self.provider.entitlement_status = 401
        with self.assertLogs(LOGGER, level="WARNING"):
            self.assertFalse(self.allowed())
        self.assertEqual(
            entitlement._trusted_for(cache.get(entitlement._key(W360_ORG)), entitlement.BACKGROUND_DENIAL_SECONDS),
            entitlement.DENIAL_RECHECK_SECONDS,
        )

    def test_a_refusal_closes_background_work_for_every_organization_without_asking(self):
        other, _user, _membership = make_org_with_owner("Second", "second@example.com")
        self.link(other, "11111111-2222-4333-8444-555555555555")
        self.provider.entitlement_status = 401
        with self.assertLogs(LOGGER, level="WARNING"):
            self.assertFalse(self.allowed())
        asked = self.asked()
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            self.assertFalse(access.background_allowed(other, task="test.task"))
        self.assertEqual(self.asked(), asked)  # not asked: the whole client is refused
        self.assertEqual(logs.records[0].reason, entitlement.REFUSED)

    def test_a_redis_outage_never_raises_and_the_last_answer_stands(self):
        self.deny()
        self.assertFalse(self.allowed())
        self.allow()
        with mock.patch.object(entitlement, "cache", DeadCache()):
            with self.assertLogs(LOGGER, level="WARNING"):
                self.assertFalse(self.allowed())  # the per-process copy: still denied

    def test_a_redis_outage_with_nothing_known_asks_the_provider(self):
        with mock.patch.object(entitlement, "cache", DeadCache()):
            self.assertTrue(self.allowed())
            self.assertEqual(self.asked(), 1)
            entitlement.clear_local()
            self.deny()
            with self.assertLogs(LOGGER, level="WARNING"):
                self.assertFalse(self.allowed())

    def test_the_gate_itself_never_raises(self):
        with mock.patch.object(entitlement, "_ask", side_effect=RuntimeError("unexpected")):
            self.assertTrue(self.allowed())

    def test_a_task_given_only_an_id_is_checked_too(self):
        self.deny()
        self.assertFalse(access.background_allowed_for_id(self.organization.id, task="test.task"))
        self.assertTrue(access.background_allowed_for_id("00000000-0000-0000-0000-000000000000", task="t"))

    def test_the_question_is_the_organizations_not_a_persons(self):
        self.allowed()
        self.assertNotIn("subject", self.provider.entitlement_forms[0])


# ------------------------------------------------------------------ recurring invoices, bills, expenses


class RecurringInvoicesBackgroundTests(LinkedToWorkeasy360, RecurringInvoiceTestsBase, TransactionTestCase):
    AS_OF = datetime.date(2026, 6, 15)

    def setUp(self):
        super().setUp()
        self.link(self.org_a)

    def test_a_denied_organization_generates_nothing_and_keeps_its_schedule(self):
        with tenant_context(organization_id=self.org_a.id):
            template = self._make_template(start_date=datetime.date(2026, 4, 1), frequency=RecurringFrequency.MONTHLY)
        self.deny()
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            self.assertEqual(generate_due_invoices(as_of=self.AS_OF), [])
        self.assertEqual(logs.records[0].task, "sales.generate_recurring_invoices")
        with tenant_context(organization_id=self.org_a.id):
            self.assertEqual(Invoice.objects.count(), 0)
            self.assertEqual(RecurringInvoiceTemplate.objects.get(pk=template.pk).next_run_at, datetime.date(2026, 4, 1))

    def test_after_a_renewal_each_missed_occurrence_is_generated_exactly_once(self):
        with tenant_context(organization_id=self.org_a.id):
            self._make_template(start_date=datetime.date(2026, 4, 1), frequency=RecurringFrequency.MONTHLY)
        self.deny()
        self.assertEqual(generate_due_invoices(as_of=self.AS_OF), [])
        self.assertEqual(generate_due_invoices(as_of=self.AS_OF), [])  # still denied: still nothing
        self.allow()
        self.age_organization_answer(entitlement.BACKGROUND_DENIAL_SECONDS + 1)
        generated = generate_due_invoices(as_of=self.AS_OF)
        self.assertEqual(len(generated), 3)  # April, May and June: none lost
        self.assertEqual(generate_due_invoices(as_of=self.AS_OF), [])  # and none twice
        with tenant_context(organization_id=self.org_a.id):
            self.assertEqual(Invoice.objects.count(), 3)

    def test_an_organization_with_nothing_due_is_never_asked(self):
        with tenant_context(organization_id=self.org_a.id):
            self._make_template(start_date=datetime.date(2026, 9, 1))  # next run after AS_OF
            self._make_template(start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 1, 31))
            RecurringInvoiceTemplate.objects.filter(end_date__isnull=False).update(next_run_at=datetime.date(2026, 3, 1))
        self.deny()
        self.assertEqual(generate_due_invoices(as_of=self.AS_OF), [])
        self.assertEqual(self.asked(), 0)  # a finished template is not "due work" either

    def test_an_unlinked_organization_keeps_generating_while_another_is_denied(self):
        with tenant_context(organization_id=self.org_b.id):
            from accounting.models.account import AccountType
            from accounting.services.accounts import create_account
            from items.models.item import ItemType
            from items.services.items import create_item
            from items.services.units import create_unit
            from sales.services.recurring_invoices import create_recurring_template

            ar = create_account(organization=self.org_b, code="1100", name="AR", account_type=AccountType.ASSET)
            income = create_account(organization=self.org_b, code="4000", name="Sales", account_type=AccountType.INCOME)
            unit = create_unit(organization=self.org_b, code="EA", name="Each")
            item = create_item(
                organization=self.org_b, item_type=ItemType.SERVICE, name="Retainer", unit=unit, sales_account=income
            )
            from accounts.models import FiscalYear

            FiscalYear.objects.create(
                organization=self.org_b, start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 12, 31)
            )
            create_recurring_template(
                organization=self.org_b, customer=self.customer_b, frequency=RecurringFrequency.MONTHLY,
                start_date=datetime.date(2026, 6, 1), receivable_account=ar,
                lines=[{"item": item, "quantity": Decimal("1"), "unit_price": Decimal("10.00")}],
            )
        with tenant_context(organization_id=self.org_a.id):
            self._make_template(start_date=datetime.date(2026, 6, 1))
        self.deny()
        generated = generate_due_invoices(as_of=self.AS_OF)
        self.assertEqual([invoice.organization_id for invoice in generated], [self.org_b.id])


class RecurringBillsAndExpensesBackgroundTests(LinkedToWorkeasy360, PurchasesTransactionTestsBase):
    AS_OF = ORDER_DATE + datetime.timedelta(days=70)

    def setUp(self):
        super().setUp()
        self.link(self.org_a)
        with tenant_context(organization_id=self.org_a.id):
            self.bill_template = create_recurring_bill_template(
                organization=self.org_a, vendor=self.vendor, frequency="monthly", start_date=ORDER_DATE,
                payable_account=self.ap_account,
                lines=self._service_lines(quantity=Decimal("1"), unit_price=Decimal("100.00")),
            )
            self.expense_template = create_recurring_expense_template(
                organization=self.org_a, frequency="monthly", start_date=ORDER_DATE, amount=Decimal("500.00"),
                expense_account=self.office_expense_account, paid_through_account=self.bank_account,
                currency=self.currency, description="Office rent",
            )

    def test_nothing_due_means_nobody_is_asked(self):
        self.deny()
        self.assertEqual(generate_due_bills(as_of=ORDER_DATE - datetime.timedelta(days=1)), [])
        self.assertEqual(generate_due_expenses(as_of=ORDER_DATE - datetime.timedelta(days=1)), [])
        self.assertEqual(self.asked(), 0)

    def test_denied_bills_are_not_generated_and_the_schedule_is_kept(self):
        self.deny()
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            self.assertEqual(generate_due_bills(as_of=self.AS_OF), [])
        self.assertEqual(logs.records[0].task, "purchases.generate_recurring_bills")
        with tenant_context(organization_id=self.org_a.id):
            self.assertEqual(Bill.objects.count(), 0)
            self.assertEqual(RecurringBillTemplate.objects.get(pk=self.bill_template.pk).next_run_at, ORDER_DATE)

    def test_after_a_renewal_missed_bills_are_generated_once(self):
        self.deny()
        self.assertEqual(generate_due_bills(as_of=self.AS_OF), [])
        self.allow()
        self.age_organization_answer(entitlement.BACKGROUND_DENIAL_SECONDS + 1)
        self.assertEqual(len(generate_due_bills(as_of=self.AS_OF)), 3)
        self.assertEqual(generate_due_bills(as_of=self.AS_OF), [])

    def test_denied_expenses_are_not_generated_and_the_schedule_is_kept(self):
        self.deny()
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            self.assertEqual(generate_due_expenses(as_of=self.AS_OF), [])
        self.assertEqual(logs.records[0].task, "purchases.generate_recurring_expenses")
        with tenant_context(organization_id=self.org_a.id):
            self.assertEqual(Expense.objects.count(), 0)
            self.assertEqual(
                RecurringExpenseTemplate.objects.get(pk=self.expense_template.pk).next_run_at, ORDER_DATE
            )

    def test_after_a_renewal_missed_expenses_are_generated_once(self):
        self.deny()
        self.assertEqual(generate_due_expenses(as_of=self.AS_OF), [])
        self.allow()
        self.age_organization_answer(entitlement.BACKGROUND_DENIAL_SECONDS + 1)
        self.assertEqual(len(generate_due_expenses(as_of=self.AS_OF)), 3)
        self.assertEqual(generate_due_expenses(as_of=self.AS_OF), [])


# ------------------------------------------------------------------ automation


class AutomationBackgroundTests(LinkedToWorkeasy360, AutomationTasksTestsBase, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.link(self.org)

    def _rule(self, trigger_type, message="x"):
        with tenant_context(organization_id=self.org.id):
            rule = create_rule(
                organization=self.org, name=f"Rule {trigger_type}", trigger_type=trigger_type, conditions=[],
                actions=[{"action_id": "send_notification", "config": {"message": message}}], actor=self.owner,
            )
            return activate_rule(rule=rule, actor=self.owner)

    def _notifications(self):
        with tenant_context(organization_id=self.org.id):
            return AutomationNotification.objects.count()

    def _renew(self):
        self.allow()
        self.age_organization_answer(entitlement.BACKGROUND_DENIAL_SECONDS + 1)

    def test_events_stay_undispatched_while_denied_and_are_dispatched_after_a_renewal(self):
        from automation.models.event import AutomationEvent
        from sales.services.invoices import create_invoice, post_invoice

        self._rule("invoice.posted")
        with tenant_context(organization_id=self.org.id):
            invoice = create_invoice(
                organization=self.org, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("500.00")}],
            )
            post_invoice(invoice_id=invoice.id, organization=self.org, actor=self.owner)
        self.deny()
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            self.assertEqual(dispatch_automation_events_task(), {"dispatched": 0})
        self.assertEqual(logs.records[0].task, "automation.dispatch_events")
        with tenant_context(organization_id=self.org.id):
            self.assertIsNone(AutomationEvent.objects.get(entity_id=str(invoice.id)).dispatched_at)
            self.assertEqual(AutomationExecution.objects.count(), 0)
        self._renew()
        self.assertEqual(dispatch_automation_events_task(), {"dispatched": 1})
        self.assertEqual(dispatch_automation_events_task(), {"dispatched": 0})
        self.assertEqual(self._notifications(), 1)

    def test_a_due_schedule_does_not_fire_while_denied_and_fires_once_after_a_renewal(self):
        self._rule("schedule.daily")
        with tenant_context(organization_id=self.org.id):
            before = AutomationSchedule.objects.get().next_run_at
        self.deny()
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            self.assertEqual(run_due_schedules_task(), {"fired": 0})
        self.assertEqual(logs.records[0].task, "automation.run_due_schedules")
        with tenant_context(organization_id=self.org.id):
            self.assertEqual(AutomationSchedule.objects.get().next_run_at, before)  # not advanced, not lost
            self.assertEqual(AutomationExecution.objects.count(), 0)
        self._renew()
        self.assertEqual(run_due_schedules_task(), {"fired": 1})
        self.assertEqual(run_due_schedules_task(), {"fired": 0})  # once, not once per skipped run
        self.assertEqual(self._notifications(), 1)

    def test_a_scan_does_not_fire_while_denied_and_fires_after_a_renewal(self):
        self._rule("invoice.overdue")
        with tenant_context(organization_id=self.org.id):
            self._create_overdue_invoice()
        self.deny()
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            self.assertEqual(run_due_scans_task(), {"fired": 0})
        self.assertEqual(logs.records[0].task, "automation.run_due_scans")
        self.assertEqual(self._notifications(), 0)
        self._renew()
        self.assertEqual(run_due_scans_task(), {"fired": 1})
        self.assertEqual(self._notifications(), 1)

    def test_a_queued_execution_is_not_run_while_denied_and_the_sweeper_resumes_it_after_a_renewal(self):
        rule = self._rule("manual")
        with tenant_context(organization_id=self.org.id):
            execution = create_manual_execution(rule=rule, actor=self.owner)
        self.deny()
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            self.assertEqual(
                run_execution_task(str(execution.id), str(self.org.id)), {"skipped": "workeasy360_access"}
            )
        self.assertEqual(logs.records[0].task, "automation.run_execution")
        self.assertEqual(logs.records[0].organization_id, str(self.org.id))
        with tenant_context(organization_id=self.org.id):
            execution.refresh_from_db()
            self.assertEqual(execution.status, ExecutionStatus.PENDING)
        self.assertEqual(self._notifications(), 0)

        # The sweeper leaves it alone (and spends none of its recovery budget) while denied...
        with tenant_context(organization_id=self.org.id):
            AutomationExecution.objects.filter(pk=execution.pk).update(
                updated_at=timezone.now() - datetime.timedelta(minutes=30)
            )
        with nullcontext():  # TransactionTestCase: on_commit runs for real
            self.assertEqual(recover_stalled_executions_task(), {"recovered": 0, "abandoned": 0})
        with tenant_context(organization_id=self.org.id):
            execution.refresh_from_db()
            self.assertEqual(execution.recovery_attempts, 0)
            self.assertEqual(execution.status, ExecutionStatus.PENDING)

        # ...and runs it once access is back.
        self._renew()
        with nullcontext():  # TransactionTestCase: on_commit runs for real
            self.assertEqual(recover_stalled_executions_task(), {"recovered": 1, "abandoned": 0})
        with tenant_context(organization_id=self.org.id):
            execution.refresh_from_db()
            self.assertEqual(execution.status, ExecutionStatus.SUCCEEDED)
        self.assertEqual(self._notifications(), 1)

    def test_an_organization_with_no_due_work_is_never_asked(self):
        self._rule("schedule.daily")
        self._rule("invoice.overdue")
        with tenant_context(organization_id=self.org.id):
            AutomationSchedule.objects.update(next_run_at=datetime.date(2999, 1, 1))  # not due
        self.deny()
        dispatch_automation_events_task()  # no events
        run_due_schedules_task()  # schedule not due
        run_due_scans_task()  # an active rule, but no overdue invoice
        recover_stalled_executions_task()  # nothing stalled
        self.assertEqual(self.asked(), 0)

    def test_an_entitled_organization_runs_normally(self):
        rule = self._rule("manual")
        with tenant_context(organization_id=self.org.id):
            execution = create_manual_execution(rule=rule, actor=self.owner)
        result = run_execution_task(str(execution.id), str(self.org.id))
        self.assertEqual(result["status"], ExecutionStatus.SUCCEEDED)
        self.assertEqual(self._notifications(), 1)


# ------------------------------------------------------------------ documents (OCR)


class OcrBackgroundTests(LinkedToWorkeasy360, DocumentsTestsBase):
    def setUp(self):
        super().setUp()
        self.link(self.org_a)
        with documents_tenant(self.org_a):
            self.document = upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES, original_filename="receipt.pdf"
            )
            transition_ocr_status(Document.objects.get(pk=self.document.pk), OCRStatus.QUEUED)

    def status(self):
        with documents_tenant(self.org_a):
            return Document.objects.get(pk=self.document.pk).ocr_status

    def test_a_denied_organization_is_not_extracted_and_the_document_stays_queued(self):
        self.deny()
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            run_ocr_task(str(self.document.id), str(self.org_a.id))
        self.assertEqual(logs.records[0].task, "documents.run_ocr")
        self.assertEqual(self.status(), OCRStatus.QUEUED)

    def test_the_sweeper_neither_requeues_nor_spends_the_budget_while_denied_then_resumes(self):
        Document.all_objects.filter(pk=self.document.pk).update(
            updated_at=timezone.now() - datetime.timedelta(minutes=30)
        )
        self.deny()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(recover_stalled_ocr_task(), {"requeued": 0, "failed": 0})
        self.assertEqual(self.status(), OCRStatus.QUEUED)
        self.assertEqual(Document.all_objects.get(pk=self.document.pk).ocr_recovery_attempts, 0)

        self.allow()
        self.age_organization_answer(entitlement.BACKGROUND_DENIAL_SECONDS + 1)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(recover_stalled_ocr_task(), {"requeued": 1, "failed": 0})
        self.assertEqual(self.status(), OCRStatus.NEEDS_REVIEW)

    def test_an_organization_with_nothing_stalled_is_never_asked(self):
        self.deny()
        self.assertEqual(recover_stalled_ocr_task(), {"requeued": 0, "failed": 0})  # the document is fresh
        self.assertEqual(self.asked(), 0)

    def test_an_entitled_organization_is_extracted(self):
        run_ocr_task(str(self.document.id), str(self.org_a.id))
        self.assertEqual(self.status(), OCRStatus.NEEDS_REVIEW)


# ------------------------------------------------------------------ ai (indexing)


class IndexingBackgroundTests(LinkedToWorkeasy360, AITestsBase):
    def setUp(self):
        super().setUp()
        self.link(self.org_a)
        self.document = upload_text(self.org_a, self.user_a, "Termination needs thirty days notice.", title="Terms")

    def index_status(self):
        with ai_tenant(self.org_a):
            index = DocumentIndex.objects.filter(document=self.document).first()
            return index.status if index else None

    def test_a_denied_organization_is_not_embedded(self):
        self.deny()
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            self.assertEqual(index_document_task(str(self.document.id), str(self.org_a.id)), "skipped")
        self.assertEqual(logs.records[0].task, "ai.index_document")
        # Marked as skipped (not failed), so the sweeper can resume it without an operator.
        self.assertEqual(self.index_status(), IndexStatus.PENDING)
        with ai_tenant(self.org_a):
            self.assertEqual(DocumentIndex.objects.get(document=self.document).reason_code, SKIPPED_FOR_ACCESS)

    def test_skipped_indexing_resumes_by_itself_after_a_renewal(self):
        self.deny()
        index_document_task(str(self.document.id), str(self.org_a.id))
        # Still denied: the sweeper enqueues nothing and the document stays marked.
        self.assertEqual(resume_skipped_indexing_task(), {"resumed": 0})
        self.assertEqual(self.index_status(), IndexStatus.PENDING)
        self.allow()
        self.age_organization_answer(entitlement.BACKGROUND_DENIAL_SECONDS + 1)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(resume_skipped_indexing_task(), {"resumed": 1})
        self.assertEqual(self.index_status(), IndexStatus.INDEXED)
        self.assertEqual(resume_skipped_indexing_task(), {"resumed": 0})  # nothing left to resume

    def test_an_organization_with_nothing_marked_is_never_asked(self):
        self.deny()
        self.assertEqual(resume_skipped_indexing_task(), {"resumed": 0})
        self.assertEqual(self.asked(), 0)

    def test_an_indexed_document_is_not_marked_by_a_skipped_refresh(self):
        index_document_task(str(self.document.id), str(self.org_a.id))
        self.assertEqual(self.index_status(), IndexStatus.INDEXED)
        self.deny()
        self.age_organization_answer(entitlement.BACKGROUND_DENIAL_SECONDS + 1)
        index_document_task(str(self.document.id), str(self.org_a.id))
        self.assertEqual(self.index_status(), IndexStatus.INDEXED)  # its complete chunk set keeps serving

    def test_it_is_indexed_once_access_returns(self):
        self.deny()
        index_document_task(str(self.document.id), str(self.org_a.id))
        self.allow()
        self.age_organization_answer(entitlement.BACKGROUND_DENIAL_SECONDS + 1)
        self.assertEqual(index_document_task(str(self.document.id), str(self.org_a.id)), IndexStatus.INDEXED)

    def test_an_unlinked_organization_is_indexed_whatever_workeasy360_says(self):
        other = upload_text(self.org_b, self.user_b, "Payment is due in thirty days.", title="Terms")
        self.deny()
        self.assertEqual(index_document_task(str(other.id), str(self.org_b.id)), IndexStatus.INDEXED)


# ------------------------------------------------------------------ nothing is left unclassified

#: Every Celery task and its class (docs/workeasy360-sso.md, "Background jobs"). A and C are the only
#: classes EasyBook has today; a new task must be added here - and, if it is class A, must call the gate.
TASK_CLASSES = {
    "automation.tasks.run_execution_task": "A",
    "automation.tasks.dispatch_automation_events_task": "A",
    "automation.tasks.run_due_schedules_task": "A",
    "automation.tasks.run_due_scans_task": "A",
    "automation.tasks.recover_stalled_executions_task": "A",
    "sales.tasks.generate_recurring_invoices_task": "A",
    "purchases.tasks.generate_recurring_bills_task": "A",
    "purchases.tasks.generate_recurring_expenses_task": "A",
    "documents.tasks.run_ocr_task": "A",
    "documents.tasks.recover_stalled_ocr_task": "A",
    "ai.rag.tasks.index_document_task": "A",
    "ai.rag.tasks.resume_skipped_indexing_task": "A",
    "ai.rag.tasks.purge_expired_ai_data": "C",
}

_LIST_TASKS = """
import django
django.setup()
from config.celery import app
app.autodiscover_tasks(force=True)
import ai.tasks  # noqa: F401  (nested module the autodiscovery does not reach by itself)
print(*sorted(name for name in app.tasks if not name.startswith("celery.")), sep=chr(10))
"""


class EveryTaskIsClassifiedTests(SimpleTestCase):
    def registered_tasks(self):
        result = subprocess.run(  # noqa: S603 - fixed interpreter and script constant, no untrusted input
            [sys.executable, "-c", _LIST_TASKS],
            cwd=BACKEND_DIR,
            env={**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.test"},
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return {line for line in result.stdout.splitlines() if line.strip()}

    def test_a_new_task_must_be_classified_for_the_workeasy360_gate(self):
        self.assertEqual(self.registered_tasks(), set(TASK_CLASSES))

    def test_every_class_a_task_is_gated_in_its_source(self):
        gated = {"A": 0}
        for name, task_class in TASK_CLASSES.items():
            if task_class != "A":
                continue
            module = name.rsplit(".", 1)[0]
            source = (BACKEND_DIR / (module.replace(".", "/") + ".py")).read_text(encoding="utf-8")
            # Recurring generators gate inside the service they call; the rest gate in the task module.
            service = {
                "sales.tasks": "sales/services/recurring_invoices.py",
                "purchases.tasks": "purchases/services/recurring.py",
            }.get(module)
            if service:
                source += (BACKEND_DIR / service).read_text(encoding="utf-8")
            self.assertIn("background_allowed", source, name)
            gated["A"] += 1
        self.assertEqual(gated["A"], 12)
