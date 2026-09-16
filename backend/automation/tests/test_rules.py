from django.test import TestCase

from automation.models.action import AutomationActionConfig
from automation.models.condition import AutomationCondition
from automation.models.rule import AutomationRule, RuleStatus
from automation.services.rules import activate_rule, archive_rule, create_rule, pause_rule, update_rule
from core.exceptions import ApplicationError
from core.tenancy import tenant_context
from core.tests.factories import make_org_with_owner


class AutomationRuleLifecycleTests(TestCase):
    def setUp(self):
        self.org, self.owner, _ = make_org_with_owner("Org", "automation-rules@example.com")

    def _valid_rule_kwargs(self, **overrides):
        kwargs = {
            "organization": self.org,
            "name": "Remind overdue customers",
            "trigger_type": "invoice.overdue",
            "conditions": [{"field": "amount_due", "operator": "greater_than", "value": "10000.00"}],
            "actions": [{"action_id": "draft_payment_reminder", "config": {}}],
            "actor": self.owner,
        }
        kwargs.update(overrides)
        return kwargs

    def test_create_draft_rule(self):
        with tenant_context(organization_id=self.org.id):
            rule = create_rule(**self._valid_rule_kwargs())
            self.assertEqual(rule.conditions.count(), 1)
            self.assertEqual(rule.actions.count(), 1)
        self.assertEqual(rule.status, RuleStatus.DRAFT)
        self.assertEqual(rule.version, 1)

    def test_create_rule_rejects_unknown_trigger(self):
        with tenant_context(organization_id=self.org.id), self.assertRaises(ApplicationError):
            create_rule(**self._valid_rule_kwargs(trigger_type="not.a.trigger"))

    def test_create_rule_rejects_disallowed_condition_field(self):
        with tenant_context(organization_id=self.org.id), self.assertRaises(ApplicationError):
            create_rule(
                **self._valid_rule_kwargs(
                    conditions=[{"field": "internal_secret", "operator": "equals", "value": "x"}]
                )
            )

    def test_create_rule_rejects_disallowed_operator_for_field_type(self):
        with tenant_context(organization_id=self.org.id), self.assertRaises(ApplicationError):
            create_rule(
                **self._valid_rule_kwargs(
                    conditions=[{"field": "amount_due", "operator": "contains", "value": "1"}]
                )
            )

    def test_create_rule_rejects_non_decimal_value_on_decimal_field(self):
        with tenant_context(organization_id=self.org.id), self.assertRaises(ApplicationError):
            create_rule(
                **self._valid_rule_kwargs(
                    conditions=[{"field": "amount_due", "operator": "greater_than", "value": "not-a-number"}]
                )
            )

    def test_create_rule_requires_at_least_one_action(self):
        with tenant_context(organization_id=self.org.id), self.assertRaises(ApplicationError):
            create_rule(**self._valid_rule_kwargs(actions=[]))

    def test_create_rule_rejects_unknown_action(self):
        with tenant_context(organization_id=self.org.id), self.assertRaises(ApplicationError):
            create_rule(**self._valid_rule_kwargs(actions=[{"action_id": "post_invoice", "config": {}}]))

    def test_create_rule_rejects_action_incompatible_with_trigger(self):
        with tenant_context(organization_id=self.org.id), self.assertRaises(ApplicationError):
            create_rule(
                **self._valid_rule_kwargs(
                    trigger_type="stock.low",
                    conditions=[],
                    actions=[{"action_id": "draft_payment_reminder", "config": {}}],
                )
            )

    def test_create_rule_rejects_action_config_unknown_keys(self):
        with tenant_context(organization_id=self.org.id), self.assertRaises(ApplicationError):
            create_rule(
                **self._valid_rule_kwargs(
                    actions=[{"action_id": "send_notification", "config": {"message": "hi", "extra": "nope"}}]
                )
            )

    def test_activate_valid_rule(self):
        with tenant_context(organization_id=self.org.id):
            rule = create_rule(**self._valid_rule_kwargs())
            activated = activate_rule(rule=rule, actor=self.owner)
        self.assertEqual(activated.status, RuleStatus.ACTIVE)

    def test_activation_revalidates_and_rejects_corrupted_rule(self):
        """Proves activation runs its OWN validation gate rather than trusting
        whatever is already stored (phase section 59) — a rule containing
        data that could never pass create_rule/update_rule (simulating drift)
        must still be refused at activation."""
        with tenant_context(organization_id=self.org.id):
            rule = AutomationRule.objects.create(
                organization=self.org, name="Corrupted", trigger_type="invoice.overdue", status=RuleStatus.DRAFT,
            )
            AutomationCondition.objects.create(
                organization=self.org, rule=rule, order=0, field="not_a_real_field", operator="equals", value="x",
            )
            AutomationActionConfig.objects.create(
                organization=self.org, rule=rule, order=0, action_id="send_notification", config={"message": "hi"},
            )
            with self.assertRaises(ApplicationError):
                activate_rule(rule=rule, actor=self.owner)
            rule.refresh_from_db()
        self.assertEqual(rule.status, RuleStatus.DRAFT)

    def test_pause_active_rule(self):
        with tenant_context(organization_id=self.org.id):
            rule = create_rule(**self._valid_rule_kwargs())
            activate_rule(rule=rule, actor=self.owner)
            paused = pause_rule(rule=rule, actor=self.owner)
        self.assertEqual(paused.status, RuleStatus.PAUSED)

    def test_cannot_pause_draft_rule(self):
        with tenant_context(organization_id=self.org.id):
            rule = create_rule(**self._valid_rule_kwargs())
            with self.assertRaises(ApplicationError):
                pause_rule(rule=rule, actor=self.owner)

    def test_paused_rule_can_reactivate(self):
        with tenant_context(organization_id=self.org.id):
            rule = create_rule(**self._valid_rule_kwargs())
            activate_rule(rule=rule, actor=self.owner)
            pause_rule(rule=rule, actor=self.owner)
            reactivated = activate_rule(rule=rule, actor=self.owner)
        self.assertEqual(reactivated.status, RuleStatus.ACTIVE)

    def test_archive_rule_from_draft(self):
        with tenant_context(organization_id=self.org.id):
            rule = create_rule(**self._valid_rule_kwargs())
            archived = archive_rule(rule=rule, actor=self.owner)
        self.assertEqual(archived.status, RuleStatus.ARCHIVED)

    def test_archived_rule_cannot_be_edited_or_reactivated(self):
        with tenant_context(organization_id=self.org.id):
            rule = create_rule(**self._valid_rule_kwargs())
            archive_rule(rule=rule, actor=self.owner)
            with self.assertRaises(ApplicationError):
                update_rule(rule=rule, name="New name", actor=self.owner)
            with self.assertRaises(ApplicationError):
                activate_rule(rule=rule, actor=self.owner)

    def test_editing_conditions_bumps_version(self):
        with tenant_context(organization_id=self.org.id):
            rule = create_rule(**self._valid_rule_kwargs())
            updated = update_rule(
                rule=rule,
                conditions=[{"field": "amount_due", "operator": "greater_than", "value": "20000.00"}],
                actor=self.owner,
            )
            self.assertEqual(updated.conditions.count(), 1)
            self.assertEqual(updated.conditions.first().value, "20000.00")
        self.assertEqual(updated.version, 2)

    def test_non_structural_edit_does_not_bump_version(self):
        with tenant_context(organization_id=self.org.id):
            rule = create_rule(**self._valid_rule_kwargs())
            updated = update_rule(rule=rule, name="Renamed", actor=self.owner)
        self.assertEqual(updated.version, 1)
        self.assertEqual(updated.name, "Renamed")


class AutomationTenantIsolationTests(TestCase):
    def test_rule_from_other_org_is_not_visible(self):
        org_a, owner_a, _ = make_org_with_owner("Org A", "automation-tenant-a@example.com")
        org_b, _owner_b, _ = make_org_with_owner("Org B", "automation-tenant-b@example.com")

        with tenant_context(organization_id=org_a.id):
            create_rule(
                organization=org_a,
                name="A rule",
                trigger_type="manual",
                conditions=[],
                actions=[{"action_id": "send_notification", "config": {"message": "hi"}}],
                actor=owner_a,
            )
        with tenant_context(organization_id=org_b.id):
            self.assertEqual(AutomationRule.objects.count(), 0)
        with tenant_context(organization_id=org_a.id):
            self.assertEqual(AutomationRule.objects.count(), 1)
