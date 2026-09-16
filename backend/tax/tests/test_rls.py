"""Row Level Security for the tax app.

Walks every `TenantScopedModel` in the app and asserts the Postgres policy is
actually on its table, so a model added later without an entry in
`migrations/0002_enable_rls.py` fails here rather than shipping unprotected.

`StateCode` is the one model in this app deliberately outside that walk: it is
global reference data with no `organization` column (see tax/models/state.py),
so it is neither tenant-scoped nor RLS-protected, and asserting otherwise would
be asserting a bug. `test_state_code_is_deliberately_global` pins that decision
so a later change to org-scope it has to be a deliberate one.
"""

from django.apps import apps
from django.db import connection
from django.test import TestCase

from core.models import TenantScopedModel
from core.tenancy import clear_tenant_context, tenant_context
from tax.models import StateCode, TaxRate
from tax.tests.base import TaxTestsBase


def _tax_tenant_models():
    return [
        model
        for model in apps.get_app_config("tax").get_models()
        if issubclass(model, TenantScopedModel)
    ]


class TaxRlsCoverageTests(TestCase):
    def test_every_tenant_scoped_model_is_accounted_for(self):
        tenant_scoped = {model.__name__ for model in _tax_tenant_models()}
        everything = {model.__name__ for model in apps.get_app_config("tax").get_models()}
        # StateCode is the only permitted exception, and it is exhaustive:
        # anything else appearing here is a model someone forgot to scope.
        self.assertEqual(everything - tenant_scoped, {"StateCode"})

    def test_state_code_is_deliberately_global(self):
        self.assertFalse(issubclass(StateCode, TenantScopedModel))
        self.assertNotIn("organization", [field.name for field in StateCode._meta.get_fields()])

    def test_every_tax_table_has_rls_enabled_and_forced(self):
        tables = [model._meta.db_table for model in _tax_tenant_models()]
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT relname, relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE relname = ANY(%s)",
                [tables],
            )
            rows = {name: (enabled, forced) for name, enabled, forced in cursor.fetchall()}

        self.assertEqual(set(rows) ^ set(tables), set(), "a tax table is missing from pg_class")
        unprotected = [name for name, (enabled, forced) in rows.items() if not (enabled and forced)]
        self.assertEqual(
            unprotected, [], f"tax tables without RLS enabled AND forced: {unprotected}"
        )

    def test_every_tax_table_has_a_policy(self):
        tables = [model._meta.db_table for model in _tax_tenant_models()]
        with connection.cursor() as cursor:
            cursor.execute("SELECT tablename FROM pg_policies WHERE tablename = ANY(%s)", [tables])
            with_policy = {row[0] for row in cursor.fetchall()}
        self.assertEqual(set(tables) - with_policy, set())


class TaxFailClosedTests(TaxTestsBase):
    def test_queries_return_nothing_with_no_tenant_context(self):
        with tenant_context(organization_id=self.org_a.id):
            TaxRate.objects.create(organization=self.org_a, name="GST 18%", rate=18)

        clear_tenant_context()
        # The unscoped manager skips the application filter but not the DB
        # policy - so this second assertion is the one proving the database is
        # doing the work, not the manager.
        self.assertEqual(TaxRate.objects.count(), 0)
        self.assertEqual(TaxRate.all_objects.count(), 0)

    def test_one_organization_cannot_read_anothers_tax_configuration(self):
        with tenant_context(organization_id=self.org_a.id):
            TaxRate.objects.create(organization=self.org_a, name="GST 18%", rate=18)

        with tenant_context(organization_id=self.org_b.id):
            self.assertEqual(TaxRate.objects.count(), 0)
            self.assertEqual(TaxRate.all_objects.count(), 0)
            # Org A's profile, created in the fixture, is equally invisible.
            from tax.selectors import get_tax_profile

            self.assertIsNone(get_tax_profile(organization=self.org_a))

    def test_the_state_master_is_readable_without_a_tenant(self):
        # The counterpart assertion: global reference data must NOT fail
        # closed, or determination breaks for every tenant.
        clear_tenant_context()
        self.assertGreater(StateCode.objects.count(), 0)
