import datetime
from decimal import Decimal

from django.test import TestCase

from accounting.models.account import AccountType
from accounting.services.accounts import create_account
from accounts.models import FiscalYear, Membership
from automation.actions.handlers import AutomationTransientError
from automation.models.event import AutomationEvent
from automation.models.execution import ExecutionStatus, TriggerSource
from automation.models.notification import AutomationNotification
from automation.models.step_execution import FailureCategory, StepStatus
from automation.services.execution import (
    create_execution_for_event,
    create_manual_execution,
    retry_execution,
    run_execution,
)
from automation.services.rules import activate_rule, create_rule
from core.tenancy import tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from items.models.item import ItemType
from items.services.items import create_item
from items.services.units import create_unit
from sales.services.customers import create_customer
from sales.services.invoices import create_invoice, post_invoice


class ExecutionEngineTests(TestCase):
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("Org", "automation-exec@example.com")
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

    def _post_invoice(self, unit_price=Decimal("500.00")):
        invoice = create_invoice(
            organization=self.org, customer=self.customer, invoice_date=datetime.date(2026, 4, 10),
            due_date=datetime.date(2026, 5, 10), receivable_account=self.ar_account,
            lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": unit_price}],
        )
        return post_invoice(invoice_id=invoice.id, organization=self.org, actor=self.owner)

    def _notification_rule(self, threshold="100.00"):
        with tenant_context(organization_id=self.org.id):
            rule = create_rule(
                organization=self.org, name="Notify on posted invoice", trigger_type="invoice.posted",
                conditions=[{"field": "amount_due", "operator": "greater_than", "value": threshold}],
                actions=[{"action_id": "send_notification", "config": {"message": "Invoice posted"}}],
                actor=self.owner,
            )
            return activate_rule(rule=rule, actor=self.owner)

    def test_matching_conditions_runs_action(self):
        with tenant_context(organization_id=self.org.id):
            rule = self._notification_rule(threshold="100.00")
            invoice = self._post_invoice(unit_price=Decimal("500.00"))
            event = AutomationEvent.objects.get(event_type="invoice.posted", entity_id=str(invoice.id))

            execution = create_execution_for_event(rule=rule, event=event)
            run_execution(execution)

            execution.refresh_from_db()
            self.assertEqual(execution.status, ExecutionStatus.SUCCEEDED)
            self.assertEqual(execution.steps.count(), 1)
            self.assertEqual(execution.steps.first().status, StepStatus.SUCCEEDED)
            self.assertEqual(AutomationNotification.objects.filter(source_execution=execution).count(), 1)

    def test_failing_conditions_produce_no_step_execution(self):
        with tenant_context(organization_id=self.org.id):
            rule = self._notification_rule(threshold="100000.00")
            invoice = self._post_invoice(unit_price=Decimal("500.00"))
            event = AutomationEvent.objects.get(event_type="invoice.posted", entity_id=str(invoice.id))

            execution = create_execution_for_event(rule=rule, event=event)
            run_execution(execution)

            execution.refresh_from_db()
            self.assertEqual(execution.status, ExecutionStatus.SUCCEEDED)
            self.assertEqual(execution.steps.count(), 0)

    def test_duplicate_event_cannot_create_second_execution_for_same_rule(self):
        """The DB UniqueConstraint on (rule, trigger_event) is the
        authoritative idempotency guard (phase section 11)."""
        with tenant_context(organization_id=self.org.id):
            rule = self._notification_rule()
            invoice = self._post_invoice()
            event = AutomationEvent.objects.get(event_type="invoice.posted", entity_id=str(invoice.id))

            first = create_execution_for_event(rule=rule, event=event)
            second = create_execution_for_event(rule=rule, event=event)

            self.assertIsNotNone(first)
            self.assertIsNone(second)
            self.assertEqual(rule.executions.filter(trigger_event=event).count(), 1)

    def test_rerunning_a_succeeded_execution_does_not_duplicate_the_effect(self):
        """Crash-recovery safety (phase sections 82-84): re-running an
        execution whose step already SUCCEEDED must not create a second
        notification."""
        with tenant_context(organization_id=self.org.id):
            rule = self._notification_rule()
            invoice = self._post_invoice()
            event = AutomationEvent.objects.get(event_type="invoice.posted", entity_id=str(invoice.id))
            execution = create_execution_for_event(rule=rule, event=event)

            run_execution(execution)
            execution.refresh_from_db()
            run_execution(execution)  # simulates a retried Celery task

            self.assertEqual(AutomationNotification.objects.filter(source_execution=execution).count(), 1)

    def test_permission_lost_after_rule_creation_fails_the_step_closed(self):
        """Run-as policy (phase section 23): permissions are re-verified
        live at execution time, not cached from rule creation."""
        with tenant_context(organization_id=self.org.id):
            rule = self._notification_rule()
            membership = Membership.all_objects.get(organization=self.org, user=self.owner)
            membership.is_active = False
            membership.save(update_fields=["is_active"])

            invoice = self._post_invoice()
            event = AutomationEvent.objects.get(event_type="invoice.posted", entity_id=str(invoice.id))
            execution = create_execution_for_event(rule=rule, event=event)
            run_execution(execution)

            execution.refresh_from_db()
            step = execution.steps.first()
            self.assertEqual(step.status, StepStatus.FAILED)
            self.assertEqual(step.failure_category, FailureCategory.PERMISSION_ERROR)

    def test_transient_failure_retries_then_succeeds(self):
        with tenant_context(organization_id=self.org.id):
            rule = self._notification_rule()
            invoice = self._post_invoice()
            event = AutomationEvent.objects.get(event_type="invoice.posted", entity_id=str(invoice.id))
            execution = create_execution_for_event(rule=rule, event=event)

            calls = {"count": 0}

            def flaky_run_action(**kwargs):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise AutomationTransientError("temporary provider outage")
                return {"ok": True}

            run_execution(execution, run_action=flaky_run_action)
            execution.refresh_from_db()
            step = execution.steps.first()
            self.assertEqual(step.status, StepStatus.RETRYING)
            self.assertEqual(execution.status, ExecutionStatus.RUNNING)

            run_execution(execution, run_action=flaky_run_action)
            execution.refresh_from_db()
            step.refresh_from_db()
            self.assertEqual(step.status, StepStatus.SUCCEEDED)
            self.assertEqual(execution.status, ExecutionStatus.SUCCEEDED)
            self.assertEqual(calls["count"], 2)

    def test_permanent_failure_is_never_auto_retried(self):
        with tenant_context(organization_id=self.org.id):
            rule = self._notification_rule()
            membership = Membership.all_objects.get(organization=self.org, user=self.owner)
            membership.is_active = False
            membership.save(update_fields=["is_active"])
            invoice = self._post_invoice()
            event = AutomationEvent.objects.get(event_type="invoice.posted", entity_id=str(invoice.id))
            execution = create_execution_for_event(rule=rule, event=event)

            run_execution(execution)
            run_execution(execution)  # calling again must not re-attempt a permanent failure

            execution.refresh_from_db()
            step = execution.steps.first()
            self.assertEqual(step.attempt, 1)
            self.assertEqual(step.status, StepStatus.FAILED)
            self.assertEqual(execution.status, ExecutionStatus.FAILED)

    def test_manual_retry_reruns_after_permission_is_restored(self):
        with tenant_context(organization_id=self.org.id):
            rule = self._notification_rule()
            membership = Membership.all_objects.get(organization=self.org, user=self.owner)
            membership.is_active = False
            membership.save(update_fields=["is_active"])
            invoice = self._post_invoice()
            event = AutomationEvent.objects.get(event_type="invoice.posted", entity_id=str(invoice.id))
            execution = create_execution_for_event(rule=rule, event=event)
            run_execution(execution)
            execution.refresh_from_db()
            self.assertEqual(execution.status, ExecutionStatus.FAILED)

            membership.is_active = True
            membership.save(update_fields=["is_active"])
            retry_execution(execution, actor=self.owner)
            run_execution(execution)

            execution.refresh_from_db()
            self.assertEqual(execution.status, ExecutionStatus.SUCCEEDED)
            self.assertEqual(AutomationNotification.objects.filter(source_execution=execution).count(), 1)

    def test_cannot_retry_a_succeeded_execution(self):
        with tenant_context(organization_id=self.org.id):
            rule = self._notification_rule()
            invoice = self._post_invoice()
            event = AutomationEvent.objects.get(event_type="invoice.posted", entity_id=str(invoice.id))
            execution = create_execution_for_event(rule=rule, event=event)
            run_execution(execution)
            execution.refresh_from_db()
            from core.exceptions import ApplicationError

            with self.assertRaises(ApplicationError):
                retry_execution(execution, actor=self.owner)

    def test_ai_draft_action_wraps_existing_orchestration(self):
        with tenant_context(organization_id=self.org.id):
            rule = create_rule(
                organization=self.org, name="Draft reminder", trigger_type="invoice.overdue",
                conditions=[], actions=[{"action_id": "draft_payment_reminder", "config": {}}], actor=self.owner,
            )
            rule = activate_rule(rule=rule, actor=self.owner)
            invoice = create_invoice(
                organization=self.org, customer=self.customer, invoice_date=datetime.date(2026, 4, 1),
                due_date=datetime.date(2026, 4, 15), receivable_account=self.ar_account,
                lines=[{"item": self.item, "quantity": Decimal("1"), "unit_price": Decimal("1000.00")}],
            )
            invoice = post_invoice(invoice_id=invoice.id, organization=self.org, actor=self.owner)

            execution = create_manual_execution(rule=rule, actor=self.owner)
            execution.entity_id = str(invoice.id)
            execution.save(update_fields=["entity_id"])
            run_execution(execution)

            execution.refresh_from_db()
            step = execution.steps.first()
            self.assertEqual(step.status, StepStatus.SUCCEEDED)
            self.assertIn("draft", step.result)

    def test_manual_execution_records_initiator_and_trigger_source(self):
        with tenant_context(organization_id=self.org.id):
            rule = self._notification_rule()
            execution = create_manual_execution(rule=rule, actor=self.owner)
        self.assertEqual(execution.trigger_source, TriggerSource.MANUAL)
        self.assertEqual(execution.initiated_by_id, self.owner.id)

    def test_max_runs_per_period_blocks_manual_runs_once_exceeded(self):
        with tenant_context(organization_id=self.org.id):
            rule = create_rule(
                organization=self.org, name="Rate limited", trigger_type="manual", conditions=[],
                actions=[{"action_id": "send_notification", "config": {"message": "hi"}}],
                max_runs_per_period=1, actor=self.owner,
            )
            rule = activate_rule(rule=rule, actor=self.owner)

            create_manual_execution(rule=rule, actor=self.owner)
            from core.exceptions import ApplicationError

            with self.assertRaises(ApplicationError):
                create_manual_execution(rule=rule, actor=self.owner)

    def test_max_runs_per_period_silently_skips_event_driven_executions(self):
        with tenant_context(organization_id=self.org.id):
            rule = create_rule(
                organization=self.org, name="Rate limited", trigger_type="invoice.posted", conditions=[],
                actions=[{"action_id": "send_notification", "config": {"message": "hi"}}],
                max_runs_per_period=1, actor=self.owner,
            )
            rule = activate_rule(rule=rule, actor=self.owner)

            invoice_a = self._post_invoice()
            event_a = AutomationEvent.objects.get(event_type="invoice.posted", entity_id=str(invoice_a.id))
            self.assertIsNotNone(create_execution_for_event(rule=rule, event=event_a))

            invoice_b = self._post_invoice()
            event_b = AutomationEvent.objects.get(event_type="invoice.posted", entity_id=str(invoice_b.id))
            self.assertIsNone(create_execution_for_event(rule=rule, event=event_b))


class AutomationExecutionTenantIsolationTests(TestCase):
    def test_notification_from_other_org_is_not_visible(self):
        org_a, owner_a, _ = make_org_with_owner("Org A", "automation-exec-a@example.com")
        org_b, _, _ = make_org_with_owner("Org B", "automation-exec-b@example.com")

        with tenant_context(organization_id=org_a.id):
            rule = create_rule(
                organization=org_a, name="Notify", trigger_type="manual", conditions=[],
                actions=[{"action_id": "send_notification", "config": {"message": "hi"}}], actor=owner_a,
            )
            rule = activate_rule(rule=rule, actor=owner_a)
            execution = create_manual_execution(rule=rule, actor=owner_a)
            run_execution(execution)
            self.assertEqual(AutomationNotification.objects.count(), 1)

        with tenant_context(organization_id=org_b.id):
            self.assertEqual(AutomationNotification.objects.count(), 0)
