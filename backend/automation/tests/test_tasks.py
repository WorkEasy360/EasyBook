import datetime
import threading
from decimal import Decimal

from django.db import connection
from django.test import TransactionTestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from accounts.models import FiscalYear
from automation.models.event import AutomationEvent
from automation.models.execution import AutomationExecution, ExecutionStatus, TriggerSource
from automation.models.notification import AutomationNotification
from automation.models.schedule import AutomationScanOccurrence
from automation.services.rules import activate_rule, create_rule
from automation.tasks import dispatch_automation_events_task, run_due_scans_task, run_due_schedules_task
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit
from sales.services.customers import create_customer
from sales.services.invoices import create_invoice, post_invoice


class AutomationTasksTestsBase:
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("Org", "automation-tasks@example.com")
        self.currency = make_currency("INR")
        with tenant_context(organization_id=self.org.id):
            FiscalYear.objects.create(
                organization=self.org, start_date=datetime.date(2026, 4, 1), end_date=datetime.date(2027, 3, 31)
            )
            self.ar_account = create_account(
                organization=self.org, code="1100", name="AR", account_type=AccountType.ASSET
            )
            self.sales_account = create_account(
                organization=self.org, code="4000", name="Sales", account_type=AccountType.INCOME
            )
            self.unit = create_unit(organization=self.org, code="EA", name="Each")
            self.item = create_item(
                organization=self.org, item_type=ItemType.SERVICE, name="Consulting", unit=self.unit,
                sales_account=self.sales_account,
            )
            self.customer = create_customer(
                organization=self.org, customer_code="CUST-1", display_name="Acme", currency=self.currency
            )

    def _create_overdue_invoice(self):
        invoice = create_invoice(
            organization=self.org, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
            due_date=datetime.date(2026, 5, 1), receivable_account=self.ar_account,
            lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("500.00")}],
        )
        return post_invoice(invoice_id=invoice.id, organization=self.org, actor=self.owner)


class DispatchEventsTaskTests(AutomationTasksTestsBase, TransactionTestCase):
    def test_dispatch_runs_matching_rule_and_marks_event_dispatched(self):
        with tenant_context(organization_id=self.org.id):
            rule = create_rule(
                organization=self.org, name="Notify on posted invoice", trigger_type="invoice.posted",
                conditions=[], actions=[{"action_id": "send_notification", "config": {"message": "posted"}}],
                actor=self.owner,
            )
            activate_rule(rule=rule, actor=self.owner)

            invoice = create_invoice(
                organization=self.org, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("500.00")}],
            )
            post_invoice(invoice_id=invoice.id, organization=self.org, actor=self.owner)

        dispatch_automation_events_task()

        with tenant_context(organization_id=self.org.id):
            event = AutomationEvent.objects.get(event_type="invoice.posted", entity_id=str(invoice.id))
            self.assertIsNotNone(event.dispatched_at)
            executions = AutomationExecution.objects.filter(rule=rule, trigger_event=event)
            self.assertEqual(executions.count(), 1)
            self.assertEqual(executions.first().status, ExecutionStatus.SUCCEEDED)
            self.assertEqual(AutomationNotification.objects.count(), 1)

    def test_dispatching_twice_does_not_duplicate_the_notification(self):
        with tenant_context(organization_id=self.org.id):
            rule = create_rule(
                organization=self.org, name="Notify", trigger_type="invoice.posted", conditions=[],
                actions=[{"action_id": "send_notification", "config": {"message": "posted"}}], actor=self.owner,
            )
            activate_rule(rule=rule, actor=self.owner)
            invoice = create_invoice(
                organization=self.org, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("500.00")}],
            )
            post_invoice(invoice_id=invoice.id, organization=self.org, actor=self.owner)

        dispatch_automation_events_task()
        dispatch_automation_events_task()

        with tenant_context(organization_id=self.org.id):
            self.assertEqual(AutomationNotification.objects.count(), 1)


class ScheduledTriggerTaskTests(AutomationTasksTestsBase, TransactionTestCase):
    def test_daily_schedule_fires_once_per_day(self):
        with tenant_context(organization_id=self.org.id):
            rule = create_rule(
                organization=self.org, name="Daily digest", trigger_type="schedule.daily", conditions=[],
                actions=[{"action_id": "send_notification", "config": {"message": "daily"}}], actor=self.owner,
            )
            activate_rule(rule=rule, actor=self.owner)

        run_due_schedules_task()
        run_due_schedules_task()  # same day again — must not double-fire

        with tenant_context(organization_id=self.org.id):
            self.assertEqual(AutomationNotification.objects.count(), 1)


