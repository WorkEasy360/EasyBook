"""Recovery sweeper for automation executions that no worker will ever pick up.

Regression (phase 12 P0 remediation): an execution only runs if its
`run_execution_task` message reaches a worker. A lost enqueue (Redis blip at
commit time), a worker container killed mid-run, or a redelivery that never
came all left the execution PENDING/RUNNING forever — nothing scheduled ever
looked at it again. The sweeper re-enqueues stale executions (safe:
run_execution is idempotent per step) and gives up — FAILED, visible,
human-retryable — once the attempt budget is spent, never looping forever.
"""

import datetime

from django.test import TestCase
from django.utils import timezone

from automation.models.execution import AutomationExecution, ExecutionStatus
from automation.models.notification import AutomationNotification
from automation.models.step_execution import AutomationStepExecution, StepStatus
from automation.services.execution import create_manual_execution, run_execution
from automation.services.rules import activate_rule, create_rule
from automation.tasks import RECOVERY_MAX_ATTEMPTS, recover_stalled_executions_task
from core.tenancy import tenant_context
from core.tests.factories import make_org_with_owner


class ExecutionRecoveryTests(TestCase):
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("Org", "automation-recovery@example.com")
        with tenant_context(organization_id=self.org.id):
            rule = create_rule(
                organization=self.org, name="Manual notify", trigger_type="manual", conditions=[],
                actions=[{"action_id": "send_notification", "config": {"message": "hello"}}],
                actor=self.owner,
            )
            self.rule = activate_rule(rule=rule, actor=self.owner)

    def _age(self, execution, *, minutes):
        AutomationExecution.all_objects.filter(pk=execution.pk).update(
            updated_at=timezone.now() - datetime.timedelta(minutes=minutes)
        )

    def _sweep(self):
        with self.captureOnCommitCallbacks(execute=True):
            return recover_stalled_executions_task()

    def test_stale_pending_execution_is_re_enqueued_and_runs(self):
        with tenant_context(organization_id=self.org.id):
            execution = create_manual_execution(rule=self.rule, actor=self.owner)
        self._age(execution, minutes=30)

        result = self._sweep()

        with tenant_context(organization_id=self.org.id):
            execution.refresh_from_db()
            self.assertEqual(execution.status, ExecutionStatus.SUCCEEDED)
            self.assertEqual(AutomationNotification.objects.count(), 1)
        self.assertEqual(result["recovered"], 1)

    def test_recently_created_pending_execution_is_left_for_its_own_task(self):
        with tenant_context(organization_id=self.org.id):
            execution = create_manual_execution(rule=self.rule, actor=self.owner)

        result = self._sweep()

        with tenant_context(organization_id=self.org.id):
            execution.refresh_from_db()
            self.assertEqual(execution.status, ExecutionStatus.PENDING)
        self.assertEqual(result["recovered"], 0)

    def test_stale_running_execution_resumes_without_repeating_succeeded_steps(self):
        with tenant_context(organization_id=self.org.id):
            execution = create_manual_execution(rule=self.rule, actor=self.owner)
            run_execution(execution)
            # Simulate a worker killed after the step committed but before the
            # execution's own final status was written.
            AutomationExecution.all_objects.filter(pk=execution.pk).update(status=ExecutionStatus.RUNNING)
        self._age(execution, minutes=120)

        self._sweep()

        with tenant_context(organization_id=self.org.id):
            execution.refresh_from_db()
            self.assertEqual(execution.status, ExecutionStatus.SUCCEEDED)
            self.assertEqual(AutomationNotification.objects.count(), 1)

    def test_recovery_gives_up_visibly_once_the_attempt_budget_is_spent(self):
        with tenant_context(organization_id=self.org.id):
            execution = create_manual_execution(rule=self.rule, actor=self.owner)
            AutomationExecution.all_objects.filter(pk=execution.pk).update(recovery_attempts=RECOVERY_MAX_ATTEMPTS)
        self._age(execution, minutes=30)

        result = self._sweep()

        with tenant_context(organization_id=self.org.id):
            execution.refresh_from_db()
            self.assertEqual(execution.status, ExecutionStatus.FAILED)
            self.assertIn("recovery", execution.error_summary.lower())
            self.assertFalse(
                AutomationStepExecution.objects.filter(execution=execution, status=StepStatus.SUCCEEDED).exists()
            )
        self.assertEqual(result["abandoned"], 1)

    def test_finished_executions_are_never_touched(self):
        with tenant_context(organization_id=self.org.id):
            execution = create_manual_execution(rule=self.rule, actor=self.owner)
            run_execution(execution)
        self._age(execution, minutes=600)

        result = self._sweep()

        self.assertEqual(result["recovered"], 0)
        with tenant_context(organization_id=self.org.id):
            self.assertEqual(AutomationNotification.objects.count(), 1)
