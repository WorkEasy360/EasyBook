"""Database-level Row Level Security across every automation table — mirrors
documents/tests/test_rls.py. Walks EVERY TenantScopedModel in the app so a
model added later without an entry in migrations/0002_enable_rls.py fails
here rather than shipping unprotected."""

from django.apps import apps
from django.db import connection
from django.test import TestCase

from automation.models.rule import AutomationRule, RuleStatus
from core.models import TenantScopedModel
from core.tenancy import tenant_context
from core.tests.factories import make_org_with_owner


def _automation_tenant_models():
    return [
        model for model in apps.get_app_config("automation").get_models() if issubclass(model, TenantScopedModel)
    ]


class AutomationRlsCoverageTests(TestCase):
    def test_every_automation_model_is_tenant_scoped(self):
        models = _automation_tenant_models()
        self.assertGreater(len(models), 0)
        all_models = list(apps.get_app_config("automation").get_models())
        self.assertEqual(
            sorted(m.__name__ for m in models),
            sorted(m.__name__ for m in all_models),
            "every automation model must inherit TenantScopedModel",
        )

    def test_every_automation_table_has_rls_enabled_and_forced(self):
        tables = [model._meta.db_table for model in _automation_tenant_models()]
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = ANY(%s)",
                [tables],
            )
            state = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}

        missing = [table for table in tables if table not in state]
        self.assertEqual(missing, [], f"tables not found in pg_class: {missing}")

        unprotected = [table for table, (enabled, forced) in state.items() if not (enabled and forced)]
        self.assertEqual(
            unprotected, [], f"these automation tables lack ENABLE/FORCE ROW LEVEL SECURITY: {unprotected}"
        )

    def test_every_automation_table_has_a_policy(self):
        tables = [model._meta.db_table for model in _automation_tenant_models()]
        with connection.cursor() as cursor:
            cursor.execute("SELECT tablename FROM pg_policies WHERE tablename = ANY(%s)", [tables])
            with_policy = {row[0] for row in cursor.fetchall()}
        self.assertEqual(
            sorted(set(tables) - with_policy), [], "every automation table needs a tenant isolation policy"
        )


class AutomationFailClosedTests(TestCase):
    def test_queries_return_nothing_when_no_tenant_context_is_set(self):
        org, _, _ = make_org_with_owner("Org", "automation-rls@example.com")
        with tenant_context(organization_id=org.id):
            AutomationRule.objects.create(
                organization=org, name="Rule A", trigger_type="manual", status=RuleStatus.DRAFT,
            )
        self.assertEqual(AutomationRule.objects.count(), 0)

    def test_raw_sql_is_blocked_without_tenant_guc(self):
        org, _, _ = make_org_with_owner("Org2", "automation-rls-2@example.com")
        with tenant_context(organization_id=org.id):
            AutomationRule.objects.create(
                organization=org, name="Rule B", trigger_type="manual", status=RuleStatus.DRAFT,
            )
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL app.current_organization_id = ''")
            cursor.execute("SELECT count(*) FROM automation_automationrule WHERE name = %s", ["Rule B"])
            count = cursor.fetchone()[0]
        self.assertEqual(count, 0)