class ScanTriggerTaskTests(AutomationTasksTestsBase, TransactionTestCase):
    def test_overdue_scan_fires_and_respects_cooldown(self):
        with tenant_context(organization_id=self.org.id):
            rule = create_rule(
                organization=self.org, name="Overdue reminder", trigger_type="invoice.overdue", conditions=[],
                actions=[{"action_id": "send_notification", "config": {"message": "overdue"}}], actor=self.owner,
            )
            activate_rule(rule=rule, actor=self.owner)
            self._create_overdue_invoice()

        run_due_scans_task()
        run_due_scans_task()  # same day — cooldown must prevent a second notification

        with tenant_context(organization_id=self.org.id):
            self.assertEqual(AutomationNotification.objects.count(), 1)


class ScanOccurrenceConcurrencyTests(AutomationTasksTestsBase, TransactionTestCase):
    def test_two_schedulers_cannot_claim_the_same_scan_occurrence_twice(self):
        from automation.services.scheduling import claim_scan_occurrence

        with tenant_context(organization_id=self.org.id):
            rule = create_rule(
                organization=self.org, name="Overdue reminder", trigger_type="invoice.overdue", conditions=[],
                actions=[{"action_id": "send_notification", "config": {"message": "overdue"}}], actor=self.owner,
            )
            rule = activate_rule(rule=rule, actor=self.owner)
            invoice = self._create_overdue_invoice()

        as_of = datetime.date(2026, 9, 16)
        results = []
        lock = threading.Lock()

        def try_claim():
            try:
                with tenant_context(organization_id=self.org.id):
                    execution = AutomationExecution.objects.create(
                        organization=self.org, rule=rule, rule_version=rule.version,
                        trigger_source=TriggerSource.SCHEDULE, entity_id=str(invoice.id), executed_as=self.owner,
                    )
                    occurrence = claim_scan_occurrence(rule=rule, entity_id=invoice.id, as_of=as_of, execution=execution)
                    if occurrence is None:
                        execution.delete()
                    with lock:
                        results.append(occurrence is not None)
            finally:
                connection.close()

        threads = [threading.Thread(target=try_claim) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(results.count(True), 1, "exactly one thread must win the claim")
        with tenant_context(organization_id=self.org.id):
            self.assertEqual(AutomationScanOccurrence.objects.filter(rule=rule, entity_id=str(invoice.id)).count(), 1)


class DuplicateEventConcurrencyTests(AutomationTasksTestsBase, TransactionTestCase):
    """Phase sections 11, 81, 97, 101: 'same event / multiple workers' must
    execute the action exactly once, proven with real thread/connection
    contention (TransactionTestCase), mirroring
    sales/tests/test_concurrency.py's pattern."""

    def test_concurrent_dispatch_of_same_event_executes_exactly_once(self):
        from automation.services.execution import create_execution_for_event, run_execution

        with tenant_context(organization_id=self.org.id):
            rule = create_rule(
                organization=self.org, name="Notify", trigger_type="invoice.posted", conditions=[],
                actions=[{"action_id": "send_notification", "config": {"message": "posted"}}], actor=self.owner,
            )
            rule = activate_rule(rule=rule, actor=self.owner)
            invoice = create_invoice(
                organization=self.org, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
                due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
                lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("500.00")}],
            )
            invoice = post_invoice(invoice_id=invoice.id, organization=self.org, actor=self.owner)
            event = AutomationEvent.objects.get(event_type="invoice.posted", entity_id=str(invoice.id))

        results = []
        lock = threading.Lock()

        def worker():
            try:
                with tenant_context(organization_id=self.org.id):
                    execution = create_execution_for_event(rule=rule, event=event)
                    if execution is not None:
                        run_execution(execution)
                    with lock:
                        results.append(execution is not None)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(results.count(True), 1, "exactly one thread must win the execution creation race")
        with tenant_context(organization_id=self.org.id):
            self.assertEqual(AutomationExecution.objects.filter(rule=rule, trigger_event=event).count(), 1)
            self.assertEqual(AutomationNotification.objects.count(), 1)
