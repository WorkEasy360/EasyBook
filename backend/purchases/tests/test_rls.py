"""Database-level Row Level Security across every purchases table.

The per-document test files each prove isolation for the model they cover.
This file closes the gap those leave: it walks EVERY TenantScopedModel in the
app and asserts the Postgres policy is actually on the table — so a model
added later without an entry in `migrations/0002_enable_rls.py` fails here
rather than shipping with an unprotected table nobody thought to test.

`enable_rls_org_scoped` applies FORCE ROW LEVEL SECURITY, which is what makes
the policy bind even for the table's owner. It still does not protect against
a Postgres superuser — hence the app's runtime DB role must never be one (see
backend/CLAUDE.md).
"""

from django.apps import apps
from django.db import connection
from django.test import TestCase

from core.models import TenantScopedModel
from core.tenancy import clear_tenant_context, tenant_context
from core.tests.factories import make_currency, make_org_with_owner
from purchases.services.vendors import create_vendor


def _purchases_tenant_models():
    return [
        model
        for model in apps.get_app_config("purchases").get_models()
        if issubclass(model, TenantScopedModel)
    ]


class PurchasesRlsCoverageTests(TestCase):
    def test_every_purchases_model_is_tenant_scoped(self):
        models = _purchases_tenant_models()
        self.assertGreater(len(models), 0)
        all_models = list(apps.get_app_config("purchases").get_models())
        self.assertEqual(
            sorted(m.__name__ for m in models),
            sorted(m.__name__ for m in all_models),
            "every purchases model must inherit TenantScopedModel",
        )

    def test_every_purchases_table_has_rls_enabled_and_forced(self):
        tables = [model._meta.db_table for model in _purchases_tenant_models()]
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT relname, relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE relname = ANY(%s)",
                [tables],
            )
            state = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}

        missing = [table for table in tables if table not in state]
        self.assertEqual(missing, [], f"tables not found in pg_class: {missing}")

        unprotected = [table for table, (enabled, forced) in state.items() if not (enabled and forced)]
        self.assertEqual(
            unprotected, [],
            f"these purchases tables lack ENABLE/FORCE ROW LEVEL SECURITY: {unprotected}",
        )

    def test_every_purchases_table_has_a_policy(self):
        tables = [model._meta.db_table for model in _purchases_tenant_models()]
        with connection.cursor() as cursor:
            cursor.execute("SELECT tablename FROM pg_policies WHERE tablename = ANY(%s)", [tables])
            with_policy = {row[0] for row in cursor.fetchall()}
        self.assertEqual(
            sorted(set(tables) - with_policy), [],
            "every purchases table needs a tenant isolation policy",
        )


class PurchasesFailClosedTests(TestCase):
    def test_queries_return_nothing_when_no_tenant_context_is_set(self):
        """Fail closed, not open: with no organization selected the answer is
        an empty queryset, never every row."""
        from purchases.models.vendor import Vendor

        org, _, _ = make_org_with_owner("Org", "purch-rls@example.com")
        currency = make_currency("INR")
        with tenant_context(organization_id=org.id):
            create_vendor(
                organization=org, vendor_code="V-1", display_name="Acme", currency=currency
            )
            self.assertEqual(Vendor.objects.count(), 1)

        clear_tenant_context()
        self.assertEqual(Vendor.objects.count(), 0)
        # The unscoped manager skips the application filter but NOT the
        # database policy, so it is equally empty.
        self.assertEqual(Vendor.all_objects.count(), 0)
