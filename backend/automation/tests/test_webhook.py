from unittest import mock

from django.test import TestCase

from authz.roles import Role
from automation.actions.errors import AutomationTransientError
from automation.actions.webhook_security import validate_webhook_url
from automation.models.step_execution import StepStatus
from automation.services.execution import create_manual_execution, run_execution
from automation.services.rules import activate_rule, create_rule
from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from core.tests.factories import make_membership, make_org_with_owner, make_user


def _public_addrinfo(host, port):
    return [(2, 1, 6, "", ("93.184.216.34", 0))]


def _private_addrinfo(host, port):
    return [(2, 1, 6, "", ("10.0.0.5", 0))]


class WebhookSecurityTests(TestCase):
    def test_rejects_non_https_scheme(self):
        with self.assertRaises(ApplicationError):
            validate_webhook_url("http://example.com/hook")

    def test_rejects_localhost(self):
        with self.assertRaises(ApplicationError):
            validate_webhook_url("https://localhost/hook")

    def test_rejects_metadata_ip_literal(self):
        with mock.patch("automation.actions.webhook_security.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("169.254.169.254", 0))]):
            with self.assertRaises(ApplicationError):
                validate_webhook_url("https://169.254.169.254/hook")

    def test_rejects_hostname_resolving_to_private_ip(self):
        with mock.patch("automation.actions.webhook_security.socket.getaddrinfo", side_effect=_private_addrinfo):
            with self.assertRaises(ApplicationError):
                validate_webhook_url("https://internal.example.com/hook")

    def test_accepts_public_https_url(self):
        with mock.patch("automation.actions.webhook_security.socket.getaddrinfo", side_effect=_public_addrinfo):
            validate_webhook_url("https://example.com/hook")  # must not raise

    def test_unresolvable_hostname_rejected(self):
        import socket

        with mock.patch("automation.actions.webhook_security.socket.getaddrinfo", side_effect=socket.gaierror):
            with self.assertRaises(ApplicationError):
                validate_webhook_url("https://does-not-resolve.example/hook")


class WebhookActionRBACTests(TestCase):
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("Org", "automation-webhook-rbac@example.com")
        self.accountant = make_user("automation-webhook-accountant@example.com")
        make_membership(self.org, self.accountant, role=Role.ACCOUNTANT)

    def _payload(self):
        return {
            "organization": self.org, "name": "Notify webhook", "trigger_type": "manual", "conditions": [],
            "actions": [{"action_id": "call_webhook", "config": {"url": "https://example.com/hook"}}],
        }

    def test_accountant_cannot_create_webhook_rule(self):
        with tenant_context(organization_id=self.org.id), mock.patch(
            "automation.actions.webhook_security.socket.getaddrinfo", side_effect=_public_addrinfo
        ):
            with self.assertRaises(ApplicationError):
                create_rule(**self._payload(), actor=self.accountant)

    def test_owner_can_create_webhook_rule(self):
        with tenant_context(organization_id=self.org.id), mock.patch(
            "automation.actions.webhook_security.socket.getaddrinfo", side_effect=_public_addrinfo
        ):
            rule = create_rule(**self._payload(), actor=self.owner)
            self.assertEqual(rule.actions.first().action_id, "call_webhook")

    def test_accountant_promoted_later_cannot_retroactively_activate_downgraded_rule(self):
        """Activation re-checks the ACTING user's permission, not the
        creator's — an owner-created webhook rule cannot be (re)activated by
        an accountant who lacks automation.manage_webhooks."""
        with tenant_context(organization_id=self.org.id), mock.patch(
            "automation.actions.webhook_security.socket.getaddrinfo", side_effect=_public_addrinfo
        ):
            rule = create_rule(**self._payload(), actor=self.owner)
            with self.assertRaises(ApplicationError):
                activate_rule(rule=rule, actor=self.accountant)


class WebhookExecutionTests(TestCase):
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("Org", "automation-webhook-exec@example.com")

    def _activate_webhook_rule(self, secret=""):
        config = {"url": "https://example.com/hook"}
        if secret:
            config["secret"] = secret
        with tenant_context(organization_id=self.org.id), mock.patch(
            "automation.actions.webhook_security.socket.getaddrinfo", side_effect=_public_addrinfo
        ):
            rule = create_rule(
                organization=self.org, name="Webhook rule", trigger_type="manual", conditions=[],
                actions=[{"action_id": "call_webhook", "config": config}], actor=self.owner,
            )
            return activate_rule(rule=rule, actor=self.owner)

    def test_webhook_action_succeeds_with_fake_sender(self):
        with tenant_context(organization_id=self.org.id):
            rule = self._activate_webhook_rule(secret="topsecret")
            execution = create_manual_execution(rule=rule, actor=self.owner)
            run_execution(execution)

            execution.refresh_from_db()
            step = execution.steps.first()
            self.assertEqual(step.status, StepStatus.SUCCEEDED)
            self.assertEqual(step.result["http_status"], 200)
            self.assertIn("delivery_id", step.result)
            # idempotency_key IS the delivery_id — stable across retries.
            self.assertEqual(step.result["delivery_id"], step.idempotency_key)

    def test_webhook_endpoint_5xx_is_transient_and_retryable(self):
        with tenant_context(organization_id=self.org.id):
            rule = self._activate_webhook_rule()
            execution = create_manual_execution(rule=rule, actor=self.owner)

            def failing_sender(**kwargs):
                raise AutomationTransientError("HTTP 503")

            with mock.patch("automation.actions.webhook_sender.get_sender", return_value=failing_sender):
                run_execution(execution)

            execution.refresh_from_db()
            step = execution.steps.first()
            self.assertEqual(step.status, StepStatus.RETRYING)

    def test_webhook_secret_is_masked_in_api_serializer(self):
        from automation.api.serializers import AutomationActionConfigSerializer

        rule = self._activate_webhook_rule(secret="topsecret")
        with tenant_context(organization_id=self.org.id):
            action_config = rule.actions.first()
            data = AutomationActionConfigSerializer(action_config).data
        self.assertEqual(data["config"]["secret"], "********")
        self.assertNotEqual(data["config"]["secret"], "topsecret")
