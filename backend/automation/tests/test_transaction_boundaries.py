"""Transaction boundaries around automation execution and its task dispatch.

Regressions (phase 12 P0 remediation):

1. `run_execution_task` raised its retry marker INSIDE `tenant_context()`,
   which is a `transaction.atomic()` block — so every transient failure rolled
   back everything the attempt had just recorded: the step's attempt count and
   RETRYING status, sibling steps that had SUCCEEDED, the notifications they
   created. Each retry therefore started from nothing and repeated every
   side effect (an outbound webhook fired again per retry), the step budget
   could never be reached, and once Celery gave up the execution was left
   PENDING with no record that anything had happened.

2. The manual run and retry endpoints called `run_execution_task.delay()`
   inside the request's own transaction (ATOMIC_REQUESTS). A worker can pick
   the message up before that transaction commits, find no execution (or the
   pre-retry state), skip it, and leave the execution PENDING forever.
"""

from unittest.mock import patch

from rest_framework.test import APITestCase

from automation.actions.errors import AutomationTransientError
from automation.actions.handlers import run_action as real_run_action
from automation.models.execution import AutomationExecution, ExecutionStatus
from automation.models.notification import AutomationNotification
from automation.models.step_execution import StepStatus
from automation.services.execution import STEP_MAX_ATTEMPTS, create_manual_execution
from automation.services.rules import activate_rule, create_rule
from automation.tasks import run_execution_task
from core.tenancy import tenant_context
from core.tests.factories import make_org_with_owner


class RetryBoundaryTests(APITestCase):
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("Org", "automation-boundaries@example.com")
        with tenant_context(organization_id=self.org.id):
            rule = create_rule(
                organization=self.org, name="Notify then call out", trigger_type="manual", conditions=[],
                actions=[
                    {"action_id": "send_notification", "config": {"message": "first"}},
                    {"action_id": "send_notification", "config": {"message": "second"}},
                ],
                actor=self.owner,
            )
            self.rule = activate_rule(rule=rule, actor=self.owner)

    def test_step_state_survives_each_retry_and_side_effects_are_not_repeated(self):
        calls = {"first": 0, "second": 0}

        def second_step_always_transient(**kwargs):
            if kwargs["step"].order == 0:
                calls["first"] += 1
                return real_run_action(**kwargs)
            calls["second"] += 1
            raise AutomationTransientError("provider unavailable")

        with tenant_context(organization_id=self.org.id):
            execution = create_manual_execution(rule=self.rule, actor=self.owner)

        with patch("automation.services.execution._run_action_default", side_effect=second_step_always_transient):
            # Eager apply() runs Celery's autoretries synchronously.
            run_execution_task.apply(args=[str(execution.id), str(self.org.id)])

        with tenant_context(organization_id=self.org.id):
            execution.refresh_from_db()
            steps = {step.order: step for step in execution.steps.all()}
            self.assertEqual(AutomationNotification.objects.count(), 1)

        self.assertEqual(calls["first"], 1, "a SUCCEEDED step must never run again on retry")
        self.assertEqual(calls["second"], STEP_MAX_ATTEMPTS, "the step budget, not Celery's, must bound attempts")
        self.assertEqual(steps[0].status, StepStatus.SUCCEEDED)
        self.assertEqual(steps[1].status, StepStatus.FAILED)
        self.assertEqual(steps[1].attempt, STEP_MAX_ATTEMPTS)
        self.assertEqual(execution.status, ExecutionStatus.PARTIAL)


class DispatchAfterCommitTests(APITestCase):
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("Org", "automation-dispatch-commit@example.com")
        with tenant_context(organization_id=self.org.id):
            rule = create_rule(
                organization=self.org, name="Manual notify", trigger_type="manual", conditions=[],
                actions=[{"action_id": "send_notification", "config": {"message": "hi"}}],
                actor=self.owner,
            )
            self.rule = activate_rule(rule=rule, actor=self.owner)
        self.client.force_authenticate(user=self.owner)
        self.headers = {"HTTP_X_ORGANIZATION_ID": str(self.org.id)}

    def test_manual_run_enqueues_only_after_the_request_commits(self):
        with patch.object(run_execution_task, "delay") as delay:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                response = self.client.post(f"/api/v1/automation/rules/{self.rule.id}/run/", **self.headers)
                self.assertEqual(response.status_code, 201, response.data)
                delay.assert_not_called()
            for callback in callbacks:
                callback()
        delay.assert_called_once_with(response.data["id"], str(self.org.id))

    def test_manual_retry_enqueues_only_after_the_request_commits(self):
        with tenant_context(organization_id=self.org.id):
            execution = create_manual_execution(rule=self.rule, actor=self.owner)
        AutomationExecution.all_objects.filter(pk=execution.pk).update(status=ExecutionStatus.FAILED)

        with patch.object(run_execution_task, "delay") as delay:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                response = self.client.post(f"/api/v1/automation/executions/{execution.id}/retry/", **self.headers)
                self.assertEqual(response.status_code, 200, response.data)
                delay.assert_not_called()
            for callback in callbacks:
                callback()
        delay.assert_called_once_with(str(execution.id), str(self.org.id))
